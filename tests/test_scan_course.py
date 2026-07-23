"""scan_course.py icin birim testler - gercek tarayici GEREKTIRMEYEN saf
mantik: ayni adli sinav satirlarinin tekillestirilmesi (_dedupe_exam_rows).

FELAKET SENARYOSU regresyonu: hoca Not Defteri'nde AYNI adla iki kolon
acmissa (ya da ayni satir gizli ekran-okuyucu kopyasiyla iki kez
eslesmisse), satir ADIYLA bulunup `.first`'e tiklandigi icin ikinci sinav
SESSIZCE hic taranmazdi - kullanici eksigi fark edemezdi. Artik ikinci
satir islenmeyip elenenler listesinde ACIKCA gerekcesiyle gorunmeli.
"""

from scan_course import ExamRow, _dedupe_exam_rows


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
