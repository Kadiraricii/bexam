"""
Bir dersin Not Defteri (Grades) sayfasindaki sinavlari (Blackboard "Test")
otomatik bulur ve her biri icin PDF + ONAY kodu yakalar.

Kullanim:
    source .venv/bin/activate
    python3 scan_course.py

Tarayici acilinca SSO ile giris yap, taranacak dersin "Not Defteri"
sayfasina git (onceki BST020 ekran goruntusundeki gibi), sonra terminale
donup ENTER'a bas. Script "Goruntule" butonu olan TUM satirlari acmayi
dener; hangisinin gercek bir sinav/quiz gonderimi oldugunu satirin ADINA
degil, actiginda ONAY kodu gorunup gorunmedigine bakarak anlar (bkz.
NotSubmittedOrNotExam).
"""

from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from capture import capture_current_page
from common import (
    BASE_URL,
    DEFAULT_FOLDER_MAX_CHARS,
    OUTPUT_DIR,
    PROFILE_DIR,
    already_captured_titles,
    browser_launch_kwargs,
    derive_course_label,
    resolve_active_page,
    sanitize_filename,
)

GORUNTULE_BUTTON_NAME = "Görüntüle"
GRADES_LIST_READY_SELECTOR = f"text={GORUNTULE_BUTTON_NAME}"


class NotSubmittedOrNotExam(RuntimeError):
    """'Goruntule' tiklandiktan sonra makul bir surede ONAY kodu gorunmedi.

    Bunun iki masum sebebi olabilir: (1) bu satir hic bir sinav/quiz
    degil - bir odev, tartisma forumu vb. (2) gercekten bir sinav ama
    ogrenci onu hic gondermemis. Iki durumda da yakalanacak bir sey yok.

    Onceden hangi satirlarin denenecegine adindaki kelimeye bakarak karar
    veriliyordu ("sinav" geciyor mu vb.) - ama hocalar sinavi 'Quiz',
    'Vize', 'Final', 'Ara Sinav' gibi COK farkli adlandirabiliyor, sabit
    bir kelime listesi hicbir zaman tam kapsayamaz. Bunun yerine artik
    TUM 'Goruntule' satirlari deneniyor; gercek ayirt edici sinyal
    sayfanin ICERIGI (ONAY kodu var mi) - isim degil.
    """


def find_exam_row_names(page: Page) -> list[str]:
    """Not Defteri'ndeki 'Goruntule' butonu olan TUM satirlarin adini
    dondurur (isme gore ON-FILTRELEME yapilmiyor - bkz. NotSubmittedOrNotExam)."""
    buttons = page.get_by_role("button", name=GORUNTULE_BUTTON_NAME)
    row_names: list[str] = []
    for i in range(buttons.count()):
        button = buttons.nth(i)
        row_text = button.evaluate(
            "el => (el.closest('tr') "
            "|| el.closest('[role=\"row\"]') "
            "|| el.parentElement.parentElement).innerText"
        )
        first_line = row_text.strip().splitlines()[0].strip()
        row_names.append(first_line)
    return row_names


def return_to_grades_list(page: Page, grades_url: str) -> None:
    """Sinav overlay'ini kapatip Not Defteri listesine geri doner.

    Blackboard Ultra'da sinav goruntuleme ayri bir URL'e (SPA route)
    karsilik geliyor, bu yuzden Escape yerine tarayici gecmisini
    kullaniyoruz; o da basarisiz olursa Not Defteri URL'ine dogrudan
    gidip sert bir kurtarma yapiyoruz.
    """
    page.go_back()
    try:
        page.wait_for_selector(GRADES_LIST_READY_SELECTOR, timeout=8_000)
        return
    except PlaywrightTimeoutError:
        pass

    page.goto(grades_url)
    page.wait_for_selector(GRADES_LIST_READY_SELECTOR, timeout=15_000)


def capture_exam_row(
    page: Page, row_name: str, grades_url: str, output_dir: Path, log_title: str | None = None
) -> dict:
    """log_title verilmezse capture_current_page sayfanin KENDI icerigindeki
    basligi kullanir (info['baslik']) - bu, Not Defteri'ndeki row_name'den
    hafifce farkli olabilir VE daha da onemlisi DERS ADINI icermez. Iki
    farkli ders ayni isimde bir sinav icerirse (ör. ikisi de "Final Sinavi"),
    ders adi olmadan idempotency kontrolu (already_captured_titles) ikinci
    dersin sinavini "zaten yakalanmis" sanip SESSIZCE ATLAR. Bu yuzden
    cagiran taraf (gui.py/main() burada) ders adini iceren bir log_title
    GECMELI - bkz. scan_grade_center.py'deki ayni desen
    (f"{exam_label} - {display_name}")."""
    row = page.locator("tr", has_text=row_name).first
    button = row.get_by_role("button", name=GORUNTULE_BUTTON_NAME)
    button.click()
    try:
        page.wait_for_selector("text=ONAY:", timeout=10_000)
    except PlaywrightTimeoutError as exc:
        raise NotSubmittedOrNotExam(
            f"'{row_name}' icin ONAY kodu gorunmedi - bu satir bir sinav/quiz "
            "olmayabilir (ör. odev, tartisma) ya da hic gonderilmemis olabilir."
        ) from exc

    entry = capture_current_page(page, output_dir=output_dir, log_title=log_title)

    return_to_grades_list(page, grades_url)
    return entry


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

        grades_url = page.url
        course_label = derive_course_label(page)
        course_dir = OUTPUT_DIR / sanitize_filename(course_label, max_chars=DEFAULT_FOLDER_MAX_CHARS)

        row_names = find_exam_row_names(page)
        print(f"\nDers: {course_label}")
        print(f"{len(row_names)} satir bulundu (hangileri gercek sinav/quiz "
              f"cikacak, denendikce belli olacak): {row_names}\n")

        if not row_names:
            print(
                "UYARI: Hic 'Goruntule' satiri bulunamadi. Sayfa yapisi "
                "beklenenden farkli olabilir, bana haber ver."
            )

        captured_titles = already_captured_titles()
        ok_count = 0
        skip_count = 0
        fail_count = 0

        for row_name in row_names:
            # Ders adi dahil (bkz. capture_exam_row docstring): iki farkli
            # dersin ayni isimli sinavi birbirini "zaten yakalanmis" sanip
            # atlamasin diye.
            log_key = f"{course_label} - {row_name}"
            if log_key in captured_titles:
                print(f"Atlaniyor (zaten yakalanmis): {row_name}")
                skip_count += 1
                continue

            print(f"Deneniyor: {row_name}")
            try:
                exam_dir = course_dir / sanitize_filename(row_name, max_chars=DEFAULT_FOLDER_MAX_CHARS)
                entry = capture_exam_row(page, row_name, grades_url, exam_dir, log_title=log_key)
                print(f"  -> OK  onay={entry['onay']}  pdf={entry['pdf']}")
                if entry["bozuk_gorsel_sayisi"] > 0:
                    print(
                        f"  -> UYARI: {entry['bozuk_gorsel_sayisi']} gorsel bozuk/eksik "
                        "gorunuyor, PDF'i elle kontrol et."
                    )
                ok_count += 1
            except NotSubmittedOrNotExam as exc:
                print(f"  -> Atlandi (sinav/quiz degil ya da gonderilmemis): {exc}")
                skip_count += 1
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
                fail_count += 1
                try:
                    return_to_grades_list(page, grades_url)
                except Exception as recover_exc:
                    print(
                        f"  -> Kurtarma basarisiz ({recover_exc}), yanlis satirlara "
                        "tiklama riski tasidigi icin tarama burada durduruldu."
                    )
                    break

        print(
            f"\nBitti. Yakalanan: {ok_count}, atlanan: {skip_count}, "
            f"hatali: {fail_count}."
        )
        print("Tarayici acik kalacak, kapatmak icin ENTER'a bas.")
        input()
        context.close()


if __name__ == "__main__":
    main()
