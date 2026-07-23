"""scan_grade_center.py icin birim testler - gercek tarayici GEREKTIRMEYEN
saf mantik: kaydirma pencerelerinin ortusme-birlestirmesi (_merge_scroll_window)
ve baslik dogrulamasi (header_matches_student).

Ikisi de bu oturumda bulunan gercek hata siniflarinin regresyon testleri:
- pencereler ustuste binince ayni ogrencinin listeye 2+ kez girip sahte
  '(2)' kopya PDF'leri uretmesi,
- substring isim dogrulamasinin 'AYŞE KAYA' ararken 'AYŞE KAYAALP'
  sayfasini da 'dogru' saymasi (yanlis ogrenciye yanlis PDF riski).
"""

from scan_grade_center import SUBMIT_DATE_MARKER, _merge_scroll_window, header_matches_student

# ---------- _merge_scroll_window ----------


def test_merge_scroll_window_drops_overlapping_block_between_windows():
    # %80 kaydirma -> %20 ortusme: onceki pencerenin sonu yeni pencerenin
    # basinda AYNEN tekrar gelir. Eski "ardisik tekrar at" yaklasimi bu
    # BLOK tekrarini yakalayamiyordu (C,D ile C,D bitisik degil).
    accumulated = [("A", "10/10"), ("B", "9/10"), ("C", "8/10"), ("D", "7/10")]
    window = [("C", "8/10"), ("D", "7/10"), ("E", "6/10"), ("F", "5/10")]

    merged = _merge_scroll_window(accumulated, window)

    assert merged == [
        ("A", "10/10"), ("B", "9/10"), ("C", "8/10"), ("D", "7/10"),
        ("E", "6/10"), ("F", "5/10"),
    ]


def test_merge_scroll_window_full_relisting_adds_nothing():
    # Liste virtualized DEGILSE her kaydirma pasinda konteynerden TUM
    # liste yeniden okunur - birlestirme sonucu liste IKIYE katlanmamali.
    rows = [("A", "10/10"), ("B", "9/10"), ("C", "8/10")]

    merged = _merge_scroll_window(list(rows), list(rows))

    assert merged == rows


def test_merge_scroll_window_appends_fully_new_window():
    accumulated = [("A", "10/10"), ("B", "9/10")]
    window = [("C", "8/10"), ("D", "7/10")]

    merged = _merge_scroll_window(accumulated, window)

    assert merged == accumulated + window


def test_merge_scroll_window_preserves_real_duplicate_names_within_one_window():
    # Ayni ada (ve ayni skora) sahip IKI GERCEK ogrenci ayni pencerede
    # yan yana gorunuyorsa bunlar ortusme DEGIL, gercek veri - korunmali.
    window = [("AHMET YILMAZ", "50/100"), ("AHMET YILMAZ", "50/100"), ("ZEYNEP AK", "70/100")]

    merged = _merge_scroll_window([], window)

    assert merged == window


def test_merge_scroll_window_handles_empty_inputs():
    assert _merge_scroll_window([], []) == []
    assert _merge_scroll_window([("A", "1/1")], []) == [("A", "1/1")]
    assert _merge_scroll_window([], [("A", "1/1")]) == [("A", "1/1")]


# ---------- header_matches_student ----------


def _body_with_header(name: str) -> str:
    return f"Degerlendirme\n{name}\n{SUBMIT_DATE_MARKER}: 01.01.2026 10:00\nONAY: ABC123"


def test_header_matches_student_accepts_exact_name():
    body = _body_with_header("AYŞE KAYA")

    assert header_matches_student(body, "AYŞE KAYA") is True


def test_header_matches_student_rejects_name_that_is_prefix_of_another_students_name():
    # Sayfada gercekte 'AYŞE KAYAALP' yaziyor - 'AYŞE KAYA' icin yapilan
    # dogrulama substring mantigiyla YANLIS POZITIF verirdi; artik adin
    # hemen ardindan baska harf geliyorsa eslesme SAYILMAMALI (Turkce
    # harfler dahil).
    body = _body_with_header("AYŞE KAYAALP")

    assert header_matches_student(body, "AYŞE KAYA") is False


def test_header_matches_student_rejects_name_embedded_after_other_letters():
    body = _body_with_header("KARAYŞE KAYA")  # adin ONUNDE fazladan harf

    assert header_matches_student(body, "AYŞE KAYA") is False


def test_header_matches_student_false_when_submit_marker_missing():
    assert header_matches_student("ONAY: ABC123 ama tarih blogu yok", "AYŞE KAYA") is False


def test_header_matches_student_only_searches_window_before_marker():
    # Ad, GONDERIM TARIHI blogunun SONRASINDA geciyorsa (ör. sayfanin
    # baska bir yerindeki ogrenci listesi) dogrulama sayilmamali.
    body = f"{SUBMIT_DATE_MARKER}: 01.01.2026\nAYŞE KAYA"

    assert header_matches_student(body, "AYŞE KAYA") is False
