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

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from common import (
    BASE_URL,
    MIN_VALID_PDF_BYTES,
    OUTPUT_DIR,
    PROFILE_DIR,
    append_log,
    ensure_safe_full_path,
    extract_page_info,
    launch_browser_context,
    live_url,
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
    /* CANLI DOGRULANAN HATA: sol panel gizlenip fixed/sticky konumlandirma
       static'e cevrildikten SONRA bile, PDF'in sonunda BOS sayfalar
       kalmaya devam ediyordu. Sebep: Blackboard'un SPA'si tipik olarak
       React'in kok konteynerine (ör. #root/App/MuiBox-root gibi bir
       sarmalayici) `min-height: 100vh` (ya da benzeri, ekranin TAMAMINI
       kaplamaya zorlayan bir yukseklik) veriyor. Bu, EKRANDA "footer'in
       her zaman sayfanin altina yapismasi" gibi normal bir tasarim
       amaci tasir, ama BASILI/statik bir PDF'te asil icerik bu 100vh'den
       KISA kalinca, Chrome'un yazdirma motoru o fazladan (tamamen
       gorsel olarak BOS) yuksekligi kapsamak icin ekstra sayfalar
       ayiriyor. html/body'ye ozel yaptigimiz `min-height: 0` (asagida)
       SADECE o iki elemani kapsiyordu - ARADAKI (bilinmeyen/isimsiz)
       herhangi bir sarmalayici konteynerin KENDI min-height'ini
       ETKILEMIYORDU. Bu yuzden artik `min-height: 0` TUM elemanlara
       (`*`) uygulaniyor - bu, hicbir elemanin GERCEK icerik boyutundan
       KUCUK gorunmesine yol acmaz (min-height sadece bir ALT SINIRDIR,
       sifirlamak elemanlari kucultmez, sadece yapay/gereksiz fazladan
       bosluğu kaldirir), bu yuzden risksiz bir genel duzeltmedir. */
    min-height: 0 !important;
    /* CANLI DOGRULANAN HATA: kullanicinin paylastigi PDF'lerde ayni sayi
       dizisi ("60|80|50|...") HER SAYFANIN TEPESINDE birebir tekrarlaniyordu
       - bu, Chrome'un yazdirma motorunun `position: fixed` olan elemanlari
       HER SAYFADA yeniden basmasi olarak bilinen, cok yaygin bir davranis
       (fixed elemanlar dokuman akisindan bagimsiz, viewport'a gore
       konumlanir - coklu sayfali yazdirmada bu yuzden her sayfada "yeniden
       cizilir"). Sol "Öğrenciler" paneli (ya da onu saran bir ust eleman)
       byle bir fixed/sticky konumlandirma kullaniyor olabilir - hangi
       spesifik eleman oldugunu bilmeye GEREK KALMADAN, TUM sayfadaki
       fixed/sticky konumlandirmayi zorla `static`e ceviriyoruz. Bu, basili
       bir PDF icin ZATEN doğru yaklasim - fixed/sticky sadece EKRANDA
       kaydirirken sabit kalma amacli, basili/statik bir cikti icin hicbir
       islevi yok, sadece yukaridaki tur bir tekrar riski tasiyor. */
    position: static !important;
}
html, body {
    height: auto !important;
    min-height: 0 !important;
}
/* CANLI DOGRULANAN HATA: Blackboard, ekran-okuyucu icin gorunmez
   (sr-only / hideOffScreen) etiketlerini paylasilan CSS siniflariyla
   gizliyor (ör. "Ana gönderim içeriği", "Soru 1", "Son Not: 50 puan
   (100 puan üzerinden)" metinleri ve ogrenci listesindeki notlar).
   Bu teknik genelde 1x1px + overflow:hidden ile calisir - yukaridaki
   `* { overflow: visible !important }` kurali bunu da BOZUYOR: metin
   artik "tasip" o 1x1px kutunun disina, sayfanin GORUNUR bir yerine
   (sol ust, (0,0)) yayiliyor.
   Kullanicinin paylastigi PDF ekran goruntusunde ("60|80|50|60|60|40|...")
   tam olarak bu hata goruldu: Tum ogrencilerin gizli not etiketleri
   sayfanin en üstünde üst üste basiliyordu.
   Tüm ekran okuyucu siniflarini (hideOffScreen, sr-only, offScreen,
   visuallyHidden vb.) ve bunlarin alt elemanlarini ACIKCA istisna
   tutarak gizleme teknigini (overflow:hidden + clip) koruyoruz. */
[class*="hideOffScreen"],
[class*="sr-only"],
[class*="srOnly"],
[class*="offScreen"],
[class*="offscreen"],
[class*="off-screen"],
[class*="visuallyHidden"],
[class*="visually-hidden"],
[class*="screen-reader"],
[class*="screenreader"],
[class*="ScreenReader"],
[class*="cdk-visually-hidden"],
[aria-hidden="true"],
.sr-only,
.hideOffScreen,
.visually-hidden {
    position: absolute !important;
    width: 1px !important;
    height: 1px !important;
    padding: 0 !important;
    margin: -1px !important;
    overflow: hidden !important;
    clip: rect(0, 0, 0, 0) !important;
    clip-path: inset(50%) !important;
    white-space: nowrap !important;
    border: 0 !important;
}
[class*="hideOffScreen"] *,
[class*="sr-only"] *,
[class*="srOnly"] *,
[class*="offScreen"] *,
[class*="offscreen"] *,
[class*="off-screen"] *,
[class*="visuallyHidden"] *,
[class*="visually-hidden"] *,
[class*="screen-reader"] *,
[class*="screenreader"] *,
[class*="ScreenReader"] *,
[class*="cdk-visually-hidden"] *,
[aria-hidden="true"] *,
.sr-only *,
.hideOffScreen *,
.visually-hidden * {
    overflow: hidden !important;
}
"""

# CANLI DOGRULANAN HATA: FORCE_VISIBLE_CSS `*` secicisiyle TUM sayfadaki
# overflow/max-height'i acinca, sol "Öğrenciler" gezinme paneli (normalde
# SABIT yukseklikte, kendi ic kaydirmasi olan bir liste) de KENDI TAM
# ICERIK yuksekligine (ör. 15-30 ogrenci * satir yuksekligi) genisliyordu.
# Bu panel, asil sinav icerigiyle YAN YANA (flex) bir sutun oldugu icin,
# panel asil icerikten cok daha uzun oldugunda Chrome'un yazdirma motoru
# bu fazladan (tamamen ALAKASIZ, gezinme amacli) yuksekligi kapsamak icin
# ekstra sayfalar ayirıyordu - sonuc, PDF'in sonunda/arasinda icerigi
# BOS gorunen sayfalar (kullanicinin bildirdigi tam olarak bu). Bu panel
# zaten PDF'e GEREKMIYOR (ogrenci navigasyonu icin, sinav ICERIGI degil)
# - bu yuzden yazdirmadan once TAMAMEN gizleniyor. Ayni gerekceyle,
# sinavin kendisiyle ILGISIZ diger UI ogeleri (soru bazinda "Geri
# Bildirim" ikonu, "... ile ilgili diğer seçenekler" acilir menuleri,
# onceki/sonraki ogrenci gezinme oklari, arkaplan overlay/backdrop
# elemanlari) da gizleniyor - hem gereksiz sayfa/bosluk riskini azaltiyor
# hem de PDF'i sinavin GERCEK icerigine (sorular, cevaplar, ONAY/puan basligi)
# indirgeyip daha temiz/profesyonel bir cikti veriyor.
HIDE_NAVIGATION_CHROME_CSS = """
[role="menu"][aria-label="Öğrenciler"],
[role="menu"][aria-label*="Öğrenci"],
[aria-label^="Geri Bildirim"],
[aria-label$="ile ilgili diğer seçenekler"],
[aria-label="Önceki Öğrenci"],
[aria-label="Sonraki Öğrenci"],
[data-analytics-id*="attemptGrading.header.studentPicker"],
[class*="studentNav"],
[class*="student-nav"],
.snackbar-provider,
.MuiDrawer-root,
.MuiModal-root,
.MuiPopover-root,
.MuiDialog-root,
.MuiBackdrop-root,
[role="dialog"],
[role="tooltip"],
[class*="snackbar"],
[class*="popover"],
[class*="drawer"],
[class*="backdrop"] {
    display: none !important;
    height: 0 !important;
    min-height: 0 !important;
    max-height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
    overflow: hidden !important;
}
"""
# UYARI: `[class*="hide-in-background"]` ve `[class*="has-footer"]`
# BILEREK burada YOK - isimlerinin aksine bunlar cop degil, GERCEK
# ICERIK uzerinde duran siniflar: "hide-in-background", Not Defteri/
# Gönderimler panelinin ANA sarmalayicisinda (aktifken bile) goruluyor
# ("panel-wrap hide-in-background grades", aria-hidden="false" ve
# "active" ile birlikte); "has-footer" ise bir notlandirma satirinin
# kaydet-alt-çubuğu durumunu belirten sıradan bir Angular ng-class
# ifadesi. Şu an bu class'lar capture_current_page'in çalıştığı asıl
# "Değerlendirme" sayfasında (flexible-attempt-grading) hiç görülmüyor
# - ama isimleri yanıltıcı olduğu için buraya eklenirse ileride ASIL
# İÇERİĞİ tamamen gizleme riski taşırlar. Eklemeyin.
# UYARI: `.js-selectAttemptInputValueText` BILEREK burada YOK - o class
# gezinme/UI cop'u DEGIL, "Gönderim tarihi: ..." metnini TASIYAN eleman
# (bkz. kullanicinin paylastigi gercek DOM). Burada gizlenirse HEM PDF'te
# gonderim tarihi tamamen kaybolur HEM DE extract_page_info'nun
# gonderim_tarihi alani hep None doner (capture_current_page'deki
# body_text = page.inner_text("body") bu CSS enjekte edildikten SONRA
# calisiyor, display:none olan bir elemanin metni inner_text()'e hic
# girmez). Bu class'i bu listeye EKLEMEYIN.

AUTO_SCROLL_MAX_ITERATIONS = 120
# Esik artik common.MIN_VALID_PDF_BYTES'ta yasiyor: ayni deger
# already_captured_titles'in "bu PDF gecerli mi" kararinda da kullaniliyor
# (yarim yazilmis PDF'in ogrenciyi sonsuza dek atlatmasina karsi) - iki
# tarafin esiginin birbirinden kopmamasi icin tek kaynaktan geliyor.
AUTO_SCROLL_MIN_PDF_BYTES = MIN_VALID_PDF_BYTES

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

# PDF basimi tamamlandiktan VE gizlenen yan navigasyon/stiller
# kaldirildiktan sonra, sayfayi VE tum kaydirilabilir alanlari tekrar en
# uste (0, 0) kaydirir. Aksi halde sayfa en altta kalabiliyordu veya bir
# panel kaydirilmis/takili kalabiliyordu; bu da hocanin ya da otomatik
# taramanin sonraki ogrenciye gecmesini engelliyordu.
#
# CANLI DOGRULANAN HATA: bu sifirlama once TUM elemanlara
# (`querySelectorAll('*')`) uygulaniyordu - bu, sol "Öğrenciler" gezinme
# listesinin KENDI kaydirma konumunu da sifirliyordu. Sonuc: 18. ogrenci
# yakalandiktan sonra panel BASTAN (1, 2, 3...) gorunmeye basliyor, 19.
# ogrenciye ulasmak icin panel her seferinde yeniden asagi kaydirilmasi
# gerekiyordu - hem gereksiz zaman kaybi hem de panel virtualized ise
# (ekran disina cikan ogrencilerin DOM'dan dusurulmesi durumunda) 19.
# ogrencinin sessizce BULUNAMAMA riski. Bu yuzden artik sol "Öğrenciler"
# listesinin (ya da onun herhangi bir alt elemaninin) kaydirma konumuna
# DOKUNULMUYOR - sadece geri kalan (asil sinav icerigi gibi) alanlar
# sifirlaniyor.
RESET_SCROLL_AFTER_CAPTURE_JS = """() => {
    window.scrollTo(0, 0);
    if (document.scrollingElement) {
        document.scrollingElement.scrollTop = 0;
    }
    if (document.body) {
        document.body.scrollTop = 0;
    }
    const scrollables = document.querySelectorAll('*');
    for (const el of scrollables) {
        if (el.scrollTop && el.scrollTop > 0) {
            if (el.closest && el.closest('[role="menu"][aria-label*="Öğrenci"]')) {
                continue;
            }
            el.scrollTop = 0;
        }
    }
}"""


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


def add_style_all_frames(page: Page, css: str) -> list:
    """FORCE_VISIBLE_CSS/HIDE_NAVIGATION_CHROME_CSS gibi bir stil etiketini
    ana sayfa VE icindeki her (same-origin) cerceveye ekler, eklenen tum
    stil elemani tutamaclarini (handle) dondurur - cagiran taraf isini
    bitirince bunlarin HEPSINI kaldirmali (bkz. capture_current_page'in
    finally blogu).

    CANLI DOGRULANAN HATA: bu fonksiyon eklenmeden ONCE FORCE_VISIBLE_CSS/
    HIDE_NAVIGATION_CHROME_CSS SADECE `page.add_style_tag(...)` ile ANA
    sayfaya ekleniyordu - scroll_all_frames/wait_images_all_frames'in
    AKSINE iframe'ler (ör. odevin yuklenen Word/PDF dosyasinin gomulu
    onizleyicisi - bkz. switch_to_submission_tab docstring'i) bu CSS'i
    HIC GORMUYORDU. Sonuc: iframe icindeki kendi `min-height: 100vh`
    benzeri sarmalayicilari, position:fixed/sticky elemanlari VE
    ekran-okuyucuya-ozel (sr-only/hideOffScreen) etiketleri asla
    notralize edilmiyordu - bu da (a) PDF'in SONUNDA, onizlenen dosyanin
    kendi ic yuksekligine bagli olarak DEGISKEN sayida (kullanicinin
    bildirdigi gibi "birinde 23 sayfa birinde 15 sayfa") BOS sayfa VE
    (b) iframe'in kendi sr-only etiketlerinin, konumlandirmasi hala
    static'e zorlanmadigi icin sayfanin GORUNUR bir yerinde (kullanicinin
    bildirdigi gibi SADECE bu iceriğin bastigi sayfada, ör. 3. sayfada)
    ortaya cikmasi anlamina geliyordu. Ana sayfayla AYNI CSS'i her
    cerceveye ayri ayri eklemek bu iki sonucu da kokten cozer.

    Cross-origin bir iframe'e stil enjekte etmek guvenlik geregi mumkun
    degil - boyle bir cerceve sessizce atlanir (elimizden baska bir sey
    gelmez, cokme olmaz - AYNI scroll_all_frames/wait_images_all_frames
    davranisi)."""
    handles = []
    for frame in _iter_frames(page):
        try:
            handles.append(frame.add_style_tag(content=css))
        except Exception:
            continue
    return handles


def remove_style_all_frames(handles: list) -> None:
    """add_style_all_frames'in dondurdugu TUM tutamaclari kaldirir - tek
    tek kaldirma sirasinda bir tanesi basarisiz olsa (ör. o cerceve bu
    arada kapanmis/gecis yapmis olabilir) bile digerlerini kaldirmaya
    devam eder, hicbiri yakalama sonucunu etkilemez."""
    for handle in handles:
        try:
            handle.evaluate("el => el.remove()")
        except Exception:
            continue


# capture_current_page'in yazdirmadan hemen once calistirdigi son temizlik
# gecisi: sr-only etiketlerini INLINE stille bir daha kilitler, bos
# backdrop/overlay elemanlarini gizler, alt bosluklari sifirlar. Once
# SADECE ana sayfada `page.evaluate(...)` ile calisiyordu - AYNI
# add_style_all_frames'teki gerekceyle (bkz. o fonksiyonun docstring'i)
# artik _run_final_cleanup_all_frames ile HER cercevede calisiyor, cunku
# gomulu onizleyici iframe'inin KENDI sr-only etiketleri/bos overlay'leri
# de bu son temizligi gerektirebilir.
FINAL_CLEANUP_JS = """() => {
    const srElements = document.querySelectorAll(
        '[class*="hideOffScreen"], [class*="sr-only"], [class*="srOnly"], [class*="offScreen"], [class*="offscreen"], [class*="off-screen"], [class*="visuallyHidden"], [class*="visually-hidden"], [class*="screen-reader"], [class*="screenreader"], [class*="ScreenReader"], [class*="cdk-visually-hidden"], [aria-hidden="true"], .sr-only, .hideOffScreen, .visually-hidden'
    );
    srElements.forEach(el => {
        el.style.setProperty('overflow', 'hidden', 'important');
        el.style.setProperty('width', '1px', 'important');
        el.style.setProperty('height', '1px', 'important');
        el.style.setProperty('position', 'absolute', 'important');
        el.style.setProperty('clip', 'rect(0, 0, 0, 0)', 'important');
    });

    const overlays = document.querySelectorAll(
        '.MuiBackdrop-root, .MuiModal-root, .MuiPopover-root, .MuiDialog-root, [class*="backdrop"], [class*="overlay"]'
    );
    overlays.forEach(el => {
        if (!el.innerText || !el.innerText.trim()) {
            el.style.display = 'none';
            el.style.height = '0px';
            el.style.minHeight = '0px';
            el.style.margin = '0px';
            el.style.padding = '0px';
        }
    });

    if (document.body) {
        document.body.style.paddingBottom = '0px';
        document.body.style.marginBottom = '0px';
    }
    const mainEl = document.querySelector('main, [role="main"], [class*="attempt-grading"]');
    if (mainEl) {
        mainEl.style.paddingBottom = '0px';
        mainEl.style.marginBottom = '0px';
    }
}"""


def run_final_cleanup_all_frames(page: Page) -> None:
    """FINAL_CLEANUP_JS'i ana sayfa VE icindeki her cerceve icin calistirir
    - bkz. FINAL_CLEANUP_JS ve add_style_all_frames docstring'leri."""
    for frame in _iter_frames(page):
        try:
            frame.evaluate(FINAL_CLEANUP_JS)
        except Exception:
            continue


def capture_current_page(
    page: Page,
    output_dir: Path = OUTPUT_DIR,
    filename_stem: str | None = None,
    filename: str | None = None,
    filename_protect_suffix_chars: int = 0,
    log_title: str | None = None,
) -> dict:
    """Mevcut sayfayi PDF'e cevirir ve ONAY/GONDERIM TARIHI bilgisini kaydeder.

    filename_stem verilirse dosya adi icin sayfadan tahmin edilen baslik
    yerine bu kullanilir (ornegin hoca gorunumunde ogrenci adi), ama
    sonuna HALA '_{ONAY kodu}' eklenir (bkz. asagidaki pdf_path).

    filename verilirse (filename_stem'den FARKLI OLARAK) TAM dosya adi
    olarak KENDISI kullanilir - ONAY koduna eklenmez, cagiran taraf
    (ör. scan_grade_center.capture_student) istedigi TAM adlandirma
    semasini (ör. 'sinav_ogrenciNo_ad-soyad') kendisi olusturur.
    filename verilmisse filename_stem GOZ ARDI EDILIR.

    filename_protect_suffix_chars: filename verildiginde, Windows MAX_PATH
    kirpmasi (ensure_safe_full_path) gerekirse dosya adinin SONUNDAKI bu
    kadar karakter korunur - ogrenci PDF'lerinde sondaki
    '_{ogrenci_no}_{ad-soyad}' kimlik bolumu kirpilip iki farkli
    ogrencinin ayni dosya adina dusmesini (birinin PDF'inin sessizce
    ezilmesini) onlemek icin (bkz.
    common.student_pdf_identity_suffix_chars).

    log_title verilmezse filename_stem, o da yoksa sayfadan tahmin edilen
    baslik captures.json'daki 'baslik' alanina yazilir (filename, dedup
    icin KULLANILMAZ - o hep log_title/filename_stem'e bagli kalir).

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

    # add_style_all_frames: ana sayfa VE icindeki her (same-origin)
    # cerceveye ekler - bkz. o fonksiyonun docstring'indeki CANLI
    # DOGRULANAN HATA notu (SADECE ana sayfaya eklendiginde, gomulu
    # onizleyici iframe'inin KENDI min-height/position/sr-only sorunlari
    # notralize edilmiyor, bu da DEGISKEN sayida bos sayfaya VE sadece
    # o icerigin bastigi sayfada goruntlenen "anlamsiz sayilara" yol
    # aciyordu).
    style_handles = add_style_all_frames(page, FORCE_VISIBLE_CSS)
    # bkz. HIDE_NAVIGATION_CHROME_CSS docstring'i: sol "Öğrenciler" paneli
    # ve diger sinav-disi UI ogeleri (geri bildirim/seçenekler butonlari,
    # onceki/sonraki ogrenci oklari) yazdirmadan ONCE gizleniyor - hem
    # gereksiz bos sayfa riskini azaltir hem PDF'i daha temiz/profesyonel
    # hale getirir. AYRI bir liste olarak tutuluyor ki asagidaki finally'de
    # FORCE_VISIBLE_CSS'ten BAGIMSIZ, kendi basina guvenle kaldirilabilsin.
    nav_style_handles = add_style_all_frames(page, HIDE_NAVIGATION_CHROME_CSS)
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
        if filename:
            # Tam adlandirma cagiran tarafta belirlendi - ONAY koduna
            # ekLENMIYOR (bkz. fonksiyon docstring'i), sadece guvenli
            # dosya adi karakterlerine ceviriyoruz. onay_part BURADA
            # HESAPLANMIYOR/KULLANILMIYOR - asagidaki else dalindan
            # farkli olarak bu isim zaten kimlik belirleyici (sinav +
            # ogrenci no + ad-soyad) bilgiyi tasiyor, ONAY koduna ayrica
            # ihtiyac yok.
            pdf_path = output_dir / f"{sanitize_filename(filename)}.pdf"
            pdf_path = ensure_safe_full_path(
                pdf_path, protect_suffix_chars=filename_protect_suffix_chars
            )
        else:
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

        # Yazdirmadan hemen once, sayfa altinda bos/ekstra PDF sayfalari kalmasina
        # neden olabilen sakli backdrop/overlay/drawer elemanlarini ve min-height'leri
        # temizliyoruz - ana sayfa VE icindeki her cerceve icin (bkz.
        # run_final_cleanup_all_frames/FINAL_CLEANUP_JS docstring'i).
        run_final_cleanup_all_frames(page)

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
        # remove_style_all_frames kendi icinde her tutamaci ayri ayri dener
        # ve tek tek basarisizliklari yutar - bkz. o fonksiyonun docstring'i
        # (sayfa bu arada kapanmis/gecis yapmis olabilir, kaldirma basarisiz
        # olsa bile yakalama sonucunu etkilememeli).
        remove_style_all_frames(style_handles)
        remove_style_all_frames(nav_style_handles)
        try:
            page.evaluate(RESET_SCROLL_AFTER_CAPTURE_JS)
        except Exception:
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
        "url": live_url(page),
        "pdf": str(pdf_path),
        # Tarayici yuklemeyi bitirmis ama goruntu gelmemis (ör. bozuk link)
        # gorsel sayisi - bunlar beklemekle duzelmiyor, sadece PDF'in elle
        # kontrol edilmesi gerektigini isaret etmek icin tasiniyor.
        "bozuk_gorsel_sayisi": image_result["failed"],
    }
    append_log(entry)
    return entry


def main() -> None:
    # Windows konsolu varsayilan olarak UTF-8 olmayan bir kod sayfasi
    # (ör. cp1254) kullanabiliyor - Turkce karakterler/tire (—) iceren
    # print() cagrilari bu durumda UnicodeEncodeError firlatip taramayi
    # ortadan kesebiliyordu. errors="replace" ile en kotu ihtimalde
    # goruntu bozulur ama program COKMEZ.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # launch_browser_context: once Chrome dener, Windows'ta kurulu
        # degilse otomatik Portable Chrome yedegine duser, PROFIL KILIDI
        # hatasinda da bir kez temizleyip yeniden dener - bkz. o fonksiyonun
        # docstring'i.
        context = launch_browser_context(p, PROFILE_DIR)

        try:
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
        finally:
            # Beklenmedik bir istisna (ör. Ctrl+C/KeyboardInterrupt) ya da
            # yukaridaki dongude yakalanmamis bir hata context.close()'un
            # HIC calismamasina yol acabiliyordu - bu da PROFILE_DIR'da
            # yetim bir SingletonLock birakip bir SONRAKI calistirmanin
            # "user data directory is already in use" hatasiyla
            # basarisiz olmasina neden oluyordu. finally ile close() her
            # kosulda calismasi garanti ediliyor.
            context.close()


if __name__ == "__main__":
    main()
