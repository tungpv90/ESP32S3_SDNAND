@echo off
setlocal
cd /d "%~dp0.."

echo ============================================================
echo   tools_2: dong goi assets -^> build\tools_2\ANIM roi nap vao SD NAND
echo ============================================================
echo.

set "PY=python"
where py >nul 2>nul && set "PY=py -3"

%PY% -c "import serial, PIL" >nul 2>nul
if errorlevel 1 (
    echo [*] Dang cai pyserial + pillow...
    %PY% -m pip install --quiet -r tools_2\requirements.txt
    if errorlevel 1 goto :nopy
)

if not exist "tools_2\assets\anim.json" (
    echo [*] Chua co anim.json, dang do thu muc assets...
    %PY% tools_2\anim_pack.py init
    echo.
)

echo [*] Dang dong goi...
%PY% tools_2\anim_pack.py build --quiet
if errorlevel 1 goto :fail

echo.
echo [*] Dang nap len the...
%PY% tools_2\anim_pack.py upload %*
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
