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
    DEFAULT_WINDOW_WIDTH,
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

# BESINCI CANLI DOGRULANAN HATA (kullanicinin paylastigi ekran goruntuleri:
# ayni icerik CANLI tarayicida - hem DOM'da hem gorsel olarak - TAM
# gorunuyor, ama AYNI ANDA uretilen PDF'te YINE eksikti - onceki TUM
# zamanlama/DOM-mutasyon duzeltmeleri bunu COZEMEDI). Bu, sorunun DOM'da ya
# da script'in scroll/tiklama adimlarinda DEGIL, `page.pdf()` cagrisinin
# KENDI ic yerlesim (layout) surecinde oldugunu kanitliyor. `page.pdf(...,
# format="A4")` Chromium'un icinde A4 kagit GENISLIGINDE (~793px, 96dpi)
# bir yerlesim hesaplamasi YAPTIRIR - bu, launch_browser_context'in actigi
# GERCEK tarayici penceresinin genisliginden (DEFAULT_WINDOW_WIDTH = 1440px,
# bkz. common.py) NEREDEYSE YARISI KADAR DAR. Sinav sorularini saran
# konteynerin bir SANAL LISTE (virtualization) oldugu FINAL_CLEANUP_JS'te
# CANLI dogrulanmisti - boyle bir kutuphane genelde "hangi ogeler su an
# GORUNUR ARALIKTA" hesabini elemanlarin YUKSEKLIGINE (ki bu YUKSEKLIK
# GENISLIGE bagli - dar genislikte metin daha cok satira boluer, ogeler
# UZAR) bakarak yapar. TUM script boyunca (kaydirma, panel acma, essay
# icerik dogrulama) 1440px genislikte calisiyorduk - ama page.pdf() KENDI
# ic hesaplamasini SESSIZCE ~793px'e gore YENIDEN yapiyordu. Bu genislik
# UYUMSUZLUGU, kutuphanenin PRINT ANINDA hesapladigi "gorunur aralik"in,
# bizim 1440px'te mount ettigimiz aralikla ORTUSMEMESINE - ozellikle EN
# SONDAKI (Soru 23 gibi) ogelerin bu YENI (dar) hesaplamada "aralik disi"
# sayilip atlanmasina yol acmis olabilir. Duzeltme: page.pdf()'e artik
# "format=A4" DEGIL, script boyunca KULLANILAN AYNI genislik (1440px)
# ACIKCA veriliyor - boylece kaydirma/panel-acma/essay-dogrulama
# adimlarinin mount ettigi aralikla, print anindaki yerlesim hesabi AYNI
# genislikte yapiliyor, hicbir yeniden-hesaplama/uyumsuzluk riski kalmiyor.
# Yukseklik, ISO A-serisi kagit oranini (1:sqrt(2)) KORUYARAK genislige
# gore hesaplanan bir "sayfa yuksekligi" - Chromium bunu asan icerigi
# NORMAL sekilde birden fazla sayfaya boler (format="A4" ile AYNI
# davranis, sadece daha genis bir "sayfa" tanimiyla).
PDF_PAGE_WIDTH_PX = DEFAULT_WINDOW_WIDTH
PDF_PAGE_HEIGHT_PX = round(PDF_PAGE_WIDTH_PX * 1.41421356)


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
    /* CANLI DOGRULANAN HATA (PDF'te bir sorunun puan rozeti sadece "("
       olarak, sagdan kirpilmis gorunuyordu): bazi elemanlar (ör. bir
       puan rozetinin sarmalayicisi) EKRAN genisligine gore sabit/gomulu
       bir piksel genisligi alabiliyor - bu, EKRANDA sorun cikarmaz (yatay
       kaydirma mumkun) ama BASILI/statik bir PDF sayfasinda Chrome'un
       yazdirma motoru yatay tasmayi YENI SAYFAYA SARMAZ, sadece sayfa
       sinirinin disinda kalan kismi KIRPAR (kesip atar). `max-width:
       100%` HICBIR elemanin kendi PARENT'inden (dolayisiyla sonuçta
       sayfa genisliginden) daha genis olmasini engeller - bu bir ALT
       SINIR degil UST SINIR oldugu icin (min-height'in aksine) normalde
       zaten sayfaya sigan elemanlari ETKILEMEZ, sadece tasanlari
       kisitlar. */
    max-width: 100% !important;
    box-sizing: border-box !important;
    /* Kullanicinin istegi: dogru/yanlis cevaplarin YESIL/KIRMIZI renk
       vurgusu PDF'te GORUNSUN. Tarayicilar varsayilan olarak yazdirmada
       (ekonomik mürekkep icin) dekoratif renkleri/arka planlari SILEBILIR
       - `print_background=True` (page.pdf cagrisinda) sadece arka plan
       GORSELLERINI/renklerini ACAR, tarayicinin kendi "ekonomi modu" renk
       ayarlamasini ENGELLEMEZ. `print-color-adjust: exact` (+ eski
       tarayicilar icin -webkit- oneki) tarayiciya TAM OLARAK ekrandaki
       rengi kullanmasini, hicbir sekilde ayarlamamasini/hafifletmemesini
       soyler. */
    -webkit-print-color-adjust: exact !important;
    print-color-adjust: exact !important;
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
    /* CANLI DOGRULANAN HATA (kullanicinin renk duzeltmesi SONRASI bildirdigi
       yeni hata): Blackboard'un sayfa "tuvali" (html/body arka plani)
       varsayilan olarak beyaz DEGIL, hafif GRI (ör. #f5f5f5 - icerik
       kartlarini beyaz zeminle ayirt ettirmek icin tipik bir tasarim
       deseni). `print-color-adjust: exact` eklenmeden ONCE tarayicinin
       "ekonomik" yazdirma modu bu hafif gri arka plani zaten BEYAZA
       yakinsatiyordu (gorsel olarak fark edilmiyordu) - renk duzeltmesi
       SONRASI artik TAM olarak bu gri render ediliyor, ozellikle
       icerigin bittigi ama sayfanin (yapay fazladan yukseklik yuzunden)
       devam ettigi alt kisimlarda GORUNUR hale geliyor. html/body'yi
       ACIKCA beyaza sabitliyoruz - bu SADECE sayfa tuvalini etkiler,
       kartlarin/rozetlerin KENDI (yesil/kirmizi/vs) arka plan renklerini
       DEGISTIRMEZ (onlar daha spesifik seciciler, kazaniyorlar). */
    background-color: #ffffff !important;
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
   tutarak gizleme teknigini (overflow:hidden + clip) koruyoruz.

   CANLI DOGRULANAN HATA (kullanicinin paylastigi PDF: basliktaki toplam
   puan rozeti - ör. "50/100" - PDF'te TAMAMEN KAYIP): `[aria-hidden=
   "true"]` bu listeye BILEREK EKLENMISTI ama bu YANLIS bir varsayimdi -
   `aria-hidden="true"` "EKRAN OKUYUCUDAN gizli" demektir, "GORSEL OLARAK
   gizli (1x1px+clip teknigi)" demek DEGILDIR. Blackboard'un puan
   rozetinde GORUNUR rakamlar (`<span class="js-pill-grade">50</span>`)
   tam olarak boyle bir `aria-hidden="true"` sarmalayici ICINDE duruyor -
   sebep: AYNI bilgiyi (ör. "Deneme 1: 50 puan (100 puan üzerinden)")
   ayrica bir sr-only etiketle EKRAN OKUYUCUYA veriyor, GORSEL rakamlari
   ise (ikisinin AYNI seyi iki kez okumasini onlemek icin) ekran
   okuyucudan gizliyor - ama gozle GORUNUR kalmalari GEREKIYOR.
   `[aria-hidden="true"]`'yi bu listeye dahil etmek, bu GORUNUR rakamlari
   da (class adi hideOffScreen/sr-only OLMADIGI halde) 1x1px'e kilitleyip
   PDF'ten kayboltuyordu. Duzeltme: SADECE class-adi-tabanli (GERCEKTEN
   GORSEL gizleme teknigini isaret eden) secicileri tutuyoruz,
   `[aria-hidden="true"]`'yi CIKARDIK. */
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
.sr-only *,
.hideOffScreen *,
.visually-hidden * {
    overflow: hidden !important;
}
/* CANLI DOGRULANAN HATA (kullanicinin bildirdigi bombos son sayfalar):
   off-canvas degerlendirme paneli genelde EKRANI TAMAMEN KAPLAMAK icin
   sabit `height: 100vh` tasir. Once bunu FINAL_CLEANUP_JS icinde panelin
   ATA ZINCIRINE KALICI inline stille yazarak duzeltmeye calisildi - ama
   bu, "Öğrenciler" panelinin (AYNI zincirde, yan yana bir sutun olarak)
   KENDI sanal liste olcumunu KALICI OLARAK bozup ogrenciler arasi
   gezinmeyi kirdi (CANLI DOGRULANDI). Bu sefer AYNI duzeltme, TUM diger
   FORCE_VISIBLE_CSS kurallari gibi, capture bitince <style> etiketiyle
   BIRLIKTE OTOMATIK KALDIRILAN (bkz. remove_style_all_frames) GECICI bir
   CSS kurali olarak uygulaniyor - JS ile KALICI inline stil YOK. `*
   { min-height: 0 }` (yukarida) zaten AYNI panele/TUM sayfaya
   dokunuyordu ve gezinmeyi hic bozmuyordu (kullanicinin defalarca
   dogruladigi calisir durum) - bu da GECICI oldugu icin ayni sekilde
   guvenli olmali. Panel BIRDEN FAZLA selectorle (ui-view, ozel etiket,
   genel oznitelik) hedefleniyor - hangisi eslesirse essin. */
[bb-offcanvas-pausal-scope],
[ui-view*="gradebook-item-assessment-panel"],
bb-flexible-attempt-grading-ui {
    height: auto !important;
    max-height: none !important;
}
/* CANLI DOGRULANAN HATA (bu blogun KENDI icinde denenmis, GERI ALINMIS bir
   deneme): boS sayfa TABANINI (5 icerik + SABIT 3 bos sayfa) tamamen
   yoketmek icin panelin ALTINDAKI HER SEYE (`bb-flexible-attempt-
   grading-ui *` gibi TUM torun secicileri) `height: auto !important`
   uygulanmisti. Kullanicinin CANLI gozlemi: bu, soru numarasi
   rozetlerinin/secenek harfi (A./B./...) daire seklini BOZDU (sabit
   width+height ile daire olusturan elemanlarin height'i ARTIK icerige
   gore degisiyordu, kare/oval bir sekle donustu) VE harf ile secenek
   metnini AYRI SATIRLARA dusurdu. Bu, boS sayfa SORUNUNDAN (kozmetik,
   yazdirma-only) cok daha ciddi bir GORSEL BOZULMA oldugu icin kullanici
   ACIKCA bu deneyi GERI ALMAMIZI istedi - bos sayfa sorunu SIMDILIK
   COZULMEDEN birakiliyor, TEK panelin kendi height'ini duzelten YUKARIDAKI
   dar kural (navigasyonu hic etkilemeyen, gorsel yan etkisi de
   bildirilmeyen) KALIYOR. */
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
/* CANLI DOGRULANAN HATA (page.pdf() genisligini 1440px'e cikaran
   duzeltmeden SONRA ortaya cikti - bkz. PDF_PAGE_WIDTH_PX docstring'i):
   dar A4 genisliginde bu panel muhtemelen responsive tasarim geregi hic
   render edilmiyordu, bu yuzden simdiye kadar hicbir sorun cikarmamisti.
   1440px'e gecince PDF'in sol tarafinda "Öğrenciler/Sorular" sekmeleri,
   "Not Verme Durumu" filtresi VE "Notları Gönder" butonu gorunmeye
   basladi. Kullanicinin paylastigi gercek DOM: bunlarin HEPSI TEK bir
   `<aside aria-label="Gezinme paneli">` sarmalayicisinin icinde -
   asagidaki `[role="menu"][aria-label="Öğrenciler"]` kurali SADECE bu
   aside'in İCİNDEKİ ogrenci listesini (`<ul role="menu">`) gizliyordu,
   aside'i SARAN sekme/filtre/gonder-butonu satirlarina hic dokunmuyordu.
   Aside'in KENDISINI (aria-label="Gezinme paneli" - stabil, makeStyles
   hash'ine bagli degil) gizlemek hem ogrenci listesini hem TUM bu
   gezinme unsurlarini TEK kuralla kapsiyor. */
[aria-label="Gezinme paneli"],
[role="menu"][aria-label="Öğrenciler"],
[role="menu"][aria-label*="Öğrenci"],
[aria-label^="Geri Bildirim"],
[aria-label$="ile ilgili diğer seçenekler"],
[aria-label="Önceki Öğrenci"],
[aria-label="Sonraki Öğrenci"],
[data-analytics-id*="attemptGrading.header.studentPicker"],
[class*="studentNav"],
[class*="student-nav"],
/* CANLI DOGRULANAN (kullanicinin paylastigi gercek DOM): 'Öğrenci
   Gönderimleri' off-canvas panelinin KENDI ust bilgi alani - "15/20
   GÖNDERİLDİ / 0 NOT VERİLECEK / 0 GÖNDERİLECEK" istatistik cubugu VE
   "İçerik ve Ayarlar / Gönderimler (15) / Öğrenci Etkinliği / Soru
   Analizi" sekme cubugu. Bu panelin MESRU icerigi oldugu icin (bkz.
   FINAL_CLEANUP_JS'teki off-canvas izolasyonu) DIGER arka plan
   temizliginden ETKILENMIYORLARDI - ayri, ozel olarak gizlenmeleri
   gerekiyor. `.js-grade-column-stat-header`/`.submission-border-removal`
   class'lari VE `aria-label="Değerlendirme seçenekleri"` (sekme
   navigasyonunun KENDISI) canli DOM'dan birebir alindi - makeStyles
   hash'li class adlarina (surumler arasi degisebilir) BAGIMLI DEGILLER. */
.js-grade-column-stat-header,
.submission-border-removal,
[class*="js-grade-column-stat-header"],
[class*="submission-border-removal"],
[aria-label="Değerlendirme seçenekleri"],
/* CANLI DOGRULANAN (kullanicinin paylastigi gercek DOM): 'Öğrenci
   Gönderimleri' panelinin ust kismindaki "Öğrenci adına göre ara" arama
   kutusu - eski Angular tabanli bileşen (bb-translate-attrs, ng-model
   ile), MUI degil, bu yuzden yukaridaki MUI-tabanli kurallarin hicbiri
   onu yakalamiyordu. aria-label/placeholder ile birebir eslesiyor. */
[aria-label="Öğrenci adına göre ara"],
[placeholder="Öğrenci adına göre ara"],
[analytics-id="components.directives.grade.submissionList.filter.input.text"],
/* CANLI DOGRULANAN (kullanicinin paylastigi gercek DOM): sinav ogesinin
   KENDI panel basligi (sinav adi + ders adi + duzenle/gorunurluk/ayarlar
   dugmeleri, ör. "BST020-KısaSınav1 [2026-03-18]" basligi) VE 'Öğrenci
   Gönderimleri' panelinin siyah ust basligi (menu-toggle + baslik +
   ayarlar linki). Ikisi de eski Angular directive bilesenleri
   (`.panel-header-directive`, `.black-panel-header`) - `.black-header-
   contents` (asil sinav DENEMESI sayfasindaki ONAY/tarih basligi, KESIN
   OLARAK GORUNMESI GEREKEN farkli bir bilesen) ile KARISTIRILMAMALI,
   bu yuzden class adlari BILEREK TAM eslesme (`.black-panel-header`,
   `panel-header-directive` alt dizesi degil) ile hedefleniyor. */
.panel-header-directive,
.black-panel-header,
/* CANLI DOGRULANAN (kullanicinin paylastigi gercek DOM): 'Öğrenci
   Gönderimleri' panelindeki TUM arama/filtre satiri - arama kutusu,
   "Öğrenci Durumu"/"Not Verme Durumu" acilir filtreleri VE "İleti
   Gönder" dugmesi, hepsi bu TEK sarmalayicinin (`.search-filter`)
   icinde. Tek tek her alt kontrolu hedeflemek yerine dogrudan
   sarmalayiciyi gizlemek daha DAYANIKLI (Blackboard bu satira yeni bir
   kontrol eklerse bile otomatik kapsanir). */
.search-filter,
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

# ONLEYICI DUZELTME (henuz CANLI DOGRULANMADI, ama plazibl bir hata modu):
# Her soru ("Soru N") ayri bir ac/kapa (accordion) panelinde render
# ediliyor, panelin acik/kapali durumu chevron butonunun aria-expanded
# ozniteliginde tutuluyor (bkz. "collapsible-container-*-button" /
# "-region" cifti - kullanicinin canli DOM'dan dogruladigi yapi). Su ana
# kadar incelenen ornekte TUM soru panelleri varsayilan olarak ACIK
# (aria-expanded="true") geliyordu, yani asagidaki adim o ornek icin
# "no-op" (hicbir sey degistirmiyor). Ama bu davranisin HER sinav/HER
# soru icin garanti oldugu KANITLANMADI - ozellikle soru metni (hocanin
# ekledigi coklu fotograf) ya da ogrencinin kompozisyon cevabi COK
# uzadiginda Blackboard bazi panelleri varsayilan KAPALI baslatabilir.
# Byle bir panel kapali kalirsa CSS'teki `overflow: visible !important`
# (FORCE_VISIBLE_CSS) BUNU KURTARMAZ - cunku icerik zaten DOM'da
# render edilmemis/gizli durumda olabilir, kirpilan bir kutu degil.
# Risksiz oldugu icin (SADECE aria-label'i tam olarak "Soru <sayi>" olan
# butonlar hedefleniyor - "Geri Bildirim - Soru N" panelleri, "Gezinme
# paneli" ya da "Degerlendirme icerigi N, Metin" gibi baska HICBIR seye
# dokunulmuyor) capture akisina KOSULSUZ ekleniyor - tum sorular zaten
# acik geldiyse hicbir etkisi olmuyor.
EXPAND_ALL_QUESTIONS_JS = """
async () => {
    const delay = (ms) => new Promise((r) => setTimeout(r, ms));

    const candidates = Array.from(
        document.querySelectorAll(
            '[id^="collapsible-container-"][id$="-button"][aria-expanded="false"]'
        )
    );
    const questionButtons = candidates.filter((btn) => {
        const label = (btn.getAttribute('aria-label') || '').trim();
        return /^Soru\\s+\\d+$/.test(label);
    });

    questionButtons.forEach((btn) => btn.click());
    if (questionButtons.length > 0) {
        await delay(300);
    }

    return { expanded: questionButtons.length };
}
"""

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

# Yazdirmadan hemen once, cok uzun sinavlarda EN SON sorunun icerigine
# (GEC mount olan editor/gorsel) bir sans daha tanimak icin uygulanan son
# guvenlik payi - bkz. capture_current_page icindeki kullanim yerinin
# yorumu (ONLEYICI DUZELTME).
FINAL_SETTLE_WAIT_MS = 1500

# CANLI DOGRULANAN HATA (kullanicinin PAYLASTIGI iki PDF karsilastirmasi):
# ayni sinavin AYNI son sorusunda (Soru 23, kompozisyon) bir ogrencide
# (Yigit Sabri Arik) "Yanit" basligi PDF'e giriyor ama ALTI TAMAMEN BOS
# cikiyordu, BASKA bir ogrencide (Yunus Aksu) AYNI soru turu icin cevap
# metni TAM basiliyordu - yani hata sabit/deterministik degil, ARALIKLI
# (bazen olan bazen olmayan bir yarış durumu). Bu, WAIT_IMAGES_JS'in
# hedefledigi <img> yuklenmesinden FARKLI bir sorun: essay cevap kutusu
# (.readonly-essay-question .ql-editor, bkz. kullanicinin paylastigi
# canli DOM) bir <img> DEGIL, duz metin icerigi Blackboard'un React
# agacinca panelin acilmasindan biraz SONRA doldurulan bir Quill.js
# editoru.
#
# IKINCI CANLI DOGRULANAN HATA (bu ayni bolumun ILK surumunde): ilk
# versiyon "metin UZUNLUGU art arda iki olcumde AYNI mi" diye bakiyordu -
# ama Quill bos bir editoru DAHI ANINDA `<p><br></p>` (tek, ICERIKSIZ bir
# paragraf) ile DOM'a koyuyor - yani "henuz veri gelmedi" ile "gercekten
# bos cevap" ilk anda AYNI gorunuyor (ikisi de uzunluk=0). Gercek cevap
# verisi SONRADAN (ag istegi tamamlaninca) gelene kadar bu ilk olcum
# ZATEN sabit (0, 0) kaliyordu - dongu daha ilk 400ms'de "degismedi, hazir"
# diyip cikiyordu, gercek icerik daha gelmeden. Kullanicinin isaret ettigi
# duzeltme: uzunluk degismesini degil, editorun GERCEKTEN o placeholder'i
# (`<p><br></p>`) ASIP asmadigini kontrol et - yani en azindan bir GERCEK
# (bos olmayan) <p> gelene kadar bekle. Bu, "hala yukleniyor" ile
# "gercekten bos cevap" arasindaki farki DOGRU ayirt eder: yukleniyorsa
# placeholder GEC ASILIR (bekleriz), gercekten bos birakilmissa placeholder
# HICBIR ZAMAN asilmaz (ust sinira kadar bekleyip devam ederiz - bu adim
# ASLA hata firlatmiyor/PDF'i durdurmuyor, cunku ogrencinin gercekten bos
# birakmis olmasi MESRU bir durum, hata degil).
#
# UCUNCU CANLI DOGRULANAN HATA (kullanicinin PAYLASTIGI GERCEK, calisan
# bir editor HTML'i uzerinden): yukaridaki "placeholder mi?" kontrolu
# editorun TAM innerHTML'ini SADECE `<p><br></p>` string'ine esitleyerek
# kontrol ediyordu. Ama kullanicinin gosterdigi gibi, ogrencinin GERCEKTEN
# yazdigi (bos OLMAYAN) bir cevap bile SONUNDA sadece bosluk karakteri
# (`&nbsp;`) iceren, GORSEL OLARAK bos paragraflarla bitebiliyor (ör.
# `<p>&nbsp;&nbsp;&nbsp;&nbsp;</p>`) - bu, `<p><br></p>` STRING'INE TAM
# ESIT DEGIL, bu yuzden bir onceki mantik boyle bir paragrafi "hala
# yukleniyor" sanip GEREKSIZ YERE bekleyebilirdi (ya da tam tersi, TEK
# BASINA boyle bir `&nbsp;`'li paragraftan olusan GERCEKTEN henuz
# yuklenmemis bir editoru YANLISLIKLA "icerik var" sanip ERKEN
# cikabilirdi - HTML string'i `<p><br></p>`'den FARKLI oldugu icin).
# Duzeltme: HTML string'ine degil, editorun GORUNUR METNINE (`innerText`)
# bakiyoruz - JavaScript'in `.trim()` metodu `&nbsp;` (NBSP,
# ECMAScript'in WhiteSpace tanimina dahildir) dahil TUM bosluk
# karakterlerini siler, bu yuzden "trim() sonrasi uzunluk 0" TUM bos/
# sadece-bosluk-iceren varyasyonlari (placeholder, `&nbsp;`'li paragraf,
# vs.) DOGRU sekilde "henuz gercek icerik yok" olarak isaretler - HTML'in
# TAM olarak hangi sekilde bos oldugunun bilinmesine gerek kalmaz.
# DORDUNCU CANLI DOGRULANAN HATA (ilk versiyon SADECE bulunan editorlerin
# BOS olup olmadigina bakiyordu, KAC TANE editor bulundugunu ONCEKI
# olcumle KARSILASTIRMIYORDU): eger bir essay editoru bir AN icin DOM'dan
# TAMAMEN dusup (FINAL_CLEANUP_JS'in tetikledigi virtualization yeniden-
# hesaplamasi gibi bir sebeple) sonra TEKRAR mount olursa,
# `querySelectorAll` o AN icin SADECE geri kalan (ör. 2/3) editoru bulur -
# script "2 editor var, ikisi de dolu" gorup YANLISLIKLA "hepsi hazir"
# sanabilirdi (3.'nun HIC VAR OLMADIGINI bilmeden). Duzeltme: artik SADECE
# "bos mu" degil, editor SAYISININ da bir ONCEKI olcumle AYNI kalip
# kalmadigina bakiyoruz - sayi degisiyorsa (biri dusup geri geldiyse) bu
# "hala oturmadi" sayilip beklemeye devam ediliyor.
ESSAY_SETTLE_MAX_POLLS = 10
ESSAY_SETTLE_POLL_MS = 400
ESSAY_SETTLE_JS = """
async () => {
    const delay = (ms) => new Promise((r) => setTimeout(r, ms));
    const isEmpty = (el) => (el.innerText || '').trim().length === 0;
    const snapshot = () => {
        const els = Array.from(
            document.querySelectorAll('.readonly-essay-question .ql-editor')
        );
        return { count: els.length, pending: els.filter((el) => isEmpty(el)).length };
    };

    let prev = snapshot();
    for (let i = 0; i < %(max_polls)d; i++) {
        await delay(%(poll)d);
        const current = snapshot();
        const settled = current.pending === 0 && current.count === prev.count;
        prev = current;
        if (settled) {
            break;
        }
    }
    return prev;
}
""" % {"max_polls": ESSAY_SETTLE_MAX_POLLS, "poll": ESSAY_SETTLE_POLL_MS}

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
#
# IKINCI CANLI DOGRULANAN HATA (yukaridaki duzeltmeden SONRA, farkli bir
# sinavda tekrar goruldu - kullanicinin bildirdigi "5 ogrenci taraniyor,
# sirdakine gecerken paneli bulamiyoruz" hatasi): yukarudaki koruma
# `el.closest('[role="menu"][aria-label*="Öğrenci"]')` SADECE panelin
# KENDISINI ya da panelin ALTINDAKI (descendant) elemanlari kapsiyor -
# `.closest()` sadece YUKARI (kendisi + atalar) dogru arar. Ama
# scan_grade_center.py'deki scroll_student_into_view_and_click
# docstring'inde (CANLI DOGRULANAN, ayri bir hata) ACIKCA belgelendigi
# gibi, GERCEK kaydirilabilir eleman cogu zaman panelin KENDISI DEGIL,
# panelin bir UST atasi (sarmalayici bir div) oluyor - semantik
# `[role="menu"]` etiketi cogu zaman kendisi scrollable olmuyor. O
# sarmalayici, panelin bir ATASI (parent) oldugu icin panelin kendisi
# ONUN bir alt elemani sayilir - `.closest()` bunu YAKALAYAMAZ (yukari
# degil ASAGI bir iliski). Sonuc: capture_current_page HER ogrenciden
# sonra bu gercek kaydirma sarmalayicisini FARKINDA OLMADAN sifirliyordu,
# scan_grade_center.py da bunu her seferinde bastan kaydirmak zorunda
# kaliyordu - liste yeterince uzun/virtualized oldugunda bir noktadan
# sonra hedef ogrenci DOM'a hic girmeden 35 deneme dolup RuntimeError
# ("... panel kaydirildi ama bulunamadi") firlatiliyordu. Duzeltme:
# paneli ICEREN (yani panelin bir atasi/sarmalayicisi olan) elemanlari da
# ayni sekilde istisna tutuyoruz - `el.contains(panel)` bu iliskiyi
# (asagi dogru: el, panelin bir atasi mi) test eder, `.closest()`'in
# eksik biraktigi yonu tamamlar.
RESET_SCROLL_AFTER_CAPTURE_JS = """() => {
    window.scrollTo(0, 0);
    if (document.scrollingElement) {
        document.scrollingElement.scrollTop = 0;
    }
    if (document.body) {
        document.body.scrollTop = 0;
    }
    const studentPanel = document.querySelector('[role="menu"][aria-label*="Öğrenci"]');
    const scrollables = document.querySelectorAll('*');
    for (const el of scrollables) {
        if (el.scrollTop && el.scrollTop > 0) {
            if (el.closest && el.closest('[role="menu"][aria-label*="Öğrenci"]')) {
                continue;
            }
            if (studentPanel && el.contains && el.contains(studentPanel)) {
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


def expand_all_questions_all_frames(page: Page) -> int:
    """EXPAND_ALL_QUESTIONS_JS'i ana sayfa VE icindeki her cerceve icin
    calistirip acilan panel sayisini toplar - bkz. EXPAND_ALL_QUESTIONS_JS
    docstring'i. scroll_all_frames'ten ONCE cagrilmali: bir panel acilinca
    sayfanin toplam yuksekligi degisebilir, bu yuzden kaydirma/stabilizasyon
    olcumu panel(ler) acildiktan SONRA yapilmali - aksi halde AUTO_SCROLL_JS
    yanlis (kapali panelle olculmus, kucuk) bir yuksekligi "sabit" sanip
    erken durabilir.
    """
    total_expanded = 0
    for frame in _iter_frames(page):
        try:
            result = frame.evaluate(EXPAND_ALL_QUESTIONS_JS)
        except Exception:
            continue
        total_expanded += result["expanded"]
    return total_expanded


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


def settle_essay_answers_all_frames(page: Page) -> None:
    """ESSAY_SETTLE_JS'i ana sayfa VE icindeki her cerceve icin calistirir -
    bkz. ESSAY_SETTLE_JS docstring'i (CANLI DOGRULANAN HATA: ayni sinavin
    ayni son sorusunda bazi ogrencilerde cevap metni PDF'e giriyor, bazilarinda
    girmiyordu - essay editorunun icerigi GEC mount oldugu icin). Sonuc
    kullanilmiyor, sadece SURE gecmesi (icerik stabillesene kadar) onemli."""
    for frame in _iter_frames(page):
        try:
            frame.evaluate(ESSAY_SETTLE_JS)
        except Exception:
            continue


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
    // CANLI DOGRULANAN HATA (kullanicinin paylastigi PDF: sayfanin EN
    // USTUNDE, asil sinav icerigi gelmeden ONCE, TUM Not Defteri
    // tablosunun VE ders ust navigasyonunun ("Kurslar", "Ders İçeriği/
    // Takvim/Duyurular..." sekmeleri, HATTA baska bir capture'da ogrenci
    // LISTESI/tablosu) render edildigi goruldu): ogrenci degerlendirme
    // paneli, Not Defteri sayfasinin USTUNE acilan KAYAN bir panel
    // (`[bb-offcanvas-pausal-scope]` tasiyan `<bb-flexible-attempt-
    // grading-ui>`, ui-view="course-grades-peek-gradebook-item-
    // assessment-panel@" - CANLI DOM'da dogrulandi). Normalde bu panel
    // arka plandaki sayfayi GORSEL OLARAK KAPLAR/GIZLER (position:fixed +
    // tam ekran + z-index gibi bir teknikle) - ama FORCE_VISIBLE_CSS'teki
    // `* { position: static !important }` kurali TUM sayfaya (bu panel
    // dahil) uygulaniyor, bu da panelin "ustte yuzme" mekanizmasini
    // bozup arka plan sayfasinin NORMAL AKISA dusmesine yol aciyor.
    //
    // CANLI DOGRULANAN HATA (ISRARLA TEKRARLANDI): ILK duzeltme TUM
    // `[bb-offcanvas-pausal-scope]` elemanlarini "korunacak panel"
    // sayiyordu - ama CANLI DOM'da bu oznitelik ASIL degerlendirme
    // panelinin YANI SIRA "Öğrenci Gönderimleri" LISTE panelinde
    // (`ui-view="course-grades-panel@"`) de bulunuyor! Sonuc: o liste
    // panelinin KENDI icerigi (tum ogrenci/not tablosu) da "korunacak"
    // sayilip PDF'e sizmaya devam ediyordu. Duzeltme: ARTIK SADECE ASIL
    // degerlendirme panelini (ui-view'i "gradebook-item-assessment-panel"
    // iceren, yoksa <bb-flexible-attempt-grading-ui> etiketi, o da yoksa
    // EN SON CARE olarak ilk bb-offcanvas-pausal-scope) hedefliyoruz -
    // DIGER TUM off-canvas panelleri (liste paneli dahil) ARTIK ozel
    // muamele GORMUYOR, sadece bu TEK panelin ata zinciri korunuyor.
    const examPanel =
        document.querySelector('[ui-view*="gradebook-item-assessment-panel"]') ||
        document.querySelector('bb-flexible-attempt-grading-ui') ||
        document.querySelector('[bb-offcanvas-pausal-scope]');
    // CANLI DOGRULANAN HATA (bu bloktaki KISA omurlu bir denemenin
    // KENDISI): bombos sayfa sorununu 'height/min-height/max-height'i
    // panelin ATA ZINCIRINE KALICI OLARAK (inline stil, HICBIR YERDE geri
    // ALINMADAN) yazarak cozmeye calisildi. Kullanicinin CANLI gozlemi:
    // bu, taramanin bir sonraki ogrenciye GECEMEMESINE yol acti - kokten
    // sebep buyuk ihtimalle "Öğrenciler" panelinin (bu zincirin bir
    // yerinde, YAN YANA bir sutun olarak) KENDI sanal liste
    // virtualization'inin STABIL bir konteyner yuksekligine bagimli
    // olmasi (bu dosyanin TUM gecmisinde tekrar tekrar goruldugu AYNI
    // desen - bkz. FORCE_VISIBLE_CSS ustundeki notlar) - atalarin
    // yuksekligini KALICI OLARAK degistirmek bu olcumu bozdu. Deneme
    // TAMAMEN GERI ALINDI - sadece kardes-gizleme (display:none, asagida)
    // kaliyor, bu ONCEDEN kullanicinin kendisi tarafindan "süper oldu"
    // diye DOGRULANMISTI, yukseklik degisikligi EKLENMEDEN once.
    // CANLI DOGRULANAN HATA (kullanicinin bildirdigi: bombos sayfa sayisi
    // OGRENCIDEN OGRENCIYE DEGISIYORDU - 3, 5, 8 gibi - AYNI sinavin AYNI
    // sorulariyla): capture_student BIRDEN FAZLA ogrenciyi AYNI sayfada
    // (yeniden yuklemeden) ART ARDA yakaliyor. Asagidaki kardes-gizleme
    // (display:none) satiri HICBIR YERDE GERI ALINMIYORDU - yani 1.
    // ogrencide gizlenen elemanlar 2., 3., ... ogrenci icin de gizli
    // KALIYOR, HER capture'da BIRIKEREK artiyordu. Bu, "capture_current_
    // page sayfada kalici hicbir iz birakmamali" ilkesinin (bkz.
    // add_style_all_frames docstring'i) tam bir ihlali - VE tam olarak
    // "ogrenciden ogrenciye degisen" bombos sayfa davranisini acikliyor.
    // Duzeltme: her gizlenen kardesi bir data-* etiketle (ONCEKI inline
    // display degeriyle birlikte) isaretliyoruz - capture_current_page'in
    // finally blogunda (bkz. RESTORE_OFFCANVAS_SIBLINGS_JS) bu etiketli
    // elemanlar ORIJINAL haline GERI DONDURULUYOR, tipki stil
    // etiketlerinin kaldirilmasi gibi.
    if (examPanel) {
        let node = examPanel;
        while (node && node.parentElement && node !== document.body) {
            const parent = node.parentElement;
            Array.from(parent.children).forEach(sibling => {
                if (sibling !== node) {
                    try {
                        if (!sibling.hasAttribute('data-capture-offcanvas-hidden')) {
                            const priorValue = sibling.style.getPropertyValue('display');
                            const priorPriority = sibling.style.getPropertyPriority('display');
                            sibling.setAttribute(
                                'data-capture-offcanvas-hidden',
                                encodeURIComponent(priorValue) + '|' + priorPriority
                            );
                        }
                        sibling.style.setProperty('display', 'none', 'important');
                    } catch (e) {}
                }
            });
            node = parent;
        }
    }

    // CANLI DOGRULANAN (kullanicinin paylastigi gercek DOM): sinav
    // sorularini SARAN asil icerik konteyneri SANAL LISTE (virtualization)
    // deseni kullaniyor - `<h2 class="...hideOffScreen...">Ana gönderim
    // içeriği</h2>`'den HEMEN SONRA gelen kardes eleman, `style="transform:
    // translateY(0px)"` tasiyan bir SARMALAYICI iceriyor (sol "Öğrenciler"
    // panelinde defalarca gordugumuz AYNI teknik, ama bu sefer SORULARIN
    // kendisi icin). Boyle bir konteyner genelde TUM ogelerin TOPLAM
    // yuksekligini ONCEDEN TAHMIN edip kendi disina ayirir - bu tahmin
    // GERCEK render edilen yukseklikle TAM eslesmezse (ör. bazi
    // ogrencilerin cevap metinleri daha kisa/uzun oldugu icin), fazladan
    // BOS alan kaliyor - TAM OLARAK kullanicinin bildirdigi "5 icerik + 3
    // bombos sayfa" deseni. Bu konteyneri class adina degil, "Ana gönderim
    // içeriği" METNINE gore (STABIL - makeStyles hash'i degisse bile
    // calisir) buluyoruz - SADECE bu TEK sarmalayicinin height/max-height/
    // overflow'unu sifirliyoruz, dairesel rozetler gibi BASKA HICBIR
    // elemana dokunmuyoruz (bkz. az once GERI ALINAN, ATA-ZINCIRI/TUM-
    // TORUNLAR yaklasiminin gorsel bozulmaya yol actigi not). Gecici
    // DEGIL bu kez (bkz. RESTORE_MAIN_CONTENT_HEIGHT_JS) - data-* ile
    // isaretlenip finally'de geri alınıyor, AYNI offcanvas-gizleme
    // deseni.
    const mainContentMarker = Array.from(
        document.querySelectorAll('h1, h2, h3, div, span')
    ).find(el => el.children.length === 0 && (el.textContent || '').trim() === 'Ana gönderim içeriği');
    if (mainContentMarker && mainContentMarker.nextElementSibling) {
        const virtualizedWrapper = mainContentMarker.nextElementSibling;
        try {
            if (!virtualizedWrapper.hasAttribute('data-capture-height-reset')) {
                const priorHeight = virtualizedWrapper.style.getPropertyValue('height');
                const priorHeightPriority = virtualizedWrapper.style.getPropertyPriority('height');
                const priorMaxHeight = virtualizedWrapper.style.getPropertyValue('max-height');
                const priorMaxHeightPriority = virtualizedWrapper.style.getPropertyPriority('max-height');
                virtualizedWrapper.setAttribute(
                    'data-capture-height-reset',
                    encodeURIComponent(priorHeight) + '|' + priorHeightPriority +
                    '||' + encodeURIComponent(priorMaxHeight) + '|' + priorMaxHeightPriority
                );
            }
            virtualizedWrapper.style.setProperty('height', 'auto', 'important');
            virtualizedWrapper.style.setProperty('max-height', 'none', 'important');
        } catch (e) {}
    }

    // bkz. FORCE_VISIBLE_CSS'teki AYNI isimli CANLI DOGRULANAN HATA notu:
    // `[aria-hidden="true"]` BILEREK burada YOK - "ekran okuyucudan
    // gizli" ile "gorsel olarak gizli" AYNI SEY DEGIL, bu secici
    // basliktaki GORUNMESI GEREKEN puan rozeti rakamlarini da (yanlislikla)
    // 1x1px'e kilitleyip PDF'ten kayboltuyordu.
    const srElements = document.querySelectorAll(
        '[class*="hideOffScreen"], [class*="sr-only"], [class*="srOnly"], [class*="offScreen"], [class*="offscreen"], [class*="off-screen"], [class*="visuallyHidden"], [class*="visually-hidden"], [class*="screen-reader"], [class*="screenreader"], [class*="ScreenReader"], [class*="cdk-visually-hidden"], .sr-only, .hideOffScreen, .visually-hidden'
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


# FINAL_CLEANUP_JS'teki off-canvas kardes-gizleme adiminin 'data-capture-
# offcanvas-hidden' ile isaretledigi HER elemanin 'display' stilini
# ORIJINAL (capture ONCESI) degerine geri dondurur - bkz. o adimin
# docstring notu. Elemanin orijinal inline degeri bossa (cogu durumda
# BOYLE), stili TAMAMEN kaldirip elemani stylesheet'teki haline geri
# birakiyoruz - AYNI RESTORE_FIXED_POSITION_JS deseni (bkz. bu dosyanin
# gecmisindeki ayni isimli duzeltme notlari).
RESTORE_OFFCANVAS_SIBLINGS_JS = r"""() => {
    document.querySelectorAll('[data-capture-offcanvas-hidden]').forEach(el => {
        try {
            const raw = el.getAttribute('data-capture-offcanvas-hidden') || '';
            const sepIndex = raw.lastIndexOf('|');
            const encodedValue = sepIndex === -1 ? raw : raw.slice(0, sepIndex);
            const priority = sepIndex === -1 ? '' : raw.slice(sepIndex + 1);
            const value = decodeURIComponent(encodedValue || '');
            if (value) {
                el.style.setProperty('display', value, priority);
            } else {
                el.style.removeProperty('display');
            }
        } catch (e) {
        } finally {
            try {
                el.removeAttribute('data-capture-offcanvas-hidden');
            } catch (e) {}
        }
    });
    // bkz. FINAL_CLEANUP_JS'teki 'Ana gönderim içeriği' sarmalayicisi
    // (mainContentMarker) notu - AYNI gecici-isaretle/geri-al deseni.
    document.querySelectorAll('[data-capture-height-reset]').forEach(el => {
        try {
            const raw = el.getAttribute('data-capture-height-reset') || '';
            const [heightPart, maxHeightPart] = raw.split('||');
            const restoreProp = (part, prop) => {
                if (!part) return;
                const sepIndex = part.lastIndexOf('|');
                const encodedValue = sepIndex === -1 ? part : part.slice(0, sepIndex);
                const priority = sepIndex === -1 ? '' : part.slice(sepIndex + 1);
                const value = decodeURIComponent(encodedValue || '');
                if (value) {
                    el.style.setProperty(prop, value, priority);
                } else {
                    el.style.removeProperty(prop);
                }
            };
            restoreProp(heightPart, 'height');
            restoreProp(maxHeightPart, 'max-height');
        } catch (e) {
        } finally {
            try {
                el.removeAttribute('data-capture-height-reset');
            } catch (e) {}
        }
    });
}"""


def restore_offcanvas_siblings_all_frames(page: Page) -> None:
    """RESTORE_OFFCANVAS_SIBLINGS_JS'i ana sayfa VE icindeki her cerceve
    icin calistirir - bkz. o JS'in docstring'i. capture_current_page'in
    finally blogunda, BASARILI/BASARISIZ FARK ETMEKSIZIN cagrilmali -
    aksi halde bir sonraki ayni-sayfadaki capture (ör. capture_student'in
    dongusundeki bir sonraki ogrenci) bu capture'in gizledigi elemanlari
    KALICI OLARAK gizli bulur."""
    for frame in _iter_frames(page):
        try:
            frame.evaluate(RESTORE_OFFCANVAS_SIBLINGS_JS)
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
        # Kaydirmadan ONCE: bkz. EXPAND_ALL_QUESTIONS_JS/
        # expand_all_questions_all_frames docstring'i - kapali gelen soru
        # panelleri varsa (soru metni/kompozisyon cevabi cok uzun oldugunda
        # Blackboard'un bunu varsayilan kapali baslatma ihtimaline karsi)
        # asagidaki kaydirma/stabilizasyon olcumunden ONCE aciliyor.
        expand_all_questions_all_frames(page)

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

        # IKINCI CANLI DOGRULANAN HATA (kullanicinin bildirdigi: yukaridaki
        # ESSAY_SETTLE_JS/uzun bekleme fix'i SORUNU COZMEDI - AYNI son soru
        # bazi ogrencilerde hala bos cikiyor). Bu, "icerik henuz DOLMADI"
        # (bekleme suresi yetersiz) teorisini CURUTUYOR - eger oyle olsaydi
        # daha uzun/kosullu bekleme sorunu duzeltirdi. Daha guclu bir
        # aciklama: soru LISTESININ KENDISI virtualized/windowed olabilir
        # (bkz. sorularin sarmalayicisindaki `data-index` ozniteligi -
        # kullanicinin paylastigi DOM'da goruldu). Boyle bir listede EN
        # SONDAKI soru (Soru 23) capture BASLARKEN (sayfa henuz asagi hic
        # kaydirilmamisken) DOM'a HENUZ MOUNT OLMAMIS olabilir - yukaridaki
        # expand_all_questions_all_frames cagrisi bu NOKTADA calistigi icin
        # o an DOM'da OLMAYAN bir butonu bulup tiklayemez (0 eslesme,
        # sessizce hicbir sey yapmaz). Panel daha SONRA (scroll_all_frames
        # asagi kaydirirken) ilk kez mount oldugunda VARSAYILAN KAPALI
        # gelirse, bir daha HIC acilma sansi olmuyordu - essay editoru hic
        # mount olmadigi icin icerik ASLA gelmiyordu (ne kadar beklenirse
        # beklensin). Bu, ogrenciden ogrenciye DEGISEN cevap uzunluklarinin
        # (dolayisiyla farkli scroll/virtualization sinirlarinin) neden
        # "bazen olan bazen olmayan" bir hataya yol actigini da acikliyor.
        # Duzeltme: scroll_all_frames TAMAMEN bitip TUM icerik DOM'a girdikten
        # SONRA expand adimini BIR KEZ DAHA calistiriyoruz - artik Soru
        # 23 dahil TUM sorular DOM'da oldugu icin kapali kalan olursa bu
        # ikinci turde yakalanip acilir.
        expand_all_questions_all_frames(page)

        image_result = wait_images_all_frames(page)
        if image_result["pending"] > 0:
            raise RuntimeError(
                f"{image_result['pending']}/{image_result['total']} gorsel "
                f"{IMAGE_LOAD_MAX_WAIT_MS / 1000:.0f} saniyede yuklenmedi - sayfa "
                "eksik yuklenmis olabilir (ör. yavas internet). PDF uretilmedi, "
                "tekrar denenmesi gerekiyor."
            )

        # ONLEYICI DUZELTME (kullanicinin bildirdigi hata: COK uzun
        # sinavlarda EN SON sorunun cevabi/fotografi PDF'te eksik
        # cikiyordu). Yukaridaki scroll_all_frames SADECE EN BUYUK
        # overflow'a sahip TEK bir konteyneri hedefliyor - eger o an
        # olcum yapilirken son sorunun kendi editoru/gorseli henuz DOM'a
        # GIRMEMISSE (React'in panel acilinca icerigi GEC mount etmesi,
        # ya da agir bir sinavda tarayicinin arka planda hala calisiyor
        # olmasi ihtimaline karsi), o icerik ilk wait_images_all_frames
        # turunde SAYILMAMIS bile olabilir - "pending" 0 cikar ama
        # aslinda o an DOM'da hic yoktur. Kullanicinin acikca belirttigi
        # oncelik: fazladan BOS sayfa SORUN DEGIL, ama cevabin/fotografin
        # PDF'ten TAMAMEN kaybolmasi ciddi bir sorun (notlandirma
        # itirazinda kanit olarak kullaniliyor). Bu yuzden yazdirmadan
        # hemen once EK bir guvenlik turu: sayfayi (ve her cerceveyi)
        # tekrar en alta kaydirip kisa bir sure bekliyoruz - bu, ilk
        # turden SONRA GEC mount olan icerigin (varsa) tetiklenmesini
        # saglar - SONRA goruntu bekleme turunu (WAIT_IMAGES_JS) BIR KEZ
        # DAHA calistiriyoruz, ilk turde DOM'da hic olmayan/henuz
        # baslamamis gorseller bu ikinci turde yakalanir. Ayni sikilikta:
        # bu ikinci turde de hala pending gorsel varsa YINE PDF
        # URETILMIYOR - sessizce eksik bir PDF uretmektense acikca
        # basarisiz olup tekrar denenmesini istemek, kullanicinin asil
        # onceligiyle (eksik cevap OLMASIN) daha tutarli.
        for frame in _iter_frames(page):
            try:
                frame.evaluate(
                    "() => { "
                    "window.scrollTo(0, document.body.scrollHeight); "
                    "if (document.scrollingElement) { "
                    "document.scrollingElement.scrollTop = document.scrollingElement.scrollHeight; "
                    "} }"
                )
            except Exception:
                continue
        page.wait_for_timeout(FINAL_SETTLE_WAIT_MS)
        # bkz. ESSAY_SETTLE_JS docstring'i (CANLI DOGRULANAN HATA: ayni
        # sinavin ayni son sorusunda bazi ogrencilerde essay cevap metni
        # eksik cikiyordu) - goruntu kontrolunden ONCE, essay editorlerinin
        # metin icerigi stabillesene kadar (ya da ust sinira ulasana kadar)
        # bekliyoruz.
        settle_essay_answers_all_frames(page)
        late_image_result = wait_images_all_frames(page)
        if late_image_result["pending"] > 0:
            raise RuntimeError(
                f"{late_image_result['pending']}/{late_image_result['total']} gorsel "
                f"(sayfanin EN ALTINDA, GEC mount olmus olabilir) "
                f"{IMAGE_LOAD_MAX_WAIT_MS / 1000:.0f} saniyede yuklenmedi - sayfa "
                "eksik yuklenmis olabilir. PDF uretilmedi, tekrar denenmesi gerekiyor."
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

        # UCUNCU CANLI DOGRULANAN HATA (kullanicinin capture SONRASI canli
        # DOM kontrolunde essay cevabinin DOLU cikmasina RAGMEN, ayni anda
        # uretilen PDF'te YINE eksik cikmasi): FINAL_CLEANUP_JS (yukarida)
        # sinav sorularini saran ana konteynerin bir VIRTUALIZED LISTE
        # oldugunu belgeliyor (`transform: translateY(...)` deseni - ayni
        # sekilde "Öğrenciler" panelinde de gorulen teknik) ve bu adim o
        # konteynerin height/max-height'ini `auto`/`none`'a ZORLUYOR - bu,
        # kutuphanenin kendi boyut-izleme mekanizmasini (ResizeObserver vb.)
        # tetikleyip GORUNUR ARALIGINI YENIDEN hesaplamasina, bu sirada bir
        # AN icin Soru 23 gibi son ogeleri DOM'dan dusurup (sonra tekrar
        # takmasina) yol acabilir. run_final_cleanup_all_frames'den HEMEN
        # SONRA, ARADA HIC BEKLEME OLMADAN page.pdf() cagriliyordu - yani
        # tam bu gecis anini yakalama riski vardi. Kullanicinin capture
        # SONRASI yaptigi kontrolde icerigin DOLU cikmasi da bunu destekliyor
        # (o kontrol print'ten bir kac saniye SONRA yapildi, kutuphane o ana
        # kadar toparlanmis olabilir). Duzeltme: bu temizlik adimindan
        # SONRA, print'ten HEMEN ONCE essay editorlerinin icerigini BIR KEZ
        # DAHA (ayni ESSAY_SETTLE_JS ile) dogruluyoruz - boylece cleanup'in
        # tetikleyebilecegi herhangi bir yeniden-hesaplama/mount gecisi icin
        # de sans taniyoruz.
        settle_essay_answers_all_frames(page)

        # Blackboard'un dogru/yanlis renk vurgusu (yesil/kirmizi) muhtemelen
        # normal (screen) CSS kurallariyla geliyor - Chrome'un page.pdf()
        # cagrisi VARSAYILAN OLARAK 'print' medya turunu kullanir, bu da
        # sitenin (varsa) ayri bir @media print stil setine gecmesine ve
        # bu renk vurgularinin sessizce kaybolmasina yol acabilir. Sayfayi
        # ACIKCA 'screen' medya turunde tutarak, ekranda gorunen AYNI
        # renkli halini yazdiriyoruz.
        try:
            page.emulate_media(media="screen")
        except Exception:
            pass

        try:
            page.pdf(
                path=str(pdf_path),
                width=f"{PDF_PAGE_WIDTH_PX}px",
                height=f"{PDF_PAGE_HEIGHT_PX}px",
                print_background=True,
            )
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
        # bkz. RESTORE_OFFCANVAS_SIBLINGS_JS docstring'i: FINAL_CLEANUP_JS'in
        # off-canvas kardes-gizleme adimi (display:none) INLINE stil oldugu
        # icin yukaridaki iki remove_style_all_frames cagrisiyla (KALDIRILAN
        # <style> ETIKETLERI) GERI ALINMIYOR - ayri bir adim gerekiyor,
        # aksi halde capture_student'in AYNI sayfada ardisik yakaladigi
        # SONRAKI ogrenciler icin bu elemanlar KALICI OLARAK gizli kalirdi.
        restore_offcanvas_siblings_all_frames(page)
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
