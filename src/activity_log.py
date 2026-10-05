"""Nhật ký của TOOL lưu ra file theo ngày: data/logs/YYYY-MM-DD.log.

Mọi dòng log (app lẫn dòng lệnh) đều được ghi kèm giờ, nên tắt app mở lại vẫn xem được
trong trang "Nhật ký & Lỗi". Tự xoá file cũ hơn KEEP_DAYS ngày.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
KEEP_DAYS = 60
_lock = threading.Lock()

# Dấu hiệu dòng LỖI (đỏ) / CẢNH BÁO (vàng) / THÀNH CÔNG (xanh) — dùng để tô màu và lọc.
_ERR_MARKS = ("✖", "🛑", "⛔", "[LỖI]", "DỪNG KHẨN", "[BỊ CHẶN]", "không kiểm tra được", "LỖI:",
              "[DỪNG]", "Không chạy được")
_WARN_MARKS = ("⚠", "🔑", "✗", "😴", "[CẦN ĐĂNG NHẬP]", "[CẢNH BÁO]", "ĐÃ ĐĂNG XUẤT", "[NGHỈ]")
_OK_MARKS = ("✓", "✅", "✔", "[OK]")


def write(msg: str) -> None:
    """Ghi 1 (hoặc nhiều) dòng vào file log hôm nay. Không bao giờ ném lỗi."""
    try:
        now = datetime.now()
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / f"{now.date().isoformat()}.log"
        lines = str(msg).rstrip("\n").split("\n")
        with _lock, open(path, "a", encoding="utf-8") as f:
            for ln in lines:
                f.write(f"[{now:%H:%M:%S}] {ln}\n")
    except Exception:
        pass


def days() -> list[str]:
    """Các ngày có file log, mới nhất trước (YYYY-MM-DD)."""
    try:
        return sorted((p.stem for p in LOG_DIR.glob("*.log")), reverse=True)
    except Exception:
        return []


def read(day: str | None = None) -> list[str]:
    day = day or date.today().isoformat()
    path = LOG_DIR / f"{day}.log"
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []
    except Exception:
        return []


def level(line: str) -> str:
    """'error' | 'warn' | 'ok' | 'head' | 'info' — mức của một dòng log."""
    if any(m in line for m in _ERR_MARKS):
        return "error"
    if any(m in line for m in _WARN_MARKS):
        return "warn"
    if "===" in line:
        return "head"
    if any(m in line for m in _OK_MARKS):
        return "ok"
    return "info"


def cleanup(keep_days: int = KEEP_DAYS) -> int:
    """Xoá file log cũ hơn keep_days ngày. Trả số file đã xoá."""
    cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
    n = 0
    try:
        for p in LOG_DIR.glob("*.log"):
            if p.stem < cutoff:
                p.unlink(missing_ok=True)
                n += 1
    except Exception:
        pass
    return n
