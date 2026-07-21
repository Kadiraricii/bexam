#!/usr/bin/env bash
# Blackboard Sinav PDF Yakalayici - Baslatma (macOS / Linux)
#
# Kullanim:
#   chmod +x start.sh   (ilk seferde)
#   ./start.sh

set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "HATA: Sanal ortam (.venv) bulunamadı."
    echo "Önce kurulumu çalıştırman gerekiyor: ./setup.sh"
    exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate
python3 gui.py
