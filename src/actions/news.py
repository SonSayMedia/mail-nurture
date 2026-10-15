"""Hành động: đọc báo ~2–3 phút. Mở 1 trang báo, lướt trang chủ, CLICK đọc bài (ưu tiên bài chưa đọc).

Thời gian đọc mỗi bài TUỲ ĐỘ DÀI BÀI: đếm số chữ của bài -> ước lượng thời gian đọc lướt
(200–280 từ/phút, đọc 45–80% bài), kẹp trong phần thời gian còn lại. Còn giờ thì quay lại trang chủ
đọc bài tiếp; hết giờ thì dừng. Tổng thời gian nằm trong cấu hình news.read_seconds (mặc định 120–180s).
"""
from __future__ import annotations

import re
import time
import urllib.parse

from .base import ActionContext, goto, register

DEFAULT_READ_SECONDS = [120, 180]

# URL bài báo VN thường kết thúc .html/.htm hoặc có id số dài (vd -185xxxxx, /2026/...).
_ARTICLE_RE = re.compile(r"(\.html?($|\?))|(-\d{5,})|(/\d{4,})", re.IGNORECASE)

# Trang tìm kiếm của vài báo VN phổ biến (để đọc bài ĐÚNG NGÁCH theo từ khoá). Báo khác -> lọc theo tiêu đề.
_SEARCH_URL = {
    "tuoitre.vn": "https://tuoitre.vn/tim-kiem.htm?keywords={q}",
    "dantri.com.vn": "https://dantri.com.vn/tim-kiem/{q}.htm",
    "thanhnien.vn": "https://thanhnien.vn/tim-kiem/?q={q}",
}


def _looks_like_article(href: str) -> bool:
    return bool(_ARTICLE_RE.search(href))


def _niche_keywords(ctx: ActionContext) -> list[str]:
    from .youtube import clean_queries
    return clean_queries((ctx.cfg.get("custom") or {}).get("youtube_queries"))


def _search_url(host: str, kw: str) -> str:
    tmpl = _SEARCH_URL.get(host)
    return tmpl.format(q=urllib.parse.quote(kw)) if tmpl else ""


def _matches_niche(title: str, kws: list[str]) -> bool:
    """Tiêu đề bài có liên quan ngách không: khớp cả cụm từ khoá, hoặc có 1 từ chính (≥4 ký tự)."""
    t = (title or "").lower()
    for kw in kws:
        k = kw.lower().strip()
        if k and k in t:
            return True
        for w in k.split():
            if len(w) >= 4 and w in t:
                return True
    return False


def _article_words(p) -> int:
    """Số chữ của bài đang mở (đọc nội dung chính của trang)."""
    try:
        return int(p.evaluate(
            "() => { const el = document.querySelector('article') || document.querySelector('main')"
            " || document.body; return ((el && el.innerText) || '').split(/\\s+/).filter(Boolean).length; }") or 0)
    except Exception:
        return 0


def reading_seconds(words: int, remaining: float, rnd) -> float:
    """Thời gian đọc 1 bài theo độ dài bài, kẹp trong [25s, 110s] và không vượt thời gian còn lại."""
    words = max(words, 150)
    est = words / rnd.uniform(200, 280) * 60 * rnd.uniform(0.45, 0.8)   # đọc lướt 45–80% bài
    t = min(max(est, 25.0), 110.0)
    return max(0.0, min(t, remaining - 5))


@register("news")
def browse_news(ctx: ActionContext) -> str:
    p, h, rnd = ctx.page, ctx.human, ctx.rnd
    nc = ctx.cfg.get("news") or {}
    sites = nc.get("sites") or ["https://tuoitre.vn"]
    lo, hi = (nc.get("read_seconds") or DEFAULT_READ_SECONDS)[:2]
    budget = rnd.uniform(float(lo), float(hi))
    site = rnd.choice(sites)
    host = site.split("//")[-1].split("/")[0]
    kws = _niche_keywords(ctx)             # từ khoá ngách (nếu nhóm có khai) -> đọc báo ĐÚNG NGÁCH
    t0 = time.time()

    # Có từ khoá ngách + báo hỗ trợ tìm kiếm -> vào thẳng trang kết quả tìm theo ngách.
    base_url, niche_kw = site, ""
    if kws:
        niche_kw = rnd.choice(kws)
        surl = _search_url(host, niche_kw)
        if surl:
            try:
                goto(ctx, surl)
                base_url = surl
            except Exception:
                base_url = site
    if base_url == site:
        goto(ctx, site)
    _dismiss_consent(p, h)
    h.scroll_for(p, rnd.uniform(8, 18))                 # lướt xem tiêu đề

    seen = ctx.seen("news")           # bài đã đọc trước đây -> tránh đọc lại
    read, niche_read = 0, 0
    titles: list[tuple[str, bool]] = []     # (tiêu đề bài, có đúng ngách không)
    while time.time() - t0 < budget - 30 and read < 4:
        # Thu thập lại link mỗi lượt (sau khi quay lại danh sách, handle cũ đã hết hiệu lực).
        candidates = _collect_articles(p, host)
        if not candidates:
            break
        # Ưu tiên bài ĐÚNG NGÁCH (tiêu đề khớp từ khoá); không có thì đọc bài thường.
        niche_c = [c for c in candidates if _matches_niche(c["text"], kws)] if kws else []
        pool = niche_c or candidates
        fresh = [c for c in pool if c["href"] not in seen]
        art = rnd.choice(fresh or pool)
        is_niche = bool(niche_c) and art in niche_c
        ctx.mark_seen("news", art["href"])
        seen.add(art["href"])
        # Ưu tiên CLICK như người thật; nếu click hỏng (link bị header che...) mới goto.
        if h.human_click(p, art["handle"]):
            try:
                p.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:
                pass
        else:
            try:
                p.goto(art["href"], wait_until="domcontentloaded", timeout=45000)
            except Exception:
                break
        h.pause(1.5, 3.5)
        remaining = budget - (time.time() - t0)
        h.scroll_for(p, reading_seconds(_article_words(p), remaining, rnd))     # đọc bài theo độ dài
        read += 1
        niche_read += 1 if is_niche else 0
        titles.append((art["text"][:80], is_niche))
        if budget - (time.time() - t0) < 40:
            break
        # Còn giờ -> quay lại danh sách (trang tìm ngách nếu đang theo ngách) đọc bài tiếp.
        try:
            p.goto(base_url, wait_until="domcontentloaded", timeout=45000)
            h.pause(1.0, 2.5)
            h.scroll_for(p, rnd.uniform(4, 10))
        except Exception:
            break

    left = budget - (time.time() - t0)
    if left > 3:
        h.scroll_for(p, left)                            # đọc nốt cho đủ thời gian
    secs = int(time.time() - t0)
    scope = f" theo ngách “{niche_kw}”" if niche_kw else ""
    if read == 0:
        return f"lướt {host}{scope} ~{secs} giây, đọc 0 bài (không thấy link bài viết)"
    nn = f", {niche_read} bài đúng ngách" if kws else ""
    out = [f"Đọc {read} bài trên {host}{scope}{nn} (~{secs // 60} phút {secs % 60} giây)."]
    for t, nq in titles:
        out.append(f"• {t}" + ("  (đúng ngách)" if nq else ""))
    return "\n".join(out)


def _collect_articles(p, host: str) -> list[dict]:
    """Trả danh sách {handle, href, text} là link cùng tên miền, trông giống bài báo."""
    try:
        handles = p.query_selector_all("a")
    except Exception:
        return []
    out: list[dict] = []
    seen = set()
    for hd in handles[:250]:
        try:
            rel = hd.get_attribute("href") or ""
            if not rel or rel.startswith(("#", "mailto:", "tel:", "javascript")):
                continue
            if not _looks_like_article(rel):
                continue
            text = (hd.inner_text() or "").strip()
            if len(text) < 28:
                continue
            absu = hd.evaluate("e => e.href")     # href tuyệt đối
            if host not in absu or absu in seen:
                continue
            seen.add(absu)
            out.append({"handle": hd, "href": absu, "text": text})
            if len(out) >= 40:
                break
        except Exception:
            continue
    return out


def _dismiss_consent(p, h) -> None:
    for sel in ['button:has-text("Đồng ý")', 'button:has-text("Chấp nhận")',
                'button[aria-label*="close" i]', '.qc-cmp2-summary-buttons button']:
        try:
            if p.locator(sel).count():
                h.human_click(p, sel)
                h.micro_pause()
                return
        except Exception:
            pass
