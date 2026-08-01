"""
Bir dersin Not Defteri (Grades) sayfasindaki sinavlari (Blackboard "Test")
otomatik bulur ve her biri icin PDF + ONAY kodu yakalar.

Kullanim:
    source .venv/bin/activate
    python3 scan_course.py

Tarayici acilinca SSO ile giris yap, taranacak dersin "Not Defteri"
sayfasina git (onceki BST020 ekran goruntusundeki gibi), sonra terminale
donup ENTER'a bas. Script, "Not Verme Durumu" sutununda 'Tamamlandı' ya
da 'Tümüne Not Verildi' yazan TUM satirlari acmayi dener (bu etiketin
kendisi tiklanabilir - ayri bir 'Goruntule' dugmesi YOK, bkz.
GRADING_STATUS_COMPLETE_MARKERS); hangisinin gercek bir sinav/quiz
gonderimi oldugunu satirin ADINA degil,
actiginda ONAY kodu gorunup gorunmedigine bakarak anlar (bkz.
NotSubmittedOrNotExam).
"""

import re
import sys
import time
from pathlib import Path
from typing import NamedTuple

from playwright.sync_api import Locator, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from common import (
    BASE_URL,
    DEFAULT_FOLDER_MAX_CHARS,
    OUTPUT_DIR,
    PROFILE_DIR,
    already_captured_titles,
    derive_course_label,
    find_scrollable_ancestor_handle,
    is_browser_closed_error,
    launch_browser_context,
    live_url,
    load_student_roster,
    page_on_blackboard,
    resolve_active_page,
    sanitize_filename,
)
from scan_grade_center import (
    BATCH_PAUSE_S,
    BATCH_SIZE,
    MAX_CONSECUTIVE_FAILURES,
    capture_student,
    find_student_rows,
)

# Not Defteri'nde bir sinav satirini acan ayri bir "Goruntule" dugmesi
# YOK - "Not Verme Durumu" sutunundaki etiketin KENDISI tiklanabilir
# (tiklaninca o sinavin Gönderimler listesi aciliyor, bkz. kullanicidan
# gelen ekran goruntusu dogrulamasi). Bu yuzden hem satirlari BULMAK hem
# de her birine TIKLAMAK icin ayni isaretler kullaniliyor.
#
# CANLI KULLANICI GOZLEMI: bu etiket TEK BASINA 'Tamamlandı' degil -
# Blackboard bazen ayni anlama gelen 'Tümüne Not Verildi' metnini de
# gosteriyor (muhtemelen sinavin TUM gonderimleri notlandirildiginda bu
# ikinci ifade kullaniliyor). Ikisi de AYNI sekilde tiklanabilir bir
# durum - GRADING_STATUS_COMPLETE_MARKERS bu yuzden TEK bir sabit degil,
# bir TUPLE. Asil kural: 'Not verilecek bir şey yok' (NOTHING_TO_GRADE_
# MARKER, duz metin - tiklanamaz) VE durumu BOS olan satirlar HARIC,
# geri kalan HER status metni islenmeye calisilmali - Blackboard baska
# bir ifade daha kullanirsa (henuz gorulmemis), buraya (asagidaki tuple'a)
# eklenmesi yeterli.
GRADING_STATUS_COMPLETE_MARKERS = ("Tamamlandı", "Tümüne Not Verildi")
# Buton/satir eslemede kullanilan, ikisini de KAPSAYAN tek regex - "|" ile
# birlestirilmis, her biri re.escape ile ozel regex karakterlerinden
# arindirilmis. Log/uyari mesajlarinda insan-okunur gosterim icin
# GRADING_STATUS_COMPLETE_MARKERS_LABEL kullanilir (bkz. asagisi).
#
# IGNORECASE BILEREK eklendi: canli bir ekran goruntusunde "Tümüne Not
# Verildi" degil "Tümüne not verildi" (kucuk 'n') gorulmustu - Blackboard
# harf buyuklugunu tutarli kullanmiyor olabilir. Bu satirlar eslesmezse
# o sinav (ozellikle BUTUNLEME gibi kritik olanlar) ne islenecek ne de
# atlanan listesine girer, SESSIZCE tamamen kaybolurdu - buyuk/kucuk harf
# farkina karsi kayitsiz kalmak bedelsiz, riski tamamen ortadan kaldiriyor.
GRADING_STATUS_COMPLETE_PATTERN = re.compile(
    "|".join(re.escape(marker) for marker in GRADING_STATUS_COMPLETE_MARKERS),
    re.IGNORECASE,
)
# Log/uyari mesajlarinda gosterilecek, KENDI ICINDE tirnakli, hazir
# metin - ör. "'Tamamlandı' / 'Tümüne Not Verildi'" - cagiran taraflar
# ayrica tirnak eklemeden dogrudan f-string'e gömebilsin diye.
GRADING_STATUS_COMPLETE_MARKERS_LABEL = " / ".join(f"'{m}'" for m in GRADING_STATUS_COMPLETE_MARKERS)
NOTHING_TO_GRADE_MARKER = "Not verilecek bir şey yok"
# Not Defteri'nin yuklendigini teyit etmek icin flexible selector listesi
GRADES_LIST_READY_SELECTOR = "text=Not Verilebilir Öğeler, text=Not Defteri, text=Öğrenci Durumu, tr, [role='row']"
SUBMITTED_COUNT_PATTERN = re.compile(r"(\d+)\s*/\s*\d+\s*g[öo]nderildi", re.IGNORECASE)
# Blackboard'un yerlesik "Yoklama" ogeleri "Test" degil "Günlük" (Daily)
# kategorisinde olur ve herkes yoklamaya alindiginda durumu da
# 'Tamamlandı' gorunur - ama bu bir sinav/quiz DEGIL, tiklaninca
# Gönderimler/ONAY akisina degil tamamen farkli bir yoklama arayuzune
# gider; oradan Not Defteri'ne "geri donme" kurtarmasi da calismiyor
# (CANLI gozlem: return_to_grades_list "Kurtarma başarısız" ile patlayip
# TUM taramayi durduruyordu). Bu yuzden bu kategorideki satirlar durumu
# ne olursa olsun BASTAN eleniyor - isme degil KATEGORIYE bakiyoruz
# (ogretmen yoklama ogesini istedigi gibi adlandirabilir).
ATTENDANCE_CATEGORY_MARKER = "Günlük"
# Gönderimler tablosunun ustundeki "Öğrenci Durumu" filtresi varsayilan
# olarak "Tüm Öğrenci Durumları" acilir (bkz. kullanicinin ekran
# goruntusu) - "Gönderildi"ye ayarlanirsa hic gonderilmemis/taslak
# ogrenciler tabloya HIC girmez, satir secimi kokten basitlesir (bkz.
# _filter_submissions_to_submitted). ID/data-value CANLI DOM'dan alindi
# (kullanicinin paylastigi HTML): tetikleyici role="combobox" olan
# <div id="submission-list-dropdown-filters-student-status">, secenek
# ise role="option" data-value="Gönderildi" olan bir <li>.
STUDENT_STATUS_FILTER_TRIGGER_ID = "submission-list-dropdown-filters-student-status"
STUDENT_STATUS_FILTER_LABEL = "Öğrenci Durumu"
STUDENT_STATUS_FILTER_DEFAULT_TEXT = "Tüm Öğrenci Durumları"
SUBMITTED_FILTER_OPTION = "Gönderildi"
# Gönderimler tablosunun satirlari - CANLI DOM'dan alindi (kullanicinin
# paylastigi HTML): Not Defteri'nin Ultra tablosundan FARKLI, eski
# Angular tabanli bir bilesen. Satir: <div class="... submission-list-row
# ...">; tiklanacak asil eleman o satirin icindeki
# <a class="bb-click-target submission-name-wrapper ..."> (aria-label
# "{ad} için gönderimler"); tamamlanma isareti ise SADECE gercekten
# notlandirilmis satirlarda DOM'a giren <div class="status-is-complete">
# (yesil tik ikonu + 'Tamamlandı' metni).
SUBMISSION_ROW_SELECTOR = ".submission-list-row"
SUBMISSION_ROW_LINK_SELECTOR = "a.bb-click-target"
SUBMISSION_ROW_COMPLETE_SELECTOR = ".status-is-complete"
# Blackboard Ultra tablolari bazen gercek <tr> yerine <div role="row">
# olarak render edilebilir - satir ararken HER ZAMAN ikisini de kapsiyoruz,
# aksi halde bir tarafta calisip diger tarafta hicbir satir bulunamayabilir.
ROW_SELECTOR = "tr, [role='row']"

# Not Defteri satir listesi UZUN oldugunda (bir derste onlarca sinav/odev/
# tartisma satiri) Blackboard bu tabloyu da ogrenci paneliyle AYNI sekilde
# virtualize edebiliyor (sadece o an gorunen satirlar DOM'da) - bkz.
# find_exam_row_names ve _find_row_by_exact_name'deki asagidaki CANLI
# DOGRULANAN HATA notlari. EXAM_LIST_DISCOVERY_MAX_SCROLL_ITERATIONS
# (find_exam_row_names - TUM listeyi baştan toplarken) find_student_rows'un
# 200'uyle, EXAM_ROW_SEARCH_MAX_SCROLL_ITERATIONS (_find_row_by_exact_name -
# BELIRLI bir satiri ararken) scroll_student_into_view_and_click'in 35'iyle
# AYNI mantikla secildi (bkz. scan_grade_center.py).
EXAM_LIST_DISCOVERY_MAX_SCROLL_ITERATIONS = 200
EXAM_ROW_SEARCH_MAX_SCROLL_ITERATIONS = 35


def _first_line(text: str) -> str:
    """text.strip().splitlines()[0] - ama text bos/sadece bosluksa (ör.
    beklenmedik bir DOM durumu) [0] erisimi IndexError firlatmaz, bos
    string doner."""
    lines = text.strip().splitlines()
    return lines[0].strip() if lines else ""


def _table_scroll_anchor(page: Page):
    """Not Defteri tablosunda SU AN DOM'da olan (herhangi bir sinav)
    ilk satir elemanini dondurur - `None` ise tabloda hic satir yok
    demektir.

    find_scrollable_ancestor_handle icin tablonun/sayfanin KENDISI degil
    somut bir eleman saglar - bkz. scan_grade_center._panel_scroll_anchor
    ile AYNI gerekce: `Locator.evaluate_handle` HER ZAMAN kendi bagli
    oldugu somut elemani `el` olarak gecirir, `page.evaluate_handle` ise
    (page bir Locator DEGILSE) hicbir arguman gecirmez."""
    anchor = page.locator(ROW_SELECTOR).first
    return anchor if anchor.count() > 0 else None


def _find_row_by_exact_name_in_dom(page: Page, row_name: str) -> Locator | None:
    """_find_row_by_exact_name'in TEK BIR pas (kaydirma denemeden, sadece
    su anki DOM durumunda) arayan ic kismi - bkz. o fonksiyonun
    docstring'i."""
    rows = page.locator(ROW_SELECTOR)
    for i in range(rows.count()):
        candidate = rows.nth(i)
        if _first_line(candidate.inner_text()) == row_name:
            return candidate
    return None


def _find_row_by_exact_name(page: Page, row_name: str) -> Locator | None:
    """ROW_SELECTOR'daki satirlar arasinda ILK SATIRI (bkz. _first_line)
    row_name'e TAM esit olani bulur; yoksa None.

    CANLI HATA (Not Defteri indirme adiminda): eskiden
    `page.locator(ROW_SELECTOR, has_text=_exact_line_pattern(row_name))`
    kullaniliyordu - ama Playwright'in has_text esleyicisi, regex'i test
    etmeden ONCE elementin metnindeki TUM satir sonlarini/bosluklari TEK
    bir bosluga indirgiyor. exact_line_pattern'in ^...$ + MULTILINE
    yaklasimi satir sonlarina dayaniyor - bu yuzden ad + kategori + tarih
    + durum gibi COK sutunlu bir Not Defteri satirinda (satir sonlari
    kaybolunca tek bir uzun metne donusuyor) regex hicbir zaman eslesmiyor,
    `.first.click()` sonunda "Timeout 30000ms exceeded" ile patliyordu
    (find_exam_row_names'in kullandigi buton-rolu sorunundan TAMAMEN
    AYRI, bagimsiz bir hata). Cozum: find_exam_row_names'deki YONTEMLE
    AYNI SEKILDE, satirin GERCEK render edilmis inner_text()'ini Python
    tarafinda satir satir karsilastiriyoruz - browser tarafi normalizasyona
    hic bagimli degil.

    IKINCI CANLI HATA (kullanicinin canli gozlemi): 1. sinavdaki TUM
    ogrenciler yakalandiktan sonra Not Defteri listesine donulup 2.
    sinavin satiri aranirken, bu fonksiyon SADECE su anki DOM'a bakiyordu.
    Not Defteri'nde COK sayida satir varsa (bir derste onlarca sinav/odev)
    Blackboard bu tabloyu da virtualize edebiliyor - 2. sinav henuz DOM'a
    render edilmemis (listede asagida kalmis) olabilir. Sonuc: satir
    'bulunamadi' sayilip capture_exam_submissions NotSubmittedOrNotExam
    firlatiyor, 2. sinav GERCEKTE bir sinav/gonderilmis olsa BILE sessizce
    atlaniyordu - kullanicinin 'sınav2'ye geçerken buga giriyor, ben GUI'ye
    girip çıkmam gerekiyor' dedigi durum tam olarak bu (kullanicinin
    kendi etkilesimi -baska bir sayfaya gecip donmesi- arada bir tarayici
    reflow/yeniden-render tetikleyip satiri DOM'a sokabiliyordu, bu yuzden
    "calisir gibi" gorunuyordu). Duzeltme: satir ilk pasoda bulunamazsa,
    scan_grade_center.py'deki AYNI kanitlanmis yontemle (bir satir
    ELEMANINDAN yukari dogru en yakin GERCEKTEN kaydirilabilir atayi
    bulup adim adim kaydirmak) tabloyu asagi kaydirip HER ADIMDA tekrar
    ariyoruz.

    UCUNCU CANLI HATA (bu duzeltmenin KENDI icinde, ilk yazilan halinde):
    yukaridaki kaydirma sadece ASAGI yonde ilerliyordu - "su anki konumdan
    asagi dogru ara" varsayimi return_to_grades_list'in try_back=False
    (page.goto ile TAM sayfa yenileme, her zaman scroll=0'dan baslar)
    cagrildigi ANA akista dogruydu, ama return_to_grades_list AYRICA
    try_back=True (varsayilan) ile de cagriliyor (bkz. gui.py'deki
    recover() VE scan_course.main()'deki NotSubmittedOrNotExam/genel
    Exception kurtarmalari) - bu yolda ONCE page.go_back() denenir, TAM
    YENILEME OLMAYABILIR (tarayici gecmisi/bfcache), yani sayfa onceki
    kaydirma konumunda KALMIS olabilir. Hedef satir o konumun USTUNDE
    kalmissa (asagi-sadece arama hicbir zaman ustune cikamayacagi icin)
    fonksiyon YANLIS SEKILDE 'bulunamadi' donerdi. Duzeltme: kaydirma
    denemeden ONCE konteyneri EXPLICIT olarak basa (scrollTop=0) sarip
    oradan asagi ariyoruz - boylece cagrilma anindaki kaydirma
    konumundan TAMAMEN BAGIMSIZ, HER ZAMAN tum listeyi tarar."""
    found = _find_row_by_exact_name_in_dom(page, row_name)
    if found is not None:
        return found

    anchor = _table_scroll_anchor(page)
    if anchor is None:
        return None
    scroll_handle = find_scrollable_ancestor_handle(anchor)
    try:
        if scroll_handle.json_value() is None:
            return None
    except Exception:
        return None

    try:
        scroll_handle.evaluate("el => { el.scrollTop = 0; }")
        page.wait_for_timeout(200)
    except Exception:
        pass
    found = _find_row_by_exact_name_in_dom(page, row_name)
    if found is not None:
        return found

    for _ in range(EXAM_ROW_SEARCH_MAX_SCROLL_ITERATIONS):
        try:
            scroll_handle.evaluate("el => { el.scrollTop += el.clientHeight * 0.8; }")
        except Exception:
            break
        page.wait_for_timeout(200)
        found = _find_row_by_exact_name_in_dom(page, row_name)
        if found is not None:
            return found
        try:
            at_bottom = scroll_handle.evaluate(
                "el => el.scrollTop + el.clientHeight >= el.scrollHeight - 2"
            )
        except Exception:
            break
        if at_bottom:
            break
    return None


def _first_complete_status_row(page: Page) -> Locator | None:
    """Gönderimler tablosunda "Not Verme Durumu" TAMAMLANMIŞ olan ILK
    ogrenci satirini bulur (yoksa None); hangi ogrenci oldugu onemli
    degil - bkz. _enter_flexible_grading_view docstring (sonra sol
    panelden zaten HEPSI tek tek gezilecek).

    CANLI DOM (kullanicinin paylastigi HTML): bu liste Not Defteri'nin
    Ultra <tr>/[role='row'] tablosundan TAMAMEN FARKLI bir teknoloji -
    eski, Angular tabanli bir bilesen. Satirlar SUBMISSION_ROW_SELECTOR
    ('.submission-list-row') class'ina sahip div'ler.

    YEDEK (Fallback): Tablo 'Gönderildi' durumuna filtrelendiyse,
    tablodaki TUM satirlar zaten gonderim yapmis ogrencilerdir. Eger
    .status-is-complete yesil tik ikonu henuz yerlesmemisse bile,
    tabloda satirlar varsa ilk gonderim satirini donerek basarisizligi
    onler.
    """
    rows = page.locator(SUBMISSION_ROW_SELECTOR)
    row_count = rows.count()
    if row_count == 0:
        return None
    for i in range(row_count):
        candidate = rows.nth(i)
        if candidate.locator(SUBMISSION_ROW_COMPLETE_SELECTOR).count() > 0:
            return candidate
    return rows.first


class ExamRow(NamedTuple):
    """Not Defteri'ndeki 'Tamamlandı' durumundaki tek bir sinav satiri.

    expected_submitted, satirda gorunen 'X / Y gönderildi' metninden (X) -
    ogrenci bazli yakalama sonunda gercekte kac ogrenci yakalandigi/
    zaten var oldugu bununla karsilastirilip tutarsizlik varsa uyari
    verilir (bkz. capture_exam_submissions)."""

    name: str
    expected_submitted: int | None


class NotSubmittedOrNotExam(RuntimeError):
    """'Tamamlandı' etiketine tiklandiktan sonra makul bir surede ONAY
    kodu gorunmedi.

    Bunun iki masum sebebi olabilir: (1) bu satir hic bir sinav/quiz
    degil - bir odev, tartisma forumu vb. (2) gercekten bir sinav ama
    ogrenci onu hic gondermemis. Iki durumda da yakalanacak bir sey yok.

    Onceden hangi satirlarin denenecegine adindaki kelimeye bakarak karar
    veriliyordu ("sinav" geciyor mu vb.) - ama hocalar sinavi 'Quiz',
    'Vize', 'Final', 'Ara Sinav' gibi COK farkli adlandirabiliyor, sabit
    bir kelime listesi hicbir zaman tam kapsayamaz. Bunun yerine artik
    GRADING_STATUS_COMPLETE_MARKERS durumundaki TUM satirlar deneniyor;
    gercek ayirt edici sinyal sayfanin ICERIGI (ONAY kodu var mi) - isim
    degil.
    """


def find_exam_row_names(page: Page) -> tuple[list[ExamRow], list[str]]:
    """Not Defteri'ndeki satirlari "Not Verme Durumu" sutununa gore
    ikiye ayirir.

    GRADING_STATUS_COMPLETE_MARKERS'taki etiketlerin ('Tamamlandı' YA DA
    'Tümüne Not Verildi' - CANLI KULLANICI GOZLEMI: Blackboard ikisini de
    kullanabiliyor, ikisi de AYNI sekilde tiklanabilir) HERHANGI biriyle
    eslesen satirlar (gercekten gonderim alip notlandirilmis sinav/
    quiz'ler) islenecek listeye alinir; satirda gorunen 'X / Y gönderildi'
    metninden X (beklenen gonderim sayisi) da ayiklanir, sonradan ogrenci
    bazli yakalama sayisiyla karsilastirmak icin (bkz.
    capture_exam_submissions). Durumu 'Not verilecek bir şey yok' olan
    satirlar (hic gonderim olmayan sinav/quiz/odev satirlari, ör. hocanin
    hazirlik/deneme amacli actigi ama hic kullanilmamis testler) BASTAN
    elenir - bunlari denemek zaten hicbir zaman ONAY kodu uretmeyecegi
    icin (bkz. NotSubmittedOrNotExam) sadece zaman kaybi ve gereksiz
    sayfa gecisi riski demek.

    Doner: (islenecek ExamRow listesi, elenen satir adlari).

    NOT: durum etiketi role="button" ARANMIYOR - canli gozlemde Not
    Defteri'nin 'Liste Gorunumu'nde (ör. ?gradebookView=list URL'i)
    Blackboard'un bu etiketi buton rolu OLMADAN render ettigi gorulmustu;
    role sartiyla ariyan onceki surum bu gorunumde SIFIR satir bulup
    "bu sayfada ne yapilacagi anlasilamadi" hatasina dusuyordu. Bunun
    yerine (asagidaki excluded_rows'la AYNI yontem) dogrudan satir
    metnine (has_text) gore ariyoruz - hangi eleman turunde render
    edildiginden bagimsiz calisir.

    CANLI DOGRULANAN HATA (kullanicinin canli gozlemi): bu fonksiyon
    eskiden HIC KAYDIRMA yapmiyordu - sadece sayfa ilk yuklendiginde DOM'a
    gelen satirlara bakiyordu. Bir derste COK sayida satir varsa (onlarca
    sinav/odev/tartisma) Blackboard bu tabloyu da ogrenci paneliyle AYNI
    sekilde virtualize edebiliyor - listenin ALT kisimlarindaki sinavlar
    (kullanicinin bildirdigi "aşağıdaki sınavlar") DOM'a hic girmiyor,
    sessizce hic bulunamiyordu. Duzeltme: scan_grade_center.find_student_
    rows ile AYNI yontemle, tabloyu adim adim asagi kaydirip HER ADIMDA
    tekrar topluyoruz - bu, hem 'Tamamlandı'/'Tümüne Not Verildi' hem
    'Not verilecek bir şey yok' satirlarini kapsar."""
    included: list[ExamRow] = []
    excluded: list[str] = []
    seen_names: set[str] = set()

    def collect_from_current_dom() -> None:
        status_rows = page.locator(ROW_SELECTOR, has_text=GRADING_STATUS_COMPLETE_PATTERN)
        for i in range(status_rows.count()):
            row_text = status_rows.nth(i).inner_text()
            first_line = _first_line(row_text)
            # ROW_SELECTOR "tr, [role='row']" oldugu icin ayni satir iki kez
            # eslesebilir (bkz. excluded_rows'daki ayni not) VE ayni satir
            # birden fazla kaydirma adiminda tekrar gorunebilir -
            # seen_names HER IKI durumu da tekillestirir.
            if not first_line or first_line in seen_names:
                continue
            seen_names.add(first_line)
            # bkz. ATTENDANCE_CATEGORY_MARKER tanimindaki not - yoklama
            # ogeleri durumu 'Tamamlandı' olsa bile denenmeden BASTAN
            # eleniyor.
            if any(line.strip() == ATTENDANCE_CATEGORY_MARKER for line in row_text.splitlines()):
                excluded.append(
                    f"{first_line} (kategorisi '{ATTENDANCE_CATEGORY_MARKER}' - yoklama, sınav değil, atlandı)"
                )
                continue
            count_match = SUBMITTED_COUNT_PATTERN.search(row_text)
            expected_submitted = int(count_match.group(1)) if count_match else None
            included.append(ExamRow(first_line, expected_submitted))

        # 'Not verilecek bir şey yok' durumu tiklanabilir DEGIL (sadece duz
        # metin) - bu yuzden satirlari buton/link araciligiyla degil,
        # dogrudan satir icerigine gore buluyoruz (bkz. ROW_SELECTOR).
        excluded_rows = page.locator(ROW_SELECTOR, has_text=NOTHING_TO_GRADE_MARKER)
        for i in range(excluded_rows.count()):
            row_text = excluded_rows.nth(i).inner_text()
            first_line = _first_line(row_text)
            # ROW_SELECTOR "tr, [role='row']" oldugu icin ayni satir hem
            # <tr> hem onu saran [role='row'] olarak IKI kez eslesebilir -
            # ayni adi iki kez listeleyip sayaci sisirmemek icin
            # tekillestiriyoruz (farkli sinavlarin adlari zaten
            # birbirinden farkli).
            if first_line and first_line not in excluded:
                excluded.append(first_line)

    collect_from_current_dom()

    anchor = _table_scroll_anchor(page)
    scroll_handle = find_scrollable_ancestor_handle(anchor) if anchor is not None else None
    try:
        is_scrollable = scroll_handle is not None and scroll_handle.json_value() is not None
    except Exception:
        is_scrollable = False

    if is_scrollable:
        assert scroll_handle is not None  # is_scrollable garanti eder
        for _ in range(EXAM_LIST_DISCOVERY_MAX_SCROLL_ITERATIONS):
            try:
                scroll_handle.evaluate("el => { el.scrollTop += el.clientHeight * 0.8; }")
            except Exception:
                break
            page.wait_for_timeout(200)
            collect_from_current_dom()
            try:
                at_bottom = scroll_handle.evaluate(
                    "el => el.scrollTop + el.clientHeight >= el.scrollHeight - 2"
                )
            except Exception:
                break
            if at_bottom:
                break
        try:
            scroll_handle.evaluate("el => { el.scrollTop = 0; }")
            page.wait_for_timeout(200)
        except Exception:
            pass

    return _dedupe_exam_rows(included, excluded)


def _dedupe_exam_rows(included: list[ExamRow], excluded: list[str]) -> tuple[list[ExamRow], list[str]]:
    """AYNI ada sahip birden fazla sinav satirini tekillestirir - ilki
    islenecek listede kalir, sonrakiler ACIK bir aciklamayla elenenler
    listesine tasinir.

    Neden: capture_exam_submissions satiri ADIYLA bulup `.first`'e
    tiklar - ayni adla iki satir varsa (hoca ayni adla iki kolon acmis,
    YA DA ayni satir gorunur etiket + ekran-okuyucu kopyasi olarak iki
    kez eslesmis) ikinci "sinav" da hep ILK satiri acar: ilki iki kez
    taranir (ikincisi tamamen atlama olarak biter), IKINCI sinav ise
    SESSIZCE hic taranmaz - kullanici fark edemezdi. Sessizce yanlis is
    yapmak yerine ikinciyi islemeyip kullaniciya acikca soyluyoruz:
    Blackboard'da sinavin adini gecici olarak degistirip tekrar taramasi
    yeterli."""
    deduped: list[ExamRow] = []
    name_counts: dict[str, int] = {}
    for row in included:
        name_counts[row.name] = name_counts.get(row.name, 0) + 1
        if name_counts[row.name] == 1:
            deduped.append(row)
        else:
            excluded = excluded + [
                f"{row.name} (aynı adla {name_counts[row.name]}. satır — hangi satıra "
                "tıklanacağı ayırt edilemediği için taranmadı; Blackboard'da bu "
                "sınavın adını geçici olarak değiştirip tekrar tara)"
            ]
    return deduped, excluded


def return_to_grades_list(page: Page, grades_url: str, *, try_back: bool = True) -> None:
    """Sinav overlay'ini kapatip Not Defteri listesine geri doner.

    Blackboard Ultra'da sinav goruntuleme ayri bir URL'e (SPA route)
    karsilik geliyor, bu yuzden Escape yerine tarayici gecmisini
    kullaniyoruz; o da basarisiz olursa Not Defteri URL'ine dogrudan
    gidip sert bir kurtarma yapiyoruz.

    try_back=False: go_back() HIC denenmez, direkt grades_url'e gidilir.
    capture_exam_submissions bir sinavda ONLARCA ogrenci arasinda
    gezindikten SONRA buraya donuyor - tek bir go_back() ile Not
    Defteri'ne donme sansi neredeyse yok (her ogrenci degisimi kendi
    tarayici gecmisi girdisini push'lamis olabilir), bu yuzden orada
    once go_back'i denemek sadece 8 saniyelik bosuna bir timeout demek.
    """
    if try_back:
        page.go_back()
        try:
            page.wait_for_selector(GRADES_LIST_READY_SELECTOR, timeout=8_000)
            return
        except PlaywrightTimeoutError:
            pass

    page.goto(grades_url)
    try:
        page.wait_for_selector(GRADES_LIST_READY_SELECTOR, timeout=15_000)
    except PlaywrightTimeoutError:
        page.wait_for_timeout(2_000)
        if page.locator(ROW_SELECTOR).count() > 0:
            return
        raise


def _filter_submissions_to_submitted(page: Page) -> None:
    """Gönderimler tablosunun ustundeki 'Öğrenci Durumu' filtresini
    'Gönderildi'ye ayarlamayi dener.

    CANLI BUG DUZELTMESI (kullanici geri bildirimi):
    Filtreleme sirasinda dropdown menusu tekrar tiklandiginda ya da
    menunun acik (overlay) kalmasi durumunda, ogrenci satirina tiklanirken
    Angular filtreyi varsayilana ('Tüm Öğrenci Durumları') sifirlayip
    filtrelemeyi bozuyordu.

    Cozum:
    1. Once dropdown tetikleyicisinde zaten 'Gönderildi' yazip yazmadigi
       kontrol edilir. Zaten 'Gönderildi' ise menuye HIC TIKLANMAZ (filtre
       korunur, bozulmaz).
    2. Menuyu actiktan ve 'Gönderildi' secildikten sonra, tetikleyicinin
       GERCEKTEN 'Gönderildi' gostermeye basladigi dogrulanana kadar kisa
       kisa beklenir (bkz. asagidaki IKINCI CANLI BUG notu - Escape
       ARTIK KOSULSUZ basilmiyor).

    IKINCI CANLI BUG (kullanicinin canli gozlemi - bu turun kendisi):
    Onceki surumde secim yapildiktan HEMEN SONRA, "menu acik kalirsa
    ogrenci satirina tiklamayi engeller" diye KOSULSUZ bir Escape tusu
    basiliyordu. Gercekte MUI'nin Select bileseni bir secenege
    TIKLANINCA menuyu zaten KENDISI kapatiyor - kosulsuz Escape, bu
    secimin React/MUI tarafinda commit edilmesiyle YARISA giriyordu:
    secim henuz tam islenmeden Escape araya girerse MUI bunu "vazgec"
    olarak yorumlayip degeri ONCEKI durumuna ('Tüm Öğrenci Durumları')
    GERI ALIYORDU - yani filtre once dogru uygulanip HEMEN ARDINDAN
    eski haline donuyordu (kullanicinin bildirdigi tam olarak bu).
    Duzeltme: Escape'i artik KOSULSUZ basmiyoruz - once tetikleyicinin
    'Gönderildi' gostermeye basladigini bekleyip DOGRULUYORUZ, sadece
    bu sure icinde HALA eski deger gorunuyorsa (secim gercekten commit
    olmamis/menu hala takili kalmis olabilir) SON CARE olarak Escape
    deniyoruz - boylece basarili bir secimi kendi elimizle bozmuyoruz.
    """
    trigger_loc = page.locator(f"#{STUDENT_STATUS_FILTER_TRIGGER_ID}")
    try:
        if trigger_loc.count() > 0:
            trigger_text = trigger_loc.inner_text()
            if SUBMITTED_FILTER_OPTION in trigger_text:
                try:
                    page.keyboard.press("Escape")
                except Exception:
                    pass
                page.wait_for_timeout(300)
                return
    except Exception:
        pass

    opened = False
    try:
        trigger_loc.click(timeout=3_000)
        opened = True
    except PlaywrightTimeoutError:
        try:
            combo = page.get_by_role("combobox", name=re.compile(STUDENT_STATUS_FILTER_LABEL)).first
            if combo.count() > 0 and SUBMITTED_FILTER_OPTION in combo.inner_text():
                return
            combo.click(timeout=3_000)
            opened = True
        except PlaywrightTimeoutError:
            try:
                txt_lbl = page.get_by_text(STUDENT_STATUS_FILTER_DEFAULT_TEXT, exact=True).first
                txt_lbl.click(timeout=3_000)
                opened = True
            except PlaywrightTimeoutError:
                return

    if not opened:
        return

    option_clicked = False
    try:
        opt = page.locator(f'[role="option"][data-value="{SUBMITTED_FILTER_OPTION}"]').first
        if opt.count() > 0:
            opt.click(timeout=3_000)
            option_clicked = True
    except PlaywrightTimeoutError:
        pass

    if not option_clicked:
        try:
            opt_by_role = page.get_by_role(
                "option", name=re.compile(f"^{re.escape(SUBMITTED_FILTER_OPTION)}$")
            ).first
            if opt_by_role.count() > 0:
                opt_by_role.click(timeout=3_000)
                option_clicked = True
        except PlaywrightTimeoutError:
            pass

    # bkz. yukaridaki "IKINCI CANLI BUG" notu: burada artik Escape'i
    # KOSULSUZ basmiyoruz - once secimin gercekten commit olup
    # olmadigini (tetikleyici metninde 'Gönderildi' gorunup gorunmedigini)
    # kisa kisa kontrol ediyoruz. Commit olduysa hemen donuyoruz, Escape'e
    # HIC GEREK YOK (MUI menuyu zaten kendisi kapatti).
    for _ in range(6):
        try:
            if trigger_loc.count() > 0 and SUBMITTED_FILTER_OPTION in trigger_loc.inner_text():
                return
        except Exception:
            break
        page.wait_for_timeout(200)

    # Bu noktaya geldiysek secim ya hic commit olmadi ya da menu hala
    # takili/acik kaldi - SON CARE olarak Escape deneyip devam ediyoruz.
    try:
        page.keyboard.press("Escape")
    except Exception:
        pass
    page.wait_for_timeout(500)


def _enter_flexible_grading_view(page: Page, row_name: str) -> None:
    """Not Defteri satirindaki 'Tamamlandı' etiketine tiklandiktan SONRA
    cagrilir.

    Gozlenen gercek davranis (bkz. kullanicinin ekran goruntuleri):
    tiklama dogrudan ONAY kodu iceren "Degerlendirme" sayfasina GITMEZ -
    once o sinavin TUM ogrencilerini listeleyen bir "Gönderimler" tablosu
    acilir (ONAY metni ICERMEZ, sadece ogrenci/skor/durum sutunlari olan
    bir liste). Sol 'Ogrenciler' paneli olan gercek Degerlendirme
    sayfasina (find_student_rows'un calisacagi sayfa) ULASMAK icin o
    tablodaki 'Tamamlandı' durumundaki HERHANGI BIR ogrenci satirina
    bir kez daha tiklamak gerekiyor - hangi ogrenci onemli degil, sonra
    zaten sol panelden HEPSI tek tek gezilecek.

    Bazi durumlarda (ör. tek ogrenci gonderdiyse) Blackboard bu ara
    listeyi atlayip DOGRUDAN Degerlendirme sayfasini acabilir - bu
    yuzden once kisa bir sure ONAY metnini bekliyoruz, sadece o
    basarisiz olursa ikinci tiklamaya geciyoruz.

    NOT: ikinci tiklamanin yonu (Gönderimler tablosundaki bir ogrenci
    satirina tiklamak) CANLI Blackboard oturumunda dogrulandi - tiklanan
    sayfa gercekten ONAY koduyla acilan Degerlendirme sayfasi. Ilk
    denemede yine de "ONAY kodu gorunmedi" hatasi alinmisti - CANLI
    GOZLEM: Gönderimler tablosunun EN USTUNDE genelde hic gonderilmemis
    ('Not verilecek bir şey yok') bir ogrenci satiri oluyor. ROW_SELECTOR
    ("tr, [role='row']") hem TEK bir ogrenci satirini hem de o satirlari
    SARAN bir ust kapsayiciyi (Blackboard bazen tablo govdesine de
    role='row' verebiliyor) eslestirebiliyor - boyle bir sarici icinde
    bir yerde 'Tamamlandı' GECTIGI icin has_text'i GECIYOR ve DOM
    sirasinda cocuklarindan ONCE geldigi icin eski kod (`.first`) bu
    SARICIYI seciyordu; `.locator('button, a').first` de sarici
    icindeki EN ILK tiklanabilir ogeyi (- ilk satir hic gonderilmemis
    ogrenciyse ONUN elemanini -) buluyor, YANLIS yere tikliyordu.
    Asagida bunun yerine adaylari Python tarafinda toplayip GERCEK bir
    ogrenci satiri oldugunu (durum etiketi TAM OLARAK BIR KEZ geciyor mu
    diye sayarak) dogrulayan _first_complete_status_row kullaniliyor -
    bkz. o fonksiyonun docstring'i."""
    try:
        page.wait_for_selector("text=ONAY:", timeout=4_000)
        return
    except PlaywrightTimeoutError:
        pass

    # bkz. _filter_submissions_to_submitted docstring'i - kullanicinin
    # istegi: satir secmeden ONCE listeyi 'Gönderildi'ye filtrele, boylece
    # hic gonderilmemis ogrenciler tabloya HIC girmez. Filtre basarisiz
    # olsa da _first_complete_status_row asagida yine dogru satiri bulur.
    _filter_submissions_to_submitted(page)

    submission_row = _first_complete_status_row(page)
    if submission_row is None:
        raise NotSubmittedOrNotExam(
            f"'{row_name}' icin Gönderimler listesinde 'Tamamlandı'/"
            "'Tümüne Not Verildi' durumunda bir ogrenci satiri bulunamadi."
        )
    try:
        # bkz. SUBMISSION_ROW_LINK_SELECTOR tanimindaki not - CANLI DOM'da
        # bu satirin asil tiklanacak elemani budur (satirin KENDISINDE
        # bb-click-to-invoke-child="a.bb-click-target" oldugu icin satirin
        # herhangi bir yerine tiklamak da ayni sonucu vermeli, ama dogrudan
        # linki tiklamak daha az yan-etki riski tasir).
        clickable = submission_row.locator(SUBMISSION_ROW_LINK_SELECTOR).first
        if clickable.count() > 0:
            clickable.click(timeout=8_000)
        else:
            submission_row.click(timeout=8_000)
        # bkz. yukaridaki NOT: bu sayfa TUM sinav icerigini yukledigi icin
        # agir aciliyor - 25 saniye, onceki 10 saniyenin CANLI oturumda
        # yetersiz kaldigi gozlemlenerek buyutulmus hali.
        page.wait_for_selector("text=ONAY:", timeout=25_000)
    except PlaywrightTimeoutError as exc:
        raise NotSubmittedOrNotExam(
            f"'{row_name}' icin ONAY kodu gorunmedi (ne dogrudan ne de "
            "'Gönderimler' listesindeki bir ogrenciye tiklandiktan sonra) "
            "- bu satir bir sinav/quiz olmayabilir, hic gonderilmemis "
            "olabilir, ya da sayfa yapisi beklenenden farkli."
        ) from exc


def capture_exam_submissions(
    page: Page,
    row_name: str,
    grades_url: str,
    exam_dir: Path,
    course_label: str,
    expected_submitted: int | None,
    captured_titles: set[str],
    *,
    emit=print,
    should_stop=lambda: False,
) -> dict:
    """Not Defteri'nde bir sinav satirinin 'Tamamlandı' etiketine tiklar.

    Acilan sayfa "Degerlendirme" gorunumu - scan_grade_center.py'nin
    calistigi SOL 'Ogrenciler' paneli olan sayfanin aynisi - oldugu icin,
    burada TEK bir ogrenciyle sinirli kalmiyoruz: sol paneldeki TUM
    ogrencileri once tariyoruz (find_student_rows), sonra her birini tek
    tek yakaliyoruz (capture_student) - PDF adi zaten o fonksiyonda
    ogrencinin ADI SOYADI (display_name) oluyor.

    expected_submitted (Not Defteri satirindaki 'X / Y gönderildi'
    metninden gelen X), bu sinav icin BASARIYLA yakalanan + zaten var
    olan ogrenci sayisiyla karsilastirilir - uyusmuyorsa (ör. bir
    ogrencinin sayfasi tutarli sekilde acilamadiysa) sessizce gecilmez,
    emit ile bir UYARI bildirilir ki PDF'ler elle sayilip kontrol
    edilebilsin.

    Doner: {"ok", "skip", "fail", "navigation_lost"} - bu sinavdaki TUM
    ogrenciler icin toplam (tek bir "entry" degil, cagiran taraf artik
    coklu ogrenci PDF'i bekliyor). navigation_lost=True ise, ogrenciler
    basariyla yakalanmis olsa BILE (totals dogru sayilir) sinav sonunda
    Not Defteri'ne DONULEMEMIS demektir - cagiran taraf bu durumda bir
    sonraki sinava GECMEMELI (sayfa bilinmeyen bir durumda, yanlis
    satirlara tiklama riski var)."""
    # bkz. _find_row_by_exact_name docstring'i: has_text/exact_line_pattern
    # kombinasyonu COK sutunlu Not Defteri satirlarinda hicbir zaman
    # eslesmiyordu (CANLI hata) - satiri artik Python tarafinda inner_text()
    # karsilastirmasiyla buluyoruz.
    row = _find_row_by_exact_name(page, row_name)
    if row is None:
        raise NotSubmittedOrNotExam(
            f"'{row_name}' adinda bir Not Defteri satiri artik sayfada "
            "bulunamadi (sayfa kaymis/degismis olabilir)."
        )
    # find_exam_row_names'deki ayni not gecerli: durum etiketi role="button"
    # olmayabilir (ör. Liste Gorunumu). Once rol bazli aramayi dene, bulamazsa
    # satirin herhangi bir button/a alt elemanina, o da yoksa satirin
    # KENDISINE tikla (bkz. _enter_flexible_grading_view'daki ayni yedekleme).
    status_marker = row.get_by_role("button", name=GRADING_STATUS_COMPLETE_PATTERN).first
    if status_marker.count() > 0:
        status_marker.click()
    else:
        clickable = row.locator("button, a").first
        if clickable.count() > 0:
            clickable.click()
        else:
            row.click()
    _enter_flexible_grading_view(page, row_name)

    # ONAY metni ana icerik alaninda gorunse bile, SOL 'Ogrenciler' paneli
    # ayri bir DOM alt agaci oldugu icin hala render/virtualize ediliyor
    # olabilir - hemen taramaya baslarsak (find_student_rows) panel bos
    # gorunup 0 ogrenci bulunabilir (sessiz basarisizlik riski). Kisa bir
    # sabit bekleme, manuel akista (scan_grade_center.py) kullanicinin
    # ENTER'a basmadan once dogal olarak birakip gectigi payi taklit
    # ediyor.
    page.wait_for_timeout(800)

    emit(f"  '{row_name}' öğrenci listesi taranıyor (kaydırarak toplanıyor)...")
    student_rows = find_student_rows(page)
    emit(f"  {len(student_rows)} öğrenci satırı bulundu.")

    # exam_dir = course_dir / sanitize(row_name) (bkz. main()/gui.py'deki
    # cagiran taraf) - roster CSV'si ('Öğrenci Tara' ile uretilir) DERS
    # klasorunde, sinav alt klasorunde degil.
    roster = load_student_roster(exam_dir.parent)

    totals = {"ok": 0, "skip": 0, "fail": 0}
    name_occurrence: dict[str, int] = {}
    consecutive_failures = 0
    stopped_by_user = False
    exam_label = f"{course_label} - {row_name}"

    for index, (raw_name, sidebar_score) in enumerate(student_rows):
        if should_stop():
            emit("  Kullanıcı isteğiyle durduruldu.")
            stopped_by_user = True
            break

        occurrence = name_occurrence.get(raw_name, 0) + 1
        name_occurrence[raw_name] = occurrence
        display_name = raw_name if occurrence == 1 else f"{raw_name} ({occurrence})"

        log_key = f"{exam_label} - {display_name}"
        if log_key in captured_titles:
            emit(f"  [{index + 1}/{len(student_rows)}] {display_name} — atlandı (zaten var)")
            totals["skip"] += 1
            continue

        emit(f"  [{index + 1}/{len(student_rows)}] {display_name}")
        try:
            entry = capture_student(
                page,
                exam_dir=exam_dir,
                dom_name=raw_name,
                occurrence_index=occurrence - 1,
                display_name=display_name,
                sidebar_score=sidebar_score,
                exam_label=exam_label,
                exam_name=row_name,
                roster=roster,
            )
            emit(f"    OK  onay={entry['onay']}  puan={entry['puan']}")
            if entry["bozuk_gorsel_sayisi"] > 0:
                emit(
                    f"    UYARI  {entry['bozuk_gorsel_sayisi']} görsel bozuk/eksik "
                    "görünüyor, PDF'i elle kontrol et."
                )
            totals["ok"] += 1
            consecutive_failures = 0
        except Exception as exc:
            if is_browser_closed_error(exc):
                raise
            emit(f"    HATA/gönderilmemiş: {exc}")
            totals["fail"] += 1
            consecutive_failures += 1
            # Oturum dustuyse (sayfa login'e yonlendirildi) kalan HER
            # ogrenci de ayni sekilde basarisiz olur - devre kesicinin
            # 5 uzun denemeyi tuketmesini beklemek sadece zaman kaybi,
            # hemen net bir mesajla duruyoruz.
            if not page_on_blackboard(page):
                emit(
                    "  UYARI: Sayfa artık Blackboard'da görünmüyor — oturumun "
                    "süresi dolmuş olabilir. Tarama hemen durduruldu; tekrar "
                    "giriş yapıp taramayı tekrarla (indirilenler atlanacak)."
                )
                break
            if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                emit(
                    f"  UYARI: art arda {consecutive_failures} hata oldu, "
                    f"'{row_name}' için öğrenci taraması burada durduruldu."
                )
                break

        if (index + 1) % BATCH_SIZE == 0 and index + 1 < len(student_rows):
            emit(f"  ... {BATCH_SIZE} öğrenci sonrası kısa mola ({BATCH_PAUSE_S:.0f} sn) ...")
            # Tek parca time.sleep(20) DEGIL: kullanici "Guvenli Cikis"a
            # basarsa (should_stop) molanin bitmesini beklemeden hemen
            # uyanmaliyiz - aksi halde mola + siradaki ogrencinin isleme
            # suresi, GUI'nin cikis beklemesini (SAFE_EXIT_JOIN_TIMEOUT_S)
            # asip kullaniciyi gereksiz bir "zorla kapat?" diyaloguna
            # dusurebiliyordu. Dongu ustundeki should_stop kontrolu molayi
            # takiben zaten temiz cikisi sagliyor.
            pause_end = time.monotonic() + BATCH_PAUSE_S
            while time.monotonic() < pause_end and not should_stop():
                time.sleep(0.2)

    captured_or_known = totals["ok"] + totals["skip"]
    # stopped_by_user iken bu uyari BILEREK verilmiyor: eksikligin sebebi
    # zaten kullanicinin kendi durdurmasi - "eksik olabilir, elle say"
    # uyarisi bu durumda yaniltici bir alarm olurdu (gercek eksik-yakalama
    # durumlariyla karisirdi).
    if expected_submitted is not None and not stopped_by_user and captured_or_known != expected_submitted:
        emit(
            f"  UYARI: Not Defteri'nde '{row_name}' için {expected_submitted} gönderim "
            f"bekleniyordu, {captured_or_known} öğrenci yakalanabildi/zaten vardı "
            f"({totals['fail']} hatalı) - eksik olabilir, PDF'leri elle say."
        )

    totals["navigation_lost"] = False
    try:
        # try_back=False: bu noktada sol panelde onlarca ogrenci arasinda
        # gezinmis olabiliriz, tek bir go_back() ile Not Defteri'ne donme
        # sansi yok denecek kadar az (bkz. return_to_grades_list docstring).
        return_to_grades_list(page, grades_url, try_back=False)
    except Exception as exc:
        # NOT: burada exception'i YUTUP totals'i normal donduruyoruz -
        # aksi halde bu satira kadar BASARIYLA yakalanmis TUM ogrenci
        # PDF'leri (PDF'ler zaten diske yazildi, append_log ile
        # captures.json'a da kaydedildi) cagiran tarafin ok_count/
        # skip_count toplamina hic YANSIMAZDI (totals dondurulmeden
        # exception firlasaydi). navigation_lost=True ile cagirana
        # "sonraki sinava GECME, sayfa bilinmeyen durumda" sinyali
        # veriyoruz.
        emit(
            f"  UYARI: '{row_name}' sonrasi Not Defteri'ne donus basarisiz "
            f"({exc}) - bu sinavdaki PDF'ler zaten diske kaydedildi, ama "
            "sayfa artik bilinmeyen bir durumda oldugu icin tarama burada "
            "guvenle durdurulmali."
        )
        totals["navigation_lost"] = True
    return totals


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
            print("2) Taranacak dersin 'Not Defteri' sayfasina git.")
            print("3) Sayfa tam yuklendiginde buraya donup ENTER'a bas.\n")
            input("Hazir oldugunda ENTER: ")
            try:
                page = resolve_active_page(context) or page
            except Exception:
                pass

            grades_url = live_url(page)
            course_label = derive_course_label(page)
            course_dir = OUTPUT_DIR / sanitize_filename(course_label, max_chars=DEFAULT_FOLDER_MAX_CHARS)

            exam_rows, excluded_row_names = find_exam_row_names(page)
            print(f"\nDers: {course_label}")
            print(f"{len(exam_rows)} satir {GRADING_STATUS_COMPLETE_MARKERS_LABEL} durumunda "
                  f"bulundu, bunlar islenecek: {[er.name for er in exam_rows]}\n")
            if excluded_row_names:
                print(
                    f"{len(excluded_row_names)} satir atlandi (durumu "
                    f"{GRADING_STATUS_COMPLETE_MARKERS_LABEL} degil, ör. 'Not verilecek "
                    f"bir şey yok'): {excluded_row_names}\n"
                )

            if not exam_rows:
                print(
                    f"UYARI: Hic {GRADING_STATUS_COMPLETE_MARKERS_LABEL} satiri bulunamadi. "
                    "Sayfa yapisi beklenenden farkli olabilir, bana haber ver."
                )

            captured_titles = already_captured_titles()
            # Ogrenci bazli (her sinavdaki tek tek ogrenci PDF'leri) ve sinav
            # bazli (hic ONAY bulunamayan/gecilen sinav SATIRLARI) sayaclar
            # BILEREK ayri tutuluyor - aksi halde toplam sayi "elma armut"
            # karisimi olup ne anlama geldigi belirsizlesirdi.
            ok_count = 0
            student_skip_count = 0
            student_fail_count = 0
            exam_skip_count = len(excluded_row_names)
            exam_fail_count = 0

            for exam_row in exam_rows:
                print(f"Deneniyor: {exam_row.name}")
                try:
                    exam_dir = course_dir / sanitize_filename(
                        exam_row.name, max_chars=DEFAULT_FOLDER_MAX_CHARS
                    )
                    totals = capture_exam_submissions(
                        page,
                        exam_row.name,
                        grades_url,
                        exam_dir,
                        course_label,
                        exam_row.expected_submitted,
                        captured_titles,
                    )
                    ok_count += totals["ok"]
                    student_skip_count += totals["skip"]
                    student_fail_count += totals["fail"]
                    if totals["navigation_lost"]:
                        # capture_exam_submissions zaten UYARI'yi yazdirdi -
                        # sayfa bilinmeyen bir durumda, bir sonraki sinava
                        # GECMEK yanlis satirlara tiklama riski tasir.
                        break
                except NotSubmittedOrNotExam as exc:
                    print(f"  -> Atlandi (sinav/quiz degil ya da gonderilmemis): {exc}")
                    exam_skip_count += 1
                    try:
                        return_to_grades_list(page, grades_url)
                    except Exception as recover_exc:
                        # Sayfa artik Not Defteri listesinde degil - devam
                        # etmek, bir sonraki satiri (belki gercek bir sinavi)
                        # yanlis sayfa durumundan tiklamaya calisip zincirleme
                        # hataya yol acar. Guvenle durmak daha iyi.
                        print(
                            f"  -> Kurtarma basarisiz ({recover_exc}), yanlis satirlara "
                            "tiklama riski tasidigi icin tarama burada durduruldu."
                        )
                        break
                except Exception as exc:
                    print(f"  -> HATA: {exc}")
                    exam_fail_count += 1
                    try:
                        return_to_grades_list(page, grades_url)
                    except Exception as recover_exc:
                        print(
                            f"  -> Kurtarma basarisiz ({recover_exc}), yanlis satirlara "
                            "tiklama riski tasidigi icin tarama burada durduruldu."
                        )
                        break

            print(
                f"\nBitti. Yakalanan (öğrenci PDF'i): {ok_count}\n"
                f"Atlanan öğrenci: {student_skip_count}, hatalı öğrenci: {student_fail_count}\n"
                f"Atlanan sınav satırı: {exam_skip_count}, hatalı sınav satırı: {exam_fail_count}."
            )
            print("Tarayici acik kalacak, kapatmak icin ENTER'a bas.")
            input()
        finally:
            # Beklenmedik bir istisna (ör. Ctrl+C/KeyboardInterrupt) ya da
            # yukaridaki akista yakalanmamis bir hata context.close()'un
            # HIC calismamasina yol acabiliyordu - bu da PROFILE_DIR'da
            # yetim bir SingletonLock birakip bir SONRAKI calistirmanin
            # "user data directory is already in use" hatasiyla
            # basarisiz olmasina neden oluyordu. finally ile close() her
            # kosulda calismasi garanti ediliyor.
            context.close()


if __name__ == "__main__":
    main()
