"""Hành động: ghé WEBSITE CHỈ ĐỊNH (do người dùng khai báo) — lướt như người thật.

Danh sách URL lấy từ config `custom.sites` (có thể đặt riêng theo nhóm). Dùng để cho mail
tương tác thêm với các trang bạn muốn, cho hành vi phong phú/tự nhiên hơn.
"""
from __future__ import annotations

from .base import ActionContext, goto, register


@register("visit")
def visit_site(ctx: ActionContext) -> str:
    p, h = ctx.page, ctx.human
    sites = [s.strip() for s in ((ctx.cfg.get("custom") or {}).get("sites") or []) if s.strip()]
    if not sites:
        raise RuntimeError("chưa khai báo website chỉ định cho nhóm này")

    site = ctx.rnd.choice(sites)
    if not site.startswith("http"):
        site = "https://" + site
    goto(ctx, site)
    h.pause(1.5, 3.0)
    h.scroll_read(p, steps=ctx.rnd.randint(3, 8))    # lướt trang

    host = site.split("//")[-1].split("/")[0]
    clicked = ""
    # Đôi khi click 1 link nội bộ rồi đọc tiếp (giống người thật khám phá trang).
    if ctx.rnd.random() < 0.5:
        try:
            handles = p.query_selector_all(f"a[href*='{host}'], a[href^='/']") or []
            cand = []
            for hd in handles[:80]:
                txt = (hd.inner_text() or "").strip()
                if len(txt) > 12:
                    cand.append(hd)
            if cand and h.human_click(p, ctx.rnd.choice(cand)):
                h.pause(1.5, 3.5)
                h.scroll_read(p, steps=ctx.rnd.randint(2, 6))
                clicked = " (xem thêm 1 trang)"
        except Exception:
            pass

    return f"ghé website chỉ định: {host}{clicked}"
