@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

rem ==========================================================================
rem  Lam tu dong toan bo: tai arduino-cli -> cai core ESP32 -> bien dich
rem  -> nap firmware vao XIAO ESP32-S3 -> day thu muc assets vao SD NAND.
rem
rem  Chi can cam board vao USB roi chay file nay.
rem  Lan dau se tai khoang 1 GB (core ESP32), cac lan sau chay rat nhanh.
rem ==========================================================================

set "SKETCH=ESP32S3_SDNAND_Uploader"
set "FQBN=esp32:esp32:XIAO_ESP32S3"
set "IDX=https://espressif.github.io/arduino-esp32/package_esp32_index.json"
set "CLI=%~dp0tools\arduino-cli.exe"
set "PORT=%~1"

echo ============================================================
echo   XIAO ESP32-S3 + MKDV1GCL-ABA : cai dat va nap tu dong
echo ============================================================
echo.

rem ---------------------------------------------------------------- 1. CLI
if exist "%CLI%" goto :havecli
rem Arduino IDE 2.x co san arduino-cli -> dung lai, khoi tai them.
set "IDECLI=%ProgramFiles%\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe"
if exist "%IDECLI%" (
    set "CLI=%IDECLI%"
    echo [1/6] Dung arduino-cli co san trong Arduino IDE.
    goto :cliok
)
echo [1/6] Dang tai arduino-cli...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop';" ^
  "$u='https://downloads.arduino.cc/arduino-cli/arduino-cli_latest_Windows_64bit.zip';" ^
  "$z=Join-Path $env:TEMP 'arduino-cli.zip';" ^
  "Invoke-WebRequest -Uri $u -OutFile $z;" ^
  "Expand-Archive -Path $z -DestinationPath '%~dp0tools' -Force;" ^
  "Remove-Item $z"
if not exist "%CLI%" (
    echo [X] Tai arduino-cli that bai. Kiem tra ket noi mang.
    goto :fail
)
goto :cliok
:havecli
echo [1/6] Da co arduino-cli.
:cliok

rem ---------------------------------------------------------------- 2. core
echo [2/6] Cap nhat danh muc board...
"%CLI%" core update-index --additional-urls "%IDX%" >nul
"%CLI%" core list --additional-urls "%IDX%" 2>nul | findstr /i "esp32:esp32" >nul
if errorlevel 1 (
    echo       Dang cai core esp32:esp32 ^(~1 GB, lan dau se lau^)...
    "%CLI%" core install esp32:esp32 --additional-urls "%IDX%"
    if errorlevel 1 goto :fail
) else (
    echo       Core esp32:esp32 da co san.
)

rem ---------------------------------------------------------------- 3. port
set "PORTARG="
if not "%PORT%"=="" goto :haveport
echo [3/6] Dang tim cong COM cua board...
for /f "tokens=1" %%P in ('"%CLI%" board list ^| findstr /r /c:"^COM"') do (
    if not defined PORT set "PORT=%%P"
)
if not defined PORT (
    echo [X] Khong thay board nao. Cam XIAO ESP32-S3 vao USB roi chay lai,
    echo     hoac chi dinh tay:  setup_and_flash.bat COM7
    goto :fail
)
goto :portok
:haveport
echo [3/6] Dung cong do nguoi dung chi dinh.
set "PORTARG=--port %PORT%"
:portok
echo       Cong: %PORT%

rem ---------------------------------------------------------------- 4. build
echo [4/6] Dang bien dich %SKETCH%...
"%CLI%" compile --fqbn "%FQBN%" "%SKETCH%"
if errorlevel 1 goto :fail

rem ---------------------------------------------------------------- 5. flash
echo [5/6] Dang nap firmware vao %PORT%...
"%CLI%" upload -p %PORT% --fqbn "%FQBN%" "%SKETCH%"
if errorlevel 1 (
    echo [!] Nap that bai. Thu giu nut BOOT, nhan RESET roi chay lai.
    goto :fail
)
echo       Cho board khoi dong lai...
powershell -NoProfile -Command "Start-Sleep -Seconds 4" >nul

rem ---------------------------------------------------------------- 6. assets
echo [6/6] Dang day thu muc assets vao SD NAND...
set "PY=python"
where py >nul 2>nul && set "PY=py -3"
%PY% -c "import serial" >nul 2>nul || %PY% -m pip install --quiet pyserial
rem Sau khi nap, board tu enumerate lai; de script tu tim cong tru khi
rem nguoi dung da chi dinh san.
%PY% tools\upload_assets.py %PORTARG% %2 %3 %4 %5 %6
if errorlevel 1 goto :fail

echo.
echo [OK] Xong het. Du lieu da nam trong SD NAND.
pause
exit /b 0

:fail
echo.
echo [X] Dung lai vi co loi o buoc tren.
pause
exit /b 1
