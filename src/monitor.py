"""Tổng hợp dữ liệu cho trang "Nhật ký & Lỗi": việc cần xử lý + định dạng thời gian.

Tách khỏi giao diện để test được. Mỗi "việc" (issue) là dict:
  sev     : 'high' (cần xử lý ngay) | 'medium' (cảnh báo) | 'info' (gợi ý)
  kind    : gpm | emergency | login | challenge | open | error | no_nurture
  title, detail, hint, at, pid, name, group
  action  : nút gợi ý trên thẻ — login | recheck | ack | retry | groups | None
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from . import safety

SEV_ORDER = {"high": 0, "medium": 1, "info": 2}


def ago(at: str, now: datetime | None = None) -> str:
    """'vừa xong' / '12 phút trước' / '3 giờ trước' / '2 ngày trước'."""
    if not at:
        return ""
    try:
        t = datetime.fromisoformat(at)
    except Exception:
        return ""
    sec = ((now or datetime.now()) - t).total_seconds()
    if sec < 60:
        return "vừa xong"
    if sec < 3600:
        return f"{int(sec // 60)} phút trước"
    if sec < 86400:
        return f"{int(sec // 3600)} giờ trước"
    return f"{int(sec // 86400)} ngày trước"


def fmt_time(at: str, with_date: bool = False) -> str:
    try:
        t = datetime.fromisoformat(at)
    except Exception:
        return "—"
    return t.strftime("%d/%m %H:%M" if with_date else "%H:%M")


def duration(start: str, end: str | None) -> str:
    try:
        s = datetime.fromisoformat(start)
        e = datetime.fromisoformat(end) if end else None
    except Exception:
        return "—"
    if e is None:
        return "…"
    sec = max(0, int((e - s).total_seconds()))
    if sec < 60:
        return f"{sec} giây"
    return f"{sec // 60} phút"


def since_day(days_back: int) -> str:
    return (date.today() - timedelta(days=days_back)).isoformat()


def build_issues(rows: list[dict], store, gpm_ok: bool = True) -> list[dict]:
    """rows: danh sách mail từ bảng chính (id, name, group, raw_mode, cooldown, flag...)."""
    issues: list[dict] = []

    if not gpm_ok:
        issues.append({
            "sev": "high", "kind": "gpm", "title": "Chưa kết nối được GPM Login",
            "detail": "Tool không đọc được danh sách mail nên chưa kiểm tra được lỗi của từng mail.",
            "hint": "Mở GPM, vào Cài đặt → API, bật API (cổng 19995) rồi bấm “Thử lại”.",
            "action": "retry"})

    # Dừng khẩn gần nhất — còn hiện cho tới khi bạn bấm "Đã hiểu" hoặc có phiên chạy tốt sau đó.
    ev = store.last_event("emergency_stop")
    if ev:
        ack = store.last_event("ack_emergency")
        last_ok = store.last_ok_session_end()
        if (not ack or ack["at"] < ev["at"]) and (not last_ok or last_ok < ev["at"]):
            issues.append({
                "sev": "high", "kind": "emergency", "title": "Đợt chạy bị DỪNG KHẨN",
                "detail": ev.get("message", ""), "at": ev["at"],
                "hint": "Nhiều mail liền bị lỗi hoặc bị Google chặn. Kiểm tra GPM, mạng, proxy và "
                        "đăng nhập các mail trước khi chạy lại.",
                "action": "ack"})

    open_errs = store.open_errors()
    latest = store.latest_sessions()
    recent_day = since_day(2)
    for r in rows:
        if r.get("raw_mode") == "locked":
            continue                         # nhóm Khóa: tool không bao giờ mở -> không có lỗi
        pid = r["id"]
        base = {"pid": pid, "name": r.get("name") or pid, "group": r.get("group", "")}
        cd = float(r.get("cooldown") or 0)
        flag = r.get("flag") or ""
        if cd > 0 and safety.is_login_reason(flag):
            issues.append({**base, "sev": "high", "kind": "login", "title": "Cần đăng nhập lại Google",
                           "detail": flag,
                           "hint": "Mở profile này trong GPM, đăng nhập lại Google bằng tay, rồi bấm "
                                   "“Tôi đã đăng nhập lại” (hoặc “Kiểm tra lại” để tool tự xác nhận).",
                           "action": "login"})
        elif cd > 0:
            issues.append({**base, "sev": "medium", "kind": "challenge",
                           "title": f"Google cảnh báo bảo mật — mail đang nghỉ, còn {cd:.0f}h",
                           "detail": flag,
                           "hint": "Không cần làm gì: để mail nghỉ đủ giờ, tool sẽ tự nuôi lại. Tránh mở "
                                   "profile này lướt web trong lúc nghỉ.",
                           "action": None})
        oe = open_errs.get(pid)
        if oe:
            issues.append({**base, "sev": "high", "kind": "open", "title": "Không mở được profile",
                           "detail": oe.get("message", ""), "at": oe.get("at", ""),
                           "hint": "Thường do proxy chết/chậm hoặc profile lỗi. Kiểm tra proxy của profile "
                                   "trong GPM rồi bấm “Kiểm tra lại”.",
                           "action": "recheck"})
            continue
        s = latest.get(pid)
        if s and s.get("status") == "error" and (s.get("day") or "") >= recent_day:
            issues.append({**base, "sev": "medium", "kind": "error", "title": "Phiên nuôi gần nhất bị lỗi",
                           "detail": s.get("error") or "", "at": s.get("ended_at") or s.get("started_at") or "",
                           "hint": "Xem chi tiết ở mục “Lịch sử phiên”. Lỗi lặp lại nhiều lần thì kiểm tra "
                                   "profile trong GPM.",
                           "action": "recheck"})

    if gpm_ok and rows and not any(r.get("raw_mode") == "nurture" for r in rows):
        issues.append({"sev": "info", "kind": "no_nurture", "title": "Chưa có nhóm nào đặt “Nuôi”",
                       "detail": "Tool chưa biết nuôi mail nào nên “Chạy đợt nhẹ” sẽ không chọn mail nào.",
                       "hint": "Vào tab Nhóm mail, chọn Nuôi cho nhóm cần nuôi.", "action": "groups"})

    issues.sort(key=lambda i: (SEV_ORDER.get(i["sev"], 9), (i.get("name") or "").lower()))
    return issues


def count_attention(issues: list[dict]) -> int:
    """Số việc cần bạn chú ý (high + medium) — dùng cho số đỏ trên menu."""
    return sum(1 for i in issues if i["sev"] in ("high", "medium"))
