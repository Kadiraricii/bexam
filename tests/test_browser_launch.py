"""GERCEK Playwright + GERCEK Google Chrome ile calisan testler.

Bu oturumda bulunup duzeltilen `--no-sandbox` guvenlik regresyonunu ve
`channel="chrome"`'un genel olarak calistigini CANLI dogrular - sahte
nesnelerle degil, gercek bir Chrome surecini baslatip kapatarak.

Chrome makinede/CI runner'inda bulunamazsa testler acikca SKIP edilir
(FAIL degil) - Chrome'un ortamda olup olmamasi bu projenin kodunun
sorumlulugunda degil. CI workflow'u (bkz. .github/workflows/tests.yml)
Google Chrome'u ACIKCA kurup bu testlerin GERCEKTEN calismasini,
sessizce atlanmamasini saglar.
"""

import pytest
from playwright.sync_api import sync_playwright

import common
from capture import FORCE_VISIBLE_CSS, HIDE_NAVIGATION_CHROME_CSS, RESET_SCROLL_AFTER_CAPTURE_JS
from scan_grade_center import collect_visible_rows, collect_visible_rows_in_container, find_student_rows


def _chrome_available() -> bool:
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="chrome", headless=True)
            browser.close()
        return True
    except Exception:
        return False


requires_chrome = pytest.mark.skipif(
    not _chrome_available(), reason="Bu makinede/runner'da gerçek Google Chrome bulunamadı"
)


@requires_chrome
def test_real_chrome_launches_with_sandbox_enabled():
    # bkz. common.py::browser_launch_kwargs docstring - chromium_sandbox
    # ACIKCA True verilmezse Playwright sessizce --no-sandbox ekliyordu
    # (Chrome'un OS-duzeyi islem korumasini tamamen kapatan, bu oturumda
    # bulunup duzeltilen gercek bir guvenlik regresyonu).
    kwargs = common.browser_launch_kwargs()
    kwargs["headless"] = True  # CI'da gorunur pencereye gerek yok
    # viewport, gercek uygulamada SADECE launch_persistent_context() ile
    # kullaniliyor (context+browser tek cagrida birlesir) - context'siz
    # duz .launch() bunu KABUL ETMIYOR, bu yuzden burada cikartiyoruz.
    # Persistent-context yoluyla tam kwargs seti icin bkz.
    # test_real_chrome_persistent_context_launches_and_closes.
    kwargs.pop("viewport", None)

    with sync_playwright() as p:
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page()
            page.goto("about:blank")
            assert page.url == "about:blank"
        finally:
            browser.close()


@requires_chrome
def test_real_chrome_persistent_context_launches_and_closes(tmp_path):
    # gercek uygulamanin (gui.py/capture.py) kullandigi TAM yol:
    # launch_persistent_context + kendi profil klasoru.
    profile_dir = tmp_path / "profile"
    kwargs = common.browser_launch_kwargs()
    kwargs["headless"] = True

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(profile_dir), **kwargs)
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto("about:blank")
            assert common.live_url(page) == "about:blank"
        finally:
            context.close()


@requires_chrome
def test_real_chrome_context_pages_becomes_empty_after_closing_all_pages(tmp_path):
    # Bu oturumda bulunan "Chrome'u kapatinca hala 'bağlı' gözüküyor"
    # hatasinin kok nedeninin dogrulamasi: gui.py'nin bos-zaman
    # dongusu, TUM sekmeler/pencere kapaninca context.pages'in BOS
    # LISTE dondurdugunu (exception DEGIL) varsayarak tasarlandi - bu
    # varsayimi gercek Chrome'a karsi dogruluyoruz.
    profile_dir = tmp_path / "profile"
    kwargs = common.browser_launch_kwargs()
    kwargs["headless"] = True

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(str(profile_dir), **kwargs)
        try:
            for page in list(context.pages):
                page.close()
            assert context.pages == []
        finally:
            context.close()


@requires_chrome
def test_collect_visible_rows_finds_menuitem_student_cards():
    # CANLI DOGRULANAN HATA: kullanicinin gercek Blackboard hesabindan
    # paylastigi DOM'da ogrenci satirlari `role="menuitem"` olan `<li>`
    # elemanlariydi - isim ve puan (skor) FARKLI kardes div'lere
    # bolunmustu (isim iceren "userCardWrapper" benzeri div'in KENDISINDE
    # puan yoktu, puan ayri bir kardes div'deydi). collect_visible_rows'un
    # eski selector listesinde `[role="menuitem"]` OLMADIGI icin bu
    # sayfada HER ZAMAN '0 öğrenci satırı bulundu' sonucu cikiyordu -
    # indirme sessizce hicbir PDF uretmiyordu (bkz. proje gecmisi). Bu
    # test, gercek DOM yapisindan sadelestirilmis bir HTML'i GERCEK bir
    # Chrome sayfasina yukleyip collect_visible_rows'un artik dogru
    # (ad, not) ciftini buldugunu dogruluyor.
    html = """
    <html><body>
      <ul role="menu" aria-label="Öğrenciler">
        <li role="menuitem" class="listItem">
          <div class="listItemInner">
            <div class="card">
              <div class="topContent">
                <div class="userCardWrapper">
                  <div class="userName">YUNUS AKSU</div>
                </div>
              </div>
              <div class="gradePillContent">
                <span class="pill-grade">50</span>/<span class="pill-points-possible">100</span>
              </div>
            </div>
          </div>
        </li>
      </ul>
    </body></html>
    """
    kwargs = common.browser_launch_kwargs()
    kwargs["headless"] = True
    kwargs.pop("viewport", None)

    with sync_playwright() as p:
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page()
            page.set_content(html)
            rows = collect_visible_rows(page)
        finally:
            browser.close()

    assert rows == [("YUNUS AKSU", "50/100")]


@requires_chrome
def test_collect_visible_rows_in_container_does_not_double_count_nested_match():
    # CANLI DOGRULANAN HATA: gercek DOM'da her ogrenci satiri icin
    # `[role="menuitem"]` olan disaridaki `<li>` (isim+puan) VE onun
    # ICINDEKI, sadece ismi tasiyan "userCardWrapper" benzeri bir div
    # (`[class*="userCard"]` ile eslesen) AYRI AYRI eslesiyordu.
    # collect_visible_rows_in_container puan sarti aramadigi icin
    # (kasitli - notu olmayan ogrencileri de yakalasin diye) bu ic-ice
    # iki eslesmeyi IKI FARKLI satir sayiyordu - HER ogrenci TAM 2 KEZ
    # yakalanip PDF'i 2 kez uretiliyordu (kullanicinin canli bildirdigi
    # '30 öğrenci satırı bulundu' - 15 gercek ogrenci yerine). Bu test,
    # ayni ic-ice yapiyi tasiyan bir DOM'da artik SADECE BIR satirin
    # (en distaki, isim+puan tasiyan) dondugunu dogruluyor.
    html = """
    <html><body>
      <div id="panel">
        <li role="menuitem">
          <div class="userCardWrapper">
            <div class="userName">YUNUS AKSU</div>
          </div>
          <div class="gradePillContent">50/100</div>
        </li>
      </div>
    </body></html>
    """
    kwargs = common.browser_launch_kwargs()
    kwargs["headless"] = True
    kwargs.pop("viewport", None)

    with sync_playwright() as p:
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page()
            page.set_content(html)
            scroll_handle = page.locator("#panel").element_handle()
            rows = collect_visible_rows_in_container(scroll_handle)
        finally:
            browser.close()

    assert rows == [("YUNUS AKSU", "50/100")]


@requires_chrome
def test_find_student_rows_does_not_crash_when_name_duplicated_outside_panel():
    # CANLI DOGRULANAN COKME: gercek Degerlendirme sayfasinda ogrencinin
    # adi HEM sayfa ustundeki sabit (kaydirilamaz) kompakt baslikta HEM
    # DE sol "Öğrenciler" listesinde (role="menu" aria-label="Öğrenciler")
    # ayni metinle geciyordu. Panel dogru sekilde bu menuye
    # SINIRLANMAZSA (eski davranis: ne kullanilan iki genel selector de
    # eslesmiyordu, panel sessizce TUM SAYFAYA genisliyordu),
    # find_scroll_container `get_by_text(..., exact=True).first` ile
    # SAYFADAKI ILK eslesmeyi (sabit baslik - kaydirilabilir bir atasi
    # OLMAYAN bir alan) seciyordu. Oradan yukari kaydirilabilir bir ata
    # hicbir zaman bulunamiyor, `evaluate_handle` Python `None` DEGIL JS
    # `null` degerini SARAN bir JSHandle donduruyordu -
    # collect_visible_rows_in_container bu null uzerinde
    # querySelectorAll cagirinca "Cannot read properties of null" ile
    # PATLIYORDU (kullanicinin canli olarak bildirdigi tam hata). Bu
    # test hem cokmenin artik olmadigini hem de dogru konteynerden
    # (menu listesi) kaydirilip TUM ogrencilerin (ilk gorunenler dahil)
    # dogru toplandigini dogruluyor.
    students = [f"ÖĞRENCİ {i}" for i in range(1, 11)]
    rows_html = "".join(
        f'<li role="menuitem" style="height:40px;">'
        f'<div class="userName">{name}</div>'
        f'<div class="grade">{i * 5}/100</div></li>'
        for i, name in enumerate(students, start=1)
    )
    html = f"""
    <html><body>
      <div class="compactHeader">ÖĞRENCİ 1</div>
      <ul role="menu" aria-label="Öğrenciler"
          style="height:120px; overflow-y:auto; display:block;">
        {rows_html}
      </ul>
    </body></html>
    """
    kwargs = common.browser_launch_kwargs()
    kwargs["headless"] = True
    kwargs.pop("viewport", None)

    with sync_playwright() as p:
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page()
            page.set_content(html)
            rows = find_student_rows(page)
        finally:
            browser.close()

    assert [name for name, _score in rows] == students


@requires_chrome
def test_hide_navigation_chrome_css_hides_sidebar_and_icon_buttons_only():
    # CANLI DOGRULANAN HATA: FORCE_VISIBLE_CSS sol "Öğrenciler" panelini
    # (normalde sabit yukseklikte, kendi ic kaydirmasi olan bir liste)
    # TAM ICERIK yuksekligine genisletiyordu - bu panel asil sinav
    # icerigiyle yan yana (flex) bir sutun oldugu icin, cok uzun bir
    # ogrenci listesinde Chrome'un yazdirma motoru bu ALAKASIZ fazladan
    # yuksekligi kapsamak icin gereksiz BOS sayfalar ekliyordu
    # (kullanicinin bildirdigi tam olarak bu). Bu test, HIDE_NAVIGATION_
    # CHROME_CSS'in gercek DOM'dan alinan sidebar/buton yapisini dogru
    # gizledigini VE asil sinav icerigine (soru metni) DOKUNMADIGINI
    # gercek Chrome'da dogruluyor.
    html = """
    <html><body>
      <div id="content">Python üzerinde ISU isimli sanal ortam nasıl oluşturulur?</div>
      <h2 class="js-selectAttemptInputValueText">Gönderim tarihi: 18.03.2026 17:29</h2>
      <button aria-label="Geri Bildirim - Soru 1">fb</button>
      <button aria-label="Soru 1 ile ilgili diğer seçenekler">opt</button>
      <a aria-label="Önceki Öğrenci">prev</a>
      <a aria-label="Sonraki Öğrenci">next</a>
      <ul role="menu" aria-label="Öğrenciler"><li>YUNUS AKSU</li></ul>
    </body></html>
    """
    kwargs = common.browser_launch_kwargs()
    kwargs["headless"] = True
    kwargs.pop("viewport", None)

    with sync_playwright() as p:
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page()
            page.set_content(html)
            page.add_style_tag(content=HIDE_NAVIGATION_CHROME_CSS)
            hidden = {
                "feedback": page.get_by_label("Geri Bildirim - Soru 1").is_visible(),
                "options": page.get_by_label("Soru 1 ile ilgili diğer seçenekler").is_visible(),
                "prev": page.get_by_label("Önceki Öğrenci").is_visible(),
                "next": page.get_by_label("Sonraki Öğrenci").is_visible(),
                "sidebar": page.get_by_role("menu", name="Öğrenciler").is_visible(),
            }
            content_visible = page.locator("#content").is_visible()
            # CANLI DOGRULANAN HATA: bu class'in yanlislikla HIDE_NAVIGATION_
            # CHROME_CSS'e eklenmesi denendi - ama bu class gezinme cop'u
            # DEGIL, "Gönderim tarihi" metnini tasiyan asil eleman. Gizlenirse
            # hem PDF'te tarih kaybolur hem de extract_page_info'nun
            # gonderim_tarihi alani hep None doner. Bu regresyon testi bir
            # daha yanlislikla gizlenmedigini garanti eder.
            submit_date_visible = page.locator(".js-selectAttemptInputValueText").is_visible()
        finally:
            browser.close()

    assert not any(hidden.values()), hidden
    assert content_visible
    assert submit_date_visible


@requires_chrome
def test_force_visible_css_does_not_leak_screen_reader_only_text():
    # CANLI DOGRULANAN HATA: kullanicinin paylastigi gercek bir PDF'te,
    # FORCE_VISIBLE_CSS'in `* { overflow: visible !important }` kurali
    # Blackboard'un ekran-okuyucuya-ozel ("hideOffScreen" sinifli, normalde
    # 1x1px + overflow:hidden ile gizlenen) etiketlerini de "gorunur"
    # yapiyordu - sonuc, TUM diger ogrencilerin gizli not etiketlerinden
    # gelen sayilarin ("60 80 50 60 60 40 40 ...") sinavin GERCEK
    # icerigiyle CAKISARAK her sayfanin ustunde tekrar tekrar basilmasiydi.
    # Bu test, FORCE_VISIBLE_CSS uygulandiktan SONRA bile bu sinifin
    # orijinal (1x1px + clip + overflow:hidden) gizleme teknigini
    # KORUDUGUNU gercek Chrome'da dogruluyor.
    html = """
    <html><body>
      <div id="content">Gerçek soru metni burada</div>
      <div class="makeStyleshideOffScreen-0-2-443" id="hidden-label">
        Son Not: 50 puan (100 puan üzerinden)
      </div>
    </body></html>
    """
    kwargs = common.browser_launch_kwargs()
    kwargs["headless"] = True
    kwargs.pop("viewport", None)

    with sync_playwright() as p:
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page()
            page.set_content(html)
            page.add_style_tag(content=FORCE_VISIBLE_CSS)
            style = page.eval_on_selector(
                "#hidden-label",
                "el => { const s = getComputedStyle(el); "
                "return {overflow: s.overflow, width: s.width, height: s.height}; }",
            )
            content_visible = page.locator("#content").is_visible()
        finally:
            browser.close()

    assert style["overflow"] == "hidden"
    assert style["width"] == "1px"
    assert style["height"] == "1px"
    assert content_visible


@requires_chrome
def test_force_visible_css_neutralizes_fixed_position_for_print():
    # CANLI DOGRULANAN HATA: `hideOffScreen` duzeltmesinden SONRA bile,
    # kullanicinin paylastigi bir sonraki PDF'te AYNI sayi dizisi HER
    # SAYFANIN TEPESINDE tekrar tekrar basiliyordu. Kok sebep farkliydi:
    # `position: fixed` olan bir eleman (ör. sol "Öğrenciler" paneli),
    # dokuman akisindan bagimsiz oldugu icin Chrome'un yazdirma motoru
    # tarafindan HER SAYFADA yeniden ciziliyor - bilinen, yaygin bir
    # Chrome print davranisi. FORCE_VISIBLE_CSS artik TUM fixed/sticky
    # konumlandirmayi `static`e zorluyor (hangi eleman oldugunu bilmeye
    # gerek kalmadan kokten cozum). Bu test hem bunu HEM DE hideOffScreen
    # elemaninin position:absolute'unun bu genel kuraldan ETKILENMEDIGINI
    # (daha spesifik secici kazaniyor) gercek Chrome'da dogruluyor.
    html = """
    <html><body>
      <div id="content">Gerçek soru metni burada</div>
      <div id="fixed-sidebar" style="position: fixed; top: 0; left: 0;">
        60 80 50
      </div>
      <div class="makeStyleshideOffScreen-0-2-443" id="hidden-label">
        Son Not: 50 puan
      </div>
    </body></html>
    """
    kwargs = common.browser_launch_kwargs()
    kwargs["headless"] = True
    kwargs.pop("viewport", None)

    with sync_playwright() as p:
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page()
            page.set_content(html)
            page.add_style_tag(content=FORCE_VISIBLE_CSS)
            sidebar_position = page.eval_on_selector(
                "#fixed-sidebar", "el => getComputedStyle(el).position"
            )
            hidden_label_position = page.eval_on_selector(
                "#hidden-label", "el => getComputedStyle(el).position"
            )
        finally:
            browser.close()

    assert sidebar_position == "static"
    assert hidden_label_position == "absolute"


@requires_chrome
def test_force_visible_css_zeroes_min_height_on_any_wrapper_not_just_html_body():
    # CANLI DOGRULANAN HATA: sol panel gizlenip fixed/sticky static'e
    # cevrildikten SONRA bile, PDF'in sonunda BOS sayfalar kalmaya devam
    # ediyordu. Sebep: html/body'ye ozel yapilan `min-height: 0`, React
    # uygulama kok konteyneri gibi ARADAKI (isimsiz/bilinmeyen) bir
    # sarmalayicinin KENDI `min-height: 100vh` ayarini KAPSAMIYORDU - o
    # sarmalayici asil icerikten cok daha uzun kalip Chrome'un yazdirma
    # motorunun bu fazladan BOS alani kapsamak icin ekstra sayfa
    # ayirmasina yol aciyordu. Bu test, artik `min-height: 0`'in TUM
    # elemanlara (`*`) uygulandigini ve boylece boyle bir sarmalayicinin
    # gercek olcculmus yuksekliginin, icerigi kadar KISALDIGINI (100vh
    # degil) gercek Chrome'da dogruluyor.
    html = """
    <html><body style="margin:0;">
      <div id="wrapper" style="min-height: 100vh; box-sizing: border-box;">
        <div id="content">Tek satırlık gerçek içerik</div>
      </div>
    </body></html>
    """
    kwargs = common.browser_launch_kwargs()
    kwargs["headless"] = True
    kwargs.pop("viewport", None)

    with sync_playwright() as p:
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page()
            page.set_content(html)
            before = page.eval_on_selector("#wrapper", "el => el.getBoundingClientRect().height")
            page.add_style_tag(content=FORCE_VISIBLE_CSS)
            after = page.eval_on_selector("#wrapper", "el => el.getBoundingClientRect().height")
        finally:
            browser.close()

    assert before >= 500  # 100vh ~ viewport yüksekliği (varsayılan viewport çok daha büyük)
    assert after < 100  # artık sadece gerçek içerik kadar (min-height dayatması yok)


@requires_chrome
def test_reset_scroll_after_capture_preserves_student_panel_position():
    # CANLI DOGRULANAN HATA: PDF yakalamasindan sonraki kaydirma-sifirlama
    # adimi, once TUM elemanlarin (sol "Öğrenciler" listesi DAHIL)
    # scrollTop'unu 0'a ceviriyordu. Sonuc: 18. ogrenci yakalandiktan
    # sonra panel BASTAN gorunmeye basliyor, 19. ogrenciye ulasmak icin
    # panel her seferinde yeniden asagi kaydirilmasi gerekiyordu (bkz.
    # kullanicinin canli gozlemi). Bu test, RESET_SCROLL_AFTER_CAPTURE_JS
    # calistiktan sonra "Öğrenciler" listesinin KENDI kaydirma konumunun
    # KORUNDUGUNU, ama sinav icerigi gibi BASKA bir kaydirilabilir
    # alanin YINE DE sifirlandigini gercek Chrome'da dogruluyor.
    html = """
    <html><body>
      <ul role="menu" aria-label="Öğrenciler"
          style="height:100px; overflow-y:auto; display:block;">
        <li style="height:400px;">öğrenci listesi</li>
      </ul>
      <div id="content-scroll" style="height:100px; overflow-y:auto; display:block;">
        <div style="height:400px;">sınav içeriği</div>
      </div>
    </body></html>
    """
    kwargs = common.browser_launch_kwargs()
    kwargs["headless"] = True
    kwargs.pop("viewport", None)

    with sync_playwright() as p:
        browser = p.chromium.launch(**kwargs)
        try:
            page = browser.new_page()
            page.set_content(html)
            page.eval_on_selector('[role="menu"]', "el => { el.scrollTop = 200; }")
            page.eval_on_selector("#content-scroll", "el => { el.scrollTop = 200; }")
            page.evaluate(RESET_SCROLL_AFTER_CAPTURE_JS)
            panel_scroll = page.eval_on_selector('[role="menu"]', "el => el.scrollTop")
            content_scroll = page.eval_on_selector("#content-scroll", "el => el.scrollTop")
        finally:
            browser.close()

    assert panel_scroll == 200
    assert content_scroll == 0


def test_is_chrome_missing_error_recognizes_real_playwright_message_format():
    # Chrome makinede GERCEKTEN yoksa Playwright'in verdigi mesaj kalibi
    # (Chrome kurulu olmasa bile bu test her zaman calisir).
    exc = RuntimeError(
        "BrowserType.launch: Chromium distribution 'chrome' is not found at "
        "/path/to/chrome\nRun \"playwright install chrome\""
    )

    assert common.is_chrome_missing_error(exc)
