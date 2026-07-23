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
from scan_students import write_student_roster_csv

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


def test_sanitize_filename_preserves_parentheses_and_other_safe_punctuation():
    """CANLI DOGRULANAN gercek bug: Blackboard'in birden fazla sube/donem
    oldugunda derse verdigi ad ("BST020 - Veri Madenciliği (1)" gibi)
    eskiden parantezleri alt cizgiye ceviriyordu ("_1_"), halbuki parantez
    ne Windows'ta ne macOS/Linux'ta gecersiz bir karakter - sadece
    WINDOWS_FORBIDDEN_CHARS_PATTERN'daki 9 karakter (+kontrol karakterleri)
    gercekten temizlenmeli, geri kalan noktalama korunmali."""
    result = common.sanitize_filename("BST020 - Veri Madenciliği (1)")

    assert result == "BST020 - Veri Madenciliği (1)"


def test_sanitize_filename_still_replaces_genuinely_forbidden_characters():
    # Parantez/virgul gibi guvenli noktalama korunurken, GERCEKTEN yasak
    # olanlarin (bkz. test_sanitize_filename_replaces_windows_forbidden_
    # characters) hala temizlendiginden emin oluyoruz - regresyon olmasin.
    result = common.sanitize_filename("Sınav: Bölüm 1/2 (Final)")

    assert result == "Sınav_ Bölüm 1_2 (Final)"


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


def test_format_student_pdf_stem_uses_exam_number_and_name_format():
    stem = common.format_student_pdf_stem("Kısa Sınav 1", "Mehmet Kadir Arıcı", "2420191035")

    assert stem == "Kısa Sınav 1_2420191035_Mehmet-Kadir-Arıcı"


def test_format_student_pdf_stem_omits_student_number_when_unknown():
    stem = common.format_student_pdf_stem("Kısa Sınav 1", "Mehmet Kadir Arıcı", None)

    assert stem == "Kısa Sınav 1_Mehmet-Kadir-Arıcı"


def test_format_student_pdf_stem_joins_multiple_given_names_with_dashes():
    stem = common.format_student_pdf_stem("Final", "Ali Veli Yılmaz Öztürk", "123456")

    assert stem == "Final_123456_Ali-Veli-Yılmaz-Öztürk"


def test_load_student_roster_returns_empty_dict_when_csv_missing(tmp_path):
    assert common.load_student_roster(tmp_path) == {}


def test_load_student_roster_reads_semicolon_delimited_csv(tmp_path):
    csv_path = tmp_path / common.STUDENT_ROSTER_CSV_FILENAME
    csv_path.write_text(
        "Ad Soyad;Öğrenci Numarası\r\nMEHMET KADİR ARICI;2420191035\r\n",
        encoding="utf-8-sig",
    )

    roster = common.load_student_roster(tmp_path)

    assert roster == {common.normalize_roster_name("MEHMET KADİR ARICI"): "2420191035"}


def test_load_student_roster_excludes_duplicate_names_entirely(tmp_path):
    """Iki FARKLI gercek ogrenci tesaduf eseri ayni ad-soyada sahipse,
    isimden numaraya YANLIS eslesme riskine karsi (bkz. konusma gecmisi)
    o isim SONUCA HIC DAHIL EDILMEMELI - ikisine de rastgele/yanlis bir
    numara atamaktansa, ikisine de 'numara bilinmiyor' demek guvenli olan."""
    csv_path = tmp_path / common.STUDENT_ROSTER_CSV_FILENAME
    csv_path.write_text(
        "Ad Soyad;Öğrenci Numarası\r\n"
        "AHMET YILMAZ;2420171001\r\n"
        "AHMET YILMAZ;2520161055\r\n"
        "MEHMET KADİR ARICI;2420191035\r\n",
        encoding="utf-8-sig",
    )

    roster = common.load_student_roster(tmp_path)

    # Tekrar eden "AHMET YILMAZ" TAMAMEN yok - ne ilk ne ikinci numarasi
    # sonuca girmis. Tekrarsiz "MEHMET KADİR ARICI" normal sekilde var.
    assert roster == {common.normalize_roster_name("MEHMET KADİR ARICI"): "2420191035"}


def test_write_student_roster_csv_round_trips_through_load_student_roster(tmp_path):
    """En degerli test bu: write_student_roster_csv'nin URETTIGI dosyayi
    load_student_roster'in GERCEKTEN geri okuyabildigini dogrular - ayirac/
    baslik ikisinde de AYNI olmazsa (tam da az once duzeltilen hata
    sinifi) bu test yakalar, iki fonksiyon birbirinden bagimsiz
    degistirilip sessizce birbirinden kopabilir."""
    csv_path = tmp_path / common.STUDENT_ROSTER_CSV_FILENAME
    write_student_roster_csv(
        [("MEHMET KADİR ARICI", "2420191035"), ("AYŞE YILMAZ", "2420171001")], csv_path,
    )

    roster = common.load_student_roster(tmp_path)

    assert roster == {
        common.normalize_roster_name("MEHMET KADİR ARICI"): "2420191035",
        common.normalize_roster_name("AYŞE YILMAZ"): "2420171001",
    }


def test_load_student_roster_returns_empty_dict_for_comma_delimited_csv(tmp_path):
    """Yazici artik ';' kullaniyor (bkz. scan_students.write_student_roster_csv) -
    eski ',' ile ayrilmis bir dosya (ör. gecmis bir surumden kalma) her
    satiri TEK alan olarak okutur, len(row) < 2 kontrolu hepsini eler.
    Bu, iki tarafin AYNI ayiraci kullanmasi gerektigini kanitlayan bir
    regresyon testi - cokme yerine sessizce bos donmeli."""
    csv_path = tmp_path / common.STUDENT_ROSTER_CSV_FILENAME
    csv_path.write_text(
        "Ad Soyad,Öğrenci Numarası\r\nMEHMET KADİR ARICI,2420191035\r\n",
        encoding="utf-8-sig",
    )

    assert common.load_student_roster(tmp_path) == {}


def test_format_student_pdf_stem_converts_duplicate_name_suffix_to_clean_dash():
    """Ayni isimli 2. ogrencide display_name '... (2)' bicimini tasir
    (bkz. scan_grade_center.py occurrence mantigi) - parantezler
    sanitize_filename tarafindan '_' ile degistirilip 'AHMET-YILMAZ-_2_'
    gibi cirkin bir sonuc doguracagina, temiz bir '-2' eki olarak
    cikmali."""
    stem = common.format_student_pdf_stem("Kısa Sınav 1", "Ahmet Yılmaz (2)", "2420171019")

    assert stem == "Kısa Sınav 1_2420171019_Ahmet-Yılmaz-2"


def test_normalize_roster_name_matches_turkish_uppercase_and_mixed_case():
    """Python'un casefold'u Turkce I kuralini bilmez: 'ARICI'.casefold()
    'arici' verirken 'Arıcı'.casefold() 'arıcı' verir - ayni ogrencinin
    adi iki kaynakta farkli kasayla gelirse esleme SESSIZCE kacar ve
    ogrenci numarasi PDF adina eklenmezdi. Turkce'ye gore indirgeme
    yapildigi icin artik ikisi ayni sonuca varmali."""
    assert common.normalize_roster_name("MEHMET KADİR ARICI") == common.normalize_roster_name(
        "Mehmet Kadir Arıcı"
    )


def test_normalize_roster_name_still_collapses_whitespace():
    assert common.normalize_roster_name("  AHMET   YILMAZ ") == common.normalize_roster_name(
        "Ahmet Yılmaz"
    )


def test_exact_line_pattern_matches_whole_line_only():
    pattern = common.exact_line_pattern("AYŞE KAYA")

    assert pattern.search("baslik\nAYŞE KAYA\n50 / 100") is not None
    # Substring cakismasi OLMAMALI: baska bir ogrencinin daha uzun adi.
    assert pattern.search("baslik\nAYŞE KAYAALP\n50 / 100") is None


def test_student_pdf_identity_suffix_survives_windows_path_trimming(tmp_path):
    """Windows MAX_PATH kirpmasi ogrenci PDF'inde SONDAKI no+ad kimlik
    bolumunu ASLA yememeli - aksi halde iki farkli ogrencinin dosyasi
    ayni ada dusup biri digerinin uzerine sessizce yazardi (ogrenci
    kaybi). Kirpma sadece bastaki sinav adindan yapilmali."""
    exam_name = "Çok Uzun Bir Sınav Adı " * 10
    display_name = "Mehmet Kadir Arıcı"
    student_no = "2420191035"
    stem = common.format_student_pdf_stem(exam_name, display_name, student_no)
    # format_student_pdf_stem KENDI 120 karakter sinirini uygularken de
    # kimligi korumali (kirpma sinav adindan yapilmali).
    assert stem.endswith("_2420191035_Mehmet-Kadir-Arıcı")

    protect = common.student_pdf_identity_suffix_chars(display_name, student_no)
    # Klasor kismi tek basina limitin ALTINDA kalacak ama dosya adiyla
    # birlikte limiti asacak derinlikte bir yol kur (ensure_safe_full_path
    # bilerek sadece dosya ADINI kirpar, klasorlere dokunmaz).
    padding_len = max(common.WINDOWS_SAFE_PATH_LIMIT - 80 - len(str(tmp_path)), 1)
    long_path = tmp_path / ("k" * padding_len) / f"{stem}.pdf"

    result = common.ensure_safe_full_path(long_path, protect_suffix_chars=protect)

    assert len(str(result)) <= common.WINDOWS_SAFE_PATH_LIMIT
    assert result.stem.endswith("_2420191035_Mehmet-Kadir-Arıcı")


def test_student_pdf_identity_suffix_chars_without_student_number():
    protect = common.student_pdf_identity_suffix_chars("Ahmet Yılmaz (2)", None)

    stem = common.format_student_pdf_stem("Final", "Ahmet Yılmaz (2)", None)
    # Korunan sondaki parca, '_' ayirici dahil kimligin tamamini kapsamali.
    assert stem[-protect:] == "_Ahmet-Yılmaz-2"


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


def test_append_download_log_appends_without_overwriting_previous_sessions(tmp_path):
    """append_download_log, hem sinav PDF indirme akislarinin (gui.py
    _scan_not_defteri/_scan_grade_center) HEM 'Öğrenci Tara'nin (_scan_
    students) ORTAK kullandigi TEK fonksiyon - "a" (append) modunda
    actigi icin (bkz. fonksiyonun kendi docstring'i: 'EKLER, uzerine
    yazmaz') ardisik cagrilar birbirini SILMEMELI, sadece dosyanin
    SONUNA eklenmeli. Bu tek fonksiyonu burada saglam test etmek, TUM
    cagiran taraflar (4 farkli yer) icin ayni guvenceyi saglar."""
    log_path = tmp_path / common.DOWNLOAD_LOG_FILENAME

    common.append_download_log(
        tmp_path, "BST020 — Not Defteri", ["Kısa Sınav 1 indirildi"],
        {"ok": 20, "skip": 0, "fail": 0},
    )
    common.append_download_log(
        tmp_path, "MAT101 — Not Defteri", ["Vize indirildi"],
        {"ok": 30, "skip": 2, "fail": 1},
    )

    content = log_path.read_text(encoding="utf-8")

    assert "BST020 — Not Defteri" in content
    assert "MAT101 — Not Defteri" in content
    # ilk oturum SILINMEDI, ikincisi onun ALTINA eklendi (ustune degil)
    assert content.index("BST020") < content.index("MAT101")


def test_already_captured_titles_ignores_entries_with_missing_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "LOG_PATH", tmp_path / "captures.json")
    missing_pdf = tmp_path / "yok.pdf"
    common.append_log({"baslik": "Var Olmayan", "pdf": str(missing_pdf)})

    assert common.already_captured_titles() == set()


def test_already_captured_titles_includes_entries_with_existing_valid_pdf(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "LOG_PATH", tmp_path / "captures.json")
    real_pdf = tmp_path / "var.pdf"
    real_pdf.write_bytes(b"%PDF-1.4" + b"x" * common.MIN_VALID_PDF_BYTES)
    common.append_log({"baslik": "Var Olan", "pdf": str(real_pdf)})

    assert common.already_captured_titles() == {"Var Olan"}


def test_already_captured_titles_ignores_truncated_pdf(tmp_path, monkeypatch):
    """FELAKET SENARYOSU: PDF tam yazilirken elektrik kesildi/uygulama
    coktu - diskte YARIM bir dosya kaldi. Sadece exists() kontrol edilseydi
    bu ogrenci 'zaten var' sayilip SONSUZA KADAR atlanir, bozuk PDF de
    arsivde fark edilmeden kalirdi. Supheli kucuk dosya yok sayilmali ki
    sonraki taramada ogrenci yeniden yakalansin."""
    monkeypatch.setattr(common, "LOG_PATH", tmp_path / "captures.json")
    truncated_pdf = tmp_path / "yarim.pdf"
    truncated_pdf.write_bytes(b"%PDF-1.4")  # sadece 8 bayt - yarim kalmis
    common.append_log({"baslik": "Yarım Kalan", "pdf": str(truncated_pdf)})

    assert common.already_captured_titles() == set()


def test_read_log_quarantines_corrupt_file_and_returns_empty(tmp_path, monkeypatch):
    """FELAKET SENARYOSU: captures.json bozulmus (elle duzenleme, guc
    kesintisi...). Eskiden RuntimeError firlatilip HER indirme denemesi
    bastan bloke oluyordu - program, kullanici dosyayi elle bulup silene
    kadar kalici olarak kullanilamazdi. Artik bozuk dosya SILINMEDEN
    'captures.bozuk-*.json' adiyla kenara alinmali ve bos gecmisle devam
    edilmeli."""
    log_path = tmp_path / "captures.json"
    monkeypatch.setattr(common, "LOG_PATH", log_path)
    log_path.write_text('{"yarim": ', encoding="utf-8")  # gecersiz JSON

    entries = common.read_log()

    assert entries == []
    assert not log_path.exists()  # bozuk dosya yerinde birakilmadi
    backups = list(tmp_path.glob("captures.bozuk-*.json"))
    assert len(backups) == 1  # ... ama SILINMEDI, kurtarilabilir yedek var
    assert backups[0].read_text(encoding="utf-8") == '{"yarim": '

    # Sonraki kayit temiz bir dosyayla sorunsuz devam etmeli.
    common.append_log({"baslik": "Yeni Kayit"})
    assert common.read_log() == [{"baslik": "Yeni Kayit"}]


def test_page_on_blackboard_true_for_blackboard_and_false_for_login_page():
    on_bb = FakePage(live_url_value="https://istinye.blackboard.com/ultra/x")
    on_login = FakePage(live_url_value="https://login.microsoftonline.com/x")
    broken = FakePage(live_url_exc=RuntimeError("a"), cached_url_exc=RuntimeError("b"))

    assert common.page_on_blackboard(on_bb) is True
    assert common.page_on_blackboard(on_login) is False
    assert common.page_on_blackboard(broken) is False


def test_sanitize_filename_preserves_emoji_and_unusual_names():
    """ABARTI SENARYOSU: ogrenci/sinav adinda emoji ya da alisilmadik
    karakterler - bunlar her uc isletim sisteminde de GECERLI dosya adi
    karakterleri, temizlenmemeli ve cokme olmamali."""
    assert common.sanitize_filename("Final 🎓 Sınavı") == "Final 🎓 Sınavı"


def test_has_seen_onboarding_and_mark_onboarding_seen_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(common, "ONBOARDING_SEEN_PATH", tmp_path / "onboarding_seen")

    assert common.has_seen_onboarding() is False

    common.mark_onboarding_seen()

    assert common.has_seen_onboarding() is True


# ---------- set_windows_dpi_awareness (Windows'ta "her sey kocaman" hatasi) ----------


def test_set_windows_dpi_awareness_is_noop_on_non_windows(monkeypatch):
    import platform as _platform

    monkeypatch.setattr(_platform, "system", lambda: "Darwin")
    monkeypatch.setattr(common, "platform", _platform)

    common.set_windows_dpi_awareness()  # exception firlatmamali


def test_set_windows_dpi_awareness_calls_shcore_api_on_windows(monkeypatch):
    import platform as _platform
    import types

    monkeypatch.setattr(_platform, "system", lambda: "Windows")
    monkeypatch.setattr(common, "platform", _platform)

    calls = []
    fake_windll = types.SimpleNamespace(
        shcore=types.SimpleNamespace(SetProcessDpiAwareness=lambda v: calls.append(("shcore", v))),
    )
    import ctypes

    monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)

    common.set_windows_dpi_awareness()

    assert calls == [("shcore", 1)]


def test_set_windows_dpi_awareness_falls_back_to_user32_when_shcore_missing(monkeypatch):
    import platform as _platform
    import types

    monkeypatch.setattr(_platform, "system", lambda: "Windows")
    monkeypatch.setattr(common, "platform", _platform)

    calls = []

    class _RaisingShcore:
        def SetProcessDpiAwareness(self, _v):
            raise AttributeError("eski Windows surumunde shcore yok")

    fake_windll = types.SimpleNamespace(
        shcore=_RaisingShcore(),
        user32=types.SimpleNamespace(SetProcessDPIAware=lambda: calls.append("user32")),
    )
    import ctypes

    monkeypatch.setattr(ctypes, "windll", fake_windll, raising=False)

    common.set_windows_dpi_awareness()

    assert calls == ["user32"]


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
