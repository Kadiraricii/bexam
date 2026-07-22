"""common.py icin birim testler - gercek bir tarayici/Blackboard oturumu
GEREKTIRMEYEN saf mantik (dosya adi temizleme, hata siniflandirma, URL
dogrulama, Windows MAX_PATH kirpma). Gercek Playwright/Chrome gerektiren
testler icin bkz. test_browser_launch.py.

Bazi testler bu oturumda GERCEKTEN yasanan, canli dogrulanan hatalarin
regresyon testleridir (ör. SSO'nun page.url'i sabitleyip birakmasi,
Playwright'in sessizce --no-sandbox eklemesi) - bunlar yorumlarda acikca
belirtilmistir.
"""

import platform

import pytest

import common
from fakes import FakeContext, FakePage, FakeTitledPage

# ---------- sanitize_filename ----------


def test_sanitize_filename_replaces_windows_forbidden_characters():
    result = common.sanitize_filename('sinav<>:"/\\|?*adi')

    assert result == "sinav_________adi"


def test_sanitize_filename_appends_suffix_for_windows_reserved_name():
    result = common.sanitize_filename("CON")

    assert result == "CON_dosya"


def test_sanitize_filename_reserved_name_check_is_case_insensitive():
    result = common.sanitize_filename("aux")

    assert result == "aux_dosya"


def test_sanitize_filename_strips_trailing_dot_and_space():
    # Windows dosya adi SONUNDA nokta/bosluk kabul etmiyor.
    result = common.sanitize_filename("Final Sinavi. ")

    assert result == "Final Sinavi"


def test_sanitize_filename_truncates_to_max_chars():
    result = common.sanitize_filename("a" * 200, max_chars=10)

    assert len(result) <= 10


def test_sanitize_filename_blank_input_returns_placeholder():
    result = common.sanitize_filename("   ")

    assert result == "adsiz"


def test_sanitize_filename_preserves_turkish_characters():
    result = common.sanitize_filename("Öğrenci Çalışması İ.Ş.Ü")

    assert "ğ" in result and "İ" in result


# ---------- ensure_safe_full_path (Windows MAX_PATH) ----------


def test_ensure_safe_full_path_leaves_short_path_unchanged(tmp_path):
    short_path = tmp_path / "kisa_dosya.pdf"

    result = common.ensure_safe_full_path(short_path)

    assert result == short_path


def test_ensure_safe_full_path_shortens_path_exceeding_windows_limit(tmp_path):
    long_path = tmp_path / (("a" * 300) + ".pdf")

    result = common.ensure_safe_full_path(long_path)

    assert len(str(result)) <= common.WINDOWS_SAFE_PATH_LIMIT
    assert result.suffix == ".pdf"


def test_ensure_safe_full_path_protects_suffix_characters(tmp_path):
    # ONAY kodu (kimlik belirleyici kisim) HER ZAMAN korunmali, sadece
    # onceki aciklayici baslikdan kirpma yapilmali.
    onay = "ABC123"
    long_path = tmp_path / (("x" * 300) + f"_{onay}.pdf")

    result = common.ensure_safe_full_path(long_path, protect_suffix_chars=len(onay) + 1)

    assert result.stem.endswith(f"_{onay}")


@pytest.mark.skipif(platform.system() != "Windows", reason="MAX_PATH sadece Windows'ta gercek bir sinir")
def test_ensure_safe_full_path_allows_real_file_write_on_windows(tmp_path):
    onay = "ONAY123"
    long_path = tmp_path / (("cok-uzun-sinav-basligi-" * 15) + f"_{onay}.pdf")

    safe_path = common.ensure_safe_full_path(long_path, protect_suffix_chars=len(onay) + 1)
    safe_path.write_bytes(b"%PDF-1.4 test")

    assert safe_path.exists()


# ---------- hata siniflandirma ----------


def test_is_browser_closed_error_recognizes_real_crash_message():
    # Bu proje uzerinde GERCEKTEN alinan bir traceback'teki BIREBIR mesaj
    # (wait_for_blackboard sirasinda tarayici kapatilinca olusuyordu).
    exc = RuntimeError("Page.wait_for_timeout: Target page, context or browser has been closed")

    assert common.is_browser_closed_error(exc)


def test_is_browser_closed_error_ignores_unrelated_errors():
    assert not common.is_browser_closed_error(RuntimeError("selector bulunamadi"))


def test_is_chrome_missing_error_recognizes_real_playwright_message():
    exc = RuntimeError(
        "BrowserType.launch: Chromium distribution 'chrome' is not found at ...\n"
        'Run "playwright install chrome"'
    )

    assert common.is_chrome_missing_error(exc)


def test_is_chrome_missing_error_ignores_unrelated_errors():
    assert not common.is_chrome_missing_error(RuntimeError("net::ERR_NAME_NOT_RESOLVED"))


def test_is_profile_lock_error_recognizes_singleton_lock_message():
    assert common.is_profile_lock_error("SingletonLock already exists, failed to create a Chrome process")


def test_is_profile_lock_error_accepts_exception_instance():
    assert common.is_profile_lock_error(RuntimeError("user data directory is already in use"))


# ---------- live_url / SSO onbellek-kopmasi duzeltmesi ----------
# Bu grup, oturum icinde CANLI DOGRULANAN bir hatanin regresyon testidir:
# Azure AD SSO'nun capraz-kaynak yonlendirme zincirinde Playwright'in
# page.url onbellegi eski bir Microsoft giris URL'sinde TAKILI KALIP bir
# daha guncellenmiyordu - "Bul ve Tara" bu yuzden kullanici gercekten
# giris yapmis olsa bile surekli basarisiz oluyordu.


def test_live_url_prefers_live_javascript_over_cached_property():
    page = FakePage(
        live_url_value="https://istinye.blackboard.com/ultra/stream",
        cached_url="https://login.microsoftonline.com/stale",
    )

    assert common.live_url(page) == "https://istinye.blackboard.com/ultra/stream"


def test_live_url_falls_back_to_cached_url_when_evaluate_fails():
    page = FakePage(
        live_url_exc=RuntimeError("Execution context was destroyed"),
        cached_url="https://istinye.blackboard.com/x",
    )

    assert common.live_url(page) == "https://istinye.blackboard.com/x"


def test_live_url_returns_empty_string_when_everything_fails():
    page = FakePage(live_url_exc=RuntimeError("a"), cached_url_exc=RuntimeError("b"))

    assert common.live_url(page) == ""


def test_wait_for_blackboard_succeeds_despite_stuck_cached_url():
    # Gercek SSO hatasinin senaryosu: .url SAML sayfasinda TAKILI ama
    # canli JS durumu (evaluate) zaten Blackboard'a gecmis.
    page = FakePage(
        live_url_value="https://istinye.blackboard.com/ultra/stream",
        cached_url="https://login.microsoftonline.com/saml2?SAMLRequest=...",
    )

    assert common.wait_for_blackboard(page, attempts=1) is True


def test_wait_for_blackboard_propagates_browser_closed_error_instead_of_swallowing_it():
    # gui.py'nin bunu ozel olarak "browser_lost" diye ayirt edebilmesi
    # icin wait_for_blackboard hatayi YUTMAMALI, cagirana sizdirmali.
    page = FakePage(
        live_url_exc=RuntimeError("bulunamadi"),
        cached_url="https://login.microsoftonline.com/x",
        wait_exc=RuntimeError("Page.wait_for_timeout: Target page, context or browser has been closed"),
    )

    with pytest.raises(RuntimeError) as exc_info:
        common.wait_for_blackboard(page, attempts=2, delay_ms=1)

    assert common.is_browser_closed_error(exc_info.value)


def test_resolve_active_page_finds_blackboard_tab_via_live_url():
    stuck_page = FakePage(live_url_value="https://login.microsoftonline.com/x")
    real_page = FakePage(live_url_value="https://istinye.blackboard.com/ultra/stream")
    context = FakeContext([stuck_page, real_page])

    assert common.resolve_active_page(context) is real_page


def test_resolve_active_page_falls_back_to_last_reachable_when_none_match():
    p1 = FakePage(live_url_value="https://example.com/a")
    p2 = FakePage(live_url_value="https://example.com/b")
    context = FakeContext([p1, p2])

    assert common.resolve_active_page(context) is p2


def test_resolve_active_page_skips_pages_that_error_without_stopping_the_scan():
    broken = FakePage(live_url_exc=RuntimeError("kapandi"), cached_url_exc=RuntimeError("kapandi"))
    real_page = FakePage(live_url_value="https://istinye.blackboard.com/x")
    context = FakeContext([broken, real_page])

    assert common.resolve_active_page(context) is real_page


def test_find_blackboard_pages_returns_all_matches_not_just_first():
    bb1 = FakePage(live_url_value="https://istinye.blackboard.com/a")
    other = FakePage(live_url_value="https://example.com")
    bb2 = FakePage(live_url_value="https://istinye.blackboard.com/b")
    context = FakeContext([bb1, other, bb2])

    assert common.find_blackboard_pages(context) == [bb1, bb2]


# ---------- browser_launch_kwargs (guvenlik: --no-sandbox regresyonu) ----------


def test_browser_launch_kwargs_enables_chromium_sandbox():
    # Playwright, chromium_sandbox ACIKCA True verilmedikce sessizce
    # --no-sandbox ekliyor (bu oturumda bulunup duzeltilen gercek bir
    # guvenlik regresyonu).
    kwargs = common.browser_launch_kwargs()

    assert kwargs["chromium_sandbox"] is True


def test_browser_launch_kwargs_uses_real_chrome_channel():
    kwargs = common.browser_launch_kwargs()

    assert kwargs["channel"] == "chrome"


def test_browser_launch_kwargs_only_hides_automation_infobar_not_sandbox():
    kwargs = common.browser_launch_kwargs()

    assert "--enable-automation" in kwargs["ignore_default_args"]
    assert "--no-sandbox" not in kwargs["args"]


# ---------- diger yardimcilar ----------


def test_normalize_score_strips_whitespace_and_normalizes_decimal_separator():
    assert common.normalize_score(" 50 , 5 / 100 ") == "50.5/100"


def test_extract_page_info_parses_onay_tarih_and_puan():
    text = "Bir Sinav\nSon Not\nGÖNDERİM TARİHİ: 01.01.2026 10:00\nONAY: ABC123   50 / 100"

    info = common.extract_page_info(text)

    assert info["baslik"] == "Bir Sinav"
    assert info["onay"] == "ABC123"
    assert info["gonderim_tarihi"] == "01.01.2026 10:00"
    assert info["puan"] == "50 / 100"


def test_extract_page_info_missing_fields_returns_none_values():
    info = common.extract_page_info("ilgisiz bir metin")

    assert info["onay"] is None
    assert info["puan"] is None
    assert info["gonderim_tarihi"] is None


def test_derive_course_label_uses_last_slash_segment():
    page = FakeTitledPage("Blackboard / BST020 / Not Defteri")

    assert common.derive_course_label(page) == "Not Defteri"


def test_derive_course_label_falls_back_when_title_empty():
    page = FakeTitledPage("")

    assert common.derive_course_label(page) == "ders"


def test_cloud_sync_warning_detects_onedrive_path():
    from pathlib import Path

    warning = common.cloud_sync_warning(Path("C:/Users/x/OneDrive/Belgeler/output"))

    assert warning is not None


def test_cloud_sync_warning_none_for_local_path(tmp_path):
    assert common.cloud_sync_warning(tmp_path) is None


def test_check_output_writable_returns_none_for_writable_dir(tmp_path):
    assert common.check_output_writable(tmp_path) is None


def test_check_output_writable_creates_missing_directory(tmp_path):
    target = tmp_path / "yeni_klasor"

    result = common.check_output_writable(target)

    assert result is None
    assert target.exists()


def test_append_log_and_read_log_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "LOG_PATH", tmp_path / "captures.json")

    common.append_log({"baslik": "Test Sinavi", "onay": "X1"})
    entries = common.read_log()

    assert entries == [{"baslik": "Test Sinavi", "onay": "X1"}]


def test_append_log_handles_turkish_characters_correctly():
    # Windows'ta varsayilan yerel kodlama (ör. cp1254) kullanilsaydi bu
    # UnicodeEncodeError firlatir ya da veriyi bozardi - encoding="utf-8"
    # acikca belirtildigi icin platform BAGIMSIZ calismali.
    import tempfile
    from pathlib import Path as _Path

    with tempfile.TemporaryDirectory() as tmp:
        log_path = _Path(tmp) / "captures.json"
        original = common.LOG_PATH
        common.LOG_PATH = log_path
        try:
            common.append_log({"baslik": "Öğrenci Çalışması - İstanbul Ğ.Ş.Ü"})
            entries = common.read_log()
        finally:
            common.LOG_PATH = original

    assert entries[0]["baslik"] == "Öğrenci Çalışması - İstanbul Ğ.Ş.Ü"


def test_already_captured_titles_ignores_entries_with_missing_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "LOG_PATH", tmp_path / "captures.json")
    missing_pdf = tmp_path / "yok.pdf"
    common.append_log({"baslik": "Var Olmayan", "pdf": str(missing_pdf)})

    assert common.already_captured_titles() == set()


def test_already_captured_titles_includes_entries_with_existing_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "LOG_PATH", tmp_path / "captures.json")
    real_pdf = tmp_path / "var.pdf"
    real_pdf.write_bytes(b"%PDF-1.4")
    common.append_log({"baslik": "Var Olan", "pdf": str(real_pdf)})

    assert common.already_captured_titles() == {"Var Olan"}


def test_has_seen_onboarding_and_mark_onboarding_seen_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "ONBOARDING_SEEN_PATH", tmp_path / "onboarding_seen")

    assert common.has_seen_onboarding() is False

    common.mark_onboarding_seen()

    assert common.has_seen_onboarding() is True


# open_in_file_manager testleri BILEREK subprocess.run/os.startfile'i
# MOCK'LUYOR, GERCEKTEN cagirmiyor - erken bir surumde bu testler
# gercekten Finder/Explorer/TextEdit acip gelistiricinin ekraninda
# beklenmedik bir pencere belirmesine yol aciyordu (CI'da ephemeral
# runner'larda zararsiz olsa da, yerel `pytest` calistirmalarinda
# rahatsiz edici ve saskirtici bir yan etkiydi). Mock'lu yaklasim,
# GERCEK bir pencere hic acmadan dogru komutun dogru platformda
# cagrildigini ayni kesinlikte dogruluyor.


def test_open_in_file_manager_uses_os_startfile_on_windows(monkeypatch, tmp_path):
    import platform as _platform

    target = tmp_path / "dosya.txt"
    target.write_text("test")
    monkeypatch.setattr(_platform, "system", lambda: "Windows")
    monkeypatch.setattr(common, "platform", _platform)
    started = []
    monkeypatch.setattr("os.startfile", lambda p: started.append(p), raising=False)

    common.open_in_file_manager(target)

    assert started == [str(target)]


def test_open_in_file_manager_uses_open_command_on_macos(monkeypatch, tmp_path):
    import platform as _platform

    target = tmp_path / "dosya.txt"
    target.write_text("test")
    monkeypatch.setattr(_platform, "system", lambda: "Darwin")
    monkeypatch.setattr(common, "platform", _platform)
    calls = []
    monkeypatch.setattr(common.subprocess, "run", lambda args, **kwargs: calls.append(args))

    common.open_in_file_manager(target)

    assert calls == [["open", str(target)]]


def test_open_in_file_manager_uses_xdg_open_on_linux(monkeypatch, tmp_path):
    import platform as _platform

    target = tmp_path / "dosya.txt"
    target.write_text("test")
    monkeypatch.setattr(_platform, "system", lambda: "Linux")
    monkeypatch.setattr(common, "platform", _platform)
    calls = []
    monkeypatch.setattr(common.subprocess, "run", lambda args, **kwargs: calls.append(args))

    common.open_in_file_manager(target)

    assert calls == [["xdg-open", str(target)]]
