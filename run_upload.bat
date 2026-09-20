@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Nap assets vao SD NAND MKDV1GCL-ABA qua XIAO ESP32-S3
echo ============================================================
echo.

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

%PY% -c "import serial" >nul 2>nul
if errorlevel 1 (
    echo [*] Dang cai pyserial...
    %PY% -m pip install --quiet pyserial
    if errorlevel 1 goto :nopy
)

%PY% tools\upload_assets.py %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (echo [OK] Hoan tat.) else (echo [!] Ket thuc voi loi, ma %RC%.)
pause
exit /b %RC%

:nopy
echo [X] Khong cai duoc pyserial. Kiem tra lai Python tren may.
pause
exit /b 1
