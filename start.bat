@echo off
chcp 65001 >nul
rem Blackboard Sinav PDF Yakalayici - Baslatma (Windows)
rem
rem Kullanim: start.bat dosyasina cift tikla.

cd /d "%~dp0"

if not exist ".venv" (
    echo HATA: Sanal ortam ^(.venv^) bulunamadi.
    echo Once kurulumu calistirman gerekiyor: setup.bat
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo HATA: Sanal ortam aktif edilemedi.
    pause
    exit /b 1
)

python gui.py

if errorlevel 1 (
    echo.
    echo Program bir hatayla kapandi. Yukaridaki mesaji kontrol et.
    pause
)
