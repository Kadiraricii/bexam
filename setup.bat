@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

rem Blackboard Sinav PDF Yakalayici - Kurulum (Windows)
rem
rem Bu script:
rem   1) python'un kurulu oldugunu dogrular,
rem   2) .venv sanal ortamini olusturur (yoksa),
rem   3) requirements.txt'teki bagimliliklari kurar,
rem   4) Playwright'in kendi tarayici bilesenlerini kurar,
rem   5) GERCEK Google Chrome'un kurulu olup olmadigini kontrol eder
rem      (program channel="chrome" ile Playwright'in kendi tarayicisi
rem      degil, GERCEKTEN kurulu Google Chrome'u kullaniyor - bkz. README).
rem
rem Kullanim: setup.bat dosyasina cift tikla, ya da bir komut isteminde
rem "setup.bat" yaz.

cd /d "%~dp0"

echo ======================================================
echo  Blackboard Sinav PDF Yakalayici - Kurulum (Windows)
echo ======================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo HATA: "python" komutu bulunamadi.
    echo Once Python 3.10 ya da uzerini kur: https://python.org/downloads
    echo ONEMLI: Kurulum sirasinda "Add python.exe to PATH" kutucugunu
    echo isaretlemeyi UNUTMA - aksi halde bu script python'u bulamaz.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PY_VERSION=%%v
echo Python bulundu (surum %PY_VERSION%)

if not exist ".venv" (
    echo Sanal ortam olusturuluyor ^(.venv^)...
    python -m venv .venv
    if errorlevel 1 (
        echo HATA: Sanal ortam olusturulamadi.
        pause
        exit /b 1
    )
) else (
    echo Sanal ortam zaten var ^(.venv^)
)

call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo HATA: Sanal ortam aktif edilemedi.
    pause
    exit /b 1
)

echo Bagimliliklar kuruluyor...
python -m pip install --upgrade pip --quiet
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo HATA: Bagimliliklar kurulamadi. Yukaridaki hata mesajini kontrol et.
    pause
    exit /b 1
)

echo Playwright tarayici bilesenleri kuruluyor...
python -m playwright install chromium

echo.
set CHROME_FOUND=0
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set CHROME_FOUND=1
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set CHROME_FOUND=1
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set CHROME_FOUND=1

if "%CHROME_FOUND%"=="1" (
    echo Google Chrome bulundu.
) else (
    echo UYARI: Google Chrome bulunamadi.
    echo Bu program GERCEK Google Chrome'u kullaniyor ^(Playwright'in kendi
    echo test tarayicisini DEGIL^) - devam etmeden once kur:
    echo https://www.google.com/chrome/
)

echo.
echo ======================================================
echo  Kurulum tamamlandi.
echo  Programi baslatmak icin: start.bat
echo ======================================================
pause
