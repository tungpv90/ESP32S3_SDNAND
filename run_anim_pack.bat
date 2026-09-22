@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo   Dong goi assets -^> build\ANIM roi nap vao SD NAND
echo ============================================================
echo.

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

%PY% -c "import serial, PIL" >nul 2>nul
if errorlevel 1 (
    echo [*] Dang cai pyserial + pillow...
    %PY% -m pip install --quiet -r tools\requirements.txt
    if errorlevel 1 goto :nopy
)

if not exist "tools\assets\anim.json" (
    echo [*] Chua co anim.json, dang do thu muc assets...
    %PY% tools\anim_pack.py init
    echo.
)

echo [*] Dang dong goi...
%PY% tools\anim_pack.py build
if errorlevel 1 goto :fail

echo.
echo [*] Dang nap len the...
%PY% tools\anim_pack.py upload %*
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" (echo [OK] Hoan tat.) else (echo [!] Ket thuc voi loi, ma %RC%.)
pause
exit /b %RC%

:fail
echo.
echo [X] Dong goi that bai.
pause
exit /b 1

:nopy
echo [X] Khong cai duoc thu vien. Kiem tra lai Python tren may.
pause
exit /b 1
