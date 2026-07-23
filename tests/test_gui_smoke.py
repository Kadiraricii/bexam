"""gui.py icin duman (smoke) testleri.

Gercek bir Tk penceresi acar; sayfalarin hatasiz kuruldugunu, kompakt
modun, onboarding akisinin ve bu oturumda bulunup duzeltilen spesifik
hatalarin (Chrome kapanma algisi, acilir/kapanir uyari bandi, onboarding
tasmasi) DUZELTILMIS haliyle calistigini dogrular.

NOT: "cursor='pointinghand'" gibi SADECE belirli bir widget kurulurken
tetiklenen, platforma-ozel cokme turu hatalar bu testlerle (widget
agacinin GERCEKTEN kurulmasiyla) yakalanir - salt modul ice aktarma
testleri BUNU kacirir. En gercekci dogrulama icin ayrica bkz.
test_app_launches.py (gercek `python gui.py` alt-surec calistirmasi).
"""

import tkinter as tk

import pytest

import gui as gui_module
from conftest import (
    _fake_sync_playwright,
    find_button_with_text,
    find_labels_containing,
    find_widgets,
    is_currently_packed,
)


# ---------- _emoji_font (Windows'ta gri/monokrom emoji hatasi) ----------
# Windows'ta Tk, emoji karakterlerini VARSAYILAN olarak "Segoe UI Symbol"
# fontuyla render ediyordu - macOS'taki renkli emoji gorunumunun aksine
# gri/tek renk, silik bir sonuc (CANLI DOGRULANDI, ekran goruntusuyle).
# "Segoe UI Emoji" ACIKCA istenirse Tk dogru, renkli glif setini seciyor.


def test_emoji_font_uses_segoe_ui_emoji_on_windows(monkeypatch):
    monkeypatch.setattr(gui_module.platform, "system", lambda: "Windows")
    monkeypatch.setattr(gui_module, "_EMOJI_FONT_FAMILY", "Segoe UI Emoji")

    assert gui_module._emoji_font(20) == ("Segoe UI Emoji", 20)
    assert gui_module._emoji_font(20, "bold") == ("Segoe UI Emoji", 20, "bold")


def test_emoji_font_uses_system_default_on_non_windows(monkeypatch):
    monkeypatch.setattr(gui_module, "_EMOJI_FONT_FAMILY", "")

    assert gui_module._emoji_font(18) == ("", 18)


def test_onboarding_screen_builds_and_shows_next_button_on_first_step(gui_app):
    # Sihirbaz TEK adim gosterdigi icin ilk adimda buton "İleri →" yazar -
    # "Başla →" yalnizca SON adimda gorunur (bkz. asagidaki testler).
    button = find_button_with_text(gui_app.container, "İleri →")

    assert button is not None


def test_onboarding_next_button_becomes_start_button_on_last_step(gui_app):
    for _ in range(len(gui_module.ONBOARDING_STEPS) - 1):
        gui_app._onboarding_go_next()
    gui_app.root.update_idletasks()

    assert gui_app._onboarding_step_index == len(gui_module.ONBOARDING_STEPS) - 1
    button = find_button_with_text(gui_app.container, "Başla →")
    assert button is not None


def test_onboarding_prev_button_disabled_on_first_step_and_enabled_after_advancing(gui_app):
    assert gui_app._onboarding_prev_button._state == "disabled"

    gui_app._onboarding_go_next()
    gui_app.root.update_idletasks()

    assert gui_app._onboarding_prev_button._state == "normal"


def test_onboarding_arrow_keys_navigate_between_steps(gui_app):
    gui_app._onboarding_go_next()
    assert gui_app._onboarding_step_index == 1

    gui_app._onboarding_go_prev()
    assert gui_app._onboarding_step_index == 0

    # Ilk adimdayken bir daha "geri" gitmeye calismak sinirin ALTINA
    # inmemeli (negatif indeks olusturmamali).
    gui_app._onboarding_go_prev()
    assert gui_app._onboarding_step_index == 0


def test_onboarding_next_on_last_step_finishes_onboarding(gui_app):
    for _ in range(len(gui_module.ONBOARDING_STEPS) - 1):
        gui_app._onboarding_go_next()
    gui_app._onboarding_go_next()  # son adimdaki "Başla →" tiklamasini simule eder
    gui_app.root.update_idletasks()

    assert hasattr(gui_app, "scan_button")


def test_finish_onboarding_reveals_app_shell_with_scan_button(gui_app):
    gui_app._finish_onboarding()
    gui_app.root.update_idletasks()

    assert hasattr(gui_app, "scan_button")


@pytest.mark.parametrize("page_key", ["home", "download", "outputs", "logs", "settings", "help"])
def test_every_nav_page_builds_without_error(gui_app, page_key):
    gui_app._finish_onboarding()

    gui_app._show_page(page_key)
    gui_app.root.update_idletasks()

    assert gui_app.current_page == page_key


def test_compact_mode_enter_and_exit_round_trip(gui_app):
    gui_app._finish_onboarding()

    gui_app._enter_compact_mode()
    gui_app.root.update_idletasks()
    assert gui_app._compact_mode is True

    gui_app._exit_compact_mode()
    gui_app.root.update_idletasks()
    assert gui_app._compact_mode is False


def test_chrome_flag_notice_not_shown_during_onboarding(gui_app):
    labels = find_labels_containing(gui_app.container, "Beklenen Bir Tarayıcı")

    assert labels == []


def test_chrome_flag_notice_shown_exactly_once_on_home_page(gui_app):
    gui_app._finish_onboarding()
    gui_app._show_page("home")
    gui_app.root.update_idletasks()

    labels = find_labels_containing(gui_app.container, "Beklenen Bir Tarayıcı")

    assert len(labels) == 1


def test_chrome_flag_notice_starts_collapsed_on_home_page(gui_app):
    gui_app._finish_onboarding()
    gui_app._show_page("home")
    gui_app.root.update_idletasks()

    detail = find_labels_containing(gui_app.container, "Chrome açılışta aşağıdaki")[0]

    # detail.master ("body" cercevesi) pack_forget()/pack() ile
    # gizlenip/gosteriliyor - detail'in KENDI winfo_manager() durumu
    # (kendi dogrudan ebeveyni olan body icinde hala "pack" kalir) bunu
    # yansitmaz, bu yuzden dogrudan body'yi kontrol ediyoruz.
    assert not is_currently_packed(detail.master)


def test_chrome_flag_notice_starts_expanded_on_help_page(gui_app):
    gui_app._finish_onboarding()
    gui_app._show_page("help")
    gui_app.root.update_idletasks()

    detail = find_labels_containing(gui_app.container, "Chrome açılışta aşağıdaki")[0]

    assert is_currently_packed(detail.master)


def test_chrome_flag_notice_chevron_expands_and_collapses_on_click(gui_app):
    gui_app._finish_onboarding()
    gui_app._show_page("home")
    gui_app.root.update_idletasks()

    chevron = find_widgets(gui_app.container, lambda w: isinstance(w, gui_module.ChevronToggle))[0]
    detail = find_labels_containing(gui_app.container, "Chrome açılışta aşağıdaki")[0]

    # chevron._on_click() DOGRUDAN cagriliyor - event_generate("<Button-1>")
    # DEGIL: tk_root fixture'i pencereyi withdraw() ile gizliyor (testler
    # sirasinda ekranda pencere yanip sonmesin diye), ve CANLI DOGRULANDI
    # ki bu durumda Canvas widget'larda sentetik <Button-1> olayi dogru
    # yonlenmiyor (fiziksel buton basma olaylari gercek, haritalanmis
    # ekran koordinatlarina ihtiyac duyuyor). Isleyiciyi dogrudan cagirmak
    # AYNI kod yolunu (set_expanded + command cagrisi) sinar, pencere
    # gorunurlugunden bagimsiz sekilde.
    chevron._on_click()
    gui_app.root.update_idletasks()
    assert is_currently_packed(detail.master)

    chevron._on_click()
    gui_app.root.update_idletasks()
    assert not is_currently_packed(detail.master)


def test_browser_lost_message_visually_disables_scan_and_download_buttons(gui_app):
    gui_app._finish_onboarding()
    gui_app._connection_state = "connected"
    gui_app._scan_enabled = True
    gui_app._download_enabled = True
    gui_app._apply_tracked_state()

    gui_app._handle_message("browser_lost", None)

    assert gui_app.scan_button._state == "disabled"
    assert gui_app.download_button._state == "disabled"
    assert gui_app.open_browser_button._state == "normal"


def test_onboarding_nav_buttons_stay_within_window_bounds_on_small_window(monkeypatch):
    """Sihirbaz artik TEK adim gosterdigi icin (bkz. _render_onboarding_step)
    tasma riski yapisal olarak cok dustu, ama footer'daki (checkbox +
    Geri/İleri butonlari) kucuk bir pencerede hala pencere sinirlari
    icinde kaldigini dogruluyoruz - bu oturumda bulunan gercek bir bug'in
    (buton eskiden ekran disina itilebiliyordu) regresyon testi. Hem ILK
    adimda ("İleri →") hem SON adimda ("Başla →") kontrol ediyoruz."""
    monkeypatch.setattr(gui_module, "has_seen_onboarding", lambda: False)
    monkeypatch.setattr(gui_module, "mark_onboarding_seen", lambda: None)
    monkeypatch.setattr(gui_module, "sync_playwright", _fake_sync_playwright)

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk penceresi oluşturulamadı: {exc}")

    try:
        root.geometry("1160x650")  # kucuk yukseklik - tasma senaryosunu zorluyor
        app = gui_module.BlackboardGUI(root)
        root.update_idletasks()

        def _assert_next_button_within_bounds() -> None:
            window_bottom = root.winfo_rooty() + root.winfo_height()
            button_bottom = (
                app._onboarding_next_button.winfo_rooty() + app._onboarding_next_button.winfo_height()
            )
            assert button_bottom <= window_bottom + 5

        _assert_next_button_within_bounds()  # ilk adim: "İleri →"

        for _ in range(len(gui_module.ONBOARDING_STEPS) - 1):
            app._onboarding_go_next()
        root.update_idletasks()
        _assert_next_button_within_bounds()  # son adim: "Başla →"

        app.command_queue.put(("quit", None))
        app.worker_thread.join(timeout=10)
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_scan_students_button_exists_on_home_page(gui_app):
    gui_app._finish_onboarding()

    button = find_button_with_text(gui_app.container, "Öğrenci Tara")

    assert button is not None


def test_scan_students_appends_result_to_persistent_download_log(gui_app, monkeypatch, tmp_path):
    """_scan_students, indirme_log.txt'ye append_download_log ile ("a"
    modu - EKLEME, uzerine yazma DEGIL) yaziyor - bu, farkli tarama
    oturumlarinin birbirini SILMEDIGINI, ikisinin de dosyada YAN YANA
    kaldigini dogrular (bkz. konusma gecmisindeki kullanici endisesi)."""
    monkeypatch.setattr(gui_module, "wait_for_blackboard", lambda page: True)
    monkeypatch.setattr(gui_module, "derive_course_label", lambda page: "BST020")
    monkeypatch.setattr(
        gui_module, "find_student_roster",
        lambda page, emit: [("AHMET YILMAZ", "2420171001")],
    )

    fake_page = object()
    gui_app._scan_students(fake_page, tmp_path)
    gui_app._scan_students(fake_page, tmp_path)  # ikinci tarama - ilkini SILMEMELI

    log_path = tmp_path / gui_module.DOWNLOAD_LOG_FILENAME
    content = log_path.read_text(encoding="utf-8")

    assert content.count("BST020 — Öğrenci Tara") == 2  # iki blok da orada
    assert "1 öğrenci bulundu" in content
    assert "Özet: Yakalanan 1, Atlanan 0, Hatalı 0" in content


def test_scan_students_current_page_enqueues_command_and_disables_action_buttons(gui_app):
    gui_app._finish_onboarding()
    gui_app._connection_state = "connected"
    gui_app._scan_enabled = True
    gui_app._download_enabled = True
    gui_app._student_scan_enabled = True
    gui_app._apply_tracked_state()

    gui_app._scan_students_current_page()

    # Tek Playwright worker'ini paylasan diger iki islemle CAKISMAMASI
    # icin "Öğrenci Tara" tiklaninca UCU de kapanmali (bkz.
    # _scan_students_current_page docstring notu).
    assert gui_app.scan_button._state == "disabled"
    assert gui_app.download_button._state == "disabled"
    assert gui_app.scan_students_button._state == "disabled"
    command, payload = gui_app.command_queue.get_nowait()
    assert command == "scan_students"
    assert payload == gui_app.output_dir


def test_student_scan_done_message_reenables_buttons_when_still_connected(gui_app):
    gui_app._finish_onboarding()
    gui_app._connection_state = "connected"
    gui_app._apply_tracked_state()

    gui_app._handle_message("student_scan_done", {"count": 5, "path": "x.csv"})

    assert gui_app.scan_button._state == "normal"
    assert gui_app.scan_students_button._state == "normal"


def test_student_scan_done_message_keeps_buttons_disabled_after_browser_lost(gui_app):
    """Tarama bitmeden hemen once tarayici kapanirsa, worker onceden
    "browser_lost" gonderip baglantiyi sifirlamis olur - bu SIRADAN
    gelen "student_scan_done" mesaji butonlari YANLISLIKLA tekrar
    ACMAMALI (aksi halde tarayicisiz bir komut kuyruga girebilir)."""
    gui_app._finish_onboarding()
    gui_app._handle_message("browser_lost", None)

    gui_app._handle_message("student_scan_done", None)

    assert gui_app.scan_button._state == "disabled"
    assert gui_app.scan_students_button._state == "disabled"


def test_help_page_content_is_scrollable_on_small_window(monkeypatch):
    """Yardim sayfasi artik 5 adim + 'Öğrenci Tara' detay karti + Chrome
    uyarisi bir arada barindiriyor - kucuk bir pencerede bunlarin TOPLAMI
    pencere yuksekligini rahatlikla asabiliyor (CANLI GOZLEMLENEN bir
    tasma). _make_scrollable_area ile sarmalandigi icin artik (a) bir
    kaydirma cubugu var VE (b) icerigin gercek yuksekligi gorunen alandan
    BUYUK - yani kaydirma gercekten ISE YARIYOR, sadece sussuz durmuyor."""
    monkeypatch.setattr(gui_module, "has_seen_onboarding", lambda: True)
    monkeypatch.setattr(gui_module, "sync_playwright", _fake_sync_playwright)

    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"Tk penceresi oluşturulamadı: {exc}")

    try:
        root.geometry("1160x650")  # kucuk yukseklik - tasma senaryosunu zorluyor
        app = gui_module.BlackboardGUI(root)
        app._show_page("help")
        root.update_idletasks()

        scrollbars = find_widgets(app.container, lambda w: isinstance(w, gui_module.ttk.Scrollbar))
        assert len(scrollbars) == 1

        # _make_scrollable_area'nin create_window ile embed ettigi canvas,
        # uygulamadaki TEK "gercek widget cocuguna sahip" Canvas - digerleri
        # (ikon/rozet canvas'lari) sadece cizim ogeleri (oval/polygon)
        # tasir, gercek Tk widget cocugu YOKTUR.
        canvases = find_widgets(app.container, lambda w: isinstance(w, tk.Canvas))
        scroll_canvas = next(c for c in canvases if c.winfo_children())
        content_height = scroll_canvas.bbox("all")[3]
        assert content_height > scroll_canvas.winfo_height()

        app.command_queue.put(("quit", None))
        app.worker_thread.join(timeout=10)
    finally:
        try:
            root.destroy()
        except tk.TclError:
            pass


def test_start_download_aborts_when_page_changed_since_discovery(gui_app, monkeypatch):
    """Iki-faktorlu koruma: 'Bul ve Tara' ile 'PDF Olarak İndir' arasinda
    kullanici tarayicida BASKA sayfaya gectiyse indirme HIC baslamamali -
    yanlis sayfadan yanlis icerik inme riski var (yuksek olasilikli bir
    kullanici hatasi senaryosu)."""
    monkeypatch.setattr(
        gui_module, "live_url", lambda page: "https://istinye.blackboard.com/BASKA-sayfa"
    )
    scan_calls = []
    monkeypatch.setattr(
        gui_app, "_scan_not_defteri", lambda *a, **k: scan_calls.append(a), raising=False
    )

    gui_app._start_download(object(), {
        "kind": "not_defteri",
        "url": "https://istinye.blackboard.com/taranan-sayfa",
        "output_dir": gui_app.output_dir,
    })

    assert scan_calls == []  # indirme baslamadi
    messages = []
    while not gui_app.gui_queue.empty():
        messages.append(gui_app.gui_queue.get_nowait())
    kinds = [k for k, _ in messages]
    assert "discovery_failed" in kinds
    log_texts = [p for k, p in messages if k == "log"]
    assert any("tekrar 'Bul ve Tara'" in text for text in log_texts)


def test_start_download_proceeds_when_page_url_unchanged(gui_app, monkeypatch, tmp_path):
    monkeypatch.setattr(
        gui_module, "live_url", lambda page: "https://istinye.blackboard.com/taranan-sayfa"
    )
    scan_calls = []
    monkeypatch.setattr(
        gui_app, "_scan_not_defteri",
        lambda *a, **k: scan_calls.append(a), raising=False,
    )

    gui_app._start_download(object(), {
        "kind": "not_defteri",
        "url": "https://istinye.blackboard.com/taranan-sayfa",
        "course_label": "BST020",
        "course_dir": tmp_path / "BST020",
        "output_dir": tmp_path,
        "items": [],
    })

    assert len(scan_calls) == 1


def test_start_download_pending_uses_freshly_chosen_output_folder(gui_app, tmp_path):
    """Kesiften SONRA cikti klasoru degistirilirse indirme YENI klasore
    gitmeli - eskiden payload'daki yollar kesif anindaki klasorde kaliyor,
    PDF'ler kullanicinin az once birakip degistirdigi ESKI klasore
    iniyordu (sessiz ve kafa karistirici)."""
    gui_app._finish_onboarding()
    old_dir = tmp_path / "eski"
    new_dir = tmp_path / "yeni"
    gui_app._pending_discovery = {
        "kind": "not_defteri",
        "course_label": "BST020",
        "course_dir": old_dir / "BST020",
        "output_dir": old_dir,
        "items": [],
        "url": "https://istinye.blackboard.com/x",
    }
    gui_app.output_dir = new_dir

    gui_app._start_download_pending()

    command, payload = gui_app.command_queue.get_nowait()
    assert command == "download"
    assert payload["output_dir"] == new_dir
    assert payload["course_dir"] == new_dir / "BST020"


def test_download_tree_page_handles_missing_output_directory(gui_app, tmp_path):
    gui_app._finish_onboarding()
    gui_app.output_dir = tmp_path / "hic-olusmamis"

    gui_app._show_page("download")
    gui_app.root.update_idletasks()

    assert gui_app.current_page == "download"
