"""scan_course.py icin birim testler - gercek tarayici GEREKTIRMEYEN saf
mantik: ayni adli sinav satirlarinin tekillestirilmesi (_dedupe_exam_rows).

FELAKET SENARYOSU regresyonu: hoca Not Defteri'nde AYNI adla iki kolon
acmissa (ya da ayni satir gizli ekran-okuyucu kopyasiyla iki kez
eslesmisse), satir ADIYLA bulunup `.first`'e tiklandigi icin ikinci sinav
SESSIZCE hic taranmazdi - kullanici eksigi fark edemezdi. Artik ikinci
satir islenmeyip elenenler listesinde ACIKCA gerekcesiyle gorunmeli.
"""

from scan_course import GRADING_STATUS_COMPLETE_PATTERN, ExamRow, _dedupe_exam_rows


def test_grading_status_pattern_matches_regardless_of_case():
    """FELAKET SENARYOSU regresyonu: canli bir ekran goruntusunde 'Tümüne
    Not Verildi' degil 'Tümüne not verildi' (kucuk 'n') gorulmustu.
    Pattern case-sensitive kalsaydi bu durumdaki (ör. BUTUNLEME gibi
    kritik) bir sinav satiri ne islenecek ne de atlanan listesine girerdi -
    SESSIZCE tamamen kaybolurdu."""
    assert GRADING_STATUS_COMPLETE_PATTERN.search("Tümüne not verildi")
    assert GRADING_STATUS_COMPLETE_PATTERN.search("tamamlandı")


def test_dedupe_exam_rows_keeps_unique_names_untouched():
    included = [ExamRow("Vize", 20), ExamRow("Final", 18)]

    deduped, excluded = _dedupe_exam_rows(included, [])

    assert deduped == included
    assert excluded == []


def test_dedupe_exam_rows_moves_duplicate_name_to_excluded_with_explanation():
    included = [ExamRow("Kısa Sınav 1", 20), ExamRow("Kısa Sınav 1", 15), ExamRow("Final", 18)]

    deduped, excluded = _dedupe_exam_rows(included, [])

    # Ilki islenecek listede kaldi, Final etkilenmedi.
    assert deduped == [ExamRow("Kısa Sınav 1", 20), ExamRow("Final", 18)]
    # Ikincisi SESSIZCE yutulmadi - gerekcesiyle birlikte elenenlerde.
    assert len(excluded) == 1
    assert "Kısa Sınav 1" in excluded[0]
    assert "aynı adla" in excluded[0]


def test_dedupe_exam_rows_appends_after_existing_excluded_entries():
    included = [ExamRow("Vize", 10), ExamRow("Vize", 10)]
    existing_excluded = ["Deneme Testi"]

    deduped, excluded = _dedupe_exam_rows(included, existing_excluded)

    assert deduped == [ExamRow("Vize", 10)]
    assert excluded[0] == "Deneme Testi"  # var olan liste korunuyor
    assert len(excluded) == 2


def test_dedupe_exam_rows_counts_third_and_later_duplicates_separately():
    included = [ExamRow("Quiz", 5), ExamRow("Quiz", 5), ExamRow("Quiz", 5)]

    deduped, excluded = _dedupe_exam_rows(included, [])

    assert deduped == [ExamRow("Quiz", 5)]
    assert len(excluded) == 2
    assert "2. satır" in excluded[0]
    assert "3. satır" in excluded[1]


def test_filter_submissions_skips_click_if_already_submitted():
    from unittest.mock import MagicMock
    from scan_course import _filter_submissions_to_submitted

    page = MagicMock()
    trigger_loc = MagicMock()
    trigger_loc.count.return_value = 1
    trigger_loc.inner_text.return_value = "Öğrenci Durumu: Gönderildi"
    page.locator.return_value = trigger_loc

    _filter_submissions_to_submitted(page)

    # Menüye tekrar tıklanmamalı, Escape basılıp çıkılmalı
    trigger_loc.click.assert_not_called()
    page.keyboard.press.assert_called_with("Escape")


def test_first_complete_status_row_fallback_when_complete_selector_missing():
    from unittest.mock import MagicMock
    from scan_course import _first_complete_status_row

    page = MagicMock()
    rows = MagicMock()
    rows.count.return_value = 2

    first_row = MagicMock()
    first_row.locator.return_value.count.return_value = 0  # complete selector yok
    second_row = MagicMock()
    second_row.locator.return_value.count.return_value = 0

    rows.nth.side_effect = lambda idx: first_row if idx == 0 else second_row
    rows.first = first_row
    page.locator.return_value = rows

    res = _first_complete_status_row(page)
    assert res == first_row
