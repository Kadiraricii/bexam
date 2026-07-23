"""scan_students.py icin birim testler - gercek bir tarayici/Blackboard
oturumu GEREKTIRMEYEN, sadece _warn_if_roster_incomplete'in kendi
mantigini sinayan testler (satir tarama/sayfalama Playwright API'sinin
tamamini taklit etmek yerine, sadece page.inner_text("body") kullanan bu
fonksiyonu izole test ediyoruz)."""

import scan_students


class _FakeTextPage:
    """Sadece inner_text("body") kullanan _warn_if_roster_incomplete icin
    minimal sahte sayfa - butun Playwright locator API'sini taklit etmeye
    gerek yok."""

    def __init__(self, body_text: str = "", raise_on_inner_text: bool = False) -> None:
        self._body_text = body_text
        self._raise_on_inner_text = raise_on_inner_text

    def inner_text(self, _selector: str) -> str:
        if self._raise_on_inner_text:
            raise RuntimeError("sayfa artik erisilebilir degil")
        return self._body_text


def test_warn_if_roster_incomplete_warns_on_count_mismatch():
    page = _FakeTextPage("Öğrenciler\n1-20 / 90\nTam Ad\n...")
    messages = []

    scan_students._warn_if_roster_incomplete(page, collected_count=20, emit=messages.append)

    assert len(messages) == 1
    assert "90" in messages[0]
    assert "20" in messages[0]


def test_warn_if_roster_incomplete_stays_silent_when_counts_match():
    page = _FakeTextPage("1-20 / 20")
    messages = []

    scan_students._warn_if_roster_incomplete(page, collected_count=20, emit=messages.append)

    assert messages == []


def test_warn_if_roster_incomplete_stays_silent_when_indicator_not_found():
    page = _FakeTextPage("bu sayfada boyle bir gosterge yok")
    messages = []

    scan_students._warn_if_roster_incomplete(page, collected_count=5, emit=messages.append)

    assert messages == []


def test_warn_if_roster_incomplete_swallows_page_access_errors():
    page = _FakeTextPage(raise_on_inner_text=True)
    messages = []

    # Sayfa erisiminde hata olsa BILE bu kontrol cokmemeli - zaten
    # TOPLANMIS roster verisini etkilememeli (bkz. fonksiyon docstring'i).
    scan_students._warn_if_roster_incomplete(page, collected_count=5, emit=messages.append)

    assert messages == []


def test_total_student_count_pattern_does_not_match_score_column_values():
    """'Genel Not' sutunundaki '5,8 / 100' gibi puan degerleri (tire
    icermiyor) sayfalama gostergesiyle ('1-20 / 90', tire iceriyor)
    YANLISLIKLA eslesmemeli - aksi halde toplam ogrenci sayisi yerine
    bir ogrencinin notu okunup anlamsiz bir karsilastirma yapilirdi."""
    assert scan_students.TOTAL_STUDENT_COUNT_PATTERN.search("5,8 / 100") is None
    assert scan_students.TOTAL_STUDENT_COUNT_PATTERN.search("63,5/100") is None


def test_total_student_count_pattern_matches_pagination_indicator():
    match = scan_students.TOTAL_STUDENT_COUNT_PATTERN.search("1-20 / 90")

    assert match is not None
    assert match.group(1) == "90"
