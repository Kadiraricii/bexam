"""
Bir dersin Not Defteri > Ogrenciler sekmesindeki TAM ogrenci listesini
(Ad Soyad + Kullanici Adi) tarayip CSV dosyasina yazar.

Bu liste, capture_student'in PDF dosya adina ogrenci numarasini
eklemesi icin kullanilir (bkz. common.load_student_roster) - sinav
taramasindan (scan_course.py / gui.py "Bul ve Tara") ONCE bir kez bu
taramayi ('Öğrenci Tara') calistirmak onerilir; calistirilmamissa ozellik
sessizce devre disi kalir (PDF adinda sadece ogrenci no bolumu olmaz),
hata VERMEZ.

Kullanim:
    source .venv/bin/activate
    python3 scan_students.py

Tarayici acilinca SSO ile giris yap, taranacak dersin Not Defteri >
"Öğrenciler" sekmesine git, sonra terminale donup ENTER'a bas.
"""

import csv
import re
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

from common import (
    BASE_URL,
    DEFAULT_FOLDER_MAX_CHARS,
    OUTPUT_DIR,
    PROFILE_DIR,
    STUDENT_ROSTER_CSV_FILENAME,
    browser_launch_kwargs,
    derive_course_label,
    resolve_active_page,
    sanitize_filename,
)

# Ogrenciler tablosunda bir ogrenci satirini ayirt eden isaret: "Kullanici
# Adi" sutunundaki ogrenci numarasi (ör. '2420171019'). Blackboard Ultra
# tablolari bazen gercek <tr> yerine <div role="row"> olarak render
# edilebilir, o yuzden ikisini de kapsiyoruz.
#
# NOT: bu satir/sayfalama secicileri CANLI Blackboard oturumunda henuz
# dogrulanmadi (bkz. README/proje gecmisindeki benzer notlar) - 0 ogrenci
# bulunursa ya da sayfalama calismiyorsa terminal ciktisini paylas,
# duzeltilir.
STUDENT_NUMBER_PATTERN = re.compile(r"\b\d{6,}\b")
ROW_SELECTOR = "tr, [role='row']"
ROSTER_TABLE_READY_SELECTOR = "text=Tam Ad"
NEXT_PAGE_BUTTON_NAME = re.compile(r"[Ss]onraki")
# Sayfalama dongusune mutlak bir ust sinir: son sayfada 'Sonraki' butonu
# beklenmedik sekilde tiklanabilir kalirsa (ör. Blackboard 'disabled'
# yerine sadece aria-disabled isaretliyorsa) while True SONSUZA KADAR
# donerdi - seen_numbers zaten tekrarlari eledigi icin roster buyumez ama
# program da hic bitmezdi. 200 sayfa x ~20 ogrenci = 4000 ogrenci, gercek
# bir sinifin cok ustunde.
MAX_ROSTER_PAGES = 200
# 200 sayfalik mutlak sinira ek, cok daha erken devreye giren bir korunma:
# 'Sonraki' tiklamasi "basarili" gorunup de sayfa GERCEKTE degismiyorsa
# (ör. buton disabled yerine sadece aria-disabled isaretliyse) her tur
# ayni ogrenciler okunur, seen_numbers'a YENI numara eklenmez. Bu art arda
# 2 kez olursa sayfalama fiilen bitmis demektir - 200 turun (~100+ saniye)
# bosuna donmesini beklemeye gerek yok. Gercek bir 'sonraki sayfa'nin
# tamamen eski ogrencilerden olusmasi mumkun degil (Blackboard ayni
# ogrenciyi iki sayfada listelemez), yani erken cikis veri KAYBETTIRMEZ.
MAX_STAGNANT_ROSTER_PAGES = 2

# Blackboard'in 'Öğrenciler' tablosunun kendi sayfalama gostergesi, ör.
# "1-20 / 90" - bu, o derste KAYITLI TOPLAM ogrenci sayisini (90) verir.
# "X/Y gönderildi" (bir SINAVA kac kisinin girdigini gosteren, TAMAMEN
# AYRI bir gosterge - bkz. scan_course.SUBMITTED_COUNT_PATTERN) ile
# KARISTIRILMAMALI: burada aranan sayi bir "aralik-toplam" bicimi
# ("N-M / T"), o ise "X/Y gönderildi" metnine sahip. Dash zorunlulugu
# ("\d+\s*-\s*\d+") bilerek konuldu - "Genel Not" sutunundaki "5,8 / 100"
# gibi puan degerleriyle YANLISLIKLA eslesmesin diye (onlarda aralik/tire
# yok). CANLI DOGRULANMADI - bulunamazsa kontrol sessizce atlanir,
# toplanan veriyi ETKILEMEZ.
TOTAL_STUDENT_COUNT_PATTERN = re.compile(r"\d+\s*-\s*\d+\s*/\s*(\d+)")


def find_student_roster(page: Page, *, emit=print) -> list[tuple[str, str]]:
    """'Öğrenciler' sekmesindeki (Ad Soyad, Kullanici Adi) ciftlerini
    toplar. Sayfalama varsa ('1-20/20' gibi, bircok ogrencili buyuk bir
    sinifta) TUM sayfalari gezmeye calisir (bkz. _go_to_next_page).

    Tarama BITTIKTEN SONRA, sayfanin kendi sayfalama gostergesinden
    ("1-20 / 90" gibi) GERCEK toplam ogrenci sayisini okuyup, gercekten
    toplanabilen benzersiz ogrenci sayisiyla karsilastirir - sayfalar
    arasi gecerken SESSIZCE basarisiz olunup (bkz. _go_to_next_page
    docstring'i - butun hatalari "sayfalama bitti" sayiyor) kalan
    ogrencilerin FARK EDILMEDEN eksik kalmasi riskine karsi. Uyusmazlik
    varsa emit ile bir UYARI verilir; gosterge hic bulunamazsa (ör. sayfa
    yapisi beklenenden farkli) kontrol sessizce atlanir - bu KONTROLUN
    kendisinin basarisiz olmasi, zaten TOPLANMIS veriyi ETKILEMEZ."""
    roster: list[tuple[str, str]] = []
    seen_numbers: set[str] = set()
    stagnant_pages = 0

    for page_index in range(MAX_ROSTER_PAGES):
        count_before_page = len(seen_numbers)
        rows = page.locator(ROW_SELECTOR).filter(has_text=STUDENT_NUMBER_PATTERN)
        for i in range(rows.count()):
            row_text = rows.nth(i).inner_text()
            lines = [line.strip() for line in row_text.strip().splitlines() if line.strip()]
            if not lines:
                continue
            number_match = STUDENT_NUMBER_PATTERN.search(row_text)
            if not number_match:
                continue
            student_no = number_match.group(0)
            if student_no in seen_numbers:
                continue
            seen_numbers.add(student_no)
            roster.append((lines[0], student_no))

        # Sayfalama gorunuste "ilerliyor" ama yeni ogrenci gelmiyorsa
        # (bkz. MAX_STAGNANT_ROSTER_PAGES yorumu) erken ve guvenle cik -
        # asagidaki _warn_if_roster_incomplete zaten toplam sayiyla
        # karsilastirip gercekten eksik varsa kullaniciyi uyaracak.
        if page_index > 0 and len(seen_numbers) == count_before_page:
            stagnant_pages += 1
            if stagnant_pages >= MAX_STAGNANT_ROSTER_PAGES:
                break
        else:
            stagnant_pages = 0

        if not _go_to_next_page(page):
            break

    _warn_if_roster_incomplete(page, len(roster), emit=emit)
    return roster


def _warn_if_roster_incomplete(page, collected_count: int, *, emit) -> None:
    """bkz. find_student_roster docstring'indeki ayni bolum - burada ayri
    bir fonksiyona cikarilmasinin nedeni, bu kontrolun HATA VERMESININ
    (ör. sayfa erisiminde beklenmedik bir TclError/Playwright hatasi)
    ana taramayi hicbir sekilde ETKILEMEMESI gerektigini acikca
    izole etmek."""
    try:
        page_text = page.inner_text("body")
    except Exception:
        return
    match = TOTAL_STUDENT_COUNT_PATTERN.search(page_text)
    if not match:
        return
    expected_total = int(match.group(1))
    if expected_total != collected_count:
        emit(
            f"UYARI: Blackboard'a göre bu derste {expected_total} öğrenci "
            f"kayıtlı görünüyor, ama sadece {collected_count} tanesi "
            "taranabildi - sayfalama sırasında bir sorun olmuş olabilir "
            "(ör. yavaş bağlantı). CSV eksik olabilir, tekrar denemen "
            "önerilir."
        )


def _go_to_next_page(page: Page) -> bool:
    """Ogrenciler tablosunun 'sonraki sayfa' okuna tiklar; basariyla
    tiklanip yeni bir sayfaya gecildiyse True doner. Tek sayfalik
    listelerde (ör. 20 ogrencilik bir sinif) buton yok/devre disi
    olacagi icin False doner.

    CANLI DOGRULANMADI: sayfalanmasi gereken buyuk bir sinifta hep ayni
    ogrenciler donuyorsa (sonraki sayfaya hic gecilemiyorsa) bu fonksiyonun
    secicisi duzeltilmeli."""
    next_button = page.get_by_role("button", name=NEXT_PAGE_BUTTON_NAME)
    try:
        if next_button.count() == 0:
            return False
        button = next_button.first
        if button.is_disabled():
            return False
        button.click()
        page.wait_for_timeout(500)
        return True
    except Exception:
        return False


def write_student_roster_csv(roster: list[tuple[str, str]], csv_path: Path) -> None:
    """utf-8-sig (BOM): Excel'de Turkce karakterlerin (ç/ğ/ı/ö/ş/ü)
    bozuk gorunmemesi icin - BOM'suz UTF-8, Windows'ta Excel'in varsayilan
    yerel kodlamayla (cp1254) acmasina yol acip harfleri bozabiliyordu.

    delimiter=';': Turkiye yerel ayarlarinda (ondalik ayiraci virgul
    oldugu icin) Excel'in kendi varsayilan CSV ayiraci ',' DEGIL ';' -
    dosya cift tiklanip Excel'de acildiginda ',' ile ayrilmis bir CSV
    TEK sutunda (tum satir bir hucrede) gorunurdu. NOT: bu ayirac
    common.load_student_roster'daki okuyucuyla AYNI olmak ZORUNDA -
    biri degisip digeri unutulursa roster'in TAMAMI sessizce
    okunamaz hale gelir (bkz. o fonksiyonun docstring'i)."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Ad Soyad", "Öğrenci Numarası"])
        writer.writerows(roster)


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
        print("2) Taranacak dersin Not Defteri > 'Öğrenciler' sekmesine git.")
        print("3) Sayfa tam yuklendiginde buraya donup ENTER'a bas.\n")
        input("Hazir oldugunda ENTER: ")
        try:
            page = resolve_active_page(context) or page
        except Exception:
            pass

        course_label = derive_course_label(page)
        course_dir = OUTPUT_DIR / sanitize_filename(course_label, max_chars=DEFAULT_FOLDER_MAX_CHARS)

        print("Öğrenci listesi taranıyor...")
        roster = find_student_roster(page)
        print(f"\nDers: {course_label}")
        print(f"{len(roster)} öğrenci bulundu.")

        if not roster:
            print(
                "UYARI: Hiç öğrenci satırı bulunamadı. Not Defteri > "
                "'Öğrenciler' sekmesinde olduğundan emin ol, olmadıysa "
                "bana haber ver."
            )
        else:
            csv_path = course_dir / STUDENT_ROSTER_CSV_FILENAME
            write_student_roster_csv(roster, csv_path)
            print(f"CSV yazıldı: {csv_path}")

        print("Tarayici acik kalacak, kapatmak icin ENTER'a bas.")
        input()
        context.close()


if __name__ == "__main__":
    main()
