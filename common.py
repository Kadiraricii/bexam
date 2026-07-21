import json
import platform
import re
import subprocess
from datetime import datetime
from pathlib import Path

BASE_URL = "https://istinye.blackboard.com"

ROOT_DIR = Path(__file__).parent
PROFILE_DIR = ROOT_DIR / ".state" / "profile"
LOG_PATH = ROOT_DIR / ".state" / "captures.json"
OUTPUT_DIR = ROOT_DIR / "output"
ONBOARDING_SEEN_PATH = ROOT_DIR / ".state" / "onboarding_seen"

ONAY_PATTERN = re.compile(r"ONAY:\s*([A-F0-9]+)")
SUBMIT_DATE_PATTERN = re.compile(r"G[ÖO]NDER[İI]M TAR[İI]H[İI]:\s*([\d.]+\s+[\d:]+)")
SCORE_PATTERN = re.compile(r"\d+(?:[.,]\d+)?\s*/\s*\d+(?:[.,]\d+)?")
SCORE_SEARCH_WINDOW_CHARS = 150


DEFAULT_FILENAME_MAX_CHARS = 120
# Klasor adlari (ders/sinav) icin daha tutucu bir sinir: ders+sinav+dosya
# adi UST USTE eklenerek Windows'un 260 karakterlik TAM YOL sinirina
# CARPIMSAL olarak katkida bulunuyorlar (dosya adindan farkli olarak,
# capture.py'deki ensure_safe_full_path bu ikisini KAPSAMIYOR - o sadece
# dosya adini kirpar, klasor yapisina dokunmaz). Gercek ders/sinav adlari
# nadiren 60 karakteri asar, bu yuzden bu tutucu varsayilan pratikte
# neredeyse hicbir zaman gorunur bir kirpmaya yol acmaz.
DEFAULT_FOLDER_MAX_CHARS = 60

# Windows'ta buyuk/kucuk harf farketmeksizin dosya/klasor ADI OLARAK
# kullanilamayan ayrilmis isimler (uzanti eklense bile gecersiz kalir,
# ör. "CON.pdf" de calismaz). Bir ders/sinav/ogrenci adi tesaduf eseri
# tam olarak bunlardan biriyle eslesirse (ör. bir dersin kisa kodu "AUX"
# olsaydi) Windows'ta dosya olusturma sessizce/aciklamasizca patlardi -
# bu yuzden boyle bir durumda sonuna zararsiz bir ek ekliyoruz.
WINDOWS_RESERVED_NAMES = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str, max_chars: int = DEFAULT_FILENAME_MAX_CHARS) -> str:
    """Gecersiz karakterleri temizler VE uzunlugu sinirlar - hem macOS/
    Linux hem Windows'ta guvenli bir dosya/klasor adi uretir.

    - `[^\\w\\-. ÇçĞğİıÖöŞşÜü]` deseni zaten Windows'un yasakladigi
      `< > : " / \\ | ? *` karakterlerinin TAMAMINI da kapsiyor (bunlarin
      hicbiri \\w/tire/nokta/bosluk degil), o yuzden ayri bir Windows
      karakter listesi gerekmiyor.
    - Windows'ta dosya adi SONUNDA nokta/bosluk KABUL EDILMIYOR (sessizce
      atiliyor ya da hataya yol aciyor) - bu yuzden sadece basta degil
      sonda da temizliyoruz.
    - Windows'ta CON/PRN/AUX/NUL/COM1-9/LPT1-9 gibi AYRILMIS isimler
      (buyuk/kucuk harf farketmeksizin) dosya/klasor adi olarak
      kullanilamiyor - eslesirse sonuna zararsiz bir ek ekleniyor.
    - max_chars KARAKTER cinsinden; macOS/Linux'ta gercek sinir 255 BAYT
      (Turkce ç/ğ/ı/ö/ş/ü UTF-8'de 2 bayt), Windows'ta ise TAM YOL
      (klasor+dosya) 260 karakterle sinirli olabiliyor - bu yuzden
      kasitli olarak tutucu bir varsayilan (120) kullaniliyor; TAM YOL
      uzunlugu ayrica capture.py'de ayrica kontrol ediliyor (bkz.
      ensure_safe_full_path).
    """
    name = re.sub(r"[^\w\-. ÇçĞğİıÖöŞşÜü]", "_", name).strip()
    name = name.rstrip(". ")
    if len(name) > max_chars:
        name = name[:max_chars].rstrip(". ")
    if not name:
        return "adsiz"
    if name.lower() in WINDOWS_RESERVED_NAMES:
        name = f"{name}_dosya"
    return name


# Windows'un klasik MAX_PATH siniri (260 karakter, ters egik cizgi dahil
# tam yol). Windows 10 1607+'da "uzun yol" destegi acilabiliyor ama bu
# varsayilan olarak KAPALI ve kullanicinin makinesinde acik olacagini
# garanti edemeyiz - bu yuzden proaktif olarak bu sinirin ALTINDA
# kalmaya calisiyoruz, kullaniciya "path too long" gibi anlasilmaz bir
# hata cikmasindansa.
WINDOWS_SAFE_PATH_LIMIT = 240


def ensure_safe_full_path(path: Path, protect_suffix_chars: int = 0) -> Path:
    """Tam dosya yolu WINDOWS_SAFE_PATH_LIMIT'i asarsa, dosya adinin
    (klasorlerin degil) BASINI kirparak yolu guvenli sinira ceker.

    protect_suffix_chars: dosya adinin SONUNDAKI bu kadar karakter
    (ör. "_ONAYKODU.pdf") HER ZAMAN korunur - kirpma sadece onun ONCESINDEKI
    aciklayici baslik kismindan yapilir, cunku ONAY kodu asil kimlik
    belirleyici bilgi.

    Sadece dosya ADI kisaltilir, klasor yapisina (ders/sinav) dokunulmaz -
    hoca cok derin bir klasor secerse (ör. OneDrive senkron yolu) bu
    fonksiyon yine de elinden geleni yapar ama klasor yapisini asla
    degistirmez (kullanicinin sectigi cikti klasorunu bozmamak icin).
    """
    full = str(path)
    overflow = len(full) - WINDOWS_SAFE_PATH_LIMIT
    if overflow <= 0:
        return path

    stem = path.stem
    suffix = path.suffix
    protected = stem[-protect_suffix_chars:] if protect_suffix_chars else ""
    trimmable = stem[: len(stem) - len(protected)] if protected else stem

    new_trimmable_len = max(len(trimmable) - overflow, 0)
    new_stem = (trimmable[:new_trimmable_len] + protected).rstrip(". _") or "dosya"
    return path.with_name(f"{new_stem}{suffix}")


def open_in_file_manager(path: Path) -> None:
    """Bir dosyayi/klasoru isletim sisteminin varsayilan uygulamasinda
    acar - macOS'ta `open`, Windows'ta `os.startfile`. Onceden sadece
    macOS'a ozel `open` komutu kullaniliyordu, bu Windows'ta sessizce
    hicbir sey yapmiyordu (komut bulunamiyor hatasi)."""
    system = platform.system()
    if system == "Windows":
        import os

        os.startfile(str(path))  # type: ignore[attr-defined]
    elif system == "Darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


def normalize_score(text: str) -> str:
    return re.sub(r"\s+", "", text).replace(",", ".")


def extract_page_info(page_text: str) -> dict:
    onay_match = ONAY_PATTERN.search(page_text)
    tarih_match = SUBMIT_DATE_PATTERN.search(page_text)

    lines = [line.strip() for line in page_text.splitlines() if line.strip()]
    title = None
    for i, line in enumerate(lines):
        if line == "Son Not" and i > 0:
            title = lines[i - 1]
            break

    # Not (puan) rozeti, ekran goruntulerinde ONAY koduna hemen yakin
    # gorunuyor (ör. "...ONAY: xxx   54 / 54" / "...ONAY: xxx  50/100").
    # Genel bir "x/y" regex'ini butun sayfada aramak yanlis eslesir
    # (soru bazli puanlar da ayni formatta), bu yuzden ONAY'a yakin bir
    # pencereyle siniyoruz.
    puan = None
    if onay_match:
        window = page_text[onay_match.end():onay_match.end() + SCORE_SEARCH_WINDOW_CHARS]
        score_match = SCORE_PATTERN.search(window)
        if score_match:
            puan = score_match.group(0).strip()

    return {
        "baslik": title,
        "onay": onay_match.group(1) if onay_match else None,
        "gonderim_tarihi": tarih_match.group(1) if tarih_match else None,
        "puan": puan,
    }


def read_log() -> list[dict]:
    if not LOG_PATH.exists():
        return []
    try:
        return json.loads(LOG_PATH.read_text())
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"{LOG_PATH} bozuk gorunuyor (gecersiz JSON): {exc}. "
            "Dosyayi elle kontrol et ya da yedekleyip sil."
        ) from exc


def already_captured_titles() -> set[str]:
    """Halen diskte PDF'i duran, daha once yakalanmis sinav basliklari.

    Log kaydi olsa bile karsilik gelen PDF dosyasi silinmisse bu basligi
    "yakalanmis" saymayiz, aksi halde script onu bir daha uretmez.
    """
    titles = set()
    for entry in read_log():
        title = entry.get("baslik")
        pdf_path = entry.get("pdf")
        if title and pdf_path and Path(pdf_path).exists():
            titles.add(title)
    return titles


def append_log(entry: dict) -> None:
    """Log'a bir kayit ekler, yarim yazimda dosyayi bozmamak icin atomic yazar."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    history = read_log()
    history.append(entry)
    tmp_path = LOG_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(history, ensure_ascii=False, indent=2))
    tmp_path.replace(LOG_PATH)


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


DOWNLOAD_LOG_FILENAME = "indirme_log.txt"


def append_download_log(output_dir: Path, title: str, lines: list[str], totals: dict) -> None:
    """Bir indirme oturumunun insan-okunur ozetini output klasorundeki
    indirme_log.txt dosyasina EKLER (uzerine yazmaz) - zaman icinde
    birikimli bir gecmis olusturur. Makine-okunur .state/captures.json'un
    aksine, bu dosya dogrudan PDF'lerin yaninda durur ve kolayca acilip
    okunabilir/paylasilabilir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    header = f"===== {timestamp} — {title} ====="
    summary = (
        f"Özet: Yakalanan {totals.get('ok', 0)}, "
        f"Atlanan {totals.get('skip', 0)}, "
        f"Hatalı {totals.get('fail', 0)}"
    )
    block = "\n".join([header, *lines, summary, ""])
    log_path = output_dir / DOWNLOAD_LOG_FILENAME
    with log_path.open("a", encoding="utf-8") as f:
        f.write(block + "\n")


def resolve_active_page(context):
    """Context'teki acik sekmeler arasindan Blackboard'da olani bulur.

    SSO akisi (ozellikle Microsoft/Azure AD) bazen giris icin YENI bir
    sekme aciyor; baslangicta tek bir 'page' referansi yakalayip hep onu
    kullanmak, kullanici baska bir sekmede giris tamamlayip Blackboard'a
    geçtiginde script'in hala eski/bos sekmede kalmasina yol aciyordu.
    Bu yuzden her seferinde acik sekmeleri tarayip gercekten Blackboard
    domaininde olani seciyoruz; hicbiri yoksa en son acilan sekmeye
    duseriz (kullanicinin muhtemelen aktif baktigi sekme).

    Her sekmenin URL'sine erisim AYRI AYRI korunuyor: eski/bozuk bir
    sekmede .url okuma hata verirse (ör. o sekme kapanmis/gecis halinde),
    bu tek basina dongunun tamamini durdurup DIGER (gercekten Blackboard'da
    olan) sekmenin hic kontrol edilmemesine yol aciyordu - artik bir
    sekmedeki hata sadece o sekmeyi atlatiyor, digerlerini etkilemiyor.
    """
    last_reachable = None
    for candidate in context.pages:
        try:
            url = candidate.url
        except Exception:
            continue
        last_reachable = candidate
        if url.startswith(BASE_URL):
            return candidate
    return last_reachable


def find_blackboard_pages(context) -> list:
    """context.pages icinde BASE_URL ile baslayan TUM sekmeleri dondurur
    (sadece ilkini degil - resolve_active_page'in aksine).

    Neden lazim: resolve_active_page ILK eslesen sekmeyi secip taramaya
    onu veriyor. Kullanici birden fazla Blackboard sekmesi actiysa (ör.
    onceki dersin sekmesini kapatmayi unuttu), "Bul ve Tara" kullanicinin
    su an BAKTIGI sekmeyi degil, context.pages sirasindaki ilk esleseni
    tarayabilir - kullanici fark etmeden YANLIS ders/sinav taranmis olur.
    Bu fonksiyon o belirsizligi TESPIT etmek icin kullanilir (bkz.
    gui.py::_discover) - birden fazla varsa, sessizce tahmin etmek yerine
    kullaniciyi acikca uyarip taramayi baslatmiyoruz.
    """
    matches = []
    for candidate in context.pages:
        try:
            url = candidate.url
        except Exception:
            continue
        if url.startswith(BASE_URL):
            matches.append(candidate)
    return matches


def wait_for_blackboard(page, attempts: int = 6, delay_ms: int = 500) -> bool:
    """Sayfanin Blackboard domaininde olup olmadigini birkac kez, kisa
    araliklarla kontrol eder.

    Tek anlik bir kontrol yanlis pozitif verebiliyordu: SSO'nun son
    yonlendirme anini yakalayip sayfa hala login ekranindaymis gibi
    goruntu verebiliyordu, oysa bir kac yuz milisaniye sonra sayfa zaten
    Blackboard'a gecmis oluyordu. Bu yuzden hemen "giris yapilmamis" diye
    hukmetmek yerine, sayfanin yerlesmesi icin kisa bir sure (varsayilan
    ~3 saniye) taniyoruz.
    """
    for _ in range(attempts):
        try:
            if page.url.startswith(BASE_URL):
                return True
        except Exception:
            pass
        page.wait_for_timeout(delay_ms)
    try:
        return page.url.startswith(BASE_URL)
    except Exception:
        return False


BROWSER_CLOSED_ERROR_MARKERS = (
    "target closed",
    "target page, context or browser has been closed",
    "browser has been closed",
    "has been closed",
)


def is_browser_closed_error(exc: BaseException) -> bool:
    """Bir istisnanin, tarayici/sekme kapandigi/coktugu icin mi olustugunu
    tahmin eder (ör. kullanici taramanin ortasinda Chrome'u Cmd+Q ile
    kapatirsa, sonraki her page.* cagrisi bu tur bir hata firlatir).

    Neden onemli: bu tur hatalar genel bir "HATA" gibi 5 kez art arda
    (MAX_CONSECUTIVE_FAILURES) tekrar tekrar denenip basarisiz olmak
    yerine ANINDA taniyip durmali - tarayici zaten yok, tekrar denemenin
    hicbir anlami yok, sadece kafa karistirici log + zaman kaybi olur.
    """
    message = str(exc).lower()
    return any(marker in message for marker in BROWSER_CLOSED_ERROR_MARKERS)


CLOUD_SYNC_PATH_MARKERS = (
    "library/mobile documents",  # iCloud Drive (macOS)
    "dropbox",
    "onedrive",
)


def cloud_sync_warning(output_dir: Path) -> str | None:
    """Secilen cikti klasoru bilinen bir bulut-senkron kok dizin altindaysa
    (iCloud Drive, Dropbox, OneDrive) bir uyari metni dondurur, degilse None.

    Neden: bu tur klasorlerde dosyalar bazen "placeholder" (henuz gercekten
    diske inmemis) olarak durabiliyor ya da senkronizasyon gecikebiliyor -
    bu da capture.py'deki "PDF supheli derecede kucuk" boyut kontrolunu
    yanlis tetikleyebilir ya da yazma/okuma gecikmelerine yol acabilir.
    Engelleme degil, sadece bilgilendirme - kullanici yine de devam
    edebilir.
    """
    normalized = str(output_dir).lower()
    for marker in CLOUD_SYNC_PATH_MARKERS:
        if marker in normalized:
            return (
                "Seçtiğin klasör bir bulut-senkron alanının (iCloud/Dropbox/"
                "OneDrive) içinde görünüyor. Senkronizasyon PDF yazma/"
                "doğrulamayı zaman zaman geciktirebilir - sorun yaşarsan "
                "yerel bir klasör (ör. Masaüstü'nde ayrı bir klasör) seçmeyi "
                "dene."
            )
    return None


STALE_PROFILE_LOCK_FILENAMES = ("SingletonLock", "SingletonSocket", "SingletonCookie")

PROFILE_LOCK_ERROR_MARKERS = (
    "singletonlock",
    "processsingleton",
    "user data directory is already in use",
    "failed to create a chrome process",
)


def clear_stale_profile_lock(profile_dir: Path) -> None:
    """Onceki bir zorla-kapatma/coke sonrasi Chrome'un profil klasorunde
    (PROFILE_DIR) kalmis olabilecek kilit dosyalarini (SingletonLock vb.)
    temizlemeyi dener.

    Normalde context.close() bu dosyalari kendisi temizler, ama zorla
    sonlandirma (ör. islem oldurulmesi, guc kesilmesi, ya da eskiden -bu
    proje icinde artik duzeltilmis- "Guvenli Cikis"in aktif taramayi
    beklemeden pencereyi kapattigi durum) sonrasi yetim kalabiliyorlar,
    bu da sonraki "Tarayıcıyı Aç" denemesini basarisiz kilabiliyor.

    Tamamen kayipsiz bir islem: dosyalar gercekten baska bir Chrome
    surecince kullanimdaysa silme zaten basarisiz olur (Windows'ta dosya
    kilitli olur, Unix'te bile olsa launch_persistent_context yine de
    kendi hata mesajini verir) ve normal akisa (madde 7'nin daha net hata
    mesaji) devam edilir - kullanimda DEGILSE silinmeleri guvenlidir.
    """
    if not profile_dir.exists():
        return
    for filename in STALE_PROFILE_LOCK_FILENAMES:
        lock_path = profile_dir / filename
        try:
            lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def is_profile_lock_error(exc_or_message: BaseException | str) -> bool:
    """Bir tarayici-acma hatasinin profil kilidi cakismasindan (ör. ayni
    .state/profile klasorunu kullanan baska bir uygulama orneği zaten
    calisiyor) kaynaklanip kaynaklanmadigini tahmin eder. Hem istisna
    nesnesi hem de zaten string'e cevrilmis hata mesaji kabul eder."""
    message = str(exc_or_message).lower()
    return any(marker in message for marker in PROFILE_LOCK_ERROR_MARKERS)


def check_output_writable(output_dir: Path) -> str | None:
    """output_dir'e gercekten yazilabiliyor mu diye kucuk bir deneme yapar.

    Neden: diskte yer kalmamasi ya da klasore yazma izni olmamasi gibi
    durumlar oncesinde SADECE ilk PDF yazma denemesinde (ör. 47. ogrenci
    islenirken) ortaya cikiyordu - o ana kadar zaman kaybediliyor, hata
    mesaji da kriptik bir OSError oluyordu. Uzun bir taramaya baslamadan
    ONCE bu kontrolu yapip net bir hata vermek, hem zaman kazandiriyor
    hem daha anlasilir bir mesaj sagliyor.

    Yazilabiliyorsa None, degilse kullaniciya gosterilecek Turkce bir
    hata mesaji dondurur.
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe_path = output_dir / ".yazma_testi.tmp"
        probe_path.write_text("test")
        probe_path.unlink()
    except OSError as exc:
        return (
            f"Çıktı klasörüne yazılamadı ({exc}). Diskte yer kalmamış olabilir "
            "ya da bu klasöre yazma izni yok - klasörü değiştirmeyi dene."
        )
    return None


DEFAULT_WINDOW_WIDTH = 1440
DEFAULT_WINDOW_HEIGHT = 900


def browser_launch_kwargs() -> dict:
    """Tarayici penceresini makul, sabit bir boyutta acmak icin ortak
    launch ayarlari.

    Once ekran cozunurlugune gore tam ekran acmayi denedik, ama Blackboard'un
    kendi sayfasi sabit/dar bir genislikte kaliyor - pencereyi ekrana gore
    zorlamak sadece etrafta buyuk bos alan biraktirdi (gercek bir hata degil,
    Blackboard'un kendi tasarimi). Bunun yerine cogu sitenin rahat gorundugu
    standart bir boyut (1440x900) kullaniyoruz. viewport'u da ayni degere
    sabitliyoruz cunku viewport=None, kalici profildeki (PROFILE_DIR)
    Chrome'un kendi hatirladigi eski pencere durumuyla catisiyordu.

    channel="chrome": Playwright'in kendi ozel "Chrome for Testing"
    derlemesi yerine gercekten kurulu Google Chrome'u surer. Microsoft/
    Azure AD SSO, otomasyon araclariyla surulen tarayicilari tespit edip
    giris akisini sonsuz yonlendirme dongusune sokabiliyor (bilinen,
    dokumante edilmis bir kisitlama); gercek Chrome + otomasyon
    bayraklarinin gizlenmesi bu riski azaltiyor. Kendi ayri profilimizi
    (PROFILE_DIR) kullandigimiz icin Chrome'un "varsayilan profili
    otomatiklestirme desteklenmiyor" kisitlamasina takilmiyoruz.
    """
    return {
        "channel": "chrome",
        "viewport": {"width": DEFAULT_WINDOW_WIDTH, "height": DEFAULT_WINDOW_HEIGHT},
        "args": [
            f"--window-size={DEFAULT_WINDOW_WIDTH},{DEFAULT_WINDOW_HEIGHT}",
            "--window-position=0,0",
            "--disable-blink-features=AutomationControlled",
        ],
        "ignore_default_args": ["--enable-automation"],
    }


def derive_course_label(page) -> str:
    """Sayfa basligindan ders adini tahmin eder.

    Blackboard sekme basligi genelde '.../Ders Adi / Not Defteri' gibi
    '/' ile ayrilmis parcalar iceriyor; son parca en spesifik/okunakli
    olani oluyor. Ayrilan bir isim cikmazsa genel 'ders' etiketine
    duseriz. Bu mantik onceden capture.py, scan_course.py,
    scan_grade_center.py ve gui.py'de ayri ayri tekrarlaniyordu - tek
    yerde toplandi.
    """
    return page.title().split("/")[-1].strip() or "ders"


def has_seen_onboarding() -> bool:
    return ONBOARDING_SEEN_PATH.exists()


def mark_onboarding_seen() -> None:
    ONBOARDING_SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    ONBOARDING_SEEN_PATH.write_text(now_stamp())
