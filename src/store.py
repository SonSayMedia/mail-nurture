"""Nhật ký hoạt động lưu vào SQLite: theo dõi mỗi account làm gì, khi nào, lỗi ra sao."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "activity.db"


class Store:
    def __init__(self, path: str | Path = DB_PATH):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT NOT NULL,
                profile_name TEXT,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT DEFAULT 'running',   -- running | ok | flagged | error | skip | aborted
                error TEXT,
                day TEXT NOT NULL                -- YYYY-MM-DD
            );
            CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                profile_id TEXT NOT NULL,
                kind TEXT NOT NULL,              -- youtube | news | gmail | diary
                detail TEXT,
                ok INTEGER DEFAULT 1,
                at TEXT NOT NULL,
                FOREIGN KEY(session_id) REFERENCES sessions(id)
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_profile_day
                ON sessions(profile_id, day);
            CREATE TABLE IF NOT EXISTS flags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_id TEXT NOT NULL,
                reason TEXT,
                at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_flags_profile ON flags(profile_id);
            CREATE TABLE IF NOT EXISTS seen (
                profile_id TEXT NOT NULL,
                kind TEXT NOT NULL,      -- youtube | news
                key TEXT NOT NULL,       -- video_id | url
                at TEXT NOT NULL,
                PRIMARY KEY (profile_id, kind, key)
            );
            CREATE TABLE IF NOT EXISTS login_checks (
                profile_id TEXT PRIMARY KEY,
                ok INTEGER,              -- 1 = còn đăng nhập, 0 = đã đăng xuất
                at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS journal (
                profile_id TEXT NOT NULL,
                day TEXT NOT NULL,       -- YYYY-MM-DD
                routine TEXT,            -- chữ ký trình tự công việc, vd 'gmail>youtube'
                summary TEXT,            -- tóm tắt việc đã làm trong ngày
                at TEXT NOT NULL,
                PRIMARY KEY (profile_id, day)
            );
            -- Sự kiện đáng chú ý của tool (không mở được profile, đăng xuất, dừng khẩn...)
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                at TEXT NOT NULL,
                day TEXT NOT NULL,
                level TEXT NOT NULL,     -- info | warn | error
                kind TEXT NOT NULL,      -- open_fail | login_out | emergency_stop | ack
                profile_id TEXT,
                profile_name TEXT,
                message TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_day ON events(day);
            -- Profile hiện KHÔNG mở được (GPM quá giờ/proxy chết...). Mở được lại -> tự xoá.
            CREATE TABLE IF NOT EXISTS open_errors (
                profile_id TEXT PRIMARY KEY,
                message TEXT,
                at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    # ---- session ----
    def start_session(self, profile_id: str, profile_name: str = "") -> int:
        now = datetime.now().isoformat(timespec="seconds")
        cur = self.conn.execute(
            "INSERT INTO sessions(profile_id, profile_name, started_at, status, day) "
            "VALUES (?,?,?,?,?)",
            (profile_id, profile_name, now, "running", date.today().isoformat()),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def end_session(self, session_id: int, status: str = "ok", error: str | None = None) -> None:
        self.conn.execute(
            "UPDATE sessions SET ended_at=?, status=?, error=? WHERE id=?",
            (datetime.now().isoformat(timespec="seconds"), status, error, session_id),
        )
        self.conn.commit()

    def log_action(self, session_id: int, profile_id: str, kind: str,
                   detail: str = "", ok: bool = True) -> None:
        self.conn.execute(
            "INSERT INTO actions(session_id, profile_id, kind, detail, ok, at) "
            "VALUES (?,?,?,?,?,?)",
            (session_id, profile_id, kind, detail, 1 if ok else 0,
             datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    # ---- truy vấn ----
    def sessions_today(self, profile_id: str) -> int:
        """Số phiên ĐÃ THỰC SỰ tương tác trên tài khoản hôm nay.
        Tính: ok, flagged (có hoạt động rồi mới bị chặn), running, aborted (bị ngắt giữa chừng — đã
        tương tác một phần). Không tính: skip (không làm gì), error (lỗi — cho phép thử lại)."""
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM sessions WHERE profile_id=? AND day=? "
            "AND status IN ('ok','flagged','running','aborted')",
            (profile_id, date.today().isoformat()),
        ).fetchone()
        return int(row["c"])

    def cleanup_stale_running(self, max_age_minutes: int = 90) -> int:
        """Phiên kẹt ở 'running' quá lâu (app bị đóng/ngắt giữa chừng) -> đánh dấu 'aborted'
        (vẫn tính là đã nuôi hôm đó để không chạy lần 2 trong ngày)."""
        cutoff = datetime.fromtimestamp(datetime.now().timestamp() - max_age_minutes * 60)
        cur = self.conn.execute(
            "UPDATE sessions SET status='aborted', ended_at=?, error=? "
            "WHERE status='running' AND started_at < ?",
            (datetime.now().isoformat(timespec="seconds"),
             "không kết thúc (app bị đóng/ngắt giữa chừng)", cutoff.isoformat(timespec="seconds")),
        )
        self.conn.commit()
        return cur.rowcount

    def last_nurtured_day(self, profile_id: str) -> str:
        """Ngày gần nhất mail THỰC SỰ được nuôi ('' nếu chưa bao giờ) — dùng để luân phiên công bằng."""
        row = self.conn.execute(
            "SELECT MAX(day) d FROM sessions WHERE profile_id=? AND status IN ('ok','flagged','aborted')",
            (profile_id,),
        ).fetchone()
        return (row["d"] or "") if row else ""

    def did_action_today(self, profile_id: str, kind: str) -> bool:
        """Hôm nay profile này đã làm thành công hành động 'kind' chưa (vd 'diary')."""
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM actions WHERE profile_id=? AND kind=? AND ok=1 AND at LIKE ?",
            (profile_id, kind, date.today().isoformat() + "%"),
        ).fetchone()
        return int(row["c"]) > 0

    def total_ok_sessions(self, profile_id: str) -> int:
        """Số phiên đã nuôi thành công của account (dùng để tính 'tuổi' khởi động dần)."""
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM sessions WHERE profile_id=? AND status='ok'",
            (profile_id,),
        ).fetchone()
        return int(row["c"])

    def recent_error_streak(self, limit: int = 20) -> int:
        rows = self.conn.execute(
            "SELECT status FROM sessions WHERE status IN ('ok','error','flagged') "
            "ORDER BY id DESC LIMIT ?", (limit,),
        ).fetchall()
        streak = 0
        for r in rows:
            if r["status"] in ("error", "flagged"):
                streak += 1
            else:
                break
        return streak

    # ---- an toàn: cờ cảnh báo + cooldown ----
    def flag_profile(self, profile_id: str, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO flags(profile_id, reason, at) VALUES (?,?,?)",
            (profile_id, reason, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def cooldown_remaining_hours(self, profile_id: str, cooldown_hours: float) -> float:
        """Số giờ còn phải nghỉ vì cảnh báo gần nhất (0 nếu không còn)."""
        row = self.conn.execute(
            "SELECT at FROM flags WHERE profile_id=? ORDER BY id DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        if not row:
            return 0.0
        try:
            last = datetime.fromisoformat(row["at"])
        except Exception:
            return 0.0
        elapsed = (datetime.now() - last).total_seconds() / 3600.0
        remaining = cooldown_hours - elapsed
        return max(0.0, remaining)

    def is_in_cooldown(self, profile_id: str, cooldown_hours: float) -> bool:
        return self.cooldown_remaining_hours(profile_id, cooldown_hours) > 0

    # ---- kết quả kiểm tra đăng nhập ----
    def set_login_status(self, profile_id: str, ok: bool) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO login_checks(profile_id, ok, at) VALUES (?,?,?)",
            (profile_id, 1 if ok else 0, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def login_status(self, profile_id: str):
        """(ok: bool, at: str) của lần kiểm tra gần nhất, hoặc None nếu chưa kiểm tra."""
        row = self.conn.execute(
            "SELECT ok, at FROM login_checks WHERE profile_id=?", (profile_id,)
        ).fetchone()
        return (bool(row["ok"]), row["at"]) if row else None

    def clear_flags(self, profile_id: str) -> None:
        """Gỡ cờ/nghỉ của mail (dùng khi người dùng đã tự xử lý, vd đăng nhập lại)."""
        self.conn.execute("DELETE FROM flags WHERE profile_id=?", (profile_id,))
        self.conn.commit()

    def last_flag_reason(self, profile_id: str) -> str:
        row = self.conn.execute(
            "SELECT reason FROM flags WHERE profile_id=? ORDER BY id DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        return row["reason"] if row else ""

    # ---- trí nhớ: đã xem/đọc gì (tránh lặp) ----
    def mark_seen(self, profile_id: str, kind: str, key: str) -> None:
        if not key:
            return
        self.conn.execute(
            "INSERT OR REPLACE INTO seen(profile_id, kind, key, at) VALUES (?,?,?,?)",
            (profile_id, kind, key, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    # ---- nhật ký công việc theo ngày (nơi 2) + bộ nhớ trình tự (tự học chống lặp) ----
    def save_journal(self, profile_id: str, day: str, routine: str, summary: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO journal(profile_id, day, routine, summary, at) VALUES (?,?,?,?,?)",
            (profile_id, day, routine, summary, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def recent_routines(self, profile_id: str, days: int = 7) -> list[str]:
        """Chữ ký trình tự của các ngày gần nhất (để hôm nay chọn trình tự KHÁC đi)."""
        rows = self.conn.execute(
            "SELECT routine FROM journal WHERE profile_id=? ORDER BY day DESC LIMIT ?",
            (profile_id, days),
        ).fetchall()
        return [r["routine"] for r in rows if r["routine"]]

    def last_journal(self, profile_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT day, routine, summary FROM journal WHERE profile_id=? ORDER BY day DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        return dict(row) if row else None

    def latest_seen_key(self, profile_id: str, kind: str) -> str:
        """Key được dùng/ghi nhận GẦN NHẤT (vd doc nhật ký đang dùng)."""
        row = self.conn.execute(
            "SELECT key FROM seen WHERE profile_id=? AND kind=? ORDER BY at DESC LIMIT 1",
            (profile_id, kind),
        ).fetchone()
        return row["key"] if row else ""

    def recent_seen_keys(self, profile_id: str, kind: str, limit: int = 300) -> set[str]:
        rows = self.conn.execute(
            "SELECT key FROM seen WHERE profile_id=? AND kind=? ORDER BY at DESC LIMIT ?",
            (profile_id, kind, limit),
        ).fetchall()
        return {r["key"] for r in rows}

    # ---- sự kiện + lỗi mở profile (cho trang "Nhật ký & Lỗi") ----
    def add_event(self, kind: str, message: str = "", level: str = "info",
                  profile_id: str = "", profile_name: str = "") -> None:
        now = datetime.now()
        self.conn.execute(
            "INSERT INTO events(at, day, level, kind, profile_id, profile_name, message) "
            "VALUES (?,?,?,?,?,?,?)",
            (now.isoformat(timespec="seconds"), now.date().isoformat(), level, kind,
             profile_id or "", profile_name or "", message or ""),
        )
        self.conn.commit()

    def last_event(self, kind: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM events WHERE kind=? ORDER BY id DESC LIMIT 1", (kind,)
        ).fetchone()
        return dict(row) if row else None

    def count_events(self, kind: str, since_day: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM events WHERE kind=? AND day>=?", (kind, since_day)
        ).fetchone()
        return int(row["c"])

    def set_open_error(self, profile_id: str, message: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO open_errors(profile_id, message, at) VALUES (?,?,?)",
            (profile_id, message, datetime.now().isoformat(timespec="seconds")),
        )
        self.conn.commit()

    def clear_open_error(self, profile_id: str) -> None:
        self.conn.execute("DELETE FROM open_errors WHERE profile_id=?", (profile_id,))
        self.conn.commit()

    def open_errors(self) -> dict[str, dict]:
        """{profile_id: {message, at}} các profile hiện không mở được."""
        rows = self.conn.execute("SELECT profile_id, message, at FROM open_errors").fetchall()
        return {r["profile_id"]: {"message": r["message"], "at": r["at"]} for r in rows}

    def latest_sessions(self) -> dict[str, dict]:
        """Phiên GẦN NHẤT của từng profile: {profile_id: session}."""
        rows = self.conn.execute(
            "SELECT s.* FROM sessions s JOIN (SELECT profile_id, MAX(id) mid FROM sessions "
            "GROUP BY profile_id) m ON s.id = m.mid"
        ).fetchall()
        return {r["profile_id"]: dict(r) for r in rows}

    def last_ok_session_end(self) -> str:
        row = self.conn.execute(
            "SELECT MAX(ended_at) t FROM sessions WHERE status='ok'"
        ).fetchone()
        return (row["t"] or "") if row else ""

    def session_history(self, since_day: str, limit: int = 300) -> list[dict]:
        """Các phiên từ ngày since_day (mới nhất trước), kèm danh sách việc đã làm."""
        rows = [dict(r) for r in self.conn.execute(
            "SELECT * FROM sessions WHERE day>=? ORDER BY id DESC LIMIT ?", (since_day, limit)
        ).fetchall()]
        if rows:
            ids = [r["id"] for r in rows]
            acts: dict[int, list] = {}
            q = ("SELECT session_id, kind, detail, ok, at FROM actions WHERE session_id IN (%s) "
                 "ORDER BY id" % ",".join("?" * len(ids)))
            for a in self.conn.execute(q, ids).fetchall():
                acts.setdefault(a["session_id"], []).append(dict(a))
            for r in rows:
                r["actions"] = acts.get(r["id"], [])
        return rows

    def session_stats(self, since_day: str) -> dict:
        """Đếm phiên theo trạng thái + số việc làm thành công, từ ngày since_day."""
        out = {"ok": 0, "flagged": 0, "error": 0, "skip": 0, "running": 0, "aborted": 0}
        for r in self.conn.execute(
                "SELECT status, COUNT(*) c FROM sessions WHERE day>=? GROUP BY status", (since_day,)):
            out[r["status"]] = int(r["c"])
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM actions WHERE ok=1 AND at>=?", (since_day,)
        ).fetchone()
        out["actions_ok"] = int(row["c"])
        out["open_fail"] = self.count_events("open_fail", since_day)
        return out

    def journal_for(self, profile_id: str, day: str) -> dict | None:
        row = self.conn.execute(
            "SELECT day, routine, summary FROM journal WHERE profile_id=? AND day=?", (profile_id, day)
        ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self.conn.close()
