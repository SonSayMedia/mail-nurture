@echo off
chcp 65001 >nul
title Mail Nurture - Nuoi tai khoan qua GPM Login
cd /d "%~dp0"

echo ============================================
echo   MAIL NURTURE
echo ============================================
echo.

REM --- Tao moi truong ao lan dau ---
if not exist ".venv\Scripts\python.exe" (
    echo [Setup] Tao moi truong ao va cai thu vien lan dau...
    python -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    pip install -r requirements.txt
) else (
    call ".venv\Scripts\activate.bat"
)

REM --- Tao config.yaml lan dau ---
if not exist "config.yaml" (
    echo [Setup] Tao config.yaml tu config.example.yaml. Hay mo ra chinh lai neu can.
    copy /y "config.example.yaml" "config.yaml" >nul
)

:menu
echo.
echo --------------------------------------------
echo   1. Kiem tra ket noi GPM        (check)
echo   2. Liet ke profile             (profiles)
echo   3. Kiem tra dang nhap cac mail (check-login)
echo   4. Xem hom nay se nuoi ai      (daily --dry-run)
echo   5. Chay mot dot nuoi           (daily)
echo   6. Nuoi thu 1 profile          (run-once)
echo   0. Thoat
echo --------------------------------------------
set "choice="
set /p choice="Chon: "

if "%choice%"=="1" ( python main.py check & goto menu )
if "%choice%"=="2" ( python main.py profiles & goto menu )
if "%choice%"=="3" ( python main.py check-login & goto menu )
if "%choice%"=="4" ( python main.py daily --dry-run & goto menu )
if "%choice%"=="5" ( python main.py daily & goto menu )
if "%choice%"=="6" goto runone
if "%choice%"=="0" exit /b 0
goto menu

REM Nhap profile_id NGOAI khoi if (trong khoi if, %pid% bi doc truoc khi nhap -> chay nham profile cu)
:runone
set "pid="
set /p pid="Nhap profile_id: "
if "%pid%"=="" goto menu
python main.py run-once "%pid%"
goto menu
