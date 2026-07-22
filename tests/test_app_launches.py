"""Gercek `python gui.py` calistirmasini AYRI bir surec olarak baslatip
birkac saniye ayakta kalip kalmadigini dogrular.

Bu oturumda bulunan EN KRITIK hata (cursor="pointinghand" - SADECE
Windows'ta, SADECE belirli bir widget kurulurken ACILISTA cokme) TAM
OLARAK bu turden bir "gercekten calistir" testiyle yakalanirdi; modul
ice aktarma / fonksiyon birim testleri boyle hatalari KACIRABILIR (hata
import sirasinda degil, calisma zamaninda belirli bir widget
olusturulurken ortaya cikiyorsa). Bu yuzden bu test kasitli olarak
gercek bir `python gui.py` calistirmasini ayri bir surec olarak baslatir.

subprocess.Popen KULLANILIYOR (shell job control - `&`/`kill` DEGIL):
Windows'ta bash'in arka plan surec/sinyal semantigi native GUI
uygulamalariyla guvenilmez calisabiliyor - Python'un kendi subprocess
API'si her uc platformda (macOS/Windows/Linux) tutarli davranir.
"""

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
STARTUP_WAIT_S = 4.0


def test_gui_process_stays_alive_after_startup():
    """`python gui.py`'yi gercek, ayri bir surec olarak baslatir ve
    STARTUP_WAIT_S saniye sonra hala calisiyor mu diye bakar. Surec bu
    sure icinde kendiliginden sonlanmissa (ör. GUI kurulumu sirasinda
    yakalanmamis bir istisna, platforma ozel bir widget hatasi) bu bir
    HATA - stdout/stderr testin hata mesajina eklenir ki CI ciktisinda
    sebep hemen gorulebilsin."""
    process = subprocess.Popen(
        [sys.executable, "gui.py"],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(STARTUP_WAIT_S)
        exit_code = process.poll()
        if exit_code is not None:
            _, stderr = process.communicate(timeout=5)
            raise AssertionError(
                f"gui.py başlatıldıktan sonra {STARTUP_WAIT_S}s içinde çöktü "
                f"(çıkış kodu: {exit_code}).\nHata çıktısı:\n{stderr}"
            )
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
