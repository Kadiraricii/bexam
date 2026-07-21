"""
Blackboard sinav degerlendirme sayfasini PDF olarak indirir ve
GONDERIM TARIHI / ONAY kodunu ayiklar.

Kullanim:
    source .venv/bin/activate
    python3 capture.py

Tarayici acilinca SSO ile giris yap, PDF almak istedigin
"Degerlendirme Geri Bildirimi" sayfasina git, sonra terminale
donup ENTER'a bas. Ayni oturumda istedigin kadar sayfa yakalayabilirsin.
"""

from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from common import (
    BASE_URL,
    OUTPUT_DIR,
    PROFILE_DIR,
    append_log,
    browser_launch_kwargs,
    ensure_safe_full_path,
    extract_page_info,
    now_stamp,
    resolve_active_page,
    sanitize_filename,
)

YONERGELER_TAB_NAME = "Yönergeler"


def switch_to_submission_tab(page: Page) -> None:
    """Odev degerlendirme sayfalarinda bazen birden fazla sekme oluyor:
    ilki hocanin odev aciklamasini gosteren sabit 'Yonergeler' sekmesi,
    digeri(leri) ogrencinin GONDERDIGI dosyanin kendi adiyla etiketli
    sekmesi/sekmeleri. Sayfa varsayilan olarak 'Yonergeler' sekmesiyle
    aciliyor - bu yakalanirsa PDF'e HOCANIN ODEV TALIMATI girer,
    OGRENCININ GERCEKTEN GONDERDIGI ICERIK DEGIL (gercek bir hata,
    ekran goruntusuyle dogrulandi). ONAY/not/tarih sekmeden bagimsiz
    sayfa basliginda oldugu icin bu durumda bile DOGRU cikiyordu - bu
    yuzden fark edilmesi uzun surdu.

    Sinav/quiz sayfalarinda (test edilen BST020 gibi) boyle bir sekme
    yapisi hic gorulmedi, bu yuzden bu fonksiyon SADECE 'Yonergeler'
    adinda bir ILK sekme varsa devreye giriyor - yoksa hicbir sey
    yapmiyor, mevcut akisi bozmuyor.

    Bilinen sinirlama: ogrenci BIRDEN FAZLA dosya yuklediyse su an
    sadece SON sekme yakalaniyor, digerleri atlaniyor - bu sık
    gorulurse tum sekmeleri ayri ayri yakalayacak sekilde genisletmek
    gerekir.
    """
    tabs = page.get_by_role("tab")
    try:
        count = tabs.count()
    except Exception:
        return
    if count < 2:
        return
    try:
        first_tab_name = tabs.nth(0).inner_text().strip()
    except Exception:
        return
    if first_tab_name != YONERGELER_TAB_NAME:
        return
    try:
        tabs.nth(count - 1).click()
        # Gomulu dosya onizleyicisi (ör. PDF.js benzeri bir goruntuleyici)
        # kendi icerigini yuklemeye zaman ihtiyaci duyabilir - 300ms bazen
        # yetersizdi, tampon payi arttirildi. Bundan sonraki AUTO_SCROLL_JS
        # dongusu de (~30 saniyeye kadar) icerik hala buyuyorsa beklemeye
        # devam edecek, bu sadece ilk baslangic gecikmesi icin.
        page.wait_for_timeout(800)
    except Exception:
        # Sekmeye gecis basarisiz olursa, en azindan Yonergeler icerigiyle
        # (yanlis olsa da) devam etmek, hicbir PDF uretmemekten iyi -
        # capture_current_page kendi dogrulamalarina devam edecek.
        pass


FORCE_VISIBLE_CSS = """
* {
    overflow: visible !important;
    max-height: none !important;
}
html, body {
    height: auto !important;
}
"""

AUTO_SCROLL_MAX_ITERATIONS = 120
AUTO_SCROLL_MIN_PDF_BYTES = 3000

# Hocalarin sinav sorularina koydugu fotograflar bazen PDF'e "acilmamis"
# (bos/kirik) gorunumde giriyordu - sebebi, page.pdf() cagrisinin
# gorsellerin ag indirmesi/decode'u tamamlanmasini BEKLEMEDEN calismasiydi.
# Bu yuzden yazdirmadan once sayfadaki TUM <img> etiketlerinin gercekten
# yuklenmesini (complete=true) ve decode edilmesini bekliyoruz.
IMAGE_LOAD_MAX_WAIT_MS = 15_000
IMAGE_LOAD_POLL_MS = 300

# Kasitli olarak kaydirma sonunda basa DONMUYORUZ: icerik virtualized ise
# (sadece o an gorunen kisim DOM'da), basa donmek daha once yuklenen alt
# kismi DOM'dan dusurup PDF'te eksik birakabilir. Chrome'un yazdirma
# motoru zaten mevcut scroll pozisyonundan bagimsiz olarak butun
# dokumani basar, bu yuzden basa donmenin islevsel bir faydasi yok,
# sadece riski var.
AUTO_SCROLL_JS = """
async () => {
    const delay = (ms) => new Promise((r) => setTimeout(r, ms));

    const candidates = Array.from(document.querySelectorAll('*'));
    let target = document.scrollingElement || document.body;
    let maxOverflow = 0;
    for (const el of candidates) {
        const overflow = el.scrollHeight - el.clientHeight;
        if (overflow > maxOverflow) {
            maxOverflow = overflow;
            target = el;
        }
    }

    let lastHeight = 0;
    let stabilized = false;
    let iterations = 0;
    for (let i = 0; i < %(max_iterations)d; i++) {
        iterations = i + 1;
        target.scrollTop = target.scrollHeight;
        window.scrollTo(0, document.body.scrollHeight);
        await delay(250);
        const height = target.scrollHeight;
        if (height === lastHeight) {
            stabilized = true;
            break;
        }
        lastHeight = height;
    }

    return { stabilized, iterations, finalHeight: lastHeight };
}
""" % {"max_iterations": AUTO_SCROLL_MAX_ITERATIONS}

# "pending": henuz yuklenmesi bitmemis (img.complete === false) gorseller -
# bunlar varken PDF basilirsa bos/kirik cikar, bu yuzden bekleniyor.
# "failed": tarayici yuklemeyi BITIRMIS ama goruntu gelmemis (naturalWidth=0,
# ör. bozuk link/404) gorseller - bunlar beklemekle duzelmez, sadece
# bilgi amacli sayiliyor ki PDF elle kontrol edilebilsin.
WAIT_IMAGES_JS = """
async () => {
    const delay = (ms) => new Promise((r) => setTimeout(r, ms));
    let waited = 0;
    while (waited <= %(max_wait)d) {
        const imgs = Array.from(document.querySelectorAll('img'));
        if (imgs.every((img) => img.complete)) {
            break;
        }
        await delay(%(poll)d);
        waited += %(poll)d;
    }

    const imgs = Array.from(document.querySelectorAll('img'));
    await Promise.all(
        imgs.map((img) => (img.decode ? img.decode().catch(() => {}) : Promise.resolve()))
    );

    const pending = imgs.filter((img) => !img.complete).length;
    const failed = imgs.filter((img) => img.complete && img.naturalWidth === 0).length;
    return { total: imgs.length, pending, failed, waitedMs: waited };
}
""" % {"max_wait": IMAGE_LOAD_MAX_WAIT_MS, "poll": IMAGE_LOAD_POLL_MS}


def _iter_frames(page: Page):
    """Ana sayfa artı (varsa) icindeki tum alt cerceveler (iframe)."""
    yield page.main_frame
    for frame in page.frames:
        if frame != page.main_frame:
            yield frame


def scroll_all_frames(page: Page) -> list[dict]:
    """Ana sayfa VE icindeki her (same-origin) iframe'i ayri ayri kaydirir.

    Neden: Blackboard'un gomulu dosya onizleyicisi (ör. bir odevin
    yuklenen Word/PDF dosyasinin onizlemesi) kendi IC KAYDIRMASI olan bir
    iframe icinde render ediliyor olabilir. Sadece ANA sayfayi kaydirmak
    boyle bir durumda iframe icindeki lazy-load/virtualized icerigin
    TAMAMININ tetiklenmesini saglamayabilir - bu da PDF'te eksik icerik
    riski demek (tam olarak bu projenin onlemeye calistigi tur bir hata).

    Cross-origin bir iframe'e Playwright'tan JS erisimi guvenlik geregi
    mumkun degil - boyle bir durumda o cerceveyi sessizce atlariz (elimizden
    baska bir sey gelmez, cokme olmaz).
    """
    results = []
    for frame in _iter_frames(page):
        try:
            results.append(frame.evaluate(AUTO_SCROLL_JS))
        except Exception:
            continue
    return results


def wait_images_all_frames(page: Page) -> dict:
    """WAIT_IMAGES_JS'i ana sayfa VE icindeki her cerceve icin calistirip
    sonuclari toplar - scroll_all_frames ile ayni gerekce (gomulu
    onizleyicideki gorseller de bekleme/dogrulama kapsamina girsin)."""
    total = pending = failed = 0
    max_waited = 0
    for frame in _iter_frames(page):
        try:
            result = frame.evaluate(WAIT_IMAGES_JS)
        except Exception:
            continue
        total += result["total"]
        pending += result["pending"]
        failed += result["failed"]
        max_waited = max(max_waited, result["waitedMs"])
    return {"total": total, "pending": pending, "failed": failed, "waitedMs": max_waited}


def capture_current_page(
    page: Page,
    output_dir: Path = OUTPUT_DIR,
    filename_stem: str | None = None,
    log_title: str | None = None,
) -> dict:
    """Mevcut sayfayi PDF'e cevirir ve ONAY/GONDERIM TARIHI bilgisini kaydeder.

    filename_stem verilirse dosya adi icin sayfadan tahmin edilen baslik
    yerine bu kullanilir (ornegin hoca gorunumunde ogrenci adi). log_title
    verilmezse filename_stem, o da yoksa sayfadan tahmin edilen baslik
    captures.json'daki 'baslik' alanina yazilir.

    Kaydirma AUTO_SCROLL_MAX_ITERATIONS icinde sabitlenmezse (yani icerik
    hala buyumeye devam ederken sure dolduysa), sayfa muhtemelen eksik
    yuklenmis demektir - bu durumda PDF URETILMEZ, RuntimeError firlatilir.
    Ayni sekilde, sayfadaki gorseller (hocanin sinava koydugu fotograflar
    gibi) IMAGE_LOAD_MAX_WAIT_MS icinde yuklenmezse de PDF URETILMEZ -
    aksi halde PDF'te bos/acilmamis gorunen fotograflar birikiyordu.
    """
    # CSS'i kaydirmadan ONCE uyguluyoruz: overflow:hidden ile kirpilmis
    # alanlar erken acilirsa hem gercek toplam yukseklik dogru olculur
    # hem de icindeki gorseller (varsa) kaydirma sirasinda erken tetiklenir.
    #
    # ONEMLI: Bu CSS `*` secici ile TUM sayfadaki overflow/max-height'i
    # zorla aciyor - dropdown/modal/sabit-baslik gibi bircok UI ogesi bu
    # ozelliklere dayanir. Blackboard Ultra bir SPA oldugu icin, kullanici
    # yakalamadan sonra AYNI sekmede baska bir derse/sayfaya (sayfa tam
    # yenilenmeden, client-side route ile) gecerse bu stil etiketi DOM'da
    # KALIP o yeni sayfayi da bozabiliyordu (kullanicidan gelen rapor:
    # "tarayıcı garipleşti, taramadı"). Bu yuzden style_handle'i saklayip
    # islem bitince (basarili ya da basarisiz FARK ETMEKSIZIN) finally
    # icinde MUTLAKA kaldiriyoruz - capture_current_page sayfada kalici
    # hicbir iz birakmamali.
    #
    # Sekme degisimi CSS'ten ONCE: dogru sekme secilmeden kaydirma/olcum
    # yapmanin bir anlami yok (bkz. switch_to_submission_tab docstring -
    # odev sayfalarinda varsayilan acik gelen 'Yonergeler' sekmesi hocanin
    # odev aciklamasidir, ogrencinin gonderdigi icerik degil).
    switch_to_submission_tab(page)

    style_handle = page.add_style_tag(content=FORCE_VISIBLE_CSS)
    try:
        scroll_results = scroll_all_frames(page)
        unstable = [r for r in scroll_results if not r["stabilized"]]
        if unstable:
            worst = unstable[0]
            raise RuntimeError(
                f"Sayfa (ya da icindeki bir onizleme cercevesi) kaydirma "
                f"{AUTO_SCROLL_MAX_ITERATIONS} denemede sabitlenmedi (son yukseklik: "
                f"{worst['finalHeight']}px) - icerik hala buyuyor olabilir, sayfa "
                "eksik yuklenmis olabilir. PDF uretilmedi."
            )

        image_result = wait_images_all_frames(page)
        if image_result["pending"] > 0:
            raise RuntimeError(
                f"{image_result['pending']}/{image_result['total']} gorsel "
                f"{IMAGE_LOAD_MAX_WAIT_MS / 1000:.0f} saniyede yuklenmedi - sayfa "
                "eksik yuklenmis olabilir (ör. yavas internet). PDF uretilmedi, "
                "tekrar denenmesi gerekiyor."
            )

        page.wait_for_timeout(300)

        body_text = page.inner_text("body")
        info = extract_page_info(body_text)

        output_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_stamp()
        title_part = sanitize_filename(filename_stem or info["baslik"] or "sinav")
        onay_part = info["onay"] or stamp
        pdf_path = output_dir / f"{title_part}_{onay_part}.pdf"
        # Windows'ta TAM YOL (klasorler dahil) 260 karakteri gecerse dosya
        # yazma sessizce/anlasilmaz sekilde basarisiz olabiliyor (uzun yol
        # destegi cogu makinede varsayilan KAPALI). Cikti klasoru derin bir
        # yerdeyse (ör. OneDrive senkron yolu) bile guvende kalmak icin
        # dosya adini proaktif olarak kisaltiyoruz - ONAY kodu (kimlik
        # belirleyici kisim) HER ZAMAN korunuyor, sadece basliktan kirpilir.
        pdf_path = ensure_safe_full_path(pdf_path, protect_suffix_chars=len(onay_part) + 1)

        try:
            page.pdf(path=str(pdf_path), format="A4", print_background=True)
        except OSError as exc:
            # Diskte yer kalmamasi / klasore yazma izni olmamasi gibi
            # durumlarda Playwright/Chrome ham, kriptik bir OS hatasi
            # firlatiyor - kullaniciya (hocaya) ne oldugunu anlasilir
            # sekilde soyleyelim.
            raise RuntimeError(
                f"PDF diske yazilamadi ({exc}). Diskte yer kalmamis olabilir "
                "ya da seçili çıktı klasörüne yazma izni yok - klasörü "
                "değiştirmeyi dene."
            ) from exc
    finally:
        try:
            style_handle.evaluate("el => el.remove()")
        except Exception:
            # Sayfa bu arada kapanmis/gecis yapmis olabilir - kaldirma
            # basarisiz olsa bile yakalama sonucunu etkilememeli.
            pass

    pdf_size = pdf_path.stat().st_size
    if pdf_size < AUTO_SCROLL_MIN_PDF_BYTES:
        pdf_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"Uretilen PDF supheli derecede kucuk ({pdf_size} byte) - icerik "
            "eksik/bos olabilir. Dosya silindi, tekrar denenmesi gerekiyor."
        )

    entry = {
        "captured_at": stamp,
        "baslik": log_title or filename_stem or info["baslik"],
        "gonderim_tarihi": info["gonderim_tarihi"],
        "onay": info["onay"],
        "puan": info["puan"],
        "url": page.url,
        "pdf": str(pdf_path),
        # Tarayici yuklemeyi bitirmis ama goruntu gelmemis (ör. bozuk link)
        # gorsel sayisi - bunlar beklemekle duzelmiyor, sadece PDF'in elle
        # kontrol edilmesi gerektigini isaret etmek icin tasiniyor.
        "bozuk_gorsel_sayisi": image_result["failed"],
    }
    append_log(entry)
    return entry


def main() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, **browser_launch_kwargs()
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(BASE_URL)

        print("\nTarayici acildi.")
        print("1) Universite SSO ile giris yap.")
        print("2) PDF almak istedigin 'Degerlendirme Geri Bildirimi' sayfasina git.")
        print("3) Sayfa tam yuklendiginde buraya donup ENTER'a bas.")
        print("Cikmak icin 'q' yazip ENTER'a bas.\n")

        while True:
            command = input("Hazir oldugunda ENTER (cikmak icin q): ").strip().lower()
            if command == "q":
                break

            try:
                active_page = resolve_active_page(context) or page
            except Exception:
                active_page = page
            try:
                entry = capture_current_page(active_page)
            except Exception as exc:
                print(f"HATA: yakalama basarisiz oldu -> {exc}")
                continue

            print("\nYakalandi:")
            print(f"  Baslik          : {entry['baslik']}")
            print(f"  Gonderim tarihi : {entry['gonderim_tarihi']}")
            print(f"  Onay kodu       : {entry['onay']}")
            print(f"  Puan            : {entry['puan']}")
            print(f"  PDF             : {entry['pdf']}")
            if entry["bozuk_gorsel_sayisi"] > 0:
                print(
                    f"  UYARI           : {entry['bozuk_gorsel_sayisi']} gorsel bozuk/eksik "
                    "gorunuyor, PDF'i elle kontrol et."
                )
            print()

        context.close()


if __name__ == "__main__":
    main()
