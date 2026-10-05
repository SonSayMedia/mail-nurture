"""Hành động: đọc Gmail. Mở hộp thư, mở vài email, đọc rồi quay lại.

An toàn: chỉ MỞ và ĐỌC (email tự chuyển sang đã đọc). Không xóa, không lưu trữ,
không gửi -> tránh mọi thao tác khó hoàn tác trên hộp thư của bạn.
"""
from __future__ import annotations

from .base import ActionContext, goto, register


@register("gmail")
def read_gmail(ctx: ActionContext) -> str:
    p, h = ctx.page, ctx.human
    goto(ctx, "https://mail.google.com/mail/u/0/#inbox")
    h.pause(2.5, 5.0)

    # Chờ danh sách thư (Gmail giao diện đầy đủ dùng tr.zA cho mỗi dòng).
    try:
        p.wait_for_selector("tr.zA", timeout=20000)
    except Exception:
        # KHÔNG cuộn/click gì thêm: trang lúc này có thể là trang đăng nhập/xác minh của Google.
        # Báo lỗi thật để runner kiểm tra rủi ro và không ghi nhận "đã đọc email".
        raise RuntimeError("không tải được hộp thư Gmail (có thể mail cần đăng nhập lại)")

    rows = p.locator("tr.zA")
    total = rows.count()
    if total == 0:
        return "mở Gmail, hộp thư trống"

    opened = 0
    to_open = min(ctx.rnd.randint(1, 3), total)
    for _ in range(to_open):
        rows = p.locator("tr.zA")
        if rows.count() == 0:
            break
        idx = ctx.rnd.randint(0, min(rows.count() - 1, 8))
        if not h.human_click(p, rows.nth(idx)):
            break
        h.pause(2.0, 5.0)
        h.scroll_read(p, steps=ctx.rnd.randint(1, 4))   # đọc nội dung
        opened += 1
        p.go_back(wait_until="domcontentloaded")
        h.pause(1.5, 3.0)

    return f"đọc Gmail, mở {opened} email"
