"""
Hoca (instructor) hesabinda, acik olan bir sinav "Degerlendirme Geri
Bildirimi" panelindeki (sol tarafta ogrenci listesi olan gorunum) TUM
ogrencileri sirayla gezip her biri icin PDF + ONAY kodu yakalar.

Kullanim:
    source .venv/bin/activate
    python3 scan_grade_center.py

Tarayici acilinca SSO ile giris yap, Grade Center'dan herhangi bir
ogrencinin sinav sonucunu ac (sol tarafta "Ogrenciler" listesi cikan
gorunum), sonra terminale donup ENTER'a bas. Script sol listedeki her
ogrenciyi tek tek tiklayip yakalayacak.

Guvenlik onlemleri (bkz. ~/.claude/plans/streamed-painting-hanrahan.md):
- Her tiklamadan sonra ONAY + ogrenci adinin GONDERIM TARIHI blogunda
  gercekten gorundugu dogrulanmadan PDF uretilmez (yanlis ogrenciye
  yanlis PDF riskine karsi).
- Ogrenci listesi panel kaydirilarak toplanir (virtualized/kalabalik
  liste riskine karsi).
- Ayni isimli ogrenciler DOM sirasina (index) gore ayirt edilir.
- Tiklamalar arasinda rastgele gecikme + periyodik mola (bot tespiti
  riskine karsi).
- Art arda cok sayida hata olursa tarama erken durur (oturum dusmesi
  ihtimaline karsi).

Not: Bu script tek bir ekran goruntusune bakilarak yazildi, ilk
calistirmada bazi selector ayarlari gerekebilir - hata cikarsa terminal
ciktisini paylas, duzeltilir.
"""

import random
import re
import time
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from capture import capture_current_page
from common import (
    BASE_URL,
    DEFAULT_FOLDER_MAX_CHARS,
    OUTPUT_DIR,
    PROFILE_DIR,
    already_captured_titles,
    browser_launch_kwargs,
    derive_course_label,
    exact_line_pattern,
    extract_page_info,
    format_student_pdf_stem,
    load_student_roster,
    normalize_roster_name,
    normalize_score,
    resolve_active_page,
    sanitize_filename,
    student_pdf_identity_suffix_chars,
)

STUDENT_ROW_SCORE_PATTERN = re.compile(r"\d+([.,]\d+)?\s*/\s*\d+([.,]\d+)?")
ONAY_PATTERN = re.compile(r"ONAY:\s*([A-F0-9]+)")
SUBMIT_DATE_MARKER = "GÖNDERİM TARİHİ"
HEADER_WINDOW_CHARS = 400

MAX_WAIT_ATTEMPTS = 20
WAIT_STEP_MS = 500

MIN_CLICK_DELAY_S = 1.0
MAX_CLICK_DELAY_S = 3.0
BATCH_SIZE = 20
BATCH_PAUSE_S = 20.0
MAX_CONSECUTIVE_FAILURES = 5


def collect_visible_rows(page: Page) -> list[tuple[str, str]]:
    """Gorunen her ogrenci satirindan (ad, not) cifti toplar.

    Not, sidebar'daki skor rozetinden (ör. '50 / 100') alinir - bu,
    daha sonra tiklanan sayfadaki notla karsilastirilarak dogru
    ogrenciye tiklandigini teyit etmek icin kullanilir.

    ONEMLI - sadece "capture anchor" icin kullanilir: bu fonksiyon
    SAYISAL NOT DESENI iceren satirlarla SINIRLI (bilerek - sayfa genelinde
    guvenli bir baslangic noktasi bulmak icin). Henuz notlandirilmamis/
    muaf gibi durumdaki ogrenciler bu filtreden GECMEZ - onlari da
    yakalamak icin bkz. collect_visible_rows_in_container (panelin kendi
    DOM alt agaciyla sinirli oldugu icin sayisal not sarti aramadan da
    guvenle genisletilebiliyor).
    """
    candidates = page.get_by_role("button").filter(has_text=STUDENT_ROW_SCORE_PATTERN)
    rows: list[tuple[str, str]] = []
    for i in range(candidates.count()):
        text = candidates.nth(i).inner_text().strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        score_match = STUDENT_ROW_SCORE_PATTERN.search(text)
        score = score_match.group(0).strip() if score_match else ""
        rows.append((lines[0], score))
    return rows


def collect_visible_rows_in_container(scroll_handle) -> list[tuple[str, str]]:
    """scroll_handle (bulunan kaydirma konteyneri) icindeki TUM satir
    butonlarini toplar - collect_visible_rows'un aksine sayisal not
    ZORUNLU DEGIL.

    Neden: collect_visible_rows sadece 'X / Y' gibi sayisal bir not
    deseni iceren satirlari sayiyordu. Ama bir ogrenci henuz
    notlandirilmamis, muaf tutulmus ya da baska bir durum metniyle
    gosteriliyor olabilir (hocanin kendi ekran goruntusundeki
    '15/20 GÖNDERİLDİ' toplami da bunu dusunduruyor) - bu satirlar sayisal
    desene UYMADIGI icin collect_visible_rows onlari HIC TOPLAMAZDI, yani
    o ogrenciler script'e tamamen gorunmezdi, hicbir uyari da verilmezdi.

    Bu fonksiyon konteynerin KENDI DOM ALT AGACIYLA sinirli oldugu icin
    (sayfa geneli degil), sayisal-not sartini kaldirmak sayfadaki
    ilgisiz butonlarin (navigasyon, sekme secici vb.) karismasi riskini
    dogurmuyor - zaten bulunmus ogrenci panelinin icindeyiz.
    """
    raw_texts = scroll_handle.evaluate(
        """el => {
            const buttons = Array.from(el.querySelectorAll('[role="button"], button'));
            return buttons.map((b) => (b.innerText || '').trim()).filter(Boolean);
        }"""
    )
    rows: list[tuple[str, str]] = []
    for text in raw_texts:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        score_match = STUDENT_ROW_SCORE_PATTERN.search(text)
        score = score_match.group(0).strip() if score_match else ""
        rows.append((lines[0], score))
    return rows


def find_scroll_container(page: Page, anchor_text: str):
    """Verilen ogrenci satirindan yukari dogru en yakin kaydirilabilir atayi bulur."""
    anchor = page.get_by_role("button", name=re.compile(re.escape(anchor_text))).first
    handle = anchor.evaluate_handle(
        """el => {
            let node = el;
            while (node && node !== document.body) {
                const style = getComputedStyle(node);
                if (node.scrollHeight > node.clientHeight + 4
                    && /(auto|scroll)/.test(style.overflowY)) {
                    return node;
                }
                node = node.parentElement;
            }
            return null;
        }"""
    )
    return handle


def find_student_rows(page: Page) -> list[tuple[str, str]]:
    """Sol 'Ogrenciler' panelindeki TUM satirlarin (ad, not) ciftini toplar.

    Panel virtualized olabilir (sadece gorunenler DOM'da), bu yuzden
    adim adim kaydirip her adimda goruneni topluyoruz. Ayni isim birden
    fazla ogrencide olabilecegi icin burada dedup YAPILMIYOR - sirayla
    tum satirlar (tekrarlar dahil) toplanip cagiran tarafta ayirt
    ediliyor.

    Ilk "capture anchor" (kaydirma konteynerini bulmak icin en az bir
    satira ihtiyacimiz var) sayisal-not-filtreli collect_visible_rows ile
    bulunur; konteyner bulunduktan SONRA gercek toplama
    collect_visible_rows_in_container ile yapilir - bu, notu olmayan/
    henuz notlandirilmamis ogrencileri de yakalar (bkz. o fonksiyonun
    docstring'i).
    """
    rows = collect_visible_rows(page)
    if not rows:
        return rows

    scroll_handle = find_scroll_container(page, rows[0][0])
    if scroll_handle is None:
        return rows

    seen_order: list[tuple[str, str]] = list(collect_visible_rows_in_container(scroll_handle))
    for _ in range(200):  # cok uzun listeler icin ust sinir
        scroll_handle.evaluate("el => { el.scrollTop = el.scrollTop + el.clientHeight * 0.8; }")
        page.wait_for_timeout(200)
        # extend DEGIL, ortusme-birlestirme: pencereler ustuste biner,
        # duz eklemek ayni ogrencileri blok halinde tekrarlatirdi (bkz.
        # _merge_scroll_window docstring'i).
        seen_order = _merge_scroll_window(
            seen_order, collect_visible_rows_in_container(scroll_handle)
        )

        # Kaydirma sinirina ulasilip ulasilmadigini scrollTop'a bakarak anlariz.
        at_bottom = scroll_handle.evaluate(
            "el => el.scrollTop + el.clientHeight >= el.scrollHeight - 2"
        )
        if at_bottom:
            break

    scroll_handle.evaluate("el => { el.scrollTop = 0; }")
    page.wait_for_timeout(200)
    return seen_order


def _merge_scroll_window(accumulated: list[tuple[str, str]], window: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Kaydirma sirasinda toplanan yeni pencereyi, o ana kadar birikmis
    listeyle CAKISMA PAYINI DUSEREK birlestirir: birikmis listenin SONU
    ile yeni pencerenin BASI ayni diziyse (en buyuk ortusme), sadece
    pencerenin kalan (yeni) kismi eklenir.

    Neden: eski yaklasim ("art arda birebir ayni satiri at") SADECE
    bitisik tekrarlari yakaliyordu - oysa kaydirma pencereleri ustuste
    biner (her adimda %80 kaydiriliyor, %20 ortusme kalir) ve liste
    virtualized DEGILSE her pasta TUM liste yeniden toplanir. Iki durumda
    da tekrarlar bitisik degil BLOK halinde gelir (ör. A,B,C,D + C,D,E,F)
    ve eski sadelestirme onlari YAKALAYAMAZDI - ayni ogrenci listeye 2+
    kez girip sahte '(2)' kopya PDF'leri, sisik sayimlar ve bosa gecen
    tarama suresi uretirdi. Ortusme birlestirmesi hem bu blok tekrarlarini
    dogru eler hem de listenin FARKLI konumlarindaki gercek ayni-isimli
    ogrencileri (ortusme disinda kaldiklari icin) korur."""
    if not accumulated:
        return list(window)
    if not window:
        return accumulated
    max_overlap = min(len(accumulated), len(window))
    for overlap in range(max_overlap, 0, -1):
        if accumulated[-overlap:] == window[:overlap]:
            return accumulated + window[overlap:]
    return accumulated + window


def header_matches_student(body_text: str, student_name: str) -> bool:
    """GONDERIM TARIHI blogunun hemen ustundeki pencere icinde ogrenci
    adinin gectigini dogrular.

    Duz `student_name in window` (substring) KULLANILMIYOR: 'AYŞE KAYA'
    aranirken sayfada aslinda 'AYŞE KAYAALP' yaziyorsa substring kontrol
    YANLIS POZITIF verirdi - yani yanlis ogrencinin sayfasi 'dogrulandi'
    sayilip YANLIS icerik YANLIS isimle PDF'lenebilirdi. Adin iki yaninda
    baska bir harf/rakam OLMAMASINI sart kosuyoruz (Turkce harfler de \\w
    kapsaminda oldugu icin 'KAYAALP' icindeki 'KAYA' artik eslesmez)."""
    idx = body_text.find(SUBMIT_DATE_MARKER)
    if idx == -1:
        return False
    window = body_text[max(0, idx - HEADER_WINDOW_CHARS):idx]
    return re.search(rf"(?<!\w){re.escape(student_name)}(?!\w)", window) is not None


def capture_student(
    page: Page,
    dom_name: str,
    occurrence_index: int,
    display_name: str,
    sidebar_score: str,
    exam_dir: Path,
    exam_label: str,
    *,
    exam_name: str,
    roster: dict[str, str] | None = None,
) -> dict:
    """dom_name: sayfada gorunen ham ad (tiklama + dogrulama icin kullanilir).
    occurrence_index: bu isimdeki KACINCI ogrenci (0 = ilk) - isim bazli
    filtrelenmis locator'a gore hesaplanir, aksi halde ayni isimli
    ogrencilerde yanlis satira tiklanabilir.
    display_name: ayni isim tekrarlarinda '(2)' gibi ek tasiyan, dosya adi/
    log icin kullanilan ayirt edici ad.
    sidebar_score: soldaki listede bu ogrenci icin gorunen not (ör. '50/100') -
    acilan sayfadaki notla karsilastirilip UCUNCU bir dogrulama katmani
    olarak kullanilir (ONAY + isim + not ucu birden tutarsa PDF uretilir).
    exam_name: PDF dosya adinin BASINA gelecek sinav adi (bkz.
    common.format_student_pdf_stem) - exam_label'dan (dedup/log anahtari
    icin kullanilir, farkli sekilde formatlanmis olabilir) BILEREK AYRI
    tutuluyor.
    roster: {common.normalize_roster_name(ad): ogrenci_no} sozlugu (bkz.
    common.load_student_roster) - dom_name bu sozlukte bulunursa PDF
    adina ogrenci numarasi da eklenir, bulunamazsa (ör. 'Öğrenci Tara'
    hic calistirilmadiysa) o bolum sessizce atlanir."""
    # TAM SATIR eslesmesi (exact_line_pattern) - substring eslesme
    # ('AYŞE KAYA' ararken 'AYŞE KAYAALP' satirina da cakisma) hem yanlis
    # ogrenciye tiklanmasina hem de occurrence_index'in kaymasina yol
    # acabilirdi. dom_name zaten ayni sayfadaki satirin ilk satirindan
    # okundugu icin normalde birebir eslesir; sayfa yapisi beklenmedik
    # sekilde farkliysa (0 eslesme) eski substring davranisina duseriz -
    # yanlis-pozitif riskine ragmen hic tiklayamamaktan iyi, cunku asil
    # guvence zaten tiklama SONRASI icerik dogrulamasi (ONAY + isim + not).
    rows = page.get_by_role("button").filter(has_text=exact_line_pattern(dom_name))
    if rows.count() == 0:
        rows = page.get_by_role("button", name=re.compile(re.escape(dom_name)))
    safe_index = min(occurrence_index, max(rows.count() - 1, 0))
    rows.nth(safe_index).click()

    time.sleep(random.uniform(MIN_CLICK_DELAY_S, MAX_CLICK_DELAY_S))

    matched = False
    body_text = ""
    expected_score = normalize_score(sidebar_score) if sidebar_score else None
    for _ in range(MAX_WAIT_ATTEMPTS):
        body_text = page.inner_text("body")
        info = extract_page_info(body_text)
        score_ok = (
            not expected_score
            or not info["puan"]
            or normalize_score(info["puan"]) == expected_score
        )
        if ONAY_PATTERN.search(body_text) and header_matches_student(body_text, dom_name) and score_ok:
            matched = True
            break
        page.wait_for_timeout(WAIT_STEP_MS)

    if not matched:
        raise RuntimeError(
            f"'{display_name}' icin dogru icerik dogrulanamadi (ONAY yok, "
            "basliktaki isim eslesmiyor, ya da sayfadaki not sidebar'daki "
            f"notla ({sidebar_score!r}) uyusmuyor) - gondermemis olabilir, "
            "sayfa gecisi yavas kalmis olabilir ya da oturum dusmus olabilir"
        )

    student_no = (roster or {}).get(normalize_roster_name(dom_name))
    pdf_filename = format_student_pdf_stem(exam_name, display_name, student_no)
    return capture_current_page(
        page,
        output_dir=exam_dir,
        filename=pdf_filename,
        # Windows MAX_PATH kirpmasi gerekirse sondaki no+ad kimlik bolumu
        # korunsun - kirpma sadece bastaki sinav adindan yapilsin (bkz.
        # common.student_pdf_identity_suffix_chars docstring'i: aksi halde
        # iki ogrenci ayni dosya adina dusup biri digerini ezebilirdi).
        filename_protect_suffix_chars=student_pdf_identity_suffix_chars(display_name, student_no),
        log_title=f"{exam_label} - {display_name}",
    )


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
        print("2) Grade Center'dan herhangi bir ogrencinin sinav sonucunu ac")
        print("   (sol tarafta 'Ogrenciler' listesi gorunmeli).")
        print("3) Sayfa tam yuklendiginde buraya donup ENTER'a bas.\n")
        input("Hazir oldugunda ENTER: ")
        try:
            page = resolve_active_page(context) or page
        except Exception:
            pass

        exam_label = derive_course_label(page)
        exam_dir = OUTPUT_DIR / sanitize_filename(exam_label, max_chars=DEFAULT_FOLDER_MAX_CHARS)
        # Bu bagimsiz akista Not Defteri baglami yok, exam_dir'den baska
        # bir "ders klasoru" da yok - roster'i (varsa) bu tek klasorden
        # okumaya calisiyoruz (bkz. scan_students.py / 'Öğrenci Tara').
        roster = load_student_roster(exam_dir)

        print("Ogrenci listesi taraniyor (kaydirarak toplaniyor)...")
        student_rows = find_student_rows(page)
        print(f"\nSinav: {exam_label}")
        print(f"{len(student_rows)} ogrenci satiri bulundu.\n")

        if not student_rows:
            print(
                "UYARI: Hic ogrenci satiri bulunamadi. Sol panelde "
                "'Ogrenciler' sekmesinin acik oldugundan emin ol."
            )

        captured_titles = already_captured_titles()
        name_occurrence: dict[str, int] = {}
        ok_count = 0
        skip_count = 0
        fail_count = 0
        consecutive_failures = 0

        for row_index, (raw_name, sidebar_score) in enumerate(student_rows):
            occurrence = name_occurrence.get(raw_name, 0) + 1
            name_occurrence[raw_name] = occurrence
            display_name = raw_name if occurrence == 1 else f"{raw_name} ({occurrence})"

            log_key = f"{exam_label} - {display_name}"
            if log_key in captured_titles:
                print(f"Atlaniyor (zaten yakalanmis): {display_name}")
                skip_count += 1
                continue

            print(f"Yakalaniyor [{row_index + 1}/{len(student_rows)}]: {display_name} (not: {sidebar_score})")
            try:
                entry = capture_student(
                    page, raw_name, occurrence - 1, display_name, sidebar_score, exam_dir, exam_label,
                    exam_name=exam_label, roster=roster,
                )
                print(f"  -> OK  onay={entry['onay']}  puan={entry['puan']}  pdf={entry['pdf']}")
                if entry["bozuk_gorsel_sayisi"] > 0:
                    print(
                        f"  -> UYARI: {entry['bozuk_gorsel_sayisi']} gorsel bozuk/eksik "
                        "gorunuyor, PDF'i elle kontrol et."
                    )
                ok_count += 1
                consecutive_failures = 0
            except Exception as exc:
                print(f"  -> HATA/gonderilmemis: {exc}")
                fail_count += 1
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(
                        f"\nUYARI: Art arda {consecutive_failures} hata olustu. "
                        "Muhtemelen oturum dustu ya da baglanti sorunu var. "
                        "Tarama durduruluyor - kontrol edip tekrar calistir "
                        "(zaten yakalananlar atlanacak).\n"
                    )
                    break

            if (row_index + 1) % BATCH_SIZE == 0:
                print(f"  ... {BATCH_SIZE} ogrenci sonrasi kisa mola ({BATCH_PAUSE_S:.0f} sn) ...")
                time.sleep(BATCH_PAUSE_S)

        print(
            f"\nBitti. Yakalanan: {ok_count}, atlanan: {skip_count}, "
            f"hatali/gonderilmemis: {fail_count}."
        )
        print("Tarayici acik kalacak, kapatmak icin ENTER'a bas.")
        input()
        context.close()


if __name__ == "__main__":
    main()
