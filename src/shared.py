"""PHỐI HỢP NHIỀU MÁY dùng chung 1 bộ profile GPM (GPM đồng bộ profile giữa các máy).

Các máy cùng mạng LAN dùng chung MỘT thư mục (Windows share, vd \\\\MAY-CHINH\\MailNurture) làm "sổ trực":
  claims/<ngày>/<profile_id>.json   Sổ NHẬN VIỆC trong ngày: máy nào ghi TRƯỚC (tạo file độc quyền)
                                     thì máy đó nuôi mail đó hôm nay; máy khác thấy đã có -> bỏ qua.
                                     -> không bao giờ 2 máy nuôi cùng 1 mail / mở cùng lúc.
  flags/<profile_id>.json            Cờ cảnh báo DÙNG CHUNG (Google cảnh báo / cần đăng nhập) -> mọi máy
                                     đều cho mail nghỉ.
  diary_docs/<profile_id>.txt        Mã file nhật ký Docs của mail -> mọi máy ghi tiếp đúng 1 file.
  machines/<tên máy>.json            Máy nào đang dùng thư mục chung (để kiểm tra).
Không lưu token / key / mật khẩu ở đây.

Bỏ trống `shared.dir` = chỉ chạy 1 máy (tắt phối hợp). Đã đặt mà KHÔNG vào được thư mục -> máy đó
KHÔNG nuôi (thà dừng còn hơn nuôi trùng).
"""
from __future__ import annotations

import json
import os
import socket
from datetime import date, datetime, timedelta
from pathlib import Path


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Shared:
    def __init__(self, cfg_raw: dict | None):
        sc = (cfg_raw or {}).get("shared") or {}
        d = str(sc.get("dir") or "").strip()
        self.dir = Path(d) if d else None
        self.machine = (str(sc.get("machine_name") or "").strip() or socket.gethostname() or "may-nay")

    @property
    def enabled(self) -> bool:
        return self.dir is not None

    # ---------- hạ tầng ----------
    def _read(self, p: Path) -> dict | None:
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _write(self, p: Path, data: dict) -> None:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{self.machine}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)

    def check(self) -> tuple[bool, str]:
        """Thư mục chung có vào được + ghi được không. Ghi luôn 'dấu hiệu' của máy này."""
        if not self.enabled:
            return True, "Đang chạy 1 máy (chưa đặt thư mục chung)."
        try:
            if not self.dir.exists():
                return False, f"Không thấy thư mục chung {self.dir} (máy chủ tắt / chưa chia sẻ / sai đường dẫn)."
            probe = self.dir / f".probe-{self.machine}"
            probe.write_text(_now(), encoding="utf-8")
            probe.unlink(missing_ok=True)
            self._write(self.dir / "machines" / f"{self.machine}.json", {"machine": self.machine, "at": _now()})
            return True, f"Vào được thư mục chung {self.dir}."
        except Exception as e:  # noqa: BLE001
            return False, f"Không ghi được vào thư mục chung {self.dir}: {e}"

    def machines(self) -> list[dict]:
        out = []
        try:
            for p in (self.dir / "machines").glob("*.json"):
                info = self._read(p)
                if info:
                    out.append(info)
        except Exception:
            pass
        return sorted(out, key=lambda x: x.get("at", ""), reverse=True)

    # ---------- sổ nhận việc theo ngày ----------
    def _claim_path(self, pid: str, day: str | None = None) -> Path:
        return self.dir / "claims" / (day or date.today().isoformat()) / f"{pid}.json"

    def claim(self, pid: str, name: str = "") -> tuple[bool, dict | None]:
        """Nhận nuôi mail hôm nay. Trả (True, None) nếu nhận được; (False, thông tin máy đã nhận) nếu không."""
        p = self._claim_path(pid)
        p.parent.mkdir(parents=True, exist_ok=True)
        data = {"machine": self.machine, "name": name, "at": _now(), "status": "running"}
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)   # ĐỘC QUYỀN: chỉ 1 máy tạo được
        except FileExistsError:
            return False, (self._read(p) or {"machine": "?", "status": "running"})
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return True, None

    def claim_info(self, pid: str, day: str | None = None) -> dict | None:
        return self._read(self._claim_path(pid, day))

    def finish(self, pid: str, status: str) -> None:
        """Ghi kết quả phiên vào sổ (chỉ sửa dòng của CHÍNH máy này)."""
        p = self._claim_path(pid)
        info = self._read(p)
        if info and info.get("machine") == self.machine:
            info.update({"status": status, "ended_at": _now()})
            self._write(p, info)

    def release(self, pid: str) -> None:
        """Nhả mail (mở profile lỗi trước khi làm được gì) -> XOÁ dòng trong sổ để máy khác được thử lại
        hôm nay (tạo mới lại vẫn là độc quyền nên không thể 2 máy cùng nhận)."""
        p = self._claim_path(pid)
        info = self._read(p)
        if info and info.get("machine") == self.machine:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass

    def claimed_today(self) -> dict[str, dict]:
        """{profile_id: thông tin} các mail đã có máy nhận hôm nay."""
        out = {}
        try:
            for p in (self.dir / "claims" / date.today().isoformat()).glob("*.json"):
                out[p.stem] = self._read(p) or {}
        except Exception:
            pass
        return out

    def last_day_index(self, days: int = 60) -> dict[str, str]:
        """{profile_id: ngày gần nhất có máy nuôi} — để luân phiên công bằng giữa các máy."""
        idx: dict[str, str] = {}
        today = date.today()
        for i in range(days):
            d = (today - timedelta(days=i)).isoformat()
            try:
                for p in (self.dir / "claims" / d).glob("*.json"):
                    idx.setdefault(p.stem, d)
            except Exception:
                continue
        return idx

    # ---------- cờ cảnh báo dùng chung ----------
    def flag(self, pid: str, reason: str) -> None:
        self._write(self.dir / "flags" / f"{pid}.json", {"reason": reason, "at": _now(), "machine": self.machine})

    def flag_info(self, pid: str) -> dict | None:
        return self._read(self.dir / "flags" / f"{pid}.json")

    def cooldown(self, pid: str, hours: float) -> tuple[float, str, str]:
        """(số giờ còn phải nghỉ, lý do, máy gắn cờ) theo cờ dùng chung."""
        info = self.flag_info(pid)
        if not info:
            return 0.0, "", ""
        try:
            elapsed = (datetime.now() - datetime.fromisoformat(info["at"])).total_seconds() / 3600
        except Exception:
            return 0.0, "", ""
        return max(0.0, hours - elapsed), info.get("reason", ""), info.get("machine", "")

    def clear_flag(self, pid: str) -> None:
        try:
            (self.dir / "flags" / f"{pid}.json").unlink(missing_ok=True)
        except Exception:
            pass

    # ---------- file nhật ký dùng chung ----------
    def diary_doc(self, pid: str) -> str:
        try:
            return (self.dir / "diary_docs" / f"{pid}.txt").read_text(encoding="utf-8").strip()
        except Exception:
            return ""

    def set_diary_doc(self, pid: str, doc_id: str) -> None:
        if not doc_id:
            return
        p = self.dir / "diary_docs" / f"{pid}.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{self.machine}.tmp")
        tmp.write_text(doc_id, encoding="utf-8")
        os.replace(tmp, p)
