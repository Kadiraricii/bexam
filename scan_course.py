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
import time
from pathlib import Path
from typing import NamedTuple

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from common import (
    BASE_URL,
    DEFAULT_FOLDER_MAX_CHARS,
    OUTPUT_DIR,
    PROFILE_DIR,
    already_captured_titles,
    browser_launch_kwargs,
    derive_course_label,
    exact_line_pattern,
    is_browser_closed_error,
    live_url,
    load_student_roster,
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
GRADING_STATUS_COMPLETE_PATTERN = re.compile(
    "|".join(re.escape(marker) for marker in GRADING_STATUS_COMPLETE_MARKERS)
)
# Log/uyari mesajlarinda gosterilecek, KENDI ICINDE tirnakli, hazir
# metin - ör. "'Tamamlandı' / 'Tümüne Not Verildi'" - cagiran taraflar
# ayrica tirnak eklemeden dogrudan f-string'e gömebilsin diye.
GRADING_STATUS_COMPLETE_MARKERS_LABEL = " / ".join(f"'{m}'" for m in GRADING_STATUS_COMPLETE_MARKERS)
NOTHING_TO_GRADE_MARKER = "Not verilecek bir şey yok"
# Not Defteri'nin "Not Verilebilir Öğeler" sekmesine ozel, Gönderimler/
# Degerlendirme alt sayfalarinda GORUNMEYEN bir metin - "geri donduk mu"
# kontrolu icin (bkz. return_to_grades_list).
GRADES_LIST_READY_SELECTOR = "text=Not Verilebilir Öğeler"
SUBMITTED_COUNT_PATTERN = re.compile(r"(\d+)\s*/\s*\d+\s*g[öo]nderildi", re.IGNORECASE)
# Blackboard Ultra tablolari bazen gercek <tr> yerine <div role="row">
# olarak render edilebilir - satir ararken HER ZAMAN ikisini de kapsiyoruz,
# aksi halde bir tarafta calisip diger tarafta hicbir satir bulunamayabilir.
ROW_SELECTOR = "tr, [role='row']"


def _first_line(text: str) -> str:
    """text.strip().splitlines()[0] - ama text bos/sadece bosluksa (ör.
    beklenmedik bir DOM durumu) [0] erisimi IndexError firlatmaz, bos
    string doner."""
    lines = text.strip().splitlines()
    return lines[0].strip() if lines else ""


# Ayni ihtiyacin scan_grade_center'da da dogmasiyla (ogrenci satiri
# eslemede substring cakismasi) ortak yardimci common.exact_line_pattern'a
# tasindi - buradaki eski _exact_line_pattern'in birebir aynisi.
_exact_line_pattern = exact_line_pattern


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

    Doner: (islenecek ExamRow listesi, elenen satir adlari)."""
    complete_markers = page.get_by_role("button", name=GRADING_STATUS_COMPLETE_PATTERN)
    included: list[ExamRow] = []
    for i in range(complete_markers.count()):
        marker = complete_markers.nth(i)
        row_text = marker.evaluate(
            "el => (el.closest('tr') "
            "|| el.closest('[role=\"row\"]') "
            "|| el.parentElement.parentElement).innerText"
        )
        first_line = _first_line(row_text)
        count_match = SUBMITTED_COUNT_PATTERN.search(row_text)
        expected_submitted = int(count_match.group(1)) if count_match else None
        included.append(ExamRow(first_line, expected_submitted))

    # 'Not verilecek bir şey yok' durumu tiklanabilir DEGIL (sadece duz
    # metin) - bu yuzden satirlari buton/link araciligiyla degil, dogrudan
    # satir icerigine gore buluyoruz (bkz. ROW_SELECTOR).
    excluded_rows = page.locator(ROW_SELECTOR, has_text=NOTHING_TO_GRADE_MARKER)
    excluded: list[str] = []
    for i in range(excluded_rows.count()):
        row_text = excluded_rows.nth(i).inner_text()
        first_line = _first_line(row_text)
        # ROW_SELECTOR "tr, [role='row']" oldugu icin ayni satir hem <tr>
        # hem onu saran [role='row'] olarak IKI kez eslesebilir - ayni adi
        # iki kez listeleyip sayaci sisirmemek icin tekillestiriyoruz
        # (farkli sinavlarin adlari zaten birbirinden farkli).
        if first_line and first_line not in excluded:
            excluded.append(first_line)
    return included, excluded


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
    page.wait_for_selector(GRADES_LIST_READY_SELECTOR, timeout=15_000)


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

    NOT: ikinci tiklamanin secici (selector) tahmini CANLI Blackboard
    oturumunda henuz dogrulanmadi - burada hata alinirsa terminal
    ciktisi paylasilip secici duzeltilmeli (bkz. capture.py/
    scan_grade_center.py'deki benzer "tek ekran goruntusune bakilarak
    yazildi" notlari)."""
    try:
        page.wait_for_selector("text=ONAY:", timeout=4_000)
        return
    except PlaywrightTimeoutError:
        pass

    submission_row = page.locator(ROW_SELECTOR, has_text=GRADING_STATUS_COMPLETE_PATTERN).first
    try:
        clickable = submission_row.locator("button, a").first
        # Satirin kendisi bir buton/link ICERMEYIP dogrudan tiklanabilir
        # yapilmis olabilir (ör. <tr onclick=...> ya da <div role="row">) -
        # bu durumda button/a hic bulunamaz, satirin KENDISINE tikliyoruz.
        if clickable.count() > 0:
            clickable.click(timeout=5_000)
        else:
            submission_row.click(timeout=5_000)
        page.wait_for_selector("text=ONAY:", timeout=10_000)
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
    # has_text SUBSTRING esler - 'Vize' aranirken 'Vize Mazeret' gibi
    # baska bir sinav satirina cakismamak icin, satirin bir SATIRININ TAM
    # OLARAK row_name'e esit olmasini zorunlu kiliyoruz (bkz.
    # _exact_line_pattern). ROW_SELECTOR: bkz. yukaridaki not (tr / role=row).
    row = page.locator(ROW_SELECTOR, has_text=_exact_line_pattern(row_name)).first
    # .first: satirda GRADING_STATUS_COMPLETE_MARKERS'tan ('Tamamlandı' ya
    # da 'Tümüne Not Verildi') birini iceren birden fazla buton eslesirse
    # (ör. gorunur etiket + ekran okuyucu icin gizli bir kopya) .click()
    # Playwright'in strict-mode ihlaliyle patlar ve sinav yanlis yere
    # "hatali" sayilirdi.
    status_marker = row.get_by_role("button", name=GRADING_STATUS_COMPLETE_PATTERN).first
    status_marker.click()
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
                page, raw_name, occurrence - 1, display_name, sidebar_score, exam_dir, exam_label,
                exam_name=row_name, roster=roster,
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
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            str(PROFILE_DIR), headless=False, **browser_launch_kwargs()
        )
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
        context.close()


if __name__ == "__main__":
    main()
