@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM --- Cai dat lan dau (chi chay 1 lan, hien tien trinh) ---
if not exist ".venv\Scripts\pythonw.exe" (
    echo ================================================
    echo   MAIL NURTURE - Cai dat lan dau, vui long cho...
    echo   (chi lan dau; can Internet + Python)
    echo ================================================
    python -m venv .venv
    call ".venv\Scripts\activate.bat"
    python -m pip install --upgrade pip
    pip install -r requirements.txt
)

REM --- Tao config lan dau ---
if not exist "config.yaml" copy /y "config.example.yaml" "config.yaml" >nul

REM --- Mo app KHONG kem cua so console roi thoat ngay ---
start "" ".venv\Scripts\pythonw.exe" "app.py"
exit
