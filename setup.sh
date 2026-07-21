#!/usr/bin/env bash
# Blackboard Sinav PDF Yakalayici - Kurulum (macOS / Linux)
#
# Bu script:
#   1) python3'un kurulu oldugunu dogrular,
#   2) .venv sanal ortamini olusturur (yoksa),
#   3) requirements.txt'teki bagimliliklari kurar,
#   4) Playwright'in kendi tarayici bilesenlerini kurar,
#   5) GERCEK Google Chrome'un kurulu olup olmadigini kontrol eder
#      (program channel="chrome" ile Playwright'in kendi tarayicisi
#      degil, GERCEKTEN kurulu Google Chrome'u kullaniyor - bkz. README).
#
# Kullanim:
#   chmod +x setup.sh   (ilk seferde, calistirma izni vermek icin)
#   ./setup.sh

set -e

cd "$(dirname "$0")"

echo "======================================================"
echo " Blackboard Sınav PDF Yakalayıcı — Kurulum (macOS/Linux)"
echo "======================================================"
echo

if ! command -v python3 >/dev/null 2>&1; then
    echo "HATA: python3 bulunamadı."
    echo "Önce Python 3.10 ya da üzerini kurman gerekiyor: https://python.org"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo "✓ Python bulundu (sürüm $PYTHON_VERSION)"

if [ ! -d ".venv" ]; then
    echo "→ Sanal ortam oluşturuluyor (.venv)..."
    python3 -m venv .venv
else
    echo "✓ Sanal ortam zaten var (.venv)"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "→ pip güncelleniyor..."
python3 -m pip install --upgrade pip --quiet

echo "→ Bağımlılıklar kuruluyor (requirements.txt)..."
python3 -m pip install -r requirements.txt

echo "→ Playwright tarayıcı bileşenleri kuruluyor..."
python3 -m playwright install chromium

echo
if [ -d "/Applications/Google Chrome.app" ] || command -v google-chrome >/dev/null 2>&1; then
    echo "✓ Google Chrome bulundu."
else
    echo "⚠ UYARI: Google Chrome bulunamadı."
    echo "  Bu program GERÇEK Google Chrome'u kullanıyor (Playwright'in kendi"
    echo "  test tarayıcısını DEĞİL — Microsoft/Azure AD SSO otomasyon"
    echo "  tespiti yüzünden). Devam etmeden önce kur:"
    echo "  https://www.google.com/chrome/"
fi

echo
echo "======================================================"
echo " Kurulum tamamlandı."
echo " Programı başlatmak için: ./start.sh"
echo "======================================================"
