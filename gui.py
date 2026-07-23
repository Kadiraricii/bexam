"""
Blackboard Sinav PDF Yakalayici - GUI kontrol paneli.

Kullanim:
    source .venv/bin/activate
    python3 gui.py

Mantik (2 asama):
1. "Tarayıcıyı Aç" - taracici acilir, SSO ile giris yapilir (2FA dahil).
   Sifre hicbir yerde toplanmaz/saklanmaz - giris tamamen gorunur
   taraycida olur.
2. Tarayicida herhangi bir sayfaya gidilir, "Bul ve Tara" basilir - bu
   SADECE sayfayi tarayip NE bulundugunu gosterir, henuz indirmez.
   Sayfa TURU otomatik anlasilir:
     a) Dersin "Not Defteri" sayfasindaysa (birden fazla sinav satiri +
        Goruntule butonu varsa) -> o derste bulunan TUM sinavlar listelenir.
     b) Bir sinavin ogrenci listesi sayfasindaysa ("Ogrenciler" paneli
        varsa) -> o sinava giren TUM ogrenciler listelenir.
3. Bulunanlar onaylanirsa "PDF Olarak Indir" basilir - ancak o zaman
   gercek yakalama/indirme baslar (scan_course.py / scan_grade_center.py
   mantigi). Baska bir sayfaya gidip tekrar "Bul ve Tara" basilarak
   istenildigi kadar ders/sinav islenebilir.

Cikti: output/{ders}/{sinav}/... - ders adi sayfa basligindan tahmin
edilir. Ayrica her indirme oturumu output/indirme_log.txt'ye eklenir.

Arayuz sol menulu bir "masaustu uygulamasi" iskeleti kullanir (Ana Sayfa/
Çıktılar/Loglar/Ayarlar/Yardım) - gercek islevsellik (Tarayıcıyı Aç/Bul ve
Tara/PDF Olarak İndir akisi) Ana Sayfa'da; digerleri ya gercekten calisan
kucuk yardimci sayfalar (Çıktılar, Loglar, Ayarlar, Yardım) ya da henuz
yapilmamis oldugunu ACIKCA soyleyen bir "yakında" notu (İndirme - Ana
Sayfa'nin zaten yaptigi isin bir kopyasi olacagi icin bilerek ertelendi).

Onemli - threading: Playwright'in senkron API'si TEK bir thread'den
kullanilmak zorunda. Bu yuzden butun Playwright islemleri tek, kalici
bir worker thread'de sirayla islenir; GUI ile aralarinda iki kuyruk var
(command_queue: GUI->worker, gui_queue: worker->GUI).
"""

import platform
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from playwright.sync_api import Page, sync_playwright

from common import (
    BASE_URL,
    DOWNLOAD_LOG_FILENAME,
    OUTPUT_DIR,
    PROFILE_DIR,
    STUDENT_ROSTER_CSV_FILENAME,
    already_captured_titles,
    append_download_log,
    browser_launch_kwargs,
    check_output_writable,
    clear_stale_profile_lock,
    cloud_sync_warning,
    DEFAULT_FOLDER_MAX_CHARS,
    derive_course_label,
    find_blackboard_pages,
    has_seen_onboarding,
    is_browser_closed_error,
    is_chrome_missing_error,
    is_profile_lock_error,
    live_url,
    load_student_roster,
    mark_onboarding_seen,
    open_in_file_manager,
    resolve_active_page,
    sanitize_filename,
    set_windows_dpi_awareness,
    wait_for_blackboard,
)
from scan_course import (
    GRADING_STATUS_COMPLETE_MARKERS_LABEL,
    NotSubmittedOrNotExam,
    capture_exam_submissions,
    find_exam_row_names,
    return_to_grades_list,
)
from scan_grade_center import (
    BATCH_PAUSE_S,
    BATCH_SIZE,
    MAX_CONSECUTIVE_FAILURES,
    capture_student,
    find_student_rows,
)
from scan_students import find_student_roster, write_student_roster_csv

# ---------- gorsel dil (renk/tipografi) ----------
# Kullanicinin kendi belirttigi, Linear/Raycast/Notion Desktop esintili
# palet - "2026'ya yakisir" masaustu uygulamasi hissi icin.
COLOR_BG = "#F8FAFC"
COLOR_CARD = "#FFFFFF"
COLOR_SIDEBAR_BG = "#FFFFFF"
COLOR_BORDER = "#E2E8F0"
COLOR_TEXT = "#0F172A"
COLOR_MUTED = "#64748B"

COLOR_ACCENT = "#2563EB"
COLOR_ACCENT_HOVER = "#1D4ED8"
COLOR_ACCENT_SOFT = "#EFF6FF"

COLOR_SUCCESS = "#22C55E"
COLOR_SUCCESS_SOFT = "#DCFCE7"
COLOR_WARNING = "#F59E0B"
COLOR_WARNING_SOFT = "#FEF3C7"
COLOR_WARNING_DIM = "#FDE9BE"
COLOR_DANGER = "#EF4444"
COLOR_DANGER_SOFT = "#FEE2E2"
COLOR_GHOST_HOVER = "#F1F5F9"

FONT_TITLE = ("", 26, "bold")
FONT_SUBTITLE = ("", 13)
FONT_SECTION = ("", 15, "bold")
FONT_STEP_TITLE = ("", 16, "bold")
FONT_BODY = ("", 13)
FONT_MUTED = ("", 12)
FONT_MUTED_BOLD = ("", 12, "bold")
FONT_BUTTON = ("", 13, "bold")
FONT_STAT_NUMBER = ("", 28, "bold")
FONT_NAV_ITEM = ("", 14, "bold")

# Windows'ta Tk, emoji karakterlerini (🎓📂🔄⚡ vb.) VARSAYILAN olarak
# "Segoe UI Symbol" fontuyla render ediyor - bu, macOS'taki renkli emoji
# gorunumunun aksine GRI/TEK RENK, silik bir sonuc veriyor (CANLI
# DOGRULANDI). "Segoe UI Emoji" (Windows 10/11'de standart olarak kurulu
# gelen bir font) ACIKCA istenirse Tk dogru, renkli glif setini seciyor.
# macOS/Linux'ta sistem varsayilanini ("") bozmadan birakiyoruz - orada
# zaten dogru render oluyor.
_EMOJI_FONT_FAMILY = "Segoe UI Emoji" if platform.system() == "Windows" else ""


def _emoji_font(size: int, weight: str | None = None) -> tuple:
    """SADECE emoji/ikon icin kullanilan, baska metinle KARISMAYAN
    Canvas.create_text()/Label metinlerinde kullanilir - platforma gore
    doğru fontu seçer (bkz. yukaridaki _EMOJI_FONT_FAMILY docstring'i).
    Emoji + normal (Turkce) metnin AYNI widget'ta karistigi yerlerde
    KULLANILMAMALI - Segoe UI Emoji, Latin/Turkce karakterleri iyi
    kapsamiyor."""
    return (_EMOJI_FONT_FAMILY, size, weight) if weight else (_EMOJI_FONT_FAMILY, size)


DEFAULT_WINDOW_WIDTH = 1420
DEFAULT_WINDOW_HEIGHT = 900
DEFAULT_MIN_WIDTH = 1160
DEFAULT_MIN_HEIGHT = 720
# Kompakt (kucultulmus, her zaman en onde) mod icin sabit, standart boyut -
# kullanicinin istegi geregi bu boyut degistirilemez (resizable(False)).
COMPACT_WINDOW_WIDTH = 320
COMPACT_WINDOW_HEIGHT = 250

# "pointinghand" SADECE macOS'ta (Aqua) taninan bir Tk imlec adi -
# Windows/Linux'ta widget OLUSTURULURKEN cursor= parametresine verilirse
# TclError firlatip uygulamayi daha acilista cokertiyordu. "hand2" ise
# her uc platformda da gecerli, ayni "tiklanabilir el" imlecini verir.
CURSOR_HAND = "hand2"

# Canli islem logunun (sayfa gecislerinde/kompakt modda kaybolmamasi icin
# bellekte tutulan) gecmisine bir ust sinir - cok uzun oturumlarda bellegin
# sinirsiz buyumesini onler.
LOG_HISTORY_MAX_LINES = 2000


def _rounded_rect_points(x1: float, y1: float, x2: float, y2: float, radius: float) -> list[float]:
    """Canvas.create_polygon(..., smooth=True) ile yuvarlatilmis kose
    verecek nokta listesini uretir - bircok ozel widget tarafindan
    paylasilan kucuk bir cizim yardimcisi."""
    radius = min(radius, (x2 - x1) / 2, (y2 - y1) / 2)
    return [
        x1 + radius, y1,
        x2 - radius, y1,
        x2, y1,
        x2, y1 + radius,
        x2, y2 - radius,
        x2, y2,
        x2 - radius, y2,
        x1 + radius, y2,
        x1, y2,
        x1, y2 - radius,
        x1, y1 + radius,
        x1, y1,
    ]


class RoundedButton(tk.Canvas):
    """ttk.Button'un macOS'ta bg/hover renklerini yok saymasi yuzunden
    (Aqua tema kisitlamasi) tamamen Canvas uzerine cizilen, pill seklinde
    ozel bir buton. Var olan tum cagiran kod `.config(state=..., text=...)`
    ile calistigi icin ayni arayuzu destekliyor.

    kind: "primary" (mavi, ana eylem) | "secondary" (hafif/ghost) |
    "success" (yesil, PDF Olarak Indir gibi olumlu eylemler) |
    "danger" (kirmizi, Guvenli Cikis gibi ayrilma eylemleri)."""

    _KIND_COLORS = {
        "primary": (COLOR_ACCENT, COLOR_ACCENT_HOVER, "#ffffff"),
        "success": (COLOR_SUCCESS, "#16A34A", "#ffffff"),
        "danger": (COLOR_DANGER, "#DC2626", "#ffffff"),
    }

    def __init__(self, parent, text: str, command, kind: str = "primary",
                 width: int = 152, height: int = 38) -> None:
        bg = parent["bg"] if isinstance(parent, (tk.Frame, tk.Canvas)) else COLOR_CARD
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0)
        self._command = command
        self._kind = kind
        self._text = text
        self._width = width
        self._height = height
        self._state = "normal"
        self._hover = False
        self._pressed = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._draw()

    def _colors(self) -> tuple[str, str, str]:
        """(dolgu, yazi, kenarlik) dondurur."""
        if self._state == "disabled":
            return (COLOR_BORDER, "#a4abbd", "")
        if self._kind in self._KIND_COLORS:
            base, hover, text_color = self._KIND_COLORS[self._kind]
            fill = hover if (self._hover or self._pressed) else base
            return (fill, text_color, "")
        fill = COLOR_GHOST_HOVER if (self._hover or self._pressed) else COLOR_CARD
        return (fill, COLOR_TEXT, COLOR_BORDER)

    def _draw(self) -> None:
        self.delete("all")
        fill, text_color, outline = self._colors()
        radius = self._height / 2
        points = _rounded_rect_points(1, 1, self._width - 1, self._height - 1, radius)
        self.create_polygon(points, smooth=True, fill=fill, outline=outline or fill, width=1)
        self.create_text(
            self._width / 2, self._height / 2, text=self._text,
            fill=text_color, font=FONT_BUTTON,
        )

    def _on_enter(self, _event=None) -> None:
        if self._state == "disabled":
            return
        self._hover = True
        try:
            self.config(cursor=CURSOR_HAND)
        except tk.TclError:
            pass
        self._draw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._pressed = False
        self._draw()

    def _on_press(self, _event=None) -> None:
        if self._state == "disabled":
            return
        self._pressed = True
        self._draw()

    def _on_release(self, event=None) -> None:
        was_pressed = self._pressed
        self._pressed = False
        self._draw()
        if self._state == "disabled" or not was_pressed or self._command is None:
            return
        # Sadece serbest birakma ANI hala butonun uzerindeyse tikla say
        # (basip disariya surukleyip birakma tiklama SAYILMAMALI).
        if event is not None and not (0 <= event.x <= self._width and 0 <= event.y <= self._height):
            return
        self._command()

    def config(self, **kwargs) -> None:  # type: ignore[override]
        redraw = False
        if "state" in kwargs:
            self._state = kwargs.pop("state")
            redraw = True
        if "text" in kwargs:
            self._text = kwargs.pop("text")
            redraw = True
        if kwargs:
            super().config(**kwargs)
        if redraw:
            self._draw()

    configure = config  # type: ignore[assignment]


class StatusDot(tk.Canvas):
    """Baglanti durumunu gosteren kucuk, nabiz atan bir nokta.

    'connecting' durumunda yumusak bir nabiz animasyonu oynatir - bu,
    kendi basina tekrarlayan bir `after()` dongusu gerektirdigi icin
    yasam dongusu ozenle yonetiliyor: durum degisince VEYA widget yok
    edilince dongu MUTLAKA iptal ediliyor - aksi halde yok edilmis bir
    widget'a `after()` geri cagirmasi TclError firlatirdi."""

    def __init__(self, parent, size: int = 11) -> None:
        bg = parent["bg"] if isinstance(parent, (tk.Frame, tk.Canvas)) else COLOR_CARD
        super().__init__(parent, width=size, height=size, bg=bg, highlightthickness=0)
        self._size = size
        self._color = COLOR_MUTED
        self._pulse_job: str | None = None
        self._pulse_on = False
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        self.create_oval(1, 1, self._size - 1, self._size - 1, fill=self._color, outline="")

    def set_state(self, state: str) -> None:
        """state: 'connected' | 'connecting' | 'disconnected'."""
        self._stop_pulse()
        if state == "connected":
            self._color = COLOR_SUCCESS
        elif state == "connecting":
            self._color = COLOR_WARNING
            self._start_pulse()
        else:
            self._color = COLOR_MUTED
        self._draw()

    def _start_pulse(self) -> None:
        self._pulse_on = True
        self._pulse_tick(phase=0)

    def _pulse_tick(self, phase: int) -> None:
        if not self._pulse_on:
            return
        self._color = COLOR_WARNING if phase == 0 else COLOR_WARNING_DIM
        self._draw()
        self._pulse_job = self.after(450, lambda: self._pulse_tick(1 - phase))

    def _stop_pulse(self) -> None:
        self._pulse_on = False
        if self._pulse_job is not None:
            try:
                self.after_cancel(self._pulse_job)
            except Exception:
                pass
            self._pulse_job = None

    def destroy(self) -> None:
        self._stop_pulse()
        super().destroy()


class ChevronToggle(tk.Canvas):
    """Acilir/kapanir panelller icin kalin, cizgiyle cizilmis bir "v"/"^"
    ok simgesi (Unicode "▾"/"▴" karakterleri yerine) - bu karakterlerin
    goruntusu yazi tipine gore macOS/Windows arasinda tutarsiz ve ince/
    silik gorunebiliyordu. Canvas uzerine kendimiz cizince (bu dosyadaki
    RoundedButton/StatusDot ile ayni desen) her platformda ayni, kalin ve
    net bir sekil garanti ediliyor.

    expanded=False iken asagi (v, "genislet"), True iken yukari
    (^, "daralt") gosterir."""

    def __init__(self, parent, command, size: int = 22, expanded: bool = False) -> None:
        bg = parent["bg"] if isinstance(parent, (tk.Frame, tk.Canvas)) else COLOR_WARNING_SOFT
        super().__init__(parent, width=size, height=size, bg=bg, highlightthickness=0, cursor=CURSOR_HAND)
        self._size = size
        self._command = command
        self._expanded = expanded
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        s = self._size
        pad = s * 0.28
        # "v" (kapali/genislet): sol-ust -> orta-alt -> sag-ust
        # "^" (acik/daralt): sol-alt -> orta-ust -> sag-alt
        if self._expanded:
            points = [pad, s - pad, s / 2, pad, s - pad, s - pad]
        else:
            points = [pad, pad, s / 2, s - pad, s - pad, pad]
        self.create_line(
            *points, fill=COLOR_WARNING, width=2.4, joinstyle="round", capstyle="round", smooth=False,
        )

    def _on_click(self, _event=None) -> None:
        self.set_expanded(not self._expanded)
        if self._command is not None:
            self._command(self._expanded)

    def set_expanded(self, expanded: bool) -> None:
        self._expanded = expanded
        self._draw()


class ProgressBar(tk.Canvas):
    """Yatay, yumusak koseli, doldurulabilir bir ilerleme cubugu.
    Kendi zamanlayicisi yok - sadece disaridan gelen 'progress'
    mesajlarinda set_progress() ile yeniden ciziliyor."""

    def __init__(self, parent, width: int = 400, height: int = 10) -> None:
        bg = parent["bg"] if isinstance(parent, (tk.Frame, tk.Canvas)) else COLOR_CARD
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0)
        self._width = width
        self._height = height
        self._ratio = 0.0
        # .pack(fill="x") ile gercek genisligi degisecegi icin, cizimi
        # widget'in FIILI boyutuna gore guncel tutmak icin dinliyoruz.
        self.bind("<Configure>", self._on_resize)
        self._draw()

    def _on_resize(self, event) -> None:
        self._width = max(event.width, 1)
        self._height = max(event.height, 1)
        self._draw()

    def set_progress(self, ratio: float) -> None:
        self._ratio = max(0.0, min(1.0, ratio))
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        radius = self._height / 2
        track = _rounded_rect_points(0, 0, self._width, self._height, radius)
        self.create_polygon(track, smooth=True, fill=COLOR_BORDER, outline="")
        fill_width = self._width * self._ratio
        if fill_width >= self._height:
            fill_points = _rounded_rect_points(0, 0, fill_width, self._height, radius)
            self.create_polygon(fill_points, smooth=True, fill=COLOR_ACCENT, outline="")
        elif fill_width > 0:
            self.create_oval(0, 0, self._height, self._height, fill=COLOR_ACCENT, outline="")


class StatCard(tk.Frame):
    """Ust kisimda gorunen 4 istatistik kartindan biri (ör. 'Bulunan 42').
    Renkli, yuvarlak bir ikon karesi + buyuk sayi + kucuk etiketten olusur."""

    def __init__(self, parent, icon: str, label: str, soft_color: str) -> None:
        super().__init__(parent, bg=COLOR_CARD, highlightthickness=0)
        icon_canvas = tk.Canvas(self, width=40, height=40, bg=COLOR_CARD, highlightthickness=0)
        icon_canvas.pack(side="left", padx=(14, 10), pady=14)
        points = _rounded_rect_points(0, 0, 40, 40, 10)
        icon_canvas.create_polygon(points, smooth=True, fill=soft_color, outline="")
        icon_canvas.create_text(20, 20, text=icon, font=_emoji_font(18))

        text_col = tk.Frame(self, bg=COLOR_CARD)
        text_col.pack(side="left", fill="both", expand=True, pady=12, padx=(0, 14))
        self.number_label = tk.Label(
            text_col, text="0", bg=COLOR_CARD, fg=COLOR_TEXT, font=FONT_STAT_NUMBER, anchor="w"
        )
        self.number_label.pack(fill="x")
        tk.Label(
            text_col, text=label, bg=COLOR_CARD, fg=COLOR_MUTED, font=FONT_MUTED, anchor="w"
        ).pack(fill="x")

    def set_value(self, value) -> None:
        self.number_label.config(text=str(value))


class TimelineSteps(tk.Frame):
    """Ust surecin 4 adimini (Tarayıcı -> Tara -> Onayla -> PDF Oluştur)
    gosteren yatay bir sema. set_stage(n) ile 0..4 arasi bir asama
    verilir: n'den once gelen adimlar 'tamamlandi' (yesil tik), n'inci
    adim 'su an aktif' (mavi), sonrakiler 'bekliyor' (gri) olarak cizilir."""

    STEPS = [
        ("Tarayıcı", "Chrome açık"),
        ("Sayfayı Tara", "Keşif tamamlandı"),
        ("Onayla", "İndirme için onay"),
        ("PDF Oluştur", "İşlem bekliyor"),
    ]

    def __init__(self, parent) -> None:
        super().__init__(parent, bg=COLOR_CARD)
        self._circles: list[tk.Canvas] = []
        self._lines: list[tk.Frame] = []
        self._sub_labels: list[tk.Label] = []
        for i, (title, subtitle) in enumerate(self.STEPS):
            if i > 0:
                line = tk.Frame(self, bg=COLOR_BORDER, height=2, width=48)
                line.pack(side="left", anchor="n", pady=(17, 0))
                self._lines.append(line)
            step_col = tk.Frame(self, bg=COLOR_CARD)
            step_col.pack(side="left")
            circle = tk.Canvas(step_col, width=34, height=34, bg=COLOR_CARD, highlightthickness=0)
            circle.pack()
            self._circles.append(circle)
            tk.Label(
                step_col, text=title, bg=COLOR_CARD, fg=COLOR_TEXT,
                font=("", 13, "bold"),
            ).pack(pady=(6, 0))
            sub = tk.Label(
                step_col, text=subtitle, bg=COLOR_CARD, fg=COLOR_MUTED, font=("", 11),
            )
            sub.pack()
            self._sub_labels.append(sub)
        self.set_stage(0)

    def set_stage(self, stage: int) -> None:
        """stage: 0 = hicbiri baslamadi, 1 = tarayici hazir, 2 = tarandi/
        onay bekliyor, 3 = indiriliyor, 4 = tamamlandi."""
        subtitles_by_stage = {
            0: ["Bekleniyor", "Bekleniyor", "Bekleniyor", "Bekleniyor"],
            1: ["Chrome açık", "Bekleniyor", "Bekleniyor", "Bekleniyor"],
            2: ["Chrome açık", "Keşif tamamlandı", "İndirme için onay", "Bekleniyor"],
            3: ["Chrome açık", "Keşif tamamlandı", "Onaylandı", "İndiriliyor..."],
            4: ["Chrome açık", "Keşif tamamlandı", "Onaylandı", "Tamamlandı"],
        }
        subtitles = subtitles_by_stage.get(stage, subtitles_by_stage[0])
        for i, circle in enumerate(self._circles):
            step_number = i + 1
            circle.delete("all")
            if step_number < stage or (stage == 4):
                fill, text, tcolor = COLOR_SUCCESS, "✓", "white"
            elif step_number == stage:
                fill, text, tcolor = COLOR_ACCENT, str(step_number), "white"
            else:
                fill, text, tcolor = COLOR_BORDER, str(step_number), COLOR_MUTED
            circle.create_oval(1, 1, 33, 33, fill=fill, outline="")
            circle.create_text(17, 17, text=text, fill=tcolor, font=("", 14, "bold"))
            self._sub_labels[i].config(text=subtitles[i])
        for i, line in enumerate(self._lines):
            line.config(bg=COLOR_SUCCESS if (i + 1) < stage or stage == 4 else COLOR_BORDER)


def _format_relative_time(dt: datetime) -> str:
    """'X dakika/saat/gün önce' bicimli, insan-okunur goreli zaman."""
    delta = datetime.now() - dt
    seconds = delta.total_seconds()
    if seconds < 60:
        return "az önce"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} dakika önce"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} saat önce"
    days = hours // 24
    return f"{days} gün önce"


# Aktif bir tarama (90 ogrenciye kadar surebilir) sirasinda "Guvenli
# Cikis" basilirsa, worker thread'in su an isledigi TEK ogeyi bitirip
# durmasini bekliyoruz (bkz. self._stop_event). Tek bir ogenin en kotu
# durumda alabilecegi sure: tiklama gecikmesi (<=3sn) + dogrulama
# beklemesi (<=10sn) + gorsel yukleme beklemesi (<=15sn) - bu yuzden
# makul bir tampon payiyla 40 saniye bekliyoruz.
SAFE_EXIT_JOIN_TIMEOUT_S = 40.0


def _page_still_on_blackboard(page) -> bool:
    """Devre kesici tetiklendiginde son bilinen sayfanin hala Blackboard
    domaininde olup olmadigina bakar - degilse (ör. login sayfasina geri
    dusulmusse) oturumun suresi dolmus olabilecegini kullaniciya
    belirtmek icin kullanilir (bkz. cagiran yerdeki emit mesaji).

    live_url() kullaniyor - bkz. common.py'deki docstring: ham page.url,
    SSO'nun capraz-kaynak yonlendirme zincirinde eski bir URL'de takili
    kalabiliyor."""
    try:
        return live_url(page).startswith(BASE_URL)
    except Exception:
        return False


ONBOARDING_STEPS = [
    (
        "🌐",
        "Tarayıcıyı Aç",
        "Program sizin için Chrome tarayıcısını açar. Üniversite hesabınla "
        "normal şekilde giriş yap (2FA dahil) — Blackboard oturumun hazır "
        "hale gelir.",
        "✓ Şifre gerekmez",
    ),
    (
        "🎓",
        "(Opsiyonel) Öğrenci Tara",
        "Dersin Not Defteri > 'Öğrenciler' sekmesine git, 'Öğrenci Tara'ya "
        "bas. Program tüm öğrencilerin adını ve numarasını bir CSV "
        "dosyasına kaydeder — bu sayede indirilecek PDF'lerin adına "
        "öğrenci numarası da otomatik eklenir.",
        "✓ PDF adı: sınav_öğrenciNo_isim-soyisim",
    ),
    (
        "🔍",
        "Sayfaya Git, Bul ve Tara'ya Bas",
        "Dersin Not Defteri'ne ya da bir sınavın öğrenci listesi "
        "göründüğü sayfaya git. Bu adımda sayfa analiz edilir, bulunanlar "
        "listelenir.",
        "✓ Sadece tarar, indirmez",
    ),
    (
        "✅",
        "Bulunanları Onayla",
        "Bulunan sınavları ya da öğrencileri incele; hazırsan 'PDF Olarak "
        "İndir'e bas. Gerçek yakalama/indirme ancak bu onaydan sonra "
        "başlar.",
        "✓ Tam kontrol sizde",
    ),
    (
        "📄",
        "PDF'lerini Al",
        "Seçilen sayfalar PDF olarak oluşturulur, ders/sınav adına göre "
        "otomatik klasörlenir ve zaman damgalı indirme_log.txt kaydına "
        "eklenir.",
        "✓ Arka planda güvenli indirme",
    ),
]

ONBOARDING_FEATURES = [
    ("🛡", "Güvenli ve Özel", "Şifreniz bu programda hiçbir yerde saklanmaz."),
    ("📂", "Düzenli Arşiv", "PDF'ler ders ve sınav adına göre otomatik klasörlenir."),
    ("🔄", "Tekrarlanabilir", "Daha önce indirilenler atlanır, eksikler tamamlanır."),
    ("⚡", "Kolay Kullanım", "Sadece adımları izleyin, gerisini program halleder."),
]

COLOR_ONBOARD_SIDE_BG = "#EEF2FB"

NAV_ITEMS = [
    ("home", "🏠", "Ana Sayfa"),
    ("download", "📥", "İndirme"),
    ("outputs", "📂", "Çıktılar"),
    ("logs", "📜", "Loglar"),
    ("settings", "⚙", "Ayarlar"),
    ("help", "❓", "Yardım"),
]


class CardFrame(tk.Frame):
    """`_make_card`'in urettigi dis cerceve - ic dolgulu icerik alanini
    `.inner` olarak tasir. Duz bir tk.Frame yerine bu ince alt sinif
    kullanilarak `.inner` DECLARE edilmis bir oznitelik oluyor; aksi
    halde her `card.inner` erisimi (dusinlerce yerde) statik tip
    denetiminde "boyle bir oznitelik yok" hatasi veriyordu."""

    inner: tk.Frame


class BlackboardGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Blackboard Sınav PDF Yakalayıcı")
        self.root.geometry(f"{DEFAULT_WINDOW_WIDTH}x{DEFAULT_WINDOW_HEIGHT}")
        self.root.minsize(DEFAULT_MIN_WIDTH, DEFAULT_MIN_HEIGHT)
        self.root.configure(bg=COLOR_BG)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.gui_queue: queue.Queue = queue.Queue()
        self.command_queue: queue.Queue = queue.Queue()
        self.output_dir: Path = OUTPUT_DIR
        self.output_dir_var = tk.StringVar(value=str(self.output_dir))
        self.tracked_url_var = tk.StringVar(value="—")
        self._pending_discovery: dict | None = None
        self._dont_show_again_var = tk.BooleanVar(value=False)
        self._stop_event = threading.Event()

        # Sayfa gecisleri arasinda (ör. Loglar'a bakip Ana Sayfa'ya
        # donunce) dugmelerin YENIDEN insa edilirken YANLIS (varsayilan)
        # duruma donmemesi icin gercek durumu ayrica takip ediyoruz.
        self._connection_state = "disconnected"  # disconnected|connecting|connected
        self._scan_enabled = False
        self._download_enabled = False
        # "Öğrenci Tara" (Not Defteri > Öğrenciler sekmesinden ad+numara
        # CSV'si cikaran ayri, tek-seferlik islem) - ana tarama/indirme
        # akisindan BAGIMSIZ ama ayni TEK Playwright worker thread'ini
        # paylastigi icin, digerleriyle CAKISMAMASI icin kendi bayragi var.
        self._student_scan_enabled = False
        self._timeline_stage = 0
        self._totals = {"ok": 0, "skip": 0, "fail": 0}
        self._compact_mode = False
        # Canli islem logunun tam metni - log_text widget'i sayfa gecisinde
        # yok edilip yeniden kuruldugu (ya da kompakt modda hic olmadigi)
        # icin, mesajlar burada birikir ve Ana Sayfa her kuruldugunda
        # yeniden yazilir. Onceden kullanici baska sayfadayken/kompakt
        # moddayken gelen loglar SESSIZCE KAYBOLUYORDU.
        self._log_history: list[str] = []
        self._exiting = False

        self._configure_styles()

        self.container = tk.Frame(self.root, bg=COLOR_BG)
        self.container.pack(fill="both", expand=True)

        self.current_page = "home"
        if has_seen_onboarding():
            self._build_app_shell()
        else:
            self._build_onboarding_screen()

        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
        self.root.after(150, self._poll_gui_queue)

    # ---------- ortak arayuz yardimcilari ----------

    def _configure_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use(style.theme_use())
        except Exception:
            pass

    def _clear_container(self) -> None:
        for widget in self.container.winfo_children():
            widget.destroy()

    def _make_badge(self, parent: tk.Widget, number: int) -> tk.Canvas:
        size = 30
        canvas = tk.Canvas(parent, width=size, height=size, bg=COLOR_BG, highlightthickness=0)
        canvas.create_oval(2, 2, size - 2, size - 2, fill=COLOR_ACCENT, outline="")
        canvas.create_text(size / 2, size / 2, text=str(number), fill="white", font=("", 14, "bold"))
        return canvas

    def _make_card(self, parent: tk.Widget) -> CardFrame:
        """Beyaz bir 'kart' paneli olusturur - ince, sert kenarlik yerine
        ustte ince renkli bir 'aksan seridi' ile sayfa arka planindan
        ayristiriliyor. Icerik icin dondurulen frame'in kendisi degil,
        ic dolgulu `.inner` alani kullanilmali."""
        outer = CardFrame(parent, bg=COLOR_CARD, highlightbackground=COLOR_BORDER, highlightthickness=1)
        inner = tk.Frame(outer, bg=COLOR_CARD)
        inner.pack(fill="both", expand=True, padx=20, pady=18)
        outer.inner = inner
        return outer

    def _make_scrollable_area(self, parent: tk.Widget) -> tk.Frame:
        """Dikey kaydirilabilir bir icerik alani olusturur - donen frame'e
        normal bir Frame gibi widget eklenebilir; icerik pencereden UZUN
        olsa bile (ör. Yardim sayfasindaki adim listesi + yeni ozellik
        kartlari + Chrome uyarisi bir arada) disariya TASMAZ, kaydirilarak
        gorunur. Fare tekerlegi SADECE imlec bu alanin uzerindeyken
        dinlenir (bind_all kalici birakilirsa, sayfa degisip canvas yok
        edildikten sonra gelecek bir tekerlek olayi TclError firlatirdi -
        bu yuzden <Enter>/<Leave> ile ac/kapa yapiliyor).

        NOT: bu, onceden onboarding ekraninda TEK KULLANIMLIK olarak
        yazilmisti; simdi Yardim sayfasinda da AYNI ihtiyac dogunca (ayni
        incelikli widget-yasam-dongusu mantigini IKINCI kez elle
        kopyalamak yerine) tek, paylasilan bir yardimciya cikarildi."""
        scroll_wrap = tk.Frame(parent, bg=COLOR_BG)
        scroll_wrap.pack(fill="both", expand=True)

        canvas = tk.Canvas(scroll_wrap, bg=COLOR_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(scroll_wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        body = tk.Frame(canvas, bg=COLOR_BG)
        window = canvas.create_window((0, 0), window=body, anchor="nw")

        def _sync_scrollregion(_event=None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def _sync_body_width(event) -> None:
            # body'yi HER ZAMAN canvas'in guncel genisligine esitle - aksi
            # halde metinler kendi wraplength'lerine gore dar kalir ve
            # gereksiz yatay bosluk/kaydirma olusur.
            canvas.itemconfig(window, width=event.width)

        body.bind("<Configure>", _sync_scrollregion)
        canvas.bind("<Configure>", _sync_body_width)

        def _on_mousewheel(event) -> None:
            try:
                if getattr(event, "num", None) == 5 or getattr(event, "delta", 0) < 0:
                    canvas.yview_scroll(1, "units")
                elif getattr(event, "num", None) == 4 or getattr(event, "delta", 0) > 0:
                    canvas.yview_scroll(-1, "units")
            except tk.TclError:
                # Sayfa zaten degisip canvas yok edilmisken gecikmeli bir
                # tekerlek olayi gelirse sessizce yok say.
                pass

        def _bind_mousewheel(_event=None) -> None:
            canvas.bind_all("<MouseWheel>", _on_mousewheel)
            canvas.bind_all("<Button-4>", _on_mousewheel)
            canvas.bind_all("<Button-5>", _on_mousewheel)

        def _unbind_mousewheel(_event=None) -> None:
            canvas.unbind_all("<MouseWheel>")
            canvas.unbind_all("<Button-4>")
            canvas.unbind_all("<Button-5>")

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)

        return body

    def _build_chrome_flag_notice(
        self, parent: tk.Widget, wraplength: int = 640, start_expanded: bool = False,
    ) -> tk.Frame:
        """Chrome'un acilista gosterdigi zararsiz "desteklenmeyen bayrak"
        uyarisini (--disable-blink-features=AutomationControlled - SSO
        otomasyon tespitini onlemek icin bilerek eklenen bayrak, bkz.
        common.py::browser_launch_kwargs) aciklayan, kucuk, sari bir
        bilgi bandi. Ana Sayfa ve Yardim sayfasinda ayni metni
        tekrarlamamak icin tek yerden uretiliyor (onboarding'de artik
        gosterilmiyor - Ana Sayfa'da kalici oldugu icin tekrar gerekmiyor).

        wraplength cagirana gore ayarlanabilir.

        start_expanded=False (varsayilan, ör. Ana Sayfa): KAPALI (kucuk,
        sadece baslik) baslar - sayfada surekli buyuk yer kaplamasin diye.
        start_expanded=True (ör. Yardim sayfasi - zaten bilgi almaya
        gelinen bir sayfa, kucuk baslamasinin bir faydasi yok): direkt
        ACIK baslar. Her iki durumda da basliktaki ChevronToggle'a
        tiklanarak durum degistirilebilir."""
        card = tk.Frame(parent, bg=COLOR_WARNING_SOFT)
        inner = tk.Frame(card, bg=COLOR_WARNING_SOFT)
        inner.pack(fill="x", padx=14, pady=10)

        header_row = tk.Frame(inner, bg=COLOR_WARNING_SOFT)
        header_row.pack(fill="x")
        tk.Label(
            header_row, text="⚠  Beklenen Bir Tarayıcı Uyarısı", bg=COLOR_WARNING_SOFT,
            fg=COLOR_WARNING, font=("", 13, "bold"), anchor="w",
        ).pack(side="left", fill="x", expand=True)

        body = tk.Frame(inner, bg=COLOR_WARNING_SOFT)
        tk.Label(
            body,
            text="Chrome açılışta aşağıdaki uyarıyı gösterebilir. Üniversitenin "
            "giriş sistemiyle (SSO) uyumluluk için bilerek kullanılan bir "
            "ayardan kaynaklanır — kapatılamaz, programın işleyişini etkilemez.",
            bg=COLOR_WARNING_SOFT, fg=COLOR_TEXT, font=("", 12), anchor="w",
            justify="left", wraplength=wraplength,
        ).pack(fill="x", pady=(4, 8))

        quote_box = tk.Frame(body, bg=COLOR_CARD, highlightbackground=COLOR_WARNING, highlightthickness=1)
        quote_box.pack(fill="x")
        tk.Label(
            quote_box,
            text="\"Desteklenmeyen bir komut satırı işareti kullanıyorsunuz: "
            "--disable-blink-features=AutomationControlled. Sağlamlık ve "
            "güvenlik düzeyi düşecektir.\"",
            bg=COLOR_CARD, fg=COLOR_MUTED, font=("", 11), anchor="w",
            justify="left", wraplength=wraplength - 20,
        ).pack(fill="x", padx=10, pady=8)

        # "Guvenlik zafiyeti yok" gibi mutlak bir iddia yerine NE
        # YAPMADIGINI somut olarak belirtiyoruz - bu bayrak sadece
        # sitelerin "otomasyonla mi kontrol ediliyorum" JS sinyalini
        # gizliyor; sandbox/HTTPS/site izolasyonu gibi gercek koruma
        # katmanlarina dokunmuyor. Chrome'un uyarisi HER desteklenmeyen
        # bayrak icin gosterilen genel bir mesaj, bu bayraga ozel bir
        # risk analizi degil.
        tk.Label(
            body,
            text="Bu ayar yalnızca otomasyon tespit sinyalini gizler; sandbox, "
            "HTTPS doğrulaması ve diğer güvenlik korumaları etkilenmez.",
            bg=COLOR_WARNING_SOFT, fg=COLOR_MUTED, font=("", 11), anchor="w",
            justify="left", wraplength=wraplength,
        ).pack(fill="x", pady=(8, 0))

        def _apply_expanded(expanded: bool) -> None:
            if expanded:
                body.pack(fill="x", pady=(8, 0))
            else:
                body.pack_forget()

        toggle = ChevronToggle(header_row, command=_apply_expanded, expanded=start_expanded)
        toggle.pack(side="right")
        _apply_expanded(start_expanded)
        return card

    def _log(self, message: str) -> None:
        """Mesaji kalici gecmise ekler ve (Ana Sayfa'daysak) log widget'ina
        yazar. Ana Sayfa'da DEGILKEN cagirmak guvenli - mesaj kaybolmaz,
        sayfa yeniden kuruldugunda gecmisten geri yazilir."""
        self._log_history.append(message)
        if len(self._log_history) > LOG_HISTORY_MAX_LINES:
            self._log_history = self._log_history[-LOG_HISTORY_MAX_LINES:]
        if not self._on_home_page():
            return
        self._append_log_line(message)

    def _append_log_line(self, message: str) -> None:
        tag = None
        if "  OK" in message or message.startswith("OK"):
            tag = "ok"
        elif "HATA" in message:
            tag = "fail"
        elif "UYARI" in message:
            tag = "warn"
        elif "atlandı" in message or "Atlandı" in message:
            tag = "skip"
        start = self.log_text.index("end")
        self.log_text.insert("end", message + "\n")
        if tag:
            self.log_text.tag_add(tag, start, self.log_text.index("end"))
        self.log_text.see("end")

    def _emit_progress(self, done: int, total: int, totals: dict) -> None:
        """Uzun surebilen taramalarda (90 ogrenciye kadar) ozet etiketini
        canli guncellemek icin - kullanici islemin gercekten ilerledigini
        gorsun, arayuz donmus gibi gorunmesin."""
        self.gui_queue.put(("progress", {"done": done, "total": total, "totals": dict(totals)}))

    def _describe_last_download(self) -> str:
        log_path = self.output_dir / DOWNLOAD_LOG_FILENAME
        if not log_path.exists():
            return "Henüz indirme yapılmadı"
        mtime = datetime.fromtimestamp(log_path.stat().st_mtime)
        return _format_relative_time(mtime)

    def _update_last_download_label(self) -> None:
        """`last_download_label` sidebar'a ait bir widget - kompakt modda
        sidebar TAMAMEN yok ediliyor ama attribute REFERANSI hala duruyor
        (Python nesnesi silinmedi, sadece Tk widget'i yok edildi). Sadece
        `hasattr` kontrolu bu durumda YETERSIZ kaliyordu (yok edilmis bir
        widget'a .config cagirinca TclError firlatiyordu) - bu yuzden
        kompakt modda da ACIKCA kontrol ediyoruz."""
        if self._compact_mode or not hasattr(self, "last_download_label"):
            return
        self.last_download_label.config(text=f"Son indirme: {self._describe_last_download()}")

    # ---------- onboarding ekrani ----------

    def _build_onboarding_screen(self) -> None:
        self._clear_container()

        outer = tk.Frame(self.container, bg=COLOR_BG)
        outer.pack(fill="both", expand=True)

        # ---- Sol: guven verici tanitim ----
        side = tk.Frame(outer, bg=COLOR_ONBOARD_SIDE_BG, width=390,
                         highlightbackground=COLOR_BORDER, highlightthickness=1)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        side_scroll = tk.Frame(side, bg=COLOR_ONBOARD_SIDE_BG)
        side_scroll.pack(fill="both", expand=True, padx=34, pady=36)

        hero_canvas = tk.Canvas(
            side_scroll, width=112, height=112, bg=COLOR_ONBOARD_SIDE_BG, highlightthickness=0
        )
        hero_canvas.pack(anchor="center", pady=(4, 22))
        hero_canvas.create_polygon(
            _rounded_rect_points(2, 2, 110, 110, 26), smooth=True, fill=COLOR_ACCENT, outline="",
        )
        hero_canvas.create_text(56, 56, text="🎓", font=_emoji_font(48))

        tk.Label(
            side_scroll, text="Hoş Geldiniz!", bg=COLOR_ONBOARD_SIDE_BG, fg=COLOR_TEXT,
            font=("", 27, "bold"), justify="center",
        ).pack(anchor="center")
        tk.Label(
            side_scroll, text="Blackboard Sınav PDF Yakalayıcı", bg=COLOR_ONBOARD_SIDE_BG,
            fg=COLOR_TEXT, font=("", 17, "bold"), justify="center", wraplength=310,
        ).pack(anchor="center", pady=(10, 8))
        tk.Label(
            side_scroll,
            text="Sınav sonuç sayfalarınızı otomatik olarak PDF'e dönüştürür "
            "ve düzenli şekilde arşivler.",
            bg=COLOR_ONBOARD_SIDE_BG, fg=COLOR_MUTED, font=("", 14), justify="center",
            wraplength=310,
        ).pack(anchor="center")

        tk.Frame(side_scroll, bg=COLOR_BORDER, height=1).pack(fill="x", pady=24)

        for icon, title, desc in ONBOARDING_FEATURES:
            row = tk.Frame(side_scroll, bg=COLOR_ONBOARD_SIDE_BG)
            row.pack(fill="x", pady=10)
            icon_canvas = tk.Canvas(
                row, width=44, height=44, bg=COLOR_ONBOARD_SIDE_BG, highlightthickness=0
            )
            icon_canvas.pack(side="left", anchor="n", padx=(0, 14))
            icon_canvas.create_oval(1, 1, 43, 43, fill=COLOR_ACCENT_SOFT, outline="")
            icon_canvas.create_text(22, 22, text=icon, font=_emoji_font(19))
            text_col = tk.Frame(row, bg=COLOR_ONBOARD_SIDE_BG)
            text_col.pack(side="left", fill="x", expand=True)
            tk.Label(
                text_col, text=title, bg=COLOR_ONBOARD_SIDE_BG, fg=COLOR_TEXT,
                font=("", 15, "bold"), anchor="w",
            ).pack(fill="x")
            tk.Label(
                text_col, text=desc, bg=COLOR_ONBOARD_SIDE_BG, fg=COLOR_MUTED,
                font=("", 13), anchor="w", justify="left", wraplength=240,
            ).pack(fill="x")

        # Chrome bayrak uyarisi burada YOK artik - Ana Sayfa'da zaten
        # kalici olarak gosteriliyor, onboarding'de tekrarlamaya gerek yok.

        # ---- Sag: TEK SEFERDE BIR ADIM gosteren sihirbaz (wizard) ----
        # ONCEDEN butun adimlar alt alta, kaydirilabilir uzun bir listede
        # gosteriliyordu - kullanici geri bildirimine gore bu "alt alta"
        # gorunum kalabalik/dagınık duruyordu. Simdi TEK bir adim buyukce
        # gosteriliyor; "Geri"/"İleri" butonlariyla VE sol/sag OK
        # TUSLARIYLA gezinilebiliyor. Icerik artik hep TEK adim kadar
        # (kisa) oldugu icin ayrica bir kaydirma mekanizmasina gerek
        # kalmadi - eski tasma riski bu tasarimda yapisal olarak yok.
        right = tk.Frame(outer, bg=COLOR_BG)
        right.pack(side="left", fill="both", expand=True)

        footer = tk.Frame(right, bg=COLOR_BG)
        footer.pack(side="bottom", fill="x", padx=40, pady=(12, 28))
        tk.Checkbutton(
            footer, text="Bir daha gösterme", variable=self._dont_show_again_var,
            bg=COLOR_BG, fg=COLOR_MUTED, activebackground=COLOR_BG,
            selectcolor=COLOR_BG, font=("", 13), highlightthickness=0, bd=0,
        ).pack(side="left")
        nav_row = tk.Frame(footer, bg=COLOR_BG)
        nav_row.pack(side="right")
        self._onboarding_prev_button = RoundedButton(
            nav_row, text="◀ Geri", command=self._onboarding_go_prev, kind="secondary", width=110,
        )
        self._onboarding_prev_button.pack(side="left", padx=(0, 8))
        self._onboarding_next_button = RoundedButton(
            nav_row, text="İleri →", command=self._onboarding_go_next, kind="primary", width=150,
        )
        self._onboarding_next_button.pack(side="left")
        tk.Frame(right, bg=COLOR_BORDER, height=1).pack(side="bottom", fill="x")

        info_card = tk.Frame(right, bg=COLOR_ACCENT_SOFT)
        info_card.pack(side="bottom", fill="x", padx=40, pady=(0, 16))
        info_inner = tk.Frame(info_card, bg=COLOR_ACCENT_SOFT)
        info_inner.pack(fill="x", padx=16, pady=12)
        tk.Label(
            info_inner, text="ℹ  Önemli Not", bg=COLOR_ACCENT_SOFT, fg=COLOR_ACCENT,
            font=("", 15, "bold"), anchor="w",
        ).pack(fill="x")
        tk.Label(
            info_inner,
            text="Bu program yalnızca tarayıcınızı otomatikleştirir. Giriş "
            "bilgilerinize erişmez, kaydetmez.",
            bg=COLOR_ACCENT_SOFT, fg=COLOR_TEXT, font=("", 13), anchor="w",
            justify="left", wraplength=560,
        ).pack(fill="x", pady=(4, 0))

        content = tk.Frame(right, bg=COLOR_BG)
        content.pack(side="top", fill="both", expand=True, padx=40, pady=(36, 12))

        tk.Label(
            content, text="Başlamadan Önce", bg=COLOR_BG, fg=COLOR_TEXT,
            font=("", 24, "bold"), anchor="w",
        ).pack(fill="x")
        tk.Label(
            content,
            text=f"Aşağıdaki {len(ONBOARDING_STEPS)} basit adımı takip ederek sınavların "
            "PDF kopyalarını kolayca alabilirsiniz.",
            bg=COLOR_BG, fg=COLOR_MUTED, font=FONT_SUBTITLE, anchor="w",
        ).pack(fill="x", pady=(4, 4))

        self._onboarding_dots_canvas = tk.Canvas(content, height=20, bg=COLOR_BG, highlightthickness=0)
        self._onboarding_dots_canvas.pack(fill="x", pady=(0, 16))

        self._onboarding_step_container = tk.Frame(content, bg=COLOR_BG)
        self._onboarding_step_container.pack(fill="both", expand=True)

        # Klavye ok tuslariyla gezinme - onboarding kapaninca (bkz.
        # _finish_onboarding) MUTLAKA kaldirilmali, aksi halde Ana
        # Sayfa'dayken sol/sag ok tuslarina basildiginda bu metotlar
        # (artik var olmayan onboarding widget'larina erismeye calisip)
        # TclError firlatirdi.
        self.root.bind("<Left>", self._onboarding_go_prev)
        self.root.bind("<Right>", self._onboarding_go_next)

        self._onboarding_step_index = 0
        self._render_onboarding_step()

    def _render_onboarding_step(self) -> None:
        """Sihirbazin o an gosterilen TEK adimini (self._onboarding_step_index)
        cizer - navigasyon butonlarina/ok tuslarina basildiginda yeniden
        cagrilir. Onceki adimin widget'lari once temizlenir."""
        for widget in self._onboarding_step_container.winfo_children():
            widget.destroy()

        i = self._onboarding_step_index
        icon, title, desc, tag = ONBOARDING_STEPS[i]
        total = len(ONBOARDING_STEPS)

        card = self._make_card(self._onboarding_step_container)
        card.pack(fill="x")
        row = tk.Frame(card.inner, bg=COLOR_CARD)
        row.pack(fill="x")

        icon_canvas = tk.Canvas(row, width=56, height=56, bg=COLOR_CARD, highlightthickness=0)
        icon_canvas.pack(side="left", anchor="n", padx=(0, 16))
        icon_canvas.create_polygon(
            _rounded_rect_points(1, 1, 55, 55, 14), smooth=True, fill=COLOR_ACCENT_SOFT, outline="",
        )
        icon_canvas.create_text(28, 28, text=icon, font=_emoji_font(24))

        text_col = tk.Frame(row, bg=COLOR_CARD)
        text_col.pack(side="left", fill="both", expand=True)
        title_row = tk.Frame(text_col, bg=COLOR_CARD)
        title_row.pack(fill="x")
        badge = tk.Canvas(title_row, width=24, height=24, bg=COLOR_CARD, highlightthickness=0)
        badge.pack(side="left", padx=(0, 8))
        badge.create_oval(1, 1, 23, 23, fill=COLOR_ACCENT, outline="")
        badge.create_text(12, 12, text=str(i + 1), fill="white", font=("", 13, "bold"))
        tk.Label(
            title_row, text=title, bg=COLOR_CARD, fg=COLOR_TEXT, font=("", 19, "bold"), anchor="w",
        ).pack(side="left", fill="x", expand=True)
        tk.Label(
            text_col, text=desc, bg=COLOR_CARD, fg=COLOR_MUTED, font=("", 14),
            anchor="w", justify="left", wraplength=560,
        ).pack(fill="x", pady=(8, 10))
        tk.Label(
            text_col, text=tag, bg=COLOR_SUCCESS_SOFT, fg=COLOR_SUCCESS,
            font=("", 11, "bold"), padx=8, pady=3,
        ).pack(anchor="w")

        tk.Label(
            self._onboarding_step_container, text=f"Adım {i + 1} / {total}",
            bg=COLOR_BG, fg=COLOR_MUTED, font=("", 11), anchor="w",
        ).pack(anchor="w", pady=(10, 0))

        self._draw_onboarding_dots()
        self._update_onboarding_nav_buttons()

    def _draw_onboarding_dots(self) -> None:
        """Sihirbazin ust kismindaki kucuk ilerleme noktalarini cizer -
        su anki adim vurgulu (COLOR_ACCENT), digerleri soluk (COLOR_BORDER)."""
        canvas = self._onboarding_dots_canvas
        canvas.delete("all")
        total = len(ONBOARDING_STEPS)
        dot_radius = 4
        gap = 18
        for i in range(total):
            cx = 4 + i * gap + dot_radius
            color = COLOR_ACCENT if i == self._onboarding_step_index else COLOR_BORDER
            canvas.create_oval(
                cx - dot_radius, 10 - dot_radius, cx + dot_radius, 10 + dot_radius,
                fill=color, outline="",
            )

    def _update_onboarding_nav_buttons(self) -> None:
        is_first = self._onboarding_step_index == 0
        is_last = self._onboarding_step_index == len(ONBOARDING_STEPS) - 1
        self._onboarding_prev_button.config(state="disabled" if is_first else "normal")
        self._onboarding_next_button.config(text="Başla →" if is_last else "İleri →")

    def _onboarding_go_prev(self, _event=None) -> None:
        if self._onboarding_step_index > 0:
            self._onboarding_step_index -= 1
            self._render_onboarding_step()

    def _onboarding_go_next(self, _event=None) -> None:
        if self._onboarding_step_index < len(ONBOARDING_STEPS) - 1:
            self._onboarding_step_index += 1
            self._render_onboarding_step()
        else:
            self._finish_onboarding()

    def _finish_onboarding(self) -> None:
        # Onboarding'e ozel sol/sag ok tusu baglamalarini kaldiriyoruz -
        # aksi halde Ana Sayfa'ya gecince de bu tuslara basildiginda (artik
        # yok edilmis) onboarding widget'larina erismeye calisip TclError
        # firlatirdi.
        try:
            self.root.unbind("<Left>")
            self.root.unbind("<Right>")
        except tk.TclError:
            pass
        if self._dont_show_again_var.get():
            mark_onboarding_seen()
        self._build_app_shell()

    # ---------- uygulama iskeleti: sol menu + icerik alani ----------

    def _build_app_shell(self) -> None:
        self._clear_container()

        shell = tk.Frame(self.container, bg=COLOR_BG)
        shell.pack(fill="both", expand=True)

        self._build_sidebar(shell)

        self.content_area = tk.Frame(shell, bg=COLOR_BG)
        self.content_area.pack(side="left", fill="both", expand=True)

        self._show_page(self.current_page)

    def _build_sidebar(self, parent: tk.Widget) -> None:
        sidebar = tk.Frame(parent, bg=COLOR_SIDEBAR_BG, width=264,
                            highlightbackground=COLOR_BORDER, highlightthickness=1)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        logo_row = tk.Frame(sidebar, bg=COLOR_SIDEBAR_BG)
        logo_row.pack(fill="x", padx=18, pady=(20, 18))
        logo_canvas = tk.Canvas(logo_row, width=34, height=34, bg=COLOR_SIDEBAR_BG, highlightthickness=0)
        logo_canvas.pack(side="left")
        logo_canvas.create_polygon(
            _rounded_rect_points(0, 0, 34, 34, 9), smooth=True, fill=COLOR_ACCENT, outline=""
        )
        logo_canvas.create_text(17, 17, text="📄", font=_emoji_font(17))
        tk.Label(
            logo_row, text="Blackboard\nPDF Yakalayıcı", bg=COLOR_SIDEBAR_BG, fg=COLOR_TEXT,
            font=("", 14, "bold"), justify="left",
        ).pack(side="left", padx=(10, 0))

        self._nav_rows: dict[str, tk.Frame] = {}
        self._nav_labels: dict[str, tk.Label] = {}
        nav_col = tk.Frame(sidebar, bg=COLOR_SIDEBAR_BG)
        nav_col.pack(fill="x", padx=12)
        for key, icon, label in NAV_ITEMS:
            row = tk.Frame(nav_col, bg=COLOR_SIDEBAR_BG, cursor=CURSOR_HAND)
            row.pack(fill="x", pady=2)
            text_label = tk.Label(
                row, text=f"{icon}   {label}", bg=COLOR_SIDEBAR_BG, fg=COLOR_MUTED,
                font=FONT_NAV_ITEM, anchor="w", cursor=CURSOR_HAND,
            )
            text_label.pack(fill="x", padx=10, pady=8)
            for widget in (row, text_label):
                widget.bind("<Button-1>", lambda _e, k=key: self._show_page(k))
            self._nav_rows[key] = row
            self._nav_labels[key] = text_label
        self._refresh_nav_highlight()

        spacer = tk.Frame(sidebar, bg=COLOR_SIDEBAR_BG)
        spacer.pack(fill="both", expand=True)

        footer = tk.Frame(sidebar, bg=COLOR_SIDEBAR_BG)
        footer.pack(fill="x", padx=16, pady=(0, 16))

        output_card = tk.Frame(footer, bg=COLOR_GHOST_HOVER, highlightthickness=0)
        output_card.pack(fill="x", pady=(0, 10))
        tk.Label(
            output_card, text="📂  Çıktı Klasörü", bg=COLOR_GHOST_HOVER, fg=COLOR_MUTED,
            font=FONT_MUTED_BOLD, anchor="w",
        ).pack(fill="x", padx=10, pady=(8, 0))
        self.sidebar_output_label = tk.Label(
            output_card, textvariable=self.output_dir_var, bg=COLOR_GHOST_HOVER, fg=COLOR_TEXT,
            font=("", 11), anchor="w", justify="left", wraplength=190,
        )
        self.sidebar_output_label.pack(fill="x", padx=10, pady=(2, 8))
        RoundedButton(
            output_card, text="Değiştir", command=self._choose_output_folder,
            kind="secondary", width=170, height=32,
        ).pack(padx=10, pady=(0, 10))

        self.last_download_label = tk.Label(
            footer, text=f"Son indirme: {self._describe_last_download()}",
            bg=COLOR_SIDEBAR_BG, fg=COLOR_MUTED, font=FONT_MUTED,
        )
        self.last_download_label.pack(anchor="w", pady=(0, 12))

        self.safe_exit_button = RoundedButton(
            footer, text="Güvenli Çıkış", command=self._safe_exit, kind="danger", width=220,
        )
        self.safe_exit_button.pack()

    def _refresh_nav_highlight(self) -> None:
        for key, row in self._nav_rows.items():
            active = key == self.current_page
            bg = COLOR_ACCENT_SOFT if active else COLOR_SIDEBAR_BG
            fg = COLOR_ACCENT if active else COLOR_MUTED
            row.config(bg=bg)
            self._nav_labels[key].config(bg=bg, fg=fg, font=("", 14, "bold") if active else FONT_NAV_ITEM)

    def _show_page(self, key: str) -> None:
        self.current_page = key
        self._refresh_nav_highlight()
        for widget in self.content_area.winfo_children():
            widget.destroy()
        builders = {
            "home": self._build_home_page,
            "download": self._build_download_tree_page,
            "outputs": self._build_outputs_page,
            "logs": self._build_logs_page,
            "settings": self._build_settings_page,
            "help": self._build_help_page,
        }
        builders.get(key, self._build_home_page)(self.content_area)

    # ---------- Ana Sayfa ----------

    def _build_home_page(self, parent: tk.Widget) -> None:
        scroll_outer = tk.Frame(parent, bg=COLOR_BG)
        scroll_outer.pack(fill="both", expand=True)

        body = tk.Frame(scroll_outer, bg=COLOR_BG)
        body.pack(fill="both", expand=True, padx=28, pady=24)

        main_col = tk.Frame(body, bg=COLOR_BG)
        main_col.pack(side="left", fill="both", expand=True)
        side_col = tk.Frame(body, bg=COLOR_BG, width=310)
        side_col.pack(side="left", fill="y", padx=(20, 0))
        side_col.pack_propagate(False)

        # --- Baslik + baglanti rozeti ---
        header = tk.Frame(main_col, bg=COLOR_BG)
        header.pack(fill="x")
        title_col = tk.Frame(header, bg=COLOR_BG)
        title_col.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_col, text="Blackboard Sınav PDF Yakalayıcı",
            bg=COLOR_BG, fg=COLOR_TEXT, font=("", 22, "bold"), anchor="w",
        ).pack(fill="x")
        tk.Label(
            title_col,
            text="Blackboard üzerindeki sınav değerlendirme sayfalarını otomatik olarak PDF olarak kaydeder.",
            bg=COLOR_BG, fg=COLOR_MUTED, font=FONT_MUTED, anchor="w",
        ).pack(fill="x", pady=(2, 0))

        badge_col = tk.Frame(header, bg=COLOR_BG)
        badge_col.pack(side="right")
        status_pill = tk.Frame(badge_col, bg=COLOR_SUCCESS_SOFT)
        status_pill.pack(side="left", padx=(0, 8))
        self.status_dot = StatusDot(status_pill)
        self.status_dot.pack(side="left", padx=(10, 4), pady=6)
        self.status_text = tk.Label(
            status_pill, text="bağlı değil", bg=COLOR_SUCCESS_SOFT, fg=COLOR_MUTED, font=FONT_MUTED_BOLD
        )
        self.status_text.pack(side="left", padx=(0, 10))
        self.open_browser_button = RoundedButton(
            badge_col, text="Tarayıcıyı Aç", command=self._open_browser, kind="primary", width=162,
        )
        self.open_browser_button.pack(side="left")
        RoundedButton(
            badge_col, text="🗗 Küçült", command=self._enter_compact_mode, kind="secondary", width=110,
        ).pack(side="left", padx=(8, 0))

        self._build_chrome_flag_notice(main_col).pack(fill="x", pady=(12, 0))

        # --- Istatistik kartlari ---
        stats_row = tk.Frame(main_col, bg=COLOR_BG)
        stats_row.pack(fill="x", pady=(20, 0))
        self.stat_cards = {
            "found": StatCard(stats_row, "📋", "Bulunan", COLOR_ACCENT_SOFT),
            "ok": StatCard(stats_row, "✅", "İndirilen", COLOR_SUCCESS_SOFT),
            "skip": StatCard(stats_row, "⏭", "Atlanan", COLOR_WARNING_SOFT),
            "fail": StatCard(stats_row, "❌", "Hata", COLOR_DANGER_SOFT),
        }
        for card in self.stat_cards.values():
            card.pack(side="left", fill="x", expand=True, padx=(0, 12))

        # --- Zaman cizelgesi ---
        timeline_card = self._make_card(main_col)
        timeline_card.pack(fill="x", pady=(16, 0))
        self.timeline = TimelineSteps(timeline_card.inner)
        self.timeline.pack(anchor="center")

        # --- Eylem butonlari + ilerleme ---
        progress_card = self._make_card(main_col)
        progress_card.pack(fill="x", pady=(16, 0))
        action_row = tk.Frame(progress_card.inner, bg=COLOR_CARD)
        action_row.pack(fill="x", pady=(0, 12))
        self.scan_button = RoundedButton(
            action_row, text="Bul ve Tara", command=self._discover_current_page,
            kind="primary", width=158,
        )
        self.scan_button.pack(side="left")
        self.download_button = RoundedButton(
            action_row, text="PDF Olarak İndir", command=self._start_download_pending,
            kind="success", width=182,
        )
        self.download_button.pack(side="left", padx=(8, 0))
        # "Öğrenci Tara": dersin Not Defteri > 'Öğrenciler' sekmesindeki
        # tam ad + kullanici adi listesini CSV'ye cikarir (bkz.
        # scan_students.py) - "Bul ve Tara"/"PDF Olarak İndir" akisindan
        # BAGIMSIZ, ayri bir tek-seferlik islem oldugu icin "secondary"
        # (ghost) stilde, kendi butonu olarak ayriliyor.
        self.scan_students_button = RoundedButton(
            action_row, text="Öğrenci Tara", command=self._scan_students_current_page,
            kind="secondary", width=158,
        )
        self.scan_students_button.pack(side="left", padx=(8, 0))

        progress_header = tk.Frame(progress_card.inner, bg=COLOR_CARD)
        progress_header.pack(fill="x")
        tk.Label(
            progress_header, text="İndirme İlerlemesi", bg=COLOR_CARD, fg=COLOR_TEXT, font=FONT_SECTION,
        ).pack(side="left")
        self.progress_percent_label = tk.Label(
            progress_header, text="0%", bg=COLOR_CARD, fg=COLOR_ACCENT, font=("", 15, "bold"),
        )
        self.progress_percent_label.pack(side="right")
        self.progress_bar = ProgressBar(progress_card.inner, width=1, height=10)
        self.progress_bar.pack(fill="x", pady=(8, 6))
        self.progress_count_label = tk.Label(
            progress_card.inner, text="0 / 0 öğe", bg=COLOR_CARD, fg=COLOR_MUTED, font=FONT_MUTED,
        )
        self.progress_count_label.pack(anchor="w")

        # --- Canli islem logu ---
        log_card = self._make_card(main_col)
        log_card.pack(fill="both", expand=True, pady=(16, 0))
        log_header = tk.Frame(log_card.inner, bg=COLOR_CARD)
        log_header.pack(fill="x", pady=(0, 8))
        tk.Label(
            log_header, text="Canlı İşlem Logu", bg=COLOR_CARD, fg=COLOR_TEXT, font=FONT_SECTION,
        ).pack(side="left")
        RoundedButton(
            log_header, text="Temizle", command=self._clear_log, kind="secondary", width=104, height=32,
        ).pack(side="right")
        self.log_text = tk.Text(
            log_card.inner, height=13, wrap="word", relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=COLOR_BORDER, highlightcolor=COLOR_BORDER,
            padx=10, pady=10, bg="#fbfcfe", fg=COLOR_TEXT, font=("", 12),
        )
        self.log_text.tag_configure("ok", foreground=COLOR_SUCCESS, font=("", 12, "bold"))
        self.log_text.tag_configure("fail", foreground=COLOR_DANGER, font=("", 12, "bold"))
        self.log_text.tag_configure("warn", foreground=COLOR_WARNING, font=("", 12, "bold"))
        self.log_text.tag_configure("skip", foreground=COLOR_MUTED)
        self.log_text.pack(fill="both", expand=True)
        # Sayfa gecisleri/kompakt mod sirasinda biriken log gecmisini geri yaz.
        for line in self._log_history:
            self._append_log_line(line)

        # --- Sag panel: sistem durumu / son kesif / hizli islemler ---
        self._build_system_status_card(side_col)
        self._build_last_discovery_card(side_col)
        self._build_quick_actions_card(side_col)

        # Onceki durumu (sayfalar arasi gecisten sonra bile) dogru yansit.
        self._apply_tracked_state()

    def _build_system_status_card(self, parent: tk.Widget) -> None:
        card = self._make_card(parent)
        card.pack(fill="x")
        tk.Label(
            card.inner, text="Sistem Durumu", bg=COLOR_CARD, fg=COLOR_TEXT, font=FONT_SECTION,
        ).pack(anchor="w", pady=(0, 10))

        def _row(key: str, label: str, sub: str) -> None:
            row = tk.Frame(card.inner, bg=COLOR_CARD)
            row.pack(fill="x", pady=4)
            dot = tk.Canvas(row, width=10, height=10, bg=COLOR_CARD, highlightthickness=0)
            dot.pack(side="left", padx=(0, 8))
            dot.create_oval(1, 1, 9, 9, fill=COLOR_MUTED, outline="")
            text_col = tk.Frame(row, bg=COLOR_CARD)
            text_col.pack(side="left", fill="x", expand=True)
            title_label = tk.Label(
                text_col, text=label, bg=COLOR_CARD, fg=COLOR_TEXT, font=("", 12, "bold"), anchor="w",
            )
            title_label.pack(fill="x")
            sub_label = tk.Label(
                text_col, text=sub, bg=COLOR_CARD, fg=COLOR_MUTED, font=("", 11), anchor="w",
            )
            sub_label.pack(fill="x")
            self._sys_status_widgets[key] = (dot, title_label, sub_label)

        self._sys_status_widgets: dict[str, tuple] = {}
        # Sadece GERCEKTEN takip ettigimiz iki sinyal gosteriliyor - "oturum
        # gecerli"/"playwright hazir" gibi surekli DOGRULANMAYAN durumlari
        # yesil gostermek yaniltici olurdu.
        _row("chrome", "Chrome Bağlantısı", "Henüz açılmadı")
        _row("blackboard", "İzlenen Sayfa", "—")
        self._refresh_system_status()

    def _refresh_system_status(self) -> None:
        widgets = getattr(self, "_sys_status_widgets", None)
        if not widgets:
            return
        chrome_dot, chrome_title, chrome_sub = widgets["chrome"]
        if self._connection_state == "connected":
            chrome_dot.itemconfig(1, fill=COLOR_SUCCESS)
            chrome_sub.config(text="Bağlı ve hazır")
        elif self._connection_state == "connecting":
            chrome_dot.itemconfig(1, fill=COLOR_WARNING)
            chrome_sub.config(text="Açılıyor...")
        else:
            chrome_dot.itemconfig(1, fill=COLOR_MUTED)
            chrome_sub.config(text="Henüz açılmadı")

        bb_dot, bb_title, bb_sub = widgets["blackboard"]
        tracked = self.tracked_url_var.get()
        if tracked and tracked.startswith(BASE_URL):
            bb_dot.itemconfig(1, fill=COLOR_SUCCESS)
            bb_sub.config(text=tracked.replace(BASE_URL, "") or "/")
        elif tracked and tracked != "—":
            bb_dot.itemconfig(1, fill=COLOR_WARNING)
            bb_sub.config(text="Blackboard dışında bir sayfa")
        else:
            bb_dot.itemconfig(1, fill=COLOR_MUTED)
            bb_sub.config(text="—")

    def _build_last_discovery_card(self, parent: tk.Widget) -> None:
        card = self._make_card(parent)
        card.pack(fill="x", pady=(16, 0))
        tk.Label(
            card.inner, text="Son Keşif", bg=COLOR_CARD, fg=COLOR_TEXT, font=FONT_SECTION,
        ).pack(anchor="w", pady=(0, 8))
        self.discovery_body = tk.Frame(card.inner, bg=COLOR_CARD)
        self.discovery_body.pack(fill="x")
        self._refresh_discovery_panel()

    def _refresh_discovery_panel(self) -> None:
        body = getattr(self, "discovery_body", None)
        if body is None:
            return
        for widget in body.winfo_children():
            widget.destroy()
        if not self._pending_discovery:
            tk.Label(
                body, text="Henüz bir tarama yapılmadı.", bg=COLOR_CARD, fg=COLOR_MUTED,
                font=FONT_MUTED, wraplength=230, justify="left",
            ).pack(anchor="w")
            return
        discovery = self._pending_discovery
        label = discovery.get("course_label") or discovery.get("exam_label") or "Bilinmeyen"
        items = discovery.get("items", [])
        kind_label = "sınav/satır" if discovery.get("kind") == "not_defteri" else "öğrenci"
        header_row = tk.Frame(body, bg=COLOR_CARD)
        header_row.pack(fill="x", pady=(0, 4))
        tk.Label(
            header_row, text=label, bg=COLOR_CARD, fg=COLOR_TEXT, font=("", 12, "bold"), anchor="w",
        ).pack(side="left", fill="x", expand=True)
        tk.Label(
            header_row, text=str(len(items)), bg=COLOR_ACCENT_SOFT, fg=COLOR_ACCENT,
            font=("", 12, "bold"), padx=8,
        ).pack(side="right")
        tk.Label(
            body, text=f"{len(items)} {kind_label} bulundu — indirmeye hazır.",
            bg=COLOR_CARD, fg=COLOR_MUTED, font=("", 11), wraplength=230, justify="left",
        ).pack(anchor="w")

    def _build_quick_actions_card(self, parent: tk.Widget) -> None:
        card = self._make_card(parent)
        card.pack(fill="x", pady=(16, 0))
        tk.Label(
            card.inner, text="Hızlı İşlemler", bg=COLOR_CARD, fg=COLOR_TEXT, font=FONT_SECTION,
        ).pack(anchor="w", pady=(0, 10))
        actions = [
            ("📂  Çıktı Klasörünü Aç", self._open_output_folder),
            ("📄  Log Dosyasını Aç", self._open_download_log),
        ]
        for text, command in actions:
            row = tk.Frame(card.inner, bg=COLOR_CARD, cursor=CURSOR_HAND)
            row.pack(fill="x", pady=2)
            lbl = tk.Label(
                row, text=text, bg=COLOR_CARD, fg=COLOR_TEXT, font=("", 12),
                anchor="w", cursor=CURSOR_HAND,
            )
            lbl.pack(fill="x", padx=6, pady=6)
            for widget in (row, lbl):
                widget.bind("<Button-1>", lambda _e, cmd=command: cmd())
                widget.bind("<Enter>", lambda _e, w=row, l=lbl: (w.config(bg=COLOR_GHOST_HOVER), l.config(bg=COLOR_GHOST_HOVER)))
                widget.bind("<Leave>", lambda _e, w=row, l=lbl: (w.config(bg=COLOR_CARD), l.config(bg=COLOR_CARD)))

    def _apply_tracked_state(self) -> None:
        """Sayfa (yeniden) insa edildikten hemen sonra, GERCEK durumu
        (baglanti/dugme etkinligi/asama) taze widget'lara uygular - aksi
        halde ör. tarama surerken baska bir sekmeye bakip Ana Sayfa'ya
        donunce dugmeler yanlislikla varsayilan (etkin) duruma donerdi."""
        self.status_dot.set_state(self._connection_state)
        state_text = {"connected": "bağlı", "connecting": "açılıyor...", "disconnected": "bağlı değil"}
        self.status_text.config(text=state_text[self._connection_state])
        self.open_browser_button.config(state="disabled" if self._connection_state != "disconnected" else "normal")
        self.scan_button.config(state="normal" if self._scan_enabled else "disabled")
        self.download_button.config(state="normal" if self._download_enabled else "disabled")
        self.scan_students_button.config(state="normal" if self._student_scan_enabled else "disabled")
        self.timeline.set_stage(self._timeline_stage)
        self.stat_cards["found"].set_value(len(self._pending_discovery["items"]) if self._pending_discovery else 0)
        self.stat_cards["ok"].set_value(self._totals["ok"])
        self.stat_cards["skip"].set_value(self._totals["skip"])
        self.stat_cards["fail"].set_value(self._totals["fail"])
        self._refresh_system_status()
        self._refresh_discovery_panel()

    def _clear_log(self) -> None:
        self._log_history = []
        self.log_text.delete("1.0", "end")

    # ---------- Kompakt (kucultulmus, her zaman en onde) mod ----------

    def _enter_compact_mode(self) -> None:
        """Pencereyi sabit, standart boyutlu, her zaman en onde duran kucuk
        bir 'takip paneline' cevirir - hoca baska bir pencereyle (ör.
        Blackboard sekmesiyle) ugrasirken bile uzun bir taramanin
        ilerlemesini gozden kaybetmesin diye."""
        self._compact_mode = True
        # ONEMLI: minsize DEFAULT_MIN_WIDTH/HEIGHT'ta kalirsa pencere yoneticisi
        # kucuk kompakt boyutu REDDEDIP eski (buyuk) minimuma zorluyordu - bu
        # yuzden kompakt boyuta gecmeden ONCE minsize'i da gevsetmek sart.
        self.root.minsize(COMPACT_WINDOW_WIDTH, COMPACT_WINDOW_HEIGHT)
        self.root.resizable(False, False)
        self.root.geometry(f"{COMPACT_WINDOW_WIDTH}x{COMPACT_WINDOW_HEIGHT}")
        try:
            self.root.attributes("-topmost", True)
        except Exception:
            pass
        self._build_compact_view()

    def _exit_compact_mode(self) -> None:
        self._compact_mode = False
        try:
            self.root.attributes("-topmost", False)
        except Exception:
            pass
        self.root.resizable(True, True)
        self.root.geometry(f"{DEFAULT_WINDOW_WIDTH}x{DEFAULT_WINDOW_HEIGHT}")
        self.root.minsize(DEFAULT_MIN_WIDTH, DEFAULT_MIN_HEIGHT)
        self._build_app_shell()

    def _build_compact_view(self) -> None:
        self._clear_container()
        wrapper = tk.Frame(
            self.container, bg=COLOR_CARD, highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        wrapper.pack(fill="both", expand=True)

        header = tk.Frame(wrapper, bg=COLOR_CARD)
        header.pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(
            header, text="📄 Blackboard PDF", bg=COLOR_CARD, fg=COLOR_TEXT, font=("", 13, "bold"),
        ).pack(side="left")
        RoundedButton(
            header, text="⤢ Genişlet", command=self._exit_compact_mode, kind="secondary",
            width=104, height=28,
        ).pack(side="right")

        status_row = tk.Frame(wrapper, bg=COLOR_CARD)
        status_row.pack(fill="x", padx=14, pady=(0, 10))
        self.compact_status_dot = StatusDot(status_row)
        self.compact_status_dot.pack(side="left")
        self.compact_status_text = tk.Label(
            status_row, text="bağlı değil", bg=COLOR_CARD, fg=COLOR_MUTED, font=FONT_MUTED_BOLD,
        )
        self.compact_status_text.pack(side="left", padx=(6, 0))

        progress_row = tk.Frame(wrapper, bg=COLOR_CARD)
        progress_row.pack(fill="x", padx=14)
        tk.Label(
            progress_row, text="İlerleme", bg=COLOR_CARD, fg=COLOR_MUTED, font=FONT_MUTED,
        ).pack(side="left")
        self.compact_percent_label = tk.Label(
            progress_row, text="0%", bg=COLOR_CARD, fg=COLOR_ACCENT, font=("", 12, "bold"),
        )
        self.compact_percent_label.pack(side="right")
        self.compact_progress_bar = ProgressBar(wrapper, width=1, height=8)
        self.compact_progress_bar.pack(fill="x", padx=14, pady=(4, 8))

        self.compact_counts_label = tk.Label(
            wrapper, text="0 / 0 öğe", bg=COLOR_CARD, fg=COLOR_MUTED, font=("", 11), anchor="w",
        )
        self.compact_counts_label.pack(fill="x", padx=14)
        self.compact_totals_label = tk.Label(
            wrapper, text="✅ 0    ⏭ 0    ❌ 0", bg=COLOR_CARD, fg=COLOR_TEXT,
            font=("", 12, "bold"), anchor="w",
        )
        self.compact_totals_label.pack(fill="x", padx=14, pady=(4, 12))

        self._apply_compact_tracked_state()

    def _apply_compact_tracked_state(self) -> None:
        if not self._compact_mode:
            return
        self.compact_status_dot.set_state(self._connection_state)
        state_text = {"connected": "bağlı", "connecting": "açılıyor...", "disconnected": "bağlı değil"}
        self.compact_status_text.config(text=state_text[self._connection_state])
        total = len(self._pending_discovery["items"]) if self._pending_discovery else 0
        self.compact_counts_label.config(text=f"0 / {total} öğe")
        self.compact_totals_label.config(
            text=f"✅ {self._totals['ok']}    ⏭ {self._totals['skip']}    ❌ {self._totals['fail']}"
        )

    # ---------- Indirme: cikti klasorunu agac (tree) olarak gezinme ----------

    def _build_download_tree_page(self, parent: tk.Widget) -> None:
        """Cikti klasorunu (ders -> sinav -> PDF) tiklanip genisletilebilen
        bir agac gorunumunde gosterir - Finder'a gitmeden goz atip cift
        tiklayarak acabilmek icin. Native ttk.Treeview kullaniliyor (ozel
        Canvas widget'lardan farkli olarak, hiyerarsik agac + kaydirma +
        klavye navigasyonu gibi karmasik davranislari sifirdan yazmak
        yerine Tkinter'in kendi saglam bilesenine guveniyoruz)."""
        wrapper = tk.Frame(parent, bg=COLOR_BG)
        wrapper.pack(fill="both", expand=True, padx=28, pady=24)

        header = tk.Frame(wrapper, bg=COLOR_BG)
        header.pack(fill="x")
        title_col = tk.Frame(header, bg=COLOR_BG)
        title_col.pack(side="left", fill="x", expand=True)
        tk.Label(
            title_col, text="İndirme", bg=COLOR_BG, fg=COLOR_TEXT, font=("", 22, "bold"), anchor="w",
        ).pack(fill="x")
        tk.Label(
            title_col,
            text="Tüm ders/sınav klasörlerini ve içindeki PDF'leri buradan gezebilir, "
            "çift tıklayarak açabilirsin.",
            bg=COLOR_BG, fg=COLOR_MUTED, font=FONT_MUTED, anchor="w",
        ).pack(fill="x", pady=(2, 0))
        RoundedButton(
            header, text="Yenile", command=self._refresh_download_tree, kind="secondary", width=110,
        ).pack(side="right")

        card = self._make_card(wrapper)
        card.pack(fill="both", expand=True, pady=(16, 0))

        tree_row = tk.Frame(card.inner, bg=COLOR_CARD)
        tree_row.pack(fill="both", expand=True)

        style = ttk.Style()
        try:
            style.configure(
                "Output.Treeview", font=("", 13), rowheight=30,
                background=COLOR_CARD, fieldbackground=COLOR_CARD, foreground=COLOR_TEXT,
            )
            style.configure("Output.Treeview.Heading", font=("", 12, "bold"))
            style.map("Output.Treeview", background=[("selected", COLOR_ACCENT_SOFT)],
                      foreground=[("selected", COLOR_ACCENT)])
        except Exception:
            pass

        self.download_tree = ttk.Treeview(
            tree_row, style="Output.Treeview", columns=("info",), show="tree headings",
        )
        self.download_tree.heading("#0", text="Ad")
        self.download_tree.heading("info", text="Bilgi")
        self.download_tree.column("info", width=160, anchor="e", stretch=False)
        scrollbar = ttk.Scrollbar(tree_row, orient="vertical", command=self.download_tree.yview)
        self.download_tree.configure(yscrollcommand=scrollbar.set)
        self.download_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.download_tree.bind("<Double-1>", self._on_download_tree_double_click)

        self._tree_paths: dict[str, Path] = {}
        self._refresh_download_tree()

    def _refresh_download_tree(self) -> None:
        tree = getattr(self, "download_tree", None)
        if tree is None:
            return
        tree.delete(*tree.get_children())
        self._tree_paths = {}
        if not self.output_dir.exists():
            tree.insert(
                "", "end", text="📁  Klasör henüz oluşturulmadı (ilk indirmede oluşacak)",
                values=("",),
            )
            return
        self._populate_tree_node(tree, "", self.output_dir)

    def _populate_tree_node(self, tree: ttk.Treeview, parent_item: str, dir_path: Path) -> None:
        try:
            # macOS her klasore kendiliginden ".DS_Store" gibi gizli meta-veri
            # dosyalari birakiyor - bunlar bizim indirdigimiz gercek icerik
            # degil, acilabilecek bir uygulamalari da yok (cift tiklayinca
            # "no application knows how to open" hatasi veriyordu). Adi "."
            # ile baslayan HER SEYI (gizli dosya/klasor) listeden atliyoruz.
            entries = sorted(
                (p for p in dir_path.iterdir() if not p.name.startswith(".")),
                key=lambda p: (p.is_file(), p.name.lower()),
            )
        except OSError:
            return
        for entry in entries:
            # Tek bir okunamayan alt klasor/dosya (izin sorunu, senkron
            # sirasinda kilitli OneDrive dosyasi vb.) tum agacin insasini
            # cokertmesin - o girdi atlanir, kalanlar listelenmeye devam eder.
            try:
                if entry.is_dir():
                    item_count = sum(1 for p in entry.iterdir() if not p.name.startswith("."))
                    item = tree.insert(
                        parent_item, "end", text=f"📁  {entry.name}",
                        values=(f"{item_count} öğe",), open=(parent_item == ""),
                    )
                    self._tree_paths[item] = entry
                    self._populate_tree_node(tree, item, entry)
                else:
                    size_kb = entry.stat().st_size / 1024
                    icon = "📄" if entry.suffix.lower() == ".pdf" else "📝"
                    item = tree.insert(
                        parent_item, "end", text=f"{icon}  {entry.name}",
                        values=(f"{size_kb:.0f} KB",),
                    )
                    self._tree_paths[item] = entry
            except OSError:
                continue

    def _on_download_tree_double_click(self, _event=None) -> None:
        tree = self.download_tree
        selected = tree.focus()
        path = self._tree_paths.get(selected)
        if path is not None and path.exists():
            open_in_file_manager(path)

    # ---------- Cikti / Log / Ayarlar / Yardim sayfalari ----------

    def _build_outputs_page(self, parent: tk.Widget) -> None:
        wrapper = tk.Frame(parent, bg=COLOR_BG)
        wrapper.pack(fill="both", expand=True, padx=28, pady=24)
        tk.Label(
            wrapper, text="Çıktılar", bg=COLOR_BG, fg=COLOR_TEXT, font=("", 22, "bold"), anchor="w",
        ).pack(fill="x")
        tk.Label(
            wrapper, text="Yakalanan tüm PDF'ler bu klasörün altında, ders/sınav bazlı alt "
            "klasörlerde birikir.",
            bg=COLOR_BG, fg=COLOR_MUTED, font=FONT_MUTED, anchor="w",
        ).pack(fill="x", pady=(2, 16))

        card = self._make_card(wrapper)
        card.pack(fill="x")
        tk.Label(
            card.inner, text="📂  Çıktı Klasörü", bg=COLOR_CARD, fg=COLOR_TEXT, font=FONT_SECTION,
        ).pack(anchor="w")
        tk.Label(
            card.inner, textvariable=self.output_dir_var, bg=COLOR_CARD, fg=COLOR_MUTED,
            font=FONT_BODY, anchor="w", wraplength=600, justify="left",
        ).pack(anchor="w", pady=(4, 14))
        button_row = tk.Frame(card.inner, bg=COLOR_CARD)
        button_row.pack(fill="x")
        RoundedButton(
            button_row, text="Klasörü Aç", command=self._open_output_folder, kind="primary", width=158,
        ).pack(side="left")
        RoundedButton(
            button_row, text="Değiştir", command=self._choose_output_folder, kind="secondary", width=136,
        ).pack(side="left", padx=(8, 0))

        info_card = self._make_card(wrapper)
        info_card.pack(fill="x", pady=(16, 0))
        tk.Label(
            info_card.inner, text="Son İndirme", bg=COLOR_CARD, fg=COLOR_TEXT, font=FONT_SECTION,
        ).pack(anchor="w")
        tk.Label(
            info_card.inner, text=self._describe_last_download(), bg=COLOR_CARD, fg=COLOR_MUTED,
            font=FONT_BODY,
        ).pack(anchor="w", pady=(4, 0))

    def _build_logs_page(self, parent: tk.Widget) -> None:
        wrapper = tk.Frame(parent, bg=COLOR_BG)
        wrapper.pack(fill="both", expand=True, padx=28, pady=24)
        header = tk.Frame(wrapper, bg=COLOR_BG)
        header.pack(fill="x")
        tk.Label(
            header, text="Loglar", bg=COLOR_BG, fg=COLOR_TEXT, font=("", 22, "bold"), anchor="w",
        ).pack(side="left")
        RoundedButton(
            header, text="Log Dosyasını Aç", command=self._open_download_log,
            kind="secondary", width=192,
        ).pack(side="right")
        tk.Label(
            wrapper, text=f"'{DOWNLOAD_LOG_FILENAME}' dosyasının kalıcı, zaman damgalı geçmişi.",
            bg=COLOR_BG, fg=COLOR_MUTED, font=FONT_MUTED, anchor="w",
        ).pack(fill="x", pady=(2, 16))

        card = self._make_card(wrapper)
        card.pack(fill="both", expand=True)
        text_widget = tk.Text(
            card.inner, wrap="word", relief="flat", borderwidth=0,
            highlightthickness=1, highlightbackground=COLOR_BORDER,
            padx=10, pady=10, bg="#fbfcfe", fg=COLOR_TEXT, font=("", 12),
        )
        text_widget.pack(fill="both", expand=True)
        log_path = self.output_dir / DOWNLOAD_LOG_FILENAME
        if log_path.exists():
            text_widget.insert("1.0", log_path.read_text(encoding="utf-8"))
        else:
            text_widget.insert("1.0", "Henüz bir indirme oturumu tamamlanmadı.")
        text_widget.config(state="disabled")

    def _build_settings_page(self, parent: tk.Widget) -> None:
        wrapper = tk.Frame(parent, bg=COLOR_BG)
        wrapper.pack(fill="both", expand=True, padx=28, pady=24)
        tk.Label(
            wrapper, text="Ayarlar", bg=COLOR_BG, fg=COLOR_TEXT, font=("", 22, "bold"), anchor="w",
        ).pack(fill="x", pady=(0, 16))

        output_card = self._make_card(wrapper)
        output_card.pack(fill="x")
        tk.Label(
            output_card.inner, text="Çıktı Klasörü", bg=COLOR_CARD, fg=COLOR_TEXT, font=FONT_SECTION,
        ).pack(anchor="w", pady=(0, 8))
        row = tk.Frame(output_card.inner, bg=COLOR_CARD)
        row.pack(fill="x")
        tk.Entry(
            row, textvariable=self.output_dir_var, state="readonly", relief="flat",
            highlightthickness=1, highlightbackground=COLOR_BORDER, highlightcolor=COLOR_ACCENT,
            readonlybackground=COLOR_CARD, fg=COLOR_TEXT, disabledforeground=COLOR_TEXT,
        ).pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)
        RoundedButton(
            row, text="Değiştir", command=self._choose_output_folder, kind="secondary", width=114,
        ).pack(side="left")

        url_card = self._make_card(wrapper)
        url_card.pack(fill="x", pady=(16, 0))
        tk.Label(
            url_card.inner, text="İzlenen Sayfa (hata ayıklama)", bg=COLOR_CARD, fg=COLOR_TEXT,
            font=FONT_SECTION,
        ).pack(anchor="w", pady=(0, 8))
        url_row = tk.Frame(url_card.inner, bg=COLOR_CARD)
        url_row.pack(fill="x")
        tk.Entry(
            url_row, textvariable=self.tracked_url_var, state="readonly", relief="flat",
            highlightthickness=1, highlightbackground=COLOR_BORDER, highlightcolor=COLOR_ACCENT,
            readonlybackground=COLOR_CARD, fg=COLOR_TEXT, disabledforeground=COLOR_TEXT,
        ).pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=4)
        RoundedButton(
            url_row, text="Şimdi Kontrol Et", command=self._check_tracked_url,
            kind="secondary", width=158,
        ).pack(side="left")

    def _build_help_page(self, parent: tk.Widget) -> None:
        # Bu sayfa artik 5 adim + "Öğrenci Tara" detay karti + Chrome
        # uyarisi bir arada barindiriyor - kucuk pencerelerde tum bunlar
        # pencere yuksekligini asip alttaki icerik gorunmez/erisilmez
        # kalabiliyordu (CANLI GOZLEMLENEN bir tasma). _make_scrollable_area
        # ile sarmalayip bu riski yapisal olarak ortadan kaldiriyoruz.
        scroll_body = self._make_scrollable_area(parent)
        wrapper = tk.Frame(scroll_body, bg=COLOR_BG)
        wrapper.pack(fill="both", expand=True, padx=28, pady=24)
        tk.Label(
            wrapper, text="Nasıl Çalışır?", bg=COLOR_BG, fg=COLOR_TEXT, font=("", 22, "bold"), anchor="w",
        ).pack(fill="x", pady=(0, 16))
        for i, (icon, title, desc, tag) in enumerate(ONBOARDING_STEPS, start=1):
            card = self._make_card(wrapper)
            card.pack(fill="x", pady=(0, 10))
            row = tk.Frame(card.inner, bg=COLOR_CARD)
            row.pack(fill="x")
            self._make_badge(row, i).pack(side="left", anchor="n", padx=(0, 14))
            text_col = tk.Frame(row, bg=COLOR_CARD)
            text_col.pack(side="left", fill="x", expand=True)
            tk.Label(
                text_col, text=f"{icon}  {title}", bg=COLOR_CARD, fg=COLOR_TEXT, font=FONT_STEP_TITLE,
                anchor="w",
            ).pack(fill="x")
            tk.Label(
                text_col, text=desc, bg=COLOR_CARD, fg=COLOR_MUTED, font=FONT_BODY,
                anchor="w", justify="left", wraplength=680,
            ).pack(fill="x", pady=(2, 4))
            tk.Label(
                text_col, text=tag, bg=COLOR_SUCCESS_SOFT, fg=COLOR_SUCCESS,
                font=("", 11, "bold"), padx=8, pady=3,
            ).pack(anchor="w")

        # "Öğrenci Tara" ozelligini yukaridaki kisa adim etiketinin
        # otesinde, somut bir ornekle (CSV formati + PDF adlandirma
        # formati) daha derinlemesine aciklayan ayri bir kart - Yardim
        # sayfasi zaten detay almaya gelinen bir sayfa oldugu icin burada
        # onboarding'deki kisa versiyondan daha fazla bilgi vermek mantikli.
        feature_card = self._make_card(wrapper)
        feature_card.pack(fill="x", pady=(6, 10))
        tk.Label(
            feature_card.inner, text="📊  Öğrenci Numaralı PDF Adları",
            bg=COLOR_CARD, fg=COLOR_TEXT, font=FONT_STEP_TITLE, anchor="w",
        ).pack(fill="x")
        tk.Label(
            feature_card.inner,
            text="'Öğrenci Tara' bir kez çalıştırıldığında, dersin tüm "
            "öğrencilerinin adı ve numarası bir CSV dosyasına kaydedilir. "
            "Bu liste var olduğu sürece, o dersteki tüm sınav PDF'lerinin "
            "adına öğrenci numarası otomatik olarak eklenir — elle "
            "eşleştirme gerekmez.",
            bg=COLOR_CARD, fg=COLOR_MUTED, font=FONT_BODY, anchor="w",
            justify="left", wraplength=680,
        ).pack(fill="x", pady=(4, 10))
        example_box = tk.Frame(
            feature_card.inner, bg="#fbfcfe", highlightbackground=COLOR_BORDER, highlightthickness=1,
        )
        example_box.pack(fill="x")
        tk.Label(
            example_box,
            text="CSV sütunları:  Ad Soyad ; Öğrenci Numarası\n"
            "PDF adı formatı:  {sınav_adı}_{öğrenci_no}_{İsim-Soyisim}.pdf\n"
            "Örnek:  Kısa Sınav 1_2420171019_Ahmet-Yılmaz.pdf",
            bg="#fbfcfe", fg=COLOR_TEXT, font=("", 12), anchor="w",
            justify="left", wraplength=650, padx=12, pady=10,
        ).pack(fill="x")

        # Yardim sayfasi zaten "nasil calisir" bilgisi almaya gelinen bir
        # sayfa - burada kucuk/kapali baslamanin bir faydasi yok, direkt
        # acik gosteriyoruz (Ana Sayfa'daki gibi kucuk baslamiyor).
        self._build_chrome_flag_notice(wrapper, start_expanded=True).pack(fill="x", pady=(4, 0))

    # ---------- worker thread: TUM Playwright islemleri burada ----------

    def _worker_loop(self) -> None:
        page: Page | None = None
        context = None
        with sync_playwright() as p:
            while True:
                try:
                    command, payload = self.command_queue.get(timeout=1.0)
                except queue.Empty:
                    # Bos zamanlarda, script'in su an gercekten hangi
                    # sekmeyi/URL'yi gordugunu GUI'de canli gostermek icin
                    # taze bilgi gonderiyoruz (hata ayiklamayi kolaylastirir).
                    # SSO'nun hizli art arda yonlendirmeleri sirasinda bir
                    # sayfanin URL'sine erisim gecici hata firlatabiliyor -
                    # bu, YAKALANMAZSA butun worker thread'ini cokertip
                    # her seyi donduruyordu. Butun blok korumali.
                    #
                    # ONEMLI: bu ayni zamanda kullanicinin Chrome penceresini
                    # DOGRUDAN (uygulamanin "Guvenli Cikis"i disinda, ör.
                    # kirmizi X'e basarak) kapatip kapatmadigini tespit eden
                    # TEK yer - eskiden bu SADECE aktif bir tarama sirasinda
                    # (capture_current_page bir hata firlattiginda)
                    # yakalaniyordu. Kullanici bos beklerken (hicbir tarama
                    # yokken) pencereyi kapatirsa hicbir kod yolu bunu fark
                    # ETMIYORDU - GUI sonsuza kadar "bağlı" gostermeye devam
                    # ediyordu, ta ki kullanici "Bul ve Tara"ya basip
                    # anlasilmaz bir hatayla karsilasana kadar. Simdi HER
                    # bos-dongu turunda baglantiyi da dogruluyoruz: TUM
                    # pencereler kapandiysa (context.pages bos) ya da
                    # tarayici surecine erisim "kapandi" turu bir hata
                    # firlatiyorsa, hemen "browser_lost" gonderip context'i
                    # sifirliyoruz ki kullanici tekrar "Tarayıcıyı Aç"a
                    # basabilsin.
                    if context is not None:
                        browser_gone = False
                        try:
                            pages = context.pages
                            if not pages:
                                # Kalici profilde son pencere de kapandiginda
                                # Chrome sureci de sonlaniyor (arka planda
                                # calismaya devam etmesini saglayan ozel bir
                                # bayrak vermiyoruz) - bu yuzden bos sekme
                                # listesi de kapanmanin guvenilir bir isareti.
                                browser_gone = True
                            else:
                                active = resolve_active_page(context)
                                if active is not None:
                                    self.gui_queue.put(("tracked_url", live_url(active)))
                        except Exception as exc:
                            if is_browser_closed_error(exc):
                                browser_gone = True
                        if browser_gone:
                            context = None
                            page = None
                            self.gui_queue.put(("browser_lost", None))
                    continue

                # TUM komut isleme bloklarini (asagida) tek bir genel
                # guvenlik agiyla sariyoruz: bu worker thread, uygulamanin
                # TEK Playwright thread'i - Playwright'in senkron API'si
                # tek thread'den kullanilmak ZORUNDA (bkz. modul basindaki
                # docstring). Yukarida bircok spesifik hata durumu tek tek
                # ele alinmis olsa da, tahmin edilememis herhangi bir
                # istisna (ör. Playwright'in kendi ic hata turlerinden biri)
                # BURADA yakalanmazsa bu thread SESSIZCE olur, GUI donuk
                # kalir ama COKMEZ - kullanici hicbir dugmenin bir daha
                # ISE YARAMADIGINI fark eder ama NEDENINI asla goremez.
                # Bu yuzden son bir savunma hatti olarak butun komut
                # islemeyi try/except ile sarip, beklenmedik bir hata
                # olursa GUI'ye ACIKCA bildirip dongunun devam etmesini
                # sagliyoruz.
                try:
                    if command == "quit":
                        break

                    elif command == "open_browser":
                        try:
                            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
                            # Onceki bir zorla-kapatma sonrasi yetim kalmis
                            # kilit dosyalari varsa (baska bir surec KULLANMIYORSA
                            # kayipsiz) temizlemeyi dene - bkz. fonksiyon docstring'i.
                            clear_stale_profile_lock(PROFILE_DIR)
                            context = p.chromium.launch_persistent_context(
                                str(PROFILE_DIR), headless=False, **browser_launch_kwargs()
                            )
                        except Exception as exc:
                            # Baslatma basarisizsa yarim bir context kalmasin -
                            # aksi halde bos-zaman URL dongusu olu context'e
                            # erismeye calisir, sonraki denemeler de kafa karistirir.
                            context = None
                            self.gui_queue.put(("browser_error", str(exc)))
                            continue
                        # Baslangic sayfasina gitme, tarayici ACMANIN basarisindan
                        # ayri tutuluyor: internet yoksa/DNS cozulmuyorsa goto
                        # basarisiz olur ama Chrome PENCERESI acilmistir. Onceden
                        # bu durum "tarayici acilamadi" sayilip baglanti durumu
                        # sifirlaniyordu; kullanici tekrar "Tarayıcıyı Aç"a basinca
                        # da acik kalan pencere yuzunden profil kilidi hatasi
                        # aliyordu. Simdi pencere acildiysa "bagli" sayiyoruz -
                        # kullanici gerekirse adres cubugundan kendisi gidebilir.
                        try:
                            page = context.pages[0] if context.pages else context.new_page()
                            page.goto(BASE_URL)
                        except Exception as exc:
                            self.gui_queue.put((
                                "log",
                                f"Tarayıcı açıldı ama Blackboard sayfası yüklenemedi ({exc}).\n"
                                "İnternet bağlantını kontrol et; tarayıcıdaki adres çubuğundan "
                                "Blackboard'a kendin de gidebilirsin.",
                            ))
                        self.gui_queue.put(("browser_ready", None))

                    elif command == "discover":
                        try:
                            active_page = resolve_active_page(context) or page
                        except Exception:
                            active_page = page

                        # active_page None kalabilir: kullanici tarayiciyi hic
                        # acmadan ya da bos beklerken kapattiktan sonra GUI
                        # dugmeleri her nasilsa hala etkin kaldiysa (savunma
                        # amacli - normalde artik "browser_lost" mesaji
                        # dugmeleri devre disi birakiyor). None ile
                        # _discover'a devam etmek page.wait_for_timeout gibi
                        # cagrilarda AttributeError firlatip TUM worker
                        # thread'ini (arka planda calisan TEK Playwright
                        # thread'ini) sessizce cokertip uygulamayi kalici
                        # olarak tepkisiz birakirdi - burada erken, net bir
                        # mesajla durmak cok daha guvenli.
                        if active_page is None:
                            self.gui_queue.put((
                                "log",
                                "Tarayıcı bağlantısı yok görünüyor - önce "
                                "'Tarayıcıyı Aç'a bas.",
                            ))
                            self.gui_queue.put(("discovery_failed", None))
                            continue

                        # Birden fazla Blackboard sekmesi aciksa, hangisinin
                        # taranacagi belirsiz - sessizce ilkini secmek yerine
                        # kullaniciyi acikca uyarip taramayi baslatmiyoruz
                        # (bkz. find_blackboard_pages docstring).
                        try:
                            open_bb_tabs = find_blackboard_pages(context) if context else []
                        except Exception:
                            open_bb_tabs = []
                        if len(open_bb_tabs) > 1:
                            self.gui_queue.put((
                                "log",
                                f"{len(open_bb_tabs)} tane Blackboard sekmesi açık görünüyor, "
                                "hangisinin taranacağı belirsiz olabilir. Gereksiz sekmeleri "
                                "kapatıp (sadece taramak istediğin sayfa açık kalsın) tekrar "
                                "'Bul ve Tara'ya bas.",
                            ))
                            self.gui_queue.put(("discovery_failed", None))
                        else:
                            try:
                                self._discover(active_page, payload)
                            except Exception as exc:
                                # ör. wait_for_blackboard'un ic dongusundeki
                                # page.wait_for_timeout - kullanici tam da
                                # "Bul ve Tara" taramanin GIRIS KONTROLU
                                # sirasinda (~3 saniyelik bekleme penceresi)
                                # tarayiciyi kapatirsa buraya duser. Genel
                                # "beklenmedik hata" mesaji yerine ozel
                                # olarak yakalayip GUI'nin baglanti
                                # durumunu da (dugmeler dahil) temiz sekilde
                                # sifirliyoruz.
                                if is_browser_closed_error(exc):
                                    context = None
                                    page = None
                                    self.gui_queue.put(("browser_lost", None))
                                else:
                                    raise

                    elif command == "scan_students":
                        try:
                            active_page = resolve_active_page(context) or page
                        except Exception:
                            active_page = page
                        # bkz. "discover" kolundaki ayni kontrolun docstring'i.
                        if active_page is None:
                            self.gui_queue.put((
                                "log",
                                "Tarayıcı bağlantısı yok görünüyor - önce "
                                "'Tarayıcıyı Aç'a bas.",
                            ))
                            self.gui_queue.put(("student_scan_done", None))
                            continue
                        try:
                            self._scan_students(active_page, payload)
                        except Exception as exc:
                            if is_browser_closed_error(exc):
                                context = None
                                page = None
                                self.gui_queue.put(("browser_lost", None))
                            else:
                                raise

                    elif command == "download":
                        try:
                            active_page = resolve_active_page(context) or page
                        except Exception:
                            active_page = page
                        # bkz. "discover" kolundaki ayni kontrolun docstring'i -
                        # None page ile devam etmek yerine, her ogede tek tek
                        # basarisiz olup kafa karistirici "NoneType" hatalari
                        # biriktirmek yerine hemen net bir mesajla duruyoruz.
                        if active_page is None:
                            self.gui_queue.put((
                                "log",
                                "Tarayıcı bağlantısı yok görünüyor - önce "
                                "'Tarayıcıyı Aç'a bas.",
                            ))
                            self.gui_queue.put(("scan_done", {"ok": 0, "skip": 0, "fail": 0}))
                            continue
                        self._start_download(active_page, payload)

                    elif command == "check_url":
                        # Manuel kontrol: acik TUM sekmeleri tek tek listele,
                        # arka plandaki gorunmez zamanlayiciya guvenmeden anlik
                        # ve tam gorunurluk sagla.
                        try:
                            pages = context.pages if context is not None else []
                            self.gui_queue.put(("log", f"Açık sekme sayısı: {len(pages)}"))
                            for i, pg in enumerate(pages):
                                try:
                                    pg_url = live_url(pg)
                                except Exception as exc:
                                    pg_url = f"(okunamadı: {exc})"
                                # Playwright'in onbellekledigi .url ile sayfanin CANLI
                                # window.location.href'i ayriliyorsa (bkz. live_url
                                # docstring - SSO capraz-kaynak yonlendirmesinde
                                # gorulen bilinen kopma) bunu ACIKCA gosteriyoruz -
                                # hem kullaniciya hem ileride hata ayiklarken faydali.
                                try:
                                    cached_url = pg.url
                                except Exception:
                                    cached_url = pg_url
                                line = f"  Sekme {i + 1}: {pg_url}"
                                if cached_url != pg_url:
                                    line += f"\n    (Playwright onbellegi eski goruyor: {cached_url})"
                                self.gui_queue.put(("log", line))
                            active = resolve_active_page(context) if context is not None else None
                            if active is not None:
                                self.gui_queue.put(("tracked_url", live_url(active)))
                        except Exception as exc:
                            self.gui_queue.put(("log", f"Kontrol sırasında hata: {exc}"))
                except Exception as exc:
                    self.gui_queue.put((
                        "log",
                        f"Beklenmedik bir iç hata oluştu: {exc}\n"
                        "Bu tek seferlik olabilir, kaldığın yerden devam "
                        "edebilirsin - sorun sürerse 'Tarayıcıyı Aç'a tekrar "
                        "basmayı dene.",
                    ))

            if context is not None:
                try:
                    context.close()
                except Exception:
                    # Tarayici kullanici tarafindan zaten kapatilmis olabilir -
                    # cikis sirasinda bunun icin gurultulu bir traceback basmaya
                    # gerek yok, worker thread temiz sekilde sonlansin.
                    pass

    def _scan_students(self, page: Page, output_dir: Path) -> None:
        """Dersin Not Defteri > 'Öğrenciler' sekmesindeki tam ad + kullanici
        adi listesini tarayip CSV'ye yazar (bkz. scan_students.py) - bu
        listeyi ANA tarama/indirme akisi (capture_student) sonradan PDF
        adina ogrenci numarasi eklemek icin okur (bkz.
        common.load_student_roster/format_student_pdf_stem). Kullanicinin
        o an gercekten 'Öğrenciler' sekmesinde olmasi GEREKIYOR - bu
        fonksiyon hangi sekmede oldugunu kendisi kontrol ETMEZ (scan_students.
        find_student_roster de etmiyor), sadece gorunen tabloyu okur.

        Sonuc, _scan_not_defteri/_scan_grade_center ile AYNI mekanizmayla
        (append_download_log) indirme_log.txt'ye de kaydedilir - bu
        fonksiyon HER ZAMAN dosyanin SONUNA EKLER, var olan gecmisin
        UZERINE YAZMAZ (bkz. o fonksiyonun docstring'i, "a" - append -
        modunda aciyor), bu yuzden farkli derslerin/farkli taramalarin
        kayitlari zaman icinde birikerek kalir, birbirini SILMEZ."""
        if not wait_for_blackboard(page):
            self.gui_queue.put((
                "log",
                "Şu anda Blackboard sayfasında değilsin (birkaç saniye "
                f"boyunca kontrol edildi). Şu an: {live_url(page)}\n"
                "Tarayıcıda önce Blackboard'a gerçekten giriş yaptığından "
                "emin ol, sonra tekrar dene.",
            ))
            self.gui_queue.put(("student_scan_done", None))
            return

        course_label = derive_course_label(page)
        course_dir = output_dir / sanitize_filename(course_label, max_chars=DEFAULT_FOLDER_MAX_CHARS)

        session_lines: list[str] = []

        def emit(message: str) -> None:
            self.gui_queue.put(("log", message))
            session_lines.append(message)

        emit(f"'{course_label}' için öğrenci listesi taranıyor...")
        # find_student_roster, sayfalama sirasinda beklenenden az ogrenci
        # toplanirsa bu emit ile UYARI verir (bkz. o fonksiyonun
        # docstring'i) - hem GUI log paneline hem indirme_log.txt'ye
        # dussun diye ayni emit'i kullaniyoruz.
        roster = find_student_roster(page, emit=emit)
        if not roster:
            emit(
                "Hiç öğrenci satırı bulunamadı. Dersin Not Defteri > "
                "'Öğrenciler' sekmesinde olduğundan emin ol."
            )
            append_download_log(
                output_dir, f"{course_label} — Öğrenci Tara", session_lines,
                {"ok": 0, "skip": 0, "fail": 0},
            )
            self.gui_queue.put(("student_scan_done", None))
            return

        csv_path = course_dir / STUDENT_ROSTER_CSV_FILENAME
        write_student_roster_csv(roster, csv_path)
        emit(f"{len(roster)} öğrenci bulundu. CSV yazıldı: {csv_path}")
        append_download_log(
            output_dir, f"{course_label} — Öğrenci Tara", session_lines,
            {"ok": len(roster), "skip": 0, "fail": 0},
        )
        self.gui_queue.put(("student_scan_done", {"count": len(roster), "path": str(csv_path)}))

    def _discover(self, page: Page, output_dir: Path) -> None:
        """Sadece sayfayi tarayip NE bulundugunu bildirir - henuz indirmez.
        Bulunanlar self._pending_discovery olarak GUI tarafinda saklanip
        "PDF Olarak Indir" ile onaylanana kadar gercek yakalama baslamaz."""
        # Giris kontrolu: SSO'nun son yonlendirme anini yanlislikla "giris
        # yapilmamis" diye yorumlamamak icin sayfanin yerlesmesine ~3
        # saniye firsat taniyoruz (wait_for_blackboard).
        if not wait_for_blackboard(page):
            self.gui_queue.put((
                "log",
                "Şu anda Blackboard sayfasında değilsin (birkaç saniye "
                f"boyunca kontrol edildi). Şu an: {live_url(page)}\n"
                "Tarayıcıda önce Blackboard'a gerçekten giriş yaptığından "
                "emin ol, sonra tekrar dene.",
            ))
            self.gui_queue.put(("discovery_failed", None))
            return

        course_label = derive_course_label(page)
        course_dir = output_dir / sanitize_filename(course_label, max_chars=DEFAULT_FOLDER_MAX_CHARS)

        exam_rows, excluded_exam_names = find_exam_row_names(page)
        if exam_rows:
            listed = "\n".join(f"  - {er.name}" for er in exam_rows)
            excluded_note = ""
            if excluded_exam_names:
                excluded_note = (
                    f"\n{len(excluded_exam_names)} satır atlandı (durumu "
                    f"{GRADING_STATUS_COMPLETE_MARKERS_LABEL} değil, ör. 'Not "
                    f"verilecek bir şey yok'): {', '.join(excluded_exam_names)}"
                )
            self.gui_queue.put((
                "log",
                f"'Not Defteri' sayfası algılandı ({course_label}): "
                f"{len(exam_rows)} satır {GRADING_STATUS_COMPLETE_MARKERS_LABEL} "
                f"durumunda bulundu, bunlar indirilecek:\n{listed}{excluded_note}",
            ))
            self.gui_queue.put(("discovery_done", {
                "kind": "not_defteri",
                "course_label": course_label,
                "course_dir": course_dir,
                "output_dir": output_dir,
                "items": exam_rows,
                # Indirme basladiginda sayfanin HALA bu adres oldugu
                # dogrulanacak (bkz. _start_download) - kullanici kesif ile
                # indirme arasinda baska sayfaya gectiyse yanlis sayfadan
                # indirme baslamasin.
                "url": live_url(page),
            }))
            return

        student_rows = find_student_rows(page)
        if student_rows:
            exam_label = course_label
            self.gui_queue.put((
                "log", f"Sınav öğrenci listesi algılandı ({exam_label}): {len(student_rows)} öğrenci bulundu."
            ))
            self.gui_queue.put(("discovery_done", {
                "kind": "grade_center",
                "course_dir": course_dir,
                "exam_label": exam_label,
                "output_dir": output_dir,
                "items": student_rows,
                # bkz. yukaridaki not_defteri payload'indaki ayni alanin notu.
                "url": live_url(page),
            }))
            return

        self.gui_queue.put((
            "log",
            "Bu sayfada ne yapılacağı anlaşılamadı. Dersin Not Defteri sayfasında "
            "ya da bir sınavın öğrenci listesi göründüğü sayfada olduğundan emin ol "
            "(giriş yapıldığından da emin ol).",
        ))
        self.gui_queue.put(("discovery_failed", None))

    def _start_download(self, page: Page, discovery: dict) -> None:
        # Iki-faktorlu koruma: "Bul ve Tara" ile "PDF Olarak İndir" arasinda
        # kullanici tarayicida BASKA bir sayfaya gecmis olabilir (yuksek
        # olasilikli bir kullanici hatasi). Kesif sirasindaki adres ile su
        # anki adres uyusmuyorsa indirmeye HIC baslamiyoruz - aksi halde
        # tarama yanlis sayfada satir/ogrenci arayip ya kafa karistirici
        # hatalarla dolar ya da (daha kotusu) yanlis derse ait icerik
        # yanlis klasore inerdi.
        expected_url = discovery.get("url") or ""
        try:
            current_url = live_url(page)
        except Exception:
            current_url = ""
        if expected_url and current_url and current_url != expected_url:
            self.gui_queue.put((
                "log",
                "Sayfa, 'Bul ve Tara' yapıldığı andakinden farklı görünüyor:\n"
                f"  taranan: {expected_url}\n  şimdiki: {current_url}\n"
                "Yanlış sayfadan indirme riskine karşı işlem başlatılmadı — "
                "indirmek istediğin sayfaya dönüp tekrar 'Bul ve Tara'ya bas.",
            ))
            self.gui_queue.put(("discovery_failed", None))
            return

        write_error = check_output_writable(discovery["output_dir"])
        if write_error:
            self.gui_queue.put(("log", write_error))
            self.gui_queue.put(("discovery_failed", None))
            return

        captured_titles = already_captured_titles()
        if discovery["kind"] == "not_defteri":
            self._scan_not_defteri(
                page,
                discovery["course_label"],
                discovery["course_dir"],
                discovery["output_dir"],
                discovery["items"],
                captured_titles,
            )
        elif discovery["kind"] == "grade_center":
            self._scan_grade_center(
                page,
                discovery["course_dir"],
                discovery["exam_label"],
                discovery["output_dir"],
                discovery["items"],
                captured_titles,
            )

    def _scan_not_defteri(
        self, page, course_label, course_dir, output_dir, exam_rows, captured_titles
    ) -> None:
        session_lines: list[str] = []

        def emit(message: str) -> None:
            self.gui_queue.put(("log", message))
            session_lines.append(message)

        def recover(grades_url: str) -> bool:
            """Bir sinav denemesinden sonra Not Defteri listesine donmeyi
            dener. BASARISIZ olursa devam ETMEK, sayfa artik listede
            olmadigi icin BIR SONRAKI satiri (belki gercek bir sinavi)
            yanlis/bozuk bir sayfa durumundan tiklamaya calisip zincirleme
            hataya yol acar - bu yuzden basarisizlikta True/False dondurup
            cagiran taraf taramayi GUVENLE durduruyor, yanlis satirlara
            tiklama riskine girmiyor."""
            try:
                return_to_grades_list(page, grades_url)
                return True
            except Exception as exc:
                emit(
                    f"  Kurtarma başarısız (sayfa artık Not Defteri listesinde "
                    f"değil: {exc}). Yanlış satırlara tıklama riski taşıdığı "
                    "için tarama burada güvenle durduruldu."
                )
                return False

        emit(f"Ders: {course_label}")
        # live_url: bu deger sonradan return_to_grades_list icinde
        # KURTARMA navigasyonu icin kullaniliyor - yanlis/eski bir URL
        # burada donarsa tum tarama yanlis sayfaya geri donmeye calisir.
        grades_url = live_url(page)
        totals = {"ok": 0, "skip": 0, "fail": 0}
        consecutive_failures = 0
        for index, exam_row in enumerate(exam_rows):
            if self._stop_event.is_set():
                emit("Kullanıcı isteğiyle durduruldu.")
                break

            self._emit_progress(index + 1, len(exam_rows), totals)
            emit(f"[{index + 1}/{len(exam_rows)}] {exam_row.name}")
            try:
                exam_dir = course_dir / sanitize_filename(
                    exam_row.name, max_chars=DEFAULT_FOLDER_MAX_CHARS
                )
                # capture_exam_submissions bu sinavdaki SOL 'Ogrenciler'
                # panelindeki TUM ogrencileri tek tek yakalar (tek bir
                # ogrenciyle sinirli degil) - bkz. o fonksiyonun docstring'i.
                exam_totals = capture_exam_submissions(
                    page,
                    exam_row.name,
                    grades_url,
                    exam_dir,
                    course_label,
                    exam_row.expected_submitted,
                    captured_titles,
                    emit=emit,
                    should_stop=self._stop_event.is_set,
                )
                totals["ok"] += exam_totals["ok"]
                totals["skip"] += exam_totals["skip"]
                totals["fail"] += exam_totals["fail"]
                consecutive_failures = 0
                if exam_totals["navigation_lost"]:
                    # capture_exam_submissions zaten bir UYARI log'ladi -
                    # sayfa bilinmeyen bir durumda, bir sonraki sinava
                    # GECMEK yanlis satirlara tiklama riski tasir.
                    break
            except NotSubmittedOrNotExam:
                # Bu satir bir sinav/quiz degilmis (odev, tartisma vb.) ya
                # da hic gonderilmemis - bu bir HATA degil, gercek bir
                # bilgi. consecutive_failures'i KASITLI OLARAK artirmiyoruz:
                # aksi halde bir dersteki bir dizi odev/tartisma satiri,
                # devre kesiciyi gercek sinavlara hic ulasamadan
                # tetikleyebilirdi.
                emit(f"  Atlandı — sınav/quiz değilmiş gibi görünüyor ya da gönderilmemiş.")
                totals["skip"] += 1
                if not recover(grades_url):
                    break
            except Exception as exc:
                if is_browser_closed_error(exc):
                    # Tarayici/sekme kapanmis/coktu - devre kesicinin 5
                    # denemeyi tuketmesini beklemenin anlami yok, tarayici
                    # zaten yok. Hemen dur, net bir mesaj ver, GUI'nin
                    # baglanti durumunu sifirla.
                    emit(
                        "  Tarayıcı kapanmış görünüyor — 'Tarayıcıyı Aç'a "
                        "tekrar basman gerekiyor."
                    )
                    totals["fail"] += 1
                    self.gui_queue.put(("browser_lost", None))
                    break
                emit(f"  HATA: {exc}")
                totals["fail"] += 1
                consecutive_failures += 1
                if not recover(grades_url):
                    break
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    emit(
                        f"  UYARI: art arda {consecutive_failures} hata, tarama durduruldu."
                        + ("" if _page_still_on_blackboard(page) else
                           " (oturumun süresi dolmuş olabilir — tarayıcıda tekrar giriş "
                           "yapıp 'Bul ve Tara'yı tekrarla)")
                    )
                    break
        append_download_log(output_dir, f"{course_label} — Not Defteri", session_lines, totals)
        self.gui_queue.put(("scan_done", totals))

    def _scan_grade_center(
        self, page, course_dir, exam_label, output_dir, student_rows, captured_titles
    ) -> None:
        session_lines: list[str] = []

        def emit(message: str) -> None:
            self.gui_queue.put(("log", message))
            session_lines.append(message)

        exam_dir = course_dir / sanitize_filename(exam_label, max_chars=DEFAULT_FOLDER_MAX_CHARS)
        # Bu akista (kind='grade_center') Not Defteri baglami yok -
        # 'Öğrenci Tara' ile uretilmis roster'i (varsa) course_dir'den
        # okumaya calisiyoruz (bkz. common.load_student_roster).
        roster = load_student_roster(course_dir)
        name_occurrence: dict[str, int] = {}
        consecutive_failures = 0
        totals = {"ok": 0, "skip": 0, "fail": 0}

        for row_index, (raw_name, sidebar_score) in enumerate(student_rows):
            if self._stop_event.is_set():
                emit("Kullanıcı isteğiyle durduruldu.")
                break

            occurrence = name_occurrence.get(raw_name, 0) + 1
            name_occurrence[raw_name] = occurrence
            display_name = raw_name if occurrence == 1 else f"{raw_name} ({occurrence})"

            # Ilerleme, atlanan (zaten var) ogrenciler icin de bildirilmeli -
            # aksi halde cogu ogrencisi onceden indirilmis bir tekrar
            # taramada cubuk uzun sure 0'da donmus gibi gorunuyordu.
            self._emit_progress(row_index + 1, len(student_rows), totals)

            log_key = f"{exam_label} - {display_name}"
            if log_key in captured_titles:
                emit(f"[{row_index + 1}/{len(student_rows)}] {display_name} — atlandı (zaten var)")
                totals["skip"] += 1
                continue

            emit(f"[{row_index + 1}/{len(student_rows)}] {display_name}")
            try:
                entry = capture_student(
                    page, raw_name, occurrence - 1, display_name, sidebar_score, exam_dir, exam_label,
                    exam_name=exam_label, roster=roster,
                )
                emit(f"  OK  onay={entry['onay']}  puan={entry['puan']}")
                if entry["bozuk_gorsel_sayisi"] > 0:
                    emit(
                        f"  UYARI  {entry['bozuk_gorsel_sayisi']} görsel bozuk/eksik "
                        "görünüyor, PDF'i elle kontrol et."
                    )
                totals["ok"] += 1
                consecutive_failures = 0
            except Exception as exc:
                if is_browser_closed_error(exc):
                    emit(
                        "  Tarayıcı kapanmış görünüyor — 'Tarayıcıyı Aç'a "
                        "tekrar basman gerekiyor."
                    )
                    totals["fail"] += 1
                    self.gui_queue.put(("browser_lost", None))
                    break
                emit(f"  HATA: {exc}")
                totals["fail"] += 1
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    emit(
                        f"  UYARI: art arda {consecutive_failures} hata, tarama durduruldu."
                        + ("" if _page_still_on_blackboard(page) else
                           " (oturumun süresi dolmuş olabilir — tarayıcıda tekrar giriş "
                           "yapıp 'Bul ve Tara'yı tekrarla)")
                    )
                    break

            if (row_index + 1) % BATCH_SIZE == 0 and row_index + 1 < len(student_rows):
                emit(f"  ... {BATCH_SIZE} öğrenci sonrası kısa mola ...")
                # time.sleep yerine Event.wait: kullanici Guvenli Cikis'a
                # basarsa mola bitmesini beklemeden hemen uyaniyoruz.
                if self._stop_event.wait(timeout=BATCH_PAUSE_S):
                    emit("Kullanıcı isteğiyle durduruldu.")
                    break

        append_download_log(output_dir, f"{exam_label} — Öğrenci Listesi", session_lines, totals)
        self.gui_queue.put(("scan_done", totals))

    # ---------- GUI thread ----------

    def _poll_gui_queue(self) -> None:
        try:
            while True:
                kind, payload = self.gui_queue.get_nowait()
                self._handle_message(kind, payload)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_gui_queue)

    def _on_home_page(self) -> bool:
        # Kompakt moda gecince Ana Sayfa widget'lari (status_dot,
        # progress_bar vb.) yok ediliyor ama self.current_page hala
        # "home" ve eski widget REFERANSLARI (attribute'lar) hala
        # duruyor - hasattr tek basina yetersiz, TclError'a yol aciyordu
        # (yok edilmis bir widget'a .config cagirmak). Kompakt modda
        # kesinlikle "Ana Sayfa'dayim" SAYILMAMALI.
        return (
            not self._compact_mode
            and self.current_page == "home"
            and hasattr(self, "status_dot")
        )

    def _handle_message(self, kind: str, payload) -> None:
        if kind == "browser_ready":
            self._connection_state = "connected"
            if self._on_home_page():
                self.status_dot.set_state("connected")
                self.status_text.config(text="bağlı")
                self.open_browser_button.config(state="disabled")
            self._scan_enabled = True
            self._student_scan_enabled = True
            if self._on_home_page():
                self.scan_button.config(state="normal")
                self.scan_students_button.config(state="normal")
            self._timeline_stage = 1
            if self._on_home_page():
                self.timeline.set_stage(1)
                self._refresh_system_status()
            self._apply_compact_tracked_state()
        elif kind == "browser_error":
            self._connection_state = "disconnected"
            if self._on_home_page():
                self.open_browser_button.config(state="normal")
                self.status_dot.set_state("disconnected")
                self.status_text.config(text="bağlı değil")
                self._refresh_system_status()
            self._apply_compact_tracked_state()
            if payload and is_profile_lock_error(payload):
                messagebox.showerror(
                    "Hata",
                    "Tarayıcı açılamadı. Program zaten başka bir pencerede "
                    "açık olabilir — diğer pencereyi (varsa) kapatıp tekrar dene.",
                )
            elif payload and is_chrome_missing_error(payload):
                messagebox.showerror(
                    "Google Chrome bulunamadı",
                    "Bu program gerçek Google Chrome tarayıcısını kullanıyor "
                    "ama bilgisayarda kurulu görünmüyor.\n\n"
                    "Şu adresten Chrome'u kurup programı tekrar başlat:\n"
                    "https://www.google.com/chrome/",
                )
            else:
                messagebox.showerror("Hata", f"Tarayıcı açılamadı: {payload}")
        elif kind == "tracked_url":
            self.tracked_url_var.set(payload)
            if self._on_home_page():
                self._refresh_system_status()
        elif kind == "log":
            self._log(payload)
        elif kind == "progress":
            totals = payload["totals"]
            self._totals = totals
            total = payload["total"] or 1
            ratio = payload["done"] / total
            if self._on_home_page():
                self.progress_percent_label.config(text=f"{int(ratio * 100)}%")
                self.progress_count_label.config(text=f"{payload['done']} / {payload['total']} öğe")
                self.progress_bar.set_progress(ratio)
                self.stat_cards["ok"].set_value(totals["ok"])
                self.stat_cards["skip"].set_value(totals["skip"])
                self.stat_cards["fail"].set_value(totals["fail"])
            if self._compact_mode:
                self.compact_percent_label.config(text=f"{int(ratio * 100)}%")
                self.compact_progress_bar.set_progress(ratio)
                self.compact_counts_label.config(text=f"{payload['done']} / {payload['total']} öğe")
                self.compact_totals_label.config(
                    text=f"✅ {totals['ok']}    ⏭ {totals['skip']}    ❌ {totals['fail']}"
                )
        elif kind == "discovery_done":
            self._pending_discovery = payload
            self._scan_enabled = True
            self._download_enabled = True
            self._student_scan_enabled = True
            self._timeline_stage = 2
            count = len(payload["items"])
            self._log(f"Hazır: {count} öğe bulundu. İndirmek için 'PDF Olarak İndir'e bas.\n")
            if self._on_home_page():
                self.progress_bar.set_progress(0.0)
                self.progress_percent_label.config(text="0%")
                self.progress_count_label.config(text=f"0 / {count} öğe")
                self.scan_button.config(state="normal")
                self.download_button.config(state="normal")
                self.scan_students_button.config(state="normal")
                self.timeline.set_stage(2)
                self.stat_cards["found"].set_value(count)
                self._refresh_discovery_panel()
            if self._compact_mode:
                self.compact_percent_label.config(text="0%")
                self.compact_progress_bar.set_progress(0.0)
                self.compact_counts_label.config(text=f"0 / {count} öğe")
        elif kind == "discovery_failed":
            self._pending_discovery = None
            self._scan_enabled = self._connection_state == "connected"
            self._download_enabled = False
            self._student_scan_enabled = self._connection_state == "connected"
            if self._on_home_page():
                self.scan_button.config(state="normal" if self._scan_enabled else "disabled")
                self.download_button.config(state="disabled")
                self.scan_students_button.config(state="normal" if self._student_scan_enabled else "disabled")
                self._refresh_discovery_panel()
        elif kind == "browser_lost":
            # Tarayici/sekme kapanmis/coktu - ya aktif bir tarama sirasinda
            # (bkz. is_browser_closed_error) ya da kullanici bos beklerken
            # dogrudan Chrome penceresini kapattigi icin (bkz. worker
            # dongusundeki bos-zaman kontrolu). Baglanti durumunu
            # sifirlayip kullanicinin tekrar "Tarayıcıyı Aç"a basmasini
            # bekliyoruz.
            #
            # ONEMLI: sadece _scan_enabled/_download_enabled BAYRAKLARINI
            # degil, dugmelerin GORSEL durumunu da devre disi birakiyoruz.
            # Aksi halde (ör. tarayici bos beklerken kapatildiginda) "Bul
            # ve Tara" TIKLANABILIR kalirdi - tiklanirsa worker'a context/
            # page=None ile bir komut giderdi, bu da worker thread'ini
            # (arka planda calisan TEK Playwright thread'i) sessizce
            # cokertip butun uygulamayi kalici olarak tepkisiz birakabilirdi.
            self._connection_state = "disconnected"
            self._scan_enabled = False
            self._download_enabled = False
            self._student_scan_enabled = False
            self._pending_discovery = None
            if self._on_home_page():
                self.status_dot.set_state("disconnected")
                self.status_text.config(text="bağlı değil")
                self.open_browser_button.config(state="normal")
                self.scan_button.config(state="disabled")
                self.download_button.config(state="disabled")
                self.scan_students_button.config(state="disabled")
                self._refresh_system_status()
                self._refresh_discovery_panel()
            self._apply_compact_tracked_state()
        elif kind == "student_scan_done":
            # bkz. _scan_students_current_page'in kapattigi ayni ucunu
            # burada geri aciyoruz - tarayici bu arada kapanmis olabilir
            # (browser_lost bu mesajdan ONCE gelip baglantiyi sifirlamis
            # olur), o durumda tekrar acmak yanlis olur.
            self._scan_enabled = self._connection_state == "connected"
            self._download_enabled = self._connection_state == "connected" and bool(self._pending_discovery)
            self._student_scan_enabled = self._connection_state == "connected"
            if self._on_home_page():
                self.scan_button.config(state="normal" if self._scan_enabled else "disabled")
                self.download_button.config(state="normal" if self._download_enabled else "disabled")
                self.scan_students_button.config(state="normal" if self._student_scan_enabled else "disabled")
        elif kind == "scan_done":
            totals = payload or {"ok": 0, "skip": 0, "fail": 0}
            self._totals = totals
            self._timeline_stage = 4
            self._pending_discovery = None
            # Tarama sirasinda tarayici kapandiysa "browser_lost" bu
            # mesajdan ONCE islenip baglantiyi sifirlamis olur - o durumda
            # "Bul ve Tara"yi yeniden acmak yanlis olurdu (tiklanirsa
            # tarayicisiz bir komut kuyruga girer).
            self._scan_enabled = self._connection_state == "connected"
            self._download_enabled = False
            self._student_scan_enabled = self._connection_state == "connected"
            self._log("--- indirme bitti ---\n")
            if self._on_home_page():
                self.progress_bar.set_progress(1.0)
                self.progress_percent_label.config(text="100%")
                self.stat_cards["ok"].set_value(totals["ok"])
                self.stat_cards["skip"].set_value(totals["skip"])
                self.stat_cards["fail"].set_value(totals["fail"])
                self.scan_button.config(state="normal" if self._scan_enabled else "disabled")
                self.download_button.config(state="disabled")
                self.scan_students_button.config(state="normal" if self._student_scan_enabled else "disabled")
                self.timeline.set_stage(4)
                self._refresh_discovery_panel()
            if self._compact_mode:
                self.compact_percent_label.config(text="100%")
                self.compact_progress_bar.set_progress(1.0)
                self.compact_totals_label.config(
                    text=f"✅ {totals['ok']}    ⏭ {totals['skip']}    ❌ {totals['fail']}"
                )
            self._update_last_download_label()
            self._notify_scan_finished()

    def _notify_scan_finished(self) -> None:
        """Uzun surebilen bir tarama bitince (ör. 90 ogrenci, 10+ dakika),
        kullanici baska bir pencereyle mesguldur diye sesli+gorsel bir
        uyari veriyoruz - aksi halde bittigini fark etmesi gecikebilir."""
        self.root.bell()
        try:
            self.root.attributes("-topmost", True)
            self.root.lift()
            # Kompakt moddaysak topmost KALICI olarak acik olmali (kullanicinin
            # bu modu secme sebebi tam olarak bu) - sadece normal moddaki
            # gecici "dikkat cek" pulse'unu geri kapatiyoruz.
            if not self._compact_mode:
                self.root.after(300, lambda: self.root.attributes("-topmost", False))
        except Exception:
            pass

    # ---------- buton komutlari ----------

    def _open_browser(self) -> None:
        self._connection_state = "connecting"
        self.open_browser_button.config(state="disabled")
        self.status_dot.set_state("connecting")
        self.status_text.config(text="açılıyor...")
        self._refresh_system_status()
        self.command_queue.put(("open_browser", None))

    def _discover_current_page(self) -> None:
        self.scan_button.config(state="disabled")
        self.download_button.config(state="disabled")
        # scan_students_button de kapatiliyor: worker TEK bir Playwright
        # thread'i - "Öğrenci Tara" bu tarama surerken tiklanirsa komut
        # kuyruga girer ama tarama BITTIKTEN SONRA, o an sayfa artik
        # farkli bir yerde olabilir - yanlis sayfanin ogrenci listesini
        # tarama riskine karsi, bir islem surerken digerleri kapali.
        self.scan_students_button.config(state="disabled")
        # Sadece gorsel durum degil, BAYRAK da guncellenmeli: tarama
        # surerken kullanici baska sayfaya gecip Ana Sayfa'ya donerse
        # _apply_tracked_state butonlari bu bayraklara gore yeniden
        # kuruyor - bayrak True kalirsa dugme yeniden tiklanabilir olup
        # ayni komut kuyruga IKINCI kez eklenebilirdi.
        self._scan_enabled = False
        self._download_enabled = False
        self._student_scan_enabled = False
        self._pending_discovery = None
        self._refresh_discovery_panel()
        self.progress_bar.set_progress(0.0)
        self.progress_percent_label.config(text="0%")
        self._log("Taranıyor...")
        self.command_queue.put(("discover", self.output_dir))

    def _start_download_pending(self) -> None:
        if not self._pending_discovery:
            return
        # Kesif ile indirme arasinda kullanici cikti klasorunu degistirmis
        # olabilir - payload'daki yollar kesif ANINDAKI klasore gore
        # hesaplanmisti. Guncellemezsek PDF'ler kullanicinin az once
        # birakip degistirdigi ESKI klasore inerdi (sessiz ve kafa
        # karistirici). Ders klasoru adi ayni kaldigi icin sadece koku
        # yeni secilen klasore tasimak yeterli.
        if self._pending_discovery.get("output_dir") != self.output_dir:
            updated = dict(self._pending_discovery)
            updated["course_dir"] = self.output_dir / updated["course_dir"].name
            updated["output_dir"] = self.output_dir
            self._pending_discovery = updated
        self.scan_button.config(state="disabled")
        self.download_button.config(state="disabled")
        self.scan_students_button.config(state="disabled")
        # bkz. _discover_current_page'deki ayni not: bayraklar da
        # kapatilmazsa, indirme surerken sayfa degistirip donen kullanici
        # icin butonlar yeniden aktif olur ve AYNI indirme kuyruga ikinci
        # kez eklenebilirdi.
        self._scan_enabled = False
        self._download_enabled = False
        self._student_scan_enabled = False
        self._timeline_stage = 3
        self.timeline.set_stage(3)
        self._log("İndiriliyor...")
        self.command_queue.put(("download", self._pending_discovery))

    def _scan_students_current_page(self) -> None:
        self.scan_button.config(state="disabled")
        self.download_button.config(state="disabled")
        self.scan_students_button.config(state="disabled")
        # bkz. _discover_current_page'deki ayni not - tek Playwright
        # worker'ini paylasan diger iki islemle CAKISMAMASI icin hepsi
        # birlikte kapatilip islem bitince birlikte geri acilir.
        self._scan_enabled = False
        self._download_enabled = False
        self._student_scan_enabled = False
        self._log("Öğrenci listesi taranıyor...")
        self.command_queue.put(("scan_students", self.output_dir))

    def _check_tracked_url(self) -> None:
        self.command_queue.put(("check_url", None))

    def _choose_output_folder(self) -> None:
        chosen = filedialog.askdirectory(
            initialdir=str(self.output_dir), title="Çıktı klasörünü seç"
        )
        if chosen:
            self.output_dir = Path(chosen)
            self.output_dir_var.set(str(self.output_dir))
            warning = cloud_sync_warning(self.output_dir)
            if warning:
                self._log(warning)
            self._update_last_download_label()

    def _open_output_folder(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        open_in_file_manager(self.output_dir)

    def _open_download_log(self) -> None:
        log_path = self.output_dir / DOWNLOAD_LOG_FILENAME
        if not log_path.exists():
            messagebox.showinfo("Log bulunamadı", "Henüz bir indirme oturumu tamamlanmadı.")
            return
        open_in_file_manager(log_path)

    def _safe_exit(self) -> None:
        """Tarayiciyi duzgunce kapatip (arka planda asili surec birakmadan)
        pencereyi kapatir. Worker thread'in gercekten bitmesini bekler.

        Onboarding ekrani gosterilirken (henuz ana ekran kurulmadan)
        pencere kapatilirsa `safe_exit_button` diye bir widget henuz
        olusturulmamis olabiliyor - bu yuzden varligini kontrol ediyoruz.

        ONEMLI: Aktif bir tarama surerken (90 ogrenciye kadar surebilir)
        eskiden sadece 10 saniye beklenip pencere ZORLA kapatiliyordu -
        bu, worker thread'i (ve icindeki gercek Chrome surecini/profil
        kilidini) taramanin ortasinda aniden oldurebiliyordu. Simdi once
        self._stop_event ile tarama donguculerine "durmasi gerektigini"
        bildiriyoruz (bkz. _scan_not_defteri / _scan_grade_center), bu
        sayede worker thread mevcut ogeyi bitirip TEMIZ sekilde donuyor.
        """
        # Pencere kapatma (X) + Guvenli Cikis butonu ayni yola dusuyor;
        # bekleme sirasinda olay dongusu calismaya devam ettigi icin
        # (asagidaki update() cagrilari) ikinci bir tiklama/kapatma denemesi
        # bu fonksiyona YENIDEN girebilirdi - tek seferlik kilit.
        if self._exiting:
            return
        self._exiting = True

        exit_button = getattr(self, "safe_exit_button", None)
        if exit_button is not None:
            exit_button.config(state="disabled", text="Kapatılıyor...")
        self.root.update_idletasks()

        self._stop_event.set()
        self.command_queue.put(("quit", None))
        # Tek parca uzun bir join() GUI thread'ini bloke edip pencereyi
        # donduruyordu - Windows bunu ~5 saniyede "Yanıt Vermiyor" olarak
        # isaretleyip kullaniciyi zorla kapatmaya yonlendiriyor (zorla
        # kapatma da tam olarak kacinmaya calistigimiz kirli cikis). Kisa
        # join adimlari arasinda olay dongusunu pompalayarak pencereyi
        # canli tutuyoruz.
        deadline = time.monotonic() + SAFE_EXIT_JOIN_TIMEOUT_S
        while self.worker_thread.is_alive() and time.monotonic() < deadline:
            self.worker_thread.join(timeout=0.1)
            try:
                self.root.update()
            except tk.TclError:
                # Pencere bu arada yok edildiyse beklemeye sessizce devam et.
                pass

        if self.worker_thread.is_alive():
            force = messagebox.askyesno(
                "Kapatma gecikti",
                "Tarayıcı hâlâ bir işlemi bitirmeye çalışıyor (beklenenden "
                "uzun sürdü). Yine de kapatmak istiyor musun?\n\n"
                "Kapatırsan tarayıcı penceresi açık kalabilir, gerekirse "
                "elle kapatman gerekebilir.",
            )
            if not force:
                self._stop_event.clear()
                self._exiting = False
                if exit_button is not None:
                    exit_button.config(state="normal", text="Güvenli Çıkış")
                return

        self.root.destroy()

    def _on_close(self) -> None:
        self._safe_exit()


def main() -> None:
    # tk.Tk() OLUSTURULMADAN ONCE cagirilmasi sart - bkz. fonksiyonun
    # kendi docstring'i (common.py). Windows disinda no-op.
    set_windows_dpi_awareness()
    root = tk.Tk()
    BlackboardGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
