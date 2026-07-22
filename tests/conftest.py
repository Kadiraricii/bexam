"""GUI (Tkinter) testleri icin ortak pytest fixture'lari.

Gercek bir pencere sistemi (display) gerektirirler - macOS ve Windows
GitHub Actions runner'lari bunu etkilesimli oturumda saglar (Linux'un
aksine Xvfb GEREKMEZ). Yine de savunma amacli: display gercekten yoksa
testler HATA yerine SKIP ile gecilir - bu CI/ortam sorunudur, kod hatasi
degildir.
"""

import tkinter as tk

import pytest

import gui as gui_module


class _FakeChromium:
    """gui.py'nin worker thread'inin GERCEK bir tarayici/surucu surecine
    ihtiyaci OLMADAN "open_browser" disindaki komutlari (discover,
    download, quit, bos-zaman dongusu) sinayabilmesi icin sahte
    Playwright.chromium nesnesi."""

    def launch_persistent_context(self, *_args, **_kwargs):
        raise RuntimeError(
            "Bu test ortaminda gercek tarayici baslatilamiyor (sahte "
            "Playwright fixture'i) - gercek tarayici gerektiren testler "
            "icin bkz. test_browser_launch.py."
        )


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeChromium()


class _FakeSyncPlaywrightContextManager:
    def __enter__(self) -> _FakePlaywright:
        return _FakePlaywright()

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


def _fake_sync_playwright() -> _FakeSyncPlaywrightContextManager:
    return _FakeSyncPlaywrightContextManager()


@pytest.fixture(scope="session")
def tk_root():
    """TUM test oturumu icin TEK, paylasilan bir Tk kok penceresi.

    Once her test kendi tk.Tk() ornegini olusturup yok ediyordu - bu
    CANLI DOGRULANDI ki gercek Windows GitHub Actions runner'inda
    "Windows fatal exception: code 0x80000003" ile butun test surecini
    cokertiyor (tam olarak tk.Tk() + threading.Thread.start() ikilisinin
    hizli ardisik tekrarinda, ikinci testin fixture kurulumu sirasinda -
    macOS'ta ayni kokten farkli bir belirtiyle - "Fatal Python error:
    Aborted" - benzer bir kararsizlik zaten yerelde de gozlemlendi).
    Tek bir Tcl yorumlayicisini oturum boyunca paylasmak bu kategoriyi
    tamamen ortadan kaldiriyor. Her testin KENDI BlackboardGUI'si (ve
    worker thread'i) hala ayri ayri olusturulup TEMIZCE kapatiliyor -
    bkz. gui_app fixture'i; sadece alttaki Tk penceresinin KENDISI
    paylasiliyor."""
    try:
        root = tk.Tk()
        root.withdraw()  # ekranda gorunmesin, sadece gercekten olusabildigini dogruluyoruz
    except tk.TclError as exc:
        pytest.skip(f"Tk penceresi oluşturulamadı (ortam sorunu olabilir): {exc}")
    yield root
    try:
        root.destroy()
    except tk.TclError:
        pass


@pytest.fixture
def gui_app(tk_root, monkeypatch):
    """Kurulu bir BlackboardGUI ornegi.

    Testler HER ZAMAN "onboarding henuz gorulmedi" durumundan baslar -
    gercek .state/onboarding_seen dosyasi (gelistiricinin kendi
    makinesinde varsa) test sonucunu ETKILEMESIN, testler de o dosyaya
    YANLISLIKLA kalici olarak yazmasin diye has_seen_onboarding/
    mark_onboarding_seen bu fixture icinde izole ediliyor.

    sync_playwright DE sahte surumle degistiriliyor: bu smoke testleri
    SADECE Tkinter widget kurulumunu/duzenini dogruluyor, gercek bir
    tarayiciya hic ihtiyaclari yok. Her testte GERCEK bir
    sync_playwright() surucu sureci baslatip kapatmak hem gereksiz
    yavaslik hem de CANLI DOGRULANMIS bir kararsizliga yol aciyordu:
    onlarca testte art arda hizla baslatilip durdurulan gercek Playwright
    ornekleri, teardown sirasinda worker_thread.join() esnasinda
    "Fatal Python error: Aborted" ile butun test surecini cokertiyordu
    (Playwright'in sync API'si tek thread + tek Playwright nesnesi icin
    tasarlanmis, hizli ardisik cok sayida ornek olusturup yok etmeye
    guvenilir sekilde dayanmiyor). Gercek tarayici gerektiren testler
    icin bkz. test_browser_launch.py (bu fixture'i KULLANMIYORLAR).
    """
    monkeypatch.setattr(gui_module, "has_seen_onboarding", lambda: False)
    monkeypatch.setattr(gui_module, "mark_onboarding_seen", lambda: None)
    monkeypatch.setattr(gui_module, "sync_playwright", _fake_sync_playwright)

    app = gui_module.BlackboardGUI(tk_root)
    yield app

    try:
        app.command_queue.put(("quit", None))
        app.worker_thread.join(timeout=10)
    except Exception:
        pass
    # tk_root artik OTURUM boyunca PAYLASILIYOR (tek Tk penceresi) - bu
    # yuzden her testin KENDI ust-duzey container'ini burada acikca yok
    # etmemiz gerekiyor, aksi halde ardisik testlerin widget agaclari
    # ayni kok altinda ustuste birikir.
    try:
        app.container.destroy()
    except tk.TclError:
        pass


def find_widgets(widget, predicate, acc=None):
    """widget alt agacinda predicate(w) True donen tum widget'lari toplar."""
    if acc is None:
        acc = []
    if predicate(widget):
        acc.append(widget)
    for child in widget.winfo_children():
        find_widgets(child, predicate, acc)
    return acc


def find_labels_containing(widget, needle, acc=None):
    def _matches(w):
        if not isinstance(w, tk.Label):
            return False
        try:
            return needle in w.cget("text")
        except tk.TclError:
            return False

    return find_widgets(widget, _matches, acc)


def is_currently_packed(widget) -> bool:
    """widget su an bir geometry manager (ör. .pack()) tarafindan
    yonetiliyor mu - yani .pack_forget() EDILMEMIS mi.

    winfo_ismapped() KULLANILMIYOR: o, widget'in GERCEKTEN ekranda
    goruntulendigini (pencere haritalanmis/viewable) kontrol eder - test
    ortaminda pencereyi withdraw() ile gizlersek (bkz. tk_root fixture,
    testler sirasinda ekranda pencere yanip sonmesin diye) TUM widget'lar
    icin HER ZAMAN False doner, gercek pack/pack_forget durumundan
    BAGIMSIZ olarak - bu da yanlis-pozitif/yanlis-negatif testlere yol
    acar. winfo_manager(), pencerenin ekranda gorunur olup olmadigindan
    BAGIMSIZ, sadece "bu widget su an bir layout'a dahil mi" sorusunu
    dogru cevaplar."""
    return widget.winfo_manager() != ""


def find_button_with_text(widget, text):
    matches = find_widgets(
        widget,
        lambda w: isinstance(w, gui_module.RoundedButton) and w._text == text,
    )
    return matches[0] if matches else None
