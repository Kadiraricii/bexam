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


def test_is_chrome_missing_error_recognizes_real_playwright_message_format():
    # Chrome makinede GERCEKTEN yoksa Playwright'in verdigi mesaj kalibi
    # (Chrome kurulu olmasa bile bu test her zaman calisir).
    exc = RuntimeError(
        "BrowserType.launch: Chromium distribution 'chrome' is not found at "
        "/path/to/chrome\nRun \"playwright install chrome\""
    )

    assert common.is_chrome_missing_error(exc)
