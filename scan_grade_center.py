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
import sys
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
    clear_stale_profile_lock,
    derive_course_label,
    exact_line_pattern,
    extract_page_info,
    format_student_pdf_stem,
    is_profile_lock_error,
    load_student_roster,
    normalize_roster_name,
    normalize_score,
    page_on_blackboard,
    resolve_active_page,
    sanitize_filename,
    student_pdf_identity_suffix_chars,
)

STUDENT_ROW_SCORE_PATTERN = re.compile(r"\d+([.,]\d+)?\s*/\s*\d+([.,]\d+)?")
# CANLI DOGRULANAN HATA: bkz. common.ONAY_PATTERN'daki ayni notun tam
# metni - Blackboard'un "Çevrimiçi Test/Quiz" degerlendirme sayfasinda bu
# etiketler TUM BUYUK HARF DEGIL, cumle-ici bicimde ("Onay: <hex kod>",
# "Gönderim tarihi: <tarih>") gorunuyor. re.IGNORECASE eklenmeden once bu
# sinav turunde ONAY_PATTERN hicbir zaman eslesmiyor, capture_student'in
# dogrulama dongusu HER ogrenci icin sonsuza kadar basarisiz oluyordu -
# yani bu sinav turunde TEK BIR PDF bile uretilemiyordu.
ONAY_PATTERN = re.compile(r"ONAY:\s*([A-F0-9]+)", re.IGNORECASE)
# SUBMIT_DATE_MARKER_PATTERN (asagida) ayni sebeple regex+IGNORECASE
# olarak tutuluyor - duz `str.find()` kullanan eski SUBMIT_DATE_MARKER
# sabiti de ayni harf-kasasi sorununu tasiyordu (header_matches_student
# icindeki .find() TUM BUYUK HARFE gore ariyordu, kucuk/cumle-ici
# bicimde hicbir zaman bulunamiyordu).
SUBMIT_DATE_MARKER_PATTERN = re.compile(r"G[ÖO]NDER[İI]M TAR[İI]H[İI]", re.IGNORECASE)
HEADER_WINDOW_CHARS = 400

MAX_WAIT_ATTEMPTS = 20
WAIT_STEP_MS = 500

MIN_CLICK_DELAY_S = 1.0
MAX_CLICK_DELAY_S = 3.0
BATCH_SIZE = 20
BATCH_PAUSE_S = 20.0
MAX_CONSECUTIVE_FAILURES = 5

SYSTEM_EXCLUDE_KEYWORDS = {
    "kurslar",
    "öğrenciler",
    "sorular",
    "not verme durumu",
    "tüm not verme durumları",
    "notları gönder",
    "not defteri",
    "not verilebilir öğeler",
    "notlandırma",
    "geri",
    "kapat",
}

# CANLI DOGRULANAN gercek DOM (kullanicinin paylastigi HTML): sol
# ogrenci paneli role="menu" + aria-label="Öğrenciler" olan bir <ul>.
# Bu, ESKI genel PANEL_FALLBACK_SELECTOR'DAN ONCE denenir - cunku bu
# sayfa turunde ('flexible-attempt-grading-panel' class'i YOK,
# data-page-title'da 'Not Verme' de gecmiyor) eski selector HER ZAMAN
# 0 sonuc verip panel'in SESSIZCE TUM SAYFAYA (page) genislemesine yol
# aciyordu. Bunun SOMUT sonucu: ogrencinin ADI sayfada BIRDEN FAZLA
# yerde geciyor (ör. sayfa USTUNDEKI kompakt basliktaki isim VE sol
# paneldeki liste satirindaki isim AYNI metni tasiyor) - panel=page
# iken find_scroll_container'in `get_by_text(...).first`'i SAYFADAKI
# ILK eslesmeyi (kompakt basliktaki isim - kaydirilamaz, sabit bir alan)
# secebiliyordu. Oradan yukari kaydirilabilir bir ata ARANINCA hicbir
# zaman bulunamiyor, find_scroll_container Python `None` DEGIL, JS
# 'null' deger SARAN bir JSHandle donduruyordu (bkz. find_student_rows
# icindeki json_value() kontrolu) - collect_visible_rows_in_container
# bu null uzerinde querySelectorAll cagirinca "Cannot read properties
# of null" hatasiyla PATLIYORDU (CANLI DOGRULANAN cokme). Panel'i bu
# DAR, dogru selector'la sinirlamak hem bu cakismayi onluyor hem de
# kaydirmanin GERCEKTEN dogru konteynerden baslamasini sagliyor.
STUDENT_LIST_PANEL_SELECTOR = '[role="menu"][aria-label="Öğrenciler"]'
PANEL_FALLBACK_SELECTOR = ".flexible-attempt-grading-panel, [data-page-title*='Not Verme']"


def _resolve_student_panel(page: Page):
    """collect_visible_rows/find_scroll_container/capture_student'in
    UCUNUN de paylastigi panel-bulma mantigi - bkz.
    STUDENT_LIST_PANEL_SELECTOR docstring'i. Once EN DAR/dogrulanmis
    selector'i, sonra eski genel fallback'i, o da bulunamazsa TUM
    sayfayi (page) dener."""
    panel = page.locator(STUDENT_LIST_PANEL_SELECTOR).first
    if panel.count() > 0:
        return panel
    panel = page.locator(PANEL_FALLBACK_SELECTOR).first
    if panel.count() > 0:
        return panel
    return page


def collect_visible_rows(page: Page) -> list[tuple[str, str]]:
    """Gorunen her ogrenci satirindan (ad, not) cifti toplar.

    Not, sidebar'daki skor rozetinden (ör. '50 / 100') alinir - bu,
    daha sonra tiklanan sayfadaki notla karsilastirilarak dogru
    ogrenciye tiklandigini teyit etmek icin kullanilir.

    CANLI DOGRULANAN HATA: kullanicinin paylastigi gercek DOM'da (bkz.
    proje gecmisi) ogrenci satirlari `role="menuitem"` olan `<li>`
    elemanlari - isim VE puan bu `<li>`'nin ICINDE farkli alt div'lere
    bolunmus durumda (isim iceren `userCardWrapper` div'inin KENDISINDE
    puan YOK, puan ayri bir kardes div'de). Asagidaki selector listesinde
    `[role="menuitem"]` OLMADAN once, isim+puani BIRLIKTE tasiyan tek
    eleman (bu `<li>`) hicbir selector'a uymuyordu - sonuc: bu sayfada
    HER ZAMAN '0 öğrenci satırı bulundu' (tarama sessizce hicbir sey
    yakalamiyordu, hata bile vermiyordu)."""
    panel = _resolve_student_panel(page)

    selectors = [
        '[class*="cardWrapper"]',
        '[class*="CardWrapper"]',
        '[class*="userCard"]',
        '[role="button"]',
        'button',
        '[role="option"]',
        '[role="listitem"]',
        '[role="treeitem"]',
        '[role="menuitem"]',
    ]
    candidates = panel.locator(", ".join(selectors)).filter(has_text=STUDENT_ROW_SCORE_PATTERN)
    rows: list[tuple[str, str]] = []
    seen_names: set[str] = set()

    for i in range(candidates.count()):
        candidate = candidates.nth(i)
        try:
            text = candidate.inner_text().strip()
        except Exception:
            continue
        if not text:
            continue
        scores_found = STUDENT_ROW_SCORE_PATTERN.findall(text)
        if len(scores_found) > 1:
            continue

        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            continue

        student_name = lines[0]
        if student_name.lower() in SYSTEM_EXCLUDE_KEYWORDS or any(kw in student_name.lower() for kw in SYSTEM_EXCLUDE_KEYWORDS):
            continue
        if STUDENT_ROW_SCORE_PATTERN.match(student_name) or len(student_name) < 2:
            continue

        score_match = STUDENT_ROW_SCORE_PATTERN.search(text)
        score = score_match.group(0).strip() if score_match else ""

        row_key = f"{student_name}_{score}"
        if row_key not in seen_names:
            seen_names.add(row_key)
            rows.append((student_name, score))

    return rows


def collect_visible_rows_in_container(scroll_handle) -> list[tuple[str, str]]:
    """scroll_handle (bulunan kaydirma konteyneri) icindeki TUM satir
    butonlarini toplar - collect_visible_rows'un aksine sayisal not
    ZORUNLU DEGIL.

    CANLI DOGRULANAN HATA: kullanicinin paylastigi gercek DOM'da her
    ogrenci satiri icin ASAGIDAKI selector listesi IKI AYRI eleman
    eslestiriyor - disaridaki `[role="menuitem"]` olan `<li>` (isim +
    puan) VE onun ICINDEKI, isim iceren "userCardWrapper" benzeri bir
    div (`[class*="userCard"]` ile eslesen, ama puan TASIMAYAN - puan
    ayri bir kardes div'de). collect_visible_rows'un aksine burada puan
    sarti ARANMADIGI icin (kasitli - notu olmayan ogrencileri de
    yakalamak icin, bkz. yukaridaki docstring) ic-ice bu iki eslesme
    AYRI AYRI satir sayiliyordu - HER ogrenci TAM 2 KEZ (aynen ayni ONAY
    koduyla) yakalanip PDF'i 2 kez uretiliyordu (CANLI DOGRULANDI: 15
    gercek ogrenci yerine '30 öğrenci satırı bulundu', her isim aynen
    '(2)' kopyasiyla ayni ONAY kodunu veriyordu).

    Cozum: sonuc kumesinde birbirini ICEREN (biri digerinin DOM atasi
    olan) eslesmeler varsa sadece EN DISTAKI (en genis) olani tutuyoruz -
    "topLevel" filtresi. Boylece rol/class fark etmeksizin, HANGI iki
    selector cakisirsa cakissin, ayni gorsel satir ARTIK SADECE BIR KEZ
    sayiliyor."""
    raw_texts = scroll_handle.evaluate(
        """el => {
            const all = Array.from(el.querySelectorAll('[class*="cardWrapper"], [class*="CardWrapper"], [class*="userCard"], [role="button"], button, [role="option"], [role="listitem"], [role="menuitem"]'));
            const topLevel = all.filter(node => !all.some(other => other !== node && other.contains(node)));
            return topLevel.map((b) => (b.innerText || '').trim()).filter(Boolean);
        }"""
    )
    rows: list[tuple[str, str]] = []
    for text in raw_texts:
        scores_found = STUDENT_ROW_SCORE_PATTERN.findall(text)
        if len(scores_found) > 1:
            continue
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            continue
        student_name = lines[0]
        if student_name.lower() in SYSTEM_EXCLUDE_KEYWORDS or len(student_name) < 2:
            continue
        score_match = STUDENT_ROW_SCORE_PATTERN.search(text)
        score = score_match.group(0).strip() if score_match else ""
        rows.append((student_name, score))
    return rows


def find_scroll_container(page: Page, anchor_text: str):
    """Verilen ogrenci satirindan yukari dogru en yakin kaydirilabilir atayi bulur."""
    panel = _resolve_student_panel(page)

    anchor = panel.get_by_text(anchor_text, exact=True).first
    if anchor.count() == 0:
        anchor = panel.get_by_role("button", name=re.compile(re.escape(anchor_text))).first
    if anchor.count() == 0:
        anchor = panel.get_by_text(re.compile(re.escape(anchor_text))).first
    if anchor.count() == 0:
        return None
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

    CANLI DOGRULANAN COKME: find_scroll_container hicbir kaydirilabilir
    ata bulamazsa Python `None` DEGIL, JS `null` degerini SARAN bir
    JSHandle donduruyor (`evaluate_handle` HER ZAMAN bir JSHandle
    dondurur, alttaki deger null olsa bile) - `scroll_handle is None`
    kontrolu bunu YAKALAYAMIYORDU, sonrasinda collect_visible_rows_in_
    container bu null'un uzerinde `querySelectorAll` cagirinca "Cannot
    read properties of null" ile PATLIYORDU. `json_value()` ile alttaki
    GERCEK JS degerini kontrol ediyoruz - `null` ise Python `None`
    doner, boylece bu durumu da guvenle yakalayip (kaydirmadan vazgecip)
    en azindan o ana kadar GORUNEN satirlarla devam ediyoruz."""
    rows = collect_visible_rows(page)
    if not rows:
        return rows

    scroll_handle = find_scroll_container(page, rows[0][0])
    if scroll_handle is None:
        return rows
    try:
        if scroll_handle.json_value() is None:
            return rows
    except Exception:
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
    marker_match = SUBMIT_DATE_MARKER_PATTERN.search(body_text)
    if marker_match is None:
        return False
    idx = marker_match.start()
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
    panel = _resolve_student_panel(page)

    selectors = [
        '[class*="cardWrapper"]',
        '[class*="CardWrapper"]',
        '[class*="userCard"]',
        '[role="button"]',
        'button',
        '[role="option"]',
        '[role="listitem"]',
        '[role="treeitem"]',
        '[role="menuitem"]',
    ]
    rows = panel.locator(", ".join(selectors)).filter(has_text=exact_line_pattern(dom_name))
    if rows.count() == 0:
        rows = panel.get_by_role("button", name=re.compile(re.escape(dom_name)))
    if rows.count() == 0:
        rows = panel.get_by_text(exact_line_pattern(dom_name))
    if rows.count() == 0:
        rows = panel.get_by_text(dom_name)

    safe_index = min(occurrence_index, max(rows.count() - 1, 0))
    rows.nth(safe_index).click()

    time.sleep(random.uniform(MIN_CLICK_DELAY_S, MAX_CLICK_DELAY_S))

    matched = False
    body_text = ""
    info = extract_page_info(body_text)
    score_ok = False
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
        # Hangi TEK KOSULUN basarisiz oldugunu (ONAY bulunamadi mi, isim
        # baslikta gecmiyor mu, yoksa puan mi tutmuyor) ACIKCA soyluyoruz -
        # eskiden uc kosulu da tek bir genel cumlede birlestiren mesaj,
        # bir sonraki hatanin GERCEK sebebini (ör. sayfa yapisi degisti mi,
        # yavas mi yuklendi, gercekten gonderilmemis mi) anlamak icin
        # kullaniciyi tekrar tekrar log/ekran goruntusu paylasmaya
        # zorluyordu - artik son denemede GERCEKTEN NE bulundugu (varsa)
        # mesaja dahil ediliyor.
        onay_found = ONAY_PATTERN.search(body_text)
        name_found = header_matches_student(body_text, dom_name)
        reasons = []
        if not onay_found:
            reasons.append("sayfada 'ONAY:'/'Onay:' metni hiç bulunamadı")
        if onay_found and not name_found:
            reasons.append(
                f"ONAY bulundu ama 'GÖNDERİM TARİHİ'/'Gönderim tarihi' bloğunun "
                f"hemen üstünde '{dom_name}' adı geçmiyor"
            )
        if onay_found and name_found and not score_ok:
            reasons.append(
                f"sayfadaki not ({info['puan']!r}) sidebar'daki notla "
                f"({sidebar_score!r}) uyuşmuyor"
            )
        if not reasons:
            reasons.append("nedeni belirlenemedi (kosullar son denemede aninda degisti olabilir)")
        raise RuntimeError(
            f"'{display_name}' icin dogru icerik dogrulanamadi: {'; '.join(reasons)} "
            "- gondermemis olabilir, sayfa gecisi yavas kalmis olabilir ya da "
            "oturum dusmus olabilir"
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
    # Windows konsolu varsayilan olarak UTF-8 olmayan bir kod sayfasi
    # (ör. cp1254) kullanabiliyor - Turkce karakterler/tire (—) iceren
    # print() cagrilari bu durumda UnicodeEncodeError firlatip taramayi
    # ortadan kesebiliyordu. errors="replace" ile en kotu ihtimalde
    # goruntu bozulur ama program COKMEZ.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        try:
            context = p.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=False, **browser_launch_kwargs()
            )
        except Exception as exc:
            # gui.py'deki ayni pattern: sadece PROFIL KILIDI hatasindan
            # sonra (zorla kapatma/coke sonrasi yetim SingletonLock)
            # temizleyip bir kez daha deniyoruz - bkz.
            # clear_stale_profile_lock docstring'i.
            if not is_profile_lock_error(exc):
                raise
            clear_stale_profile_lock(PROFILE_DIR)
            context = p.chromium.launch_persistent_context(
                str(PROFILE_DIR), headless=False, **browser_launch_kwargs()
            )

        try:
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
                    # Oturum dustuyse (sayfa login'e yonlendirildi) kalan HER
                    # ogrenci de ayni sekilde basarisiz olur - devre kesicinin
                    # 5 uzun denemeyi tuketmesini beklemek sadece zaman kaybi,
                    # hemen net bir mesajla duruyoruz (bkz. scan_course.py'deki
                    # ayni pattern).
                    if not page_on_blackboard(page):
                        print(
                            "\nUYARI: Sayfa artık Blackboard'da görünmüyor - oturumun "
                            "süresi dolmuş olabilir. Tarama hemen durduruldu; tekrar "
                            "giriş yapıp taramayı tekrarla (indirilenler atlanacak).\n"
                        )
                        break
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        print(
                            f"\nUYARI: Art arda {consecutive_failures} hata olustu. "
                            "Muhtemelen oturum dustu ya da baglanti sorunu var. "
                            "Tarama durduruluyor - kontrol edip tekrar calistir "
                            "(zaten yakalananlar atlanacak).\n"
                        )
                        break

                # Son ogrenciden SONRA gereksiz bir mola vermemek icin (roster
                # boyutu BATCH_SIZE'in tam kati oldugunda onceden burada 20
                # saniyelik bos bir bekleme oluyordu) kalan ogrenci olup
                # olmadigi da kontrol ediliyor - bkz. scan_course.py'deki
                # ayni koruma.
                if (row_index + 1) % BATCH_SIZE == 0 and row_index + 1 < len(student_rows):
                    print(f"  ... {BATCH_SIZE} ogrenci sonrasi kisa mola ({BATCH_PAUSE_S:.0f} sn) ...")
                    time.sleep(BATCH_PAUSE_S)

            print(
                f"\nBitti. Yakalanan: {ok_count}, atlanan: {skip_count}, "
                f"hatali/gonderilmemis: {fail_count}."
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
