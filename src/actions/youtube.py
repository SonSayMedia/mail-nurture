"""Hành động: xem YouTube như người thật — 2–4 video mỗi phiên, mỗi video ~2–3 phút.

• Nguồn video NGẪU NHIÊN: TÌM KIẾM (gõ từ khoá) · TRANG CHỦ (video đề xuất) · BÊN PHẢI (video gợi ý).
  Trang chủ trống (mail mới / tắt lịch sử) hoặc không có gợi ý -> tự chuyển sang nguồn khác.
• ĐỐI THỦ: nếu nhóm khai kênh đối thủ (custom.competitors), thỉnh thoảng (custom.competitor_ratio)
  mở thẳng trang video của một kênh đối thủ cùng ngách và xem video gần đây — như người nghiên cứu ngách.
• QUẢNG CÁO: nút "Bỏ qua" hiện ra là bấm (sau 0,5–1,5 giây như phản xạ người); quảng cáo không bỏ qua
  được thì chờ hết. Thời gian quảng cáo KHÔNG tính vào thời gian xem.
• Trong lúc xem: tối đa 3 thao tác ngẫu nhiên / video — tạm dừng, tua tới, tua lùi, bấm thanh tiến
  trình, xem lại từ đầu — xen với cuộn xem bình luận/gợi ý.
• An toàn: cứ vài giây kiểm tra trang chặn/xác minh của Google; có -> dừng ngay.
• Trí nhớ: không xem lại video đã xem; bỏ qua Shorts.
Số video tự tính theo quỹ thời gian còn lại của phiên (ctx.session_end / ctx.reserve_after).
"""
from __future__ import annotations

import re
import time
import urllib.parse

from .. import safety, youtube_api
from .base import ActionContext, goto, register

DEFAULTS = {
    "videos_per_session": [2, 4],
    "watch_seconds": [120, 180],
    "max_controls_per_video": 3,
    "like_prob": 0.1,
    "skip_ads": True,
}
_PER_VIDEO_OVERHEAD = 25          # giây mở video + chờ tải + (thường) 1 quảng cáo ngắn

_SKIP_SELECTORS = (".ytp-skip-ad-button", ".ytp-ad-skip-button-modern", ".ytp-ad-skip-button",
                   ".ytp-ad-skip-button-slot button", "button[id^='skip-button']")
_OPS = ("pause", "fwd", "back", "seekbar", "replay")
_OP_VI = {"pause": "tạm dừng", "fwd": "tua tới", "back": "tua lùi", "seekbar": "kéo thanh tua",
          "replay": "xem lại từ đầu"}
_CIRCLES = ["", "①", "②", "③", "④", "⑤", "⑥"]


def _num(i: int) -> str:
    return _CIRCLES[i] if 0 < i < len(_CIRCLES) else f"{i})"


def _vid(href: str) -> str:
    """Lấy videoId từ href (…/watch?v=ID…). Shorts trả rỗng (không xem Shorts)."""
    if not href or "/shorts/" in href:
        return ""
    if "v=" in href:
        return href.split("v=", 1)[1].split("&")[0].split("#")[0]
    return ""


def clean_queries(raw) -> list[str]:
    """Từ khoá chỉ định: tách theo dòng / dấu phẩy / chấm phẩy, bỏ dấu câu thừa ở 2 đầu, bỏ trùng.
    vd ['lịch sử,', 'người cổ đại, vũ trụ'] -> ['lịch sử', 'người cổ đại', 'vũ trụ']."""
    out: list[str] = []
    for item in raw or []:
        for q in re.split(r"[,;\n]", str(item)):
            q = q.strip(" \t.,;:-")
            if q and q not in out:
                out.append(q)
    return out


def clean_channels(raw) -> list[str]:
    """Danh sách kênh đối thủ: mỗi mục là @handle / link kênh / ID kênh. Tách theo dòng/phẩy, bỏ trùng."""
    out: list[str] = []
    for item in raw or []:
        for c in re.split(r"[,\n]", str(item)):
            c = c.strip().strip("<>")
            if c and c not in out:
                out.append(c)
    return out


def channel_videos_url(raw: str) -> str:
    """Chuẩn hoá một mục kênh đối thủ về trang DANH SÁCH VIDEO của kênh đó.
    @abc / abc / UC... / youtube.com/@abc / .../channel/UC... -> https://www.youtube.com/.../videos"""
    s = (raw or "").strip().strip("<>")
    if not s:
        return ""
    if s.startswith("@"):
        return f"https://www.youtube.com/{s}/videos"
    if re.fullmatch(r"UC[0-9A-Za-z_-]{20,}", s):
        return f"https://www.youtube.com/channel/{s}/videos"
    if "youtube.com" not in s and not s.startswith("http"):
        return f"https://www.youtube.com/@{s}/videos"           # handle thiếu @
    s = s.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    s = re.sub(r"/(featured|videos|streams|shorts|community|playlists|about)$", "", s)
    return s + "/videos"


def yt_cfg(ctx: ActionContext) -> dict:
    c = dict(DEFAULTS)
    c.update({k: v for k, v in (ctx.cfg.get("youtube") or {}).items() if v is not None})
    return c


def plan_video_count(ctx: ActionContext, c: dict, now: float | None = None) -> int:
    """Số video cho lần xem này: trong [min, max], vừa với thời gian còn lại của phiên."""
    vmin, vmax = int(c["videos_per_session"][0]), int(c["videos_per_session"][1])
    remaining = vmax - ctx.yt_videos          # KHÔNG BAO GIỜ vượt số video tối đa của phiên
    if remaining <= 0:
        return 0
    vmax = remaining
    vmin = max(1, min(vmin, vmax))
    if not ctx.session_end:
        return ctx.rnd.randint(vmin, vmax)
    now = time.time() if now is None else now
    avail = ctx.session_end - now - ctx.reserve_after
    per = (float(c["watch_seconds"][0]) + float(c["watch_seconds"][1])) / 2 + _PER_VIDEO_OVERHEAD
    fit = int(avail // per)
    hi = max(vmin, min(vmax, fit))
    # Phần lớn lấy số lớn nhất vừa quỹ giờ (để phiên đủ 10–15 phút), đôi khi ít hơn 1 cho tự nhiên.
    return hi if ctx.rnd.random() < 0.75 else max(vmin, hi - 1)


@register("youtube")
def watch_youtube(ctx: ActionContext) -> str:
    c = yt_cfg(ctx)
    n = plan_video_count(ctx, c)
    if n <= 0:
        return "đã xem đủ số video tối đa của phiên"
    ad0 = (ctx.ads_seen, ctx.ads_skipped, ctx.ads_waited)
    watched: list[dict] = []        # mỗi video: {src, title, vid, secs, ops, liked}
    liked_session = False
    for i in range(n):
        order = _source_order(ctx, first=(i == 0))
        opened = None
        for src in order:
            opened = _open_video(ctx, src)
            if opened:
                break
        if not opened:
            break
        src, title, vid = opened
        secs, ops_done = watch_current(ctx, ctx.rnd.randint(int(c["watch_seconds"][0]),
                                                            int(c["watch_seconds"][1])), c)
        this_liked = False
        if not liked_session and ctx.rnd.random() < float(c.get("like_prob", 0.1)):
            this_liked = _like(ctx.page, ctx.human)
            liked_session = liked_session or this_liked
        watched.append({"src": src, "title": title, "vid": vid, "secs": secs,
                        "ops": ops_done, "liked": this_liked})
        ctx.yt_videos += 1
        ctx.human.pause(1.5, 4.0)
    if not watched:
        raise RuntimeError("không mở được video YouTube nào")
    total = sum(w["secs"] for w in watched)
    ads = ads_text(ctx.ads_seen - ad0[0], ctx.ads_skipped - ad0[1], ctx.ads_waited - ad0[2])
    ad_part = "không có quảng cáo" if ads.startswith("không") else ads
    out = [f"Xem {len(watched)} video (~{total // 60} phút {total % 60} giây), {ad_part}."]
    for idx, w in enumerate(watched, 1):
        out.append(f"• Video {_num(idx)}: {w['title'][:90]}")
        out.append(f"   - Nguồn: {w['src']}")
        out.append(f"   - Thời lượng xem: {w['secs'] // 60} phút {w['secs'] % 60} giây")
        if w["ops"]:
            out.append(f"   - Thao tác: {', '.join(w['ops'])}")
        if w["liked"]:
            out.append("   - Đã bấm Thích (like)")
        if w.get("vid"):
            out.append(f"   - Link: youtu.be/{w['vid']}")
    return "\n".join(out)


def watch_more(ctx: ActionContext, seconds: float) -> str:
    """Lấp thời gian cho đủ phiên: xem thêm 1 video (nếu chưa đủ số tối đa) hoặc lướt trang chủ."""
    c = yt_cfg(ctx)
    vmax = int(c["videos_per_session"][1])
    if ctx.yt_videos < vmax and seconds >= 75:
        for src in _source_order(ctx, first=False):
            opened = _open_video(ctx, src)
            if opened:
                ad0 = (ctx.ads_seen, ctx.ads_skipped, ctx.ads_waited)
                target = int(min(float(c["watch_seconds"][1]), max(60.0, seconds - 20)))
                secs, _ = watch_current(ctx, target, c)
                ctx.yt_videos += 1
                ads = ads_text(ctx.ads_seen - ad0[0], ctx.ads_skipped - ad0[1], ctx.ads_waited - ad0[2])
                return f"xem thêm 1 video ({opened[0]}, ~{secs} giây; {ads}): {opened[1][:60]}"
    _go_home(ctx)
    ctx.human.scroll_for(ctx.page, max(10.0, seconds - 5))
    return f"lướt trang chủ YouTube ~{int(seconds)} giây"


# ---------------------------------------------------------------- chọn & mở video
def _competitors(ctx: ActionContext) -> list[str]:
    return clean_channels((ctx.cfg.get("custom") or {}).get("competitors"))


def _source_order(ctx: ActionContext, first: bool) -> list[str]:
    r = ctx.rnd.random()
    if first:
        main = "search" if r < 0.6 else "home"
        base = [main, "home" if main == "search" else "search"]
    elif r < 0.5:
        base = ["related", "home", "search"]
    elif r < 0.75:
        base = ["home", "related", "search"]
    else:
        base = ["search", "related", "home"]
    # Nhóm có khai kênh đối thủ: thỉnh thoảng ưu tiên xem video của đối thủ (nghiên cứu ngách).
    comps = _competitors(ctx)
    if comps:
        try:
            ratio = float((ctx.cfg.get("custom") or {}).get("competitor_ratio", 0.3))
        except (TypeError, ValueError):
            ratio = 0.3
        if ctx.rnd.random() < max(0.0, min(1.0, ratio)):
            return ["competitor"] + [s for s in base if s != "competitor"]
    return base


def _dismiss_consent(p, h) -> None:
    for sel in ['button[aria-label*="Từ chối" i]', 'button[aria-label*="Reject" i]',
                'button:has-text("Từ chối tất cả")', 'button:has-text("Reject all")']:
        try:
            if p.locator(sel).count():
                h.human_click(p, sel)
                h.micro_pause()
                return
        except Exception:
            pass


def _go_home(ctx: ActionContext) -> None:
    p, h = ctx.page, ctx.human
    on_yt = "youtube.com" in (p.url or "")
    logo = p.locator("ytd-topbar-logo-renderer a, a#logo")
    if on_yt and logo.count() and h.human_click(p, logo.first):
        try:
            p.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        h.pause(1.0, 2.5)
    else:
        goto(ctx, "https://www.youtube.com")
        _dismiss_consent(p, h)
        h.pause(1.0, 2.5)


def _is_title_anchor(a) -> tuple[bool, str]:
    """Liên kết TIÊU ĐỀ video (không phải ảnh thu nhỏ chỉ có chữ thời lượng)."""
    try:
        cls = a.get_attribute("class") or ""
        aid = a.get_attribute("id") or ""
        label = (a.get_attribute("title") or a.get_attribute("aria-label") or "").strip()
        if label and len(label) > 8:
            return True, label
        if "Title" in cls or aid in ("video-title", "video-title-link"):
            return True, (a.inner_text() or "").strip()
    except Exception:
        pass
    return False, ""


def _collect(ctx: ActionContext, scope: str, limit: int = 40):
    """Các video (locator, videoId, tiêu đề) trong vùng `scope`, bỏ Shorts, bỏ video đang xem."""
    p = ctx.page
    cur = _vid(p.url or "")
    loc = p.locator(f"{scope} a[href*='/watch?v=']")
    out, seen_ids = [], set()
    try:
        n = min(loc.count(), 80)
    except Exception:
        return out
    for i in range(n):
        a = loc.nth(i)
        try:
            vid = _vid(a.get_attribute("href") or "")
        except Exception:
            continue
        if not vid or vid == cur or vid in seen_ids:
            continue
        ok, title = _is_title_anchor(a)
        if not ok:
            continue
        seen_ids.add(vid)
        out.append((a, vid, title))
        if len(out) >= limit:
            break
    return out


def _pick_and_click(ctx: ActionContext, items, prefer_ids=()) -> tuple[str, str] | None:
    """Chọn 1 video (ưu tiên video API gợi ý, rồi video chưa xem) và click như người."""
    if not items:
        return None
    seen = ctx.seen("youtube")
    fresh = [it for it in items if it[1] not in seen]
    if not fresh:
        return None                 # tất cả video ở đây đã xem rồi -> KHÔNG xem lại, thử nguồn khác
    chosen = None
    for tid in prefer_ids:          # ưu tiên video theo thứ hạng view, nhưng chỉ video CHƯA xem
        chosen = next((it for it in fresh if it[1] == tid), None)
        if chosen:
            break
    if chosen is None:
        chosen = ctx.rnd.choice(fresh[:8])
    a, vid, title = chosen
    before = ctx.page.url
    if not ctx.human.human_click(ctx.page, a):
        try:
            a.click(timeout=8000)
        except Exception:
            return None
    if not _wait_watch(ctx.page, before):
        return None
    ctx.mark_seen("youtube", vid)
    return vid, title


def _wait_watch(p, before_url: str, timeout: float = 20) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        u = p.url or ""
        if "/watch?v=" in u and u != before_url:
            try:
                p.wait_for_selector("video", timeout=10000)
            except Exception:
                pass
            return True
        p.wait_for_timeout(500)
    return False


def _open_video(ctx: ActionContext, src: str) -> tuple[str, str] | None:
    p, h = ctx.page, ctx.human
    try:
        if src == "competitor":
            comps = _competitors(ctx)
            if not comps:
                return None
            chan = ctx.rnd.choice(comps)
            url = channel_videos_url(chan)
            if not url:
                return None
            goto(ctx, url)
            _dismiss_consent(p, h)
            try:
                p.wait_for_selector(
                    "ytd-rich-item-renderer a[href*='/watch?v='], ytd-grid-video-renderer a[href*='/watch?v=']",
                    timeout=10000)
            except Exception:
                return None                                    # kênh sai / chặn -> nguồn khác
            h.scroll_read(p, steps=ctx.rnd.randint(1, 3))      # liếc vài video gần đây như người thật
            items = _collect(ctx, "ytd-rich-item-renderer") or _collect(ctx, "ytd-grid-video-renderer")
            got = _pick_and_click(ctx, items[:15])
            if not got:
                return None
            vid, title = got
            # Ghi lại video đối thủ vừa xem (+ mô tả video để soạn bình luận SÁT nội dung thật).
            # CUỐI phiên mới soạn NHÁP bình luận (nếu nhóm bật), KHÔNG đăng.
            ctx.comment_candidate = {"video_id": vid, "title": title,
                                     "url": (p.url or "").split("&", 1)[0], "channel": chan,
                                     "desc": _video_description(p)}
            return (f"đối thủ ({chan})", title, vid)

        if src == "related":
            if "/watch?v=" not in (p.url or ""):
                return None
            p.mouse.wheel(0, ctx.rnd.randint(200, 600))          # liếc xuống danh sách gợi ý
            h.pause(1.0, 2.5)
            items = _collect(ctx, "#related") or _collect(ctx, "#secondary")
            got = _pick_and_click(ctx, items[:12])
            return ("gợi ý bên phải", got[1], got[0]) if got else None

        if src == "home":
            _go_home(ctx)
            try:
                p.wait_for_selector("ytd-rich-item-renderer a[href*='/watch?v=']", timeout=8000)
            except Exception:
                return None                                    # trang chủ trống -> nguồn khác
            h.scroll_read(p, steps=ctx.rnd.randint(1, 4))
            items = _collect(ctx, "ytd-rich-item-renderer")
            got = _pick_and_click(ctx, items)
            return ("trang chủ", got[1], got[0]) if got else None

        # search
        query = ctx.persona.pick_youtube_query(ctx.rnd)
        cust = ctx.cfg.get("custom") or {}
        cqs = clean_queries(cust.get("youtube_queries") or [])
        if cqs and ctx.rnd.random() < float(cust.get("query_ratio", 0.3)):
            query = ctx.rnd.choice(cqs)
        api = ctx.cfg.get("youtube_api") or {}
        # Ưu tiên chọn video: (1) nhiều view nhất trong 1 tuần → (2) nhiều view nhất mọi thời điểm
        # → (3) mới nhất VPH cao nhất. _pick_and_click chỉ chọn video CHƯA xem (không tương tác lại).
        target_ids = youtube_api.ranked_video_ids(
            youtube_api.keys_from_cfg(api), query,
            region=api.get("region", "VN"), language=api.get("language", "vi"))
        if "youtube.com" not in (p.url or ""):
            _go_home(ctx)
        box = p.locator("input#search, input[name='search_query']")
        if box.count() and h.human_click(p, box.first):
            h.micro_pause()
            try:
                p.keyboard.press("Control+A")
            except Exception:
                pass
            h.press_type(p, query)
            h.pause(0.4, 1.2)
            p.keyboard.press("Enter")
        else:
            goto(ctx, "https://www.youtube.com/results?search_query=" + urllib.parse.quote(query))
        try:
            p.wait_for_selector("ytd-video-renderer a#video-title", timeout=15000)
        except Exception:
            return None
        h.scroll_read(p, steps=ctx.rnd.randint(1, 3))
        items = _collect(ctx, "ytd-video-renderer")
        got = _pick_and_click(ctx, items, prefer_ids=target_ids)
        return (f"tìm “{query}”", got[1], got[0]) if got else None
    except RuntimeError:
        raise
    except Exception:
        return None


# ---------------------------------------------------------------- xem 1 video
def _ad_showing(p) -> bool:
    try:
        return bool(p.evaluate(
            "() => { const m = document.querySelector('#movie_player');"
            " return !!(m && (m.classList.contains('ad-showing') || m.classList.contains('ad-interrupting'))); }"))
    except Exception:
        return False


def _skip_button(p):
    for sel in _SKIP_SELECTORS:
        try:
            loc = p.locator(sel)
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def handle_ads(ctx: ActionContext, max_wait: float = 100) -> float:
    """Đang có quảng cáo: nút 'Bỏ qua' hiện ra là bấm; không bỏ qua được thì chờ hết. Trả số giây quảng cáo."""
    p, h = ctx.page, ctx.human
    t0 = time.time()
    if not _ad_showing(p):
        return 0.0
    ctx.ads_seen += 1                            # 1 lượt quảng cáo (có thể gồm 2 quảng cáo liền nhau)
    skipped_here = 0
    while time.time() - t0 < max_wait and _ad_showing(p):
        btn = _skip_button(p)
        if btn is not None:
            h.pause(0.5, 1.5)                    # phản xạ người: thấy nút rồi mới bấm
            clicked = h.human_click(p, btn)
            if not clicked:
                try:
                    btn.click(timeout=3000)
                    clicked = True
                except Exception:
                    pass
            if clicked:
                ctx.ads_skipped += 1
                skipped_here += 1
            h.pause(0.8, 1.6)
            continue
        p.wait_for_timeout(1000)
    if skipped_here == 0:
        ctx.ads_waited += 1                      # lượt này không có nút bỏ qua -> đã chờ hết
    return time.time() - t0


def ads_text(seen: int, skipped: int, waited: int) -> str:
    """Dòng đếm quảng cáo: 'quảng cáo: gặp 3 lượt, bấm Bỏ qua 2 lần, 1 lượt chờ hết'."""
    if seen <= 0:
        return "không gặp quảng cáo"
    parts = [f"quảng cáo: gặp {seen} lượt", f"bấm Bỏ qua {skipped} lần"]
    if waited:
        parts.append(f"{waited} lượt không có nút bỏ qua (chờ hết)")
    return ", ".join(parts)


def _video_description(p) -> str:
    """Lấy phần MÔ TẢ của video đang mở (để AI soạn bình luận bám sát nội dung thật). Rỗng nếu không có."""
    try:
        txt = p.evaluate(
            "() => { const el = document.querySelector('#description-inline-expander')"
            " || document.querySelector('ytd-watch-metadata #description')"
            " || document.querySelector('#description');"
            " return el ? ((el.innerText || el.textContent) || '').trim() : ''; }") or ""
        return txt[:800]
    except Exception:
        return ""


def _video_state(p) -> dict:
    try:
        return p.evaluate(
            "() => { const v = document.querySelector('video.html5-main-video') || document.querySelector('video');"
            " return v ? {d: isFinite(v.duration) ? v.duration : 0, t: v.currentTime, paused: v.paused, ended: v.ended}"
            " : {d: 0, t: 0, paused: false, ended: false}; }") or {}
    except Exception:
        return {}


def _focus_player(ctx: ActionContext) -> None:
    """Rời ô nhập chữ (phím tắt YouTube chỉ chạy khi không gõ trong ô tìm kiếm) + rê chuột lên video."""
    p = ctx.page
    try:
        p.evaluate("() => { const a = document.activeElement;"
                   " if (a && ['INPUT','TEXTAREA'].includes(a.tagName)) a.blur(); }")
    except Exception:
        pass
    try:
        box = p.locator("#movie_player").first.bounding_box()
        if box:
            ctx.human.move_mouse_curve(p, box["x"] + box["width"] * ctx.rnd.uniform(0.3, 0.7),
                                       box["y"] + box["height"] * ctx.rnd.uniform(0.3, 0.7))
    except Exception:
        pass


def _ensure_playing(ctx: ActionContext) -> None:
    st = _video_state(ctx.page)
    if st.get("paused") and not st.get("ended"):
        _focus_player(ctx)
        try:
            ctx.page.keyboard.press("k")
        except Exception:
            pass


def _do_op(ctx: ActionContext, op: str) -> None:
    p, h, rnd = ctx.page, ctx.human, ctx.rnd
    _focus_player(ctx)
    try:
        if op == "pause":
            p.keyboard.press("k")                 # tạm dừng
            h.pause(2.0, 8.0)
            p.keyboard.press("k")                 # xem tiếp
        elif op == "fwd":
            for _ in range(rnd.randint(1, 3)):
                p.keyboard.press(rnd.choice(["l", "ArrowRight"]))     # +10 giây / +5 giây
                h.pause(0.3, 0.9)
        elif op == "back":
            for _ in range(rnd.randint(1, 2)):
                p.keyboard.press(rnd.choice(["j", "ArrowLeft"]))      # -10 giây / -5 giây
                h.pause(0.3, 0.9)
        elif op == "seekbar":
            bar = p.locator(".ytp-progress-bar").first
            box = bar.bounding_box()
            st = _video_state(p)
            if box and st.get("d"):
                cur = st["t"] / st["d"]
                frac = min(0.9, max(0.02, cur + rnd.uniform(-0.1, 0.25)))
                x = box["x"] + box["width"] * frac
                y = box["y"] + box["height"] / 2
                h.move_mouse_curve(p, x, y)
                h.micro_pause()
                p.mouse.click(x, y)
        elif op == "replay":
            p.keyboard.press("0")                 # về đầu video, xem lại
    except Exception:
        pass
    h.pause(0.8, 2.0)
    _ensure_playing(ctx)


def _plan_ops(ctx: ActionContext, target: int, max_ops: int) -> list[tuple[float, str]]:
    """Tối đa `max_ops` thao tác, rải ngẫu nhiên trong lúc xem; 'xem lại từ đầu' tối đa 1 lần."""
    if target < 40 or max_ops <= 0:
        return []
    weights = [15, 35, 30, 20][: max_ops + 1]
    k = ctx.rnd.choices(range(len(weights)), weights=weights)[0]
    times = sorted(ctx.rnd.uniform(15, target - 15) for _ in range(k))
    ops, used_replay = [], False
    for t in times:
        choices = [o for o in _OPS if not (o == "replay" and used_replay)]
        op = ctx.rnd.choice(choices)
        used_replay = used_replay or op == "replay"
        ops.append((t, op))
    return ops


def watch_current(ctx: ActionContext, target: int, c: dict | None = None) -> tuple[int, int]:
    """Xem video đang mở khoảng `target` giây (KHÔNG tính quảng cáo). Trả (giây đã xem, số thao tác)."""
    c = c or yt_cfg(ctx)
    p, h, rnd = ctx.page, ctx.human, ctx.rnd
    vid0 = _vid(p.url or "")
    h.pause(1.5, 3.0)
    if c.get("skip_ads", True):
        handle_ads(ctx)
    _ensure_playing(ctx)
    dur = _video_state(p).get("d") or 0
    if dur and dur < target + 15:                 # video ngắn -> xem gần hết
        target = int(max(20, dur - rnd.uniform(3, 10)))
    ops = _plan_ops(ctx, target, int(c.get("max_controls_per_video", 3)))
    watched, i_op = 0, 0
    ops_done: list[str] = []
    scrolled_comments = False
    guard_end = time.time() + target + 240       # chặn treo (quảng cáo dài / lỗi trang)
    while watched < target and time.time() < guard_end:
        step = min(target - watched, rnd.randint(6, 15))
        p.wait_for_timeout(step * 1000)
        if c.get("skip_ads", True) and _ad_showing(p):
            handle_ads(ctx)                        # quảng cáo giữa video: không tính giờ xem
            continue
        watched += step
        level, reason = safety.assess_risk(p)
        if level == "hard":
            raise RuntimeError(f"dừng xem video — {reason}")
        if vid0 and _vid(p.url or "") != vid0:     # tự chuyển video khác / rời trang -> thôi
            break
        if i_op < len(ops) and watched >= ops[i_op][0]:
            _do_op(ctx, ops[i_op][1])
            ops_done.append(_OP_VI.get(ops[i_op][1], ops[i_op][1]))
            i_op += 1
        elif rnd.random() < 0.3:
            p.mouse.wheel(0, rnd.randint(300, 800))    # cuộn xem bình luận / gợi ý
            h.pause(1.0, 3.0)
            scrolled_comments = True
            if rnd.random() < 0.6:
                p.mouse.wheel(0, -rnd.randint(300, 800))
    if scrolled_comments:
        ops_done.append("cuộn xem bình luận")
    return int(watched), ops_done


def _like(p, h) -> bool:
    for sel in ["like-button-view-model button", 'button[aria-label*="thích video" i]',
                'button[aria-label*="like this video" i]', "#segmented-like-button button"]:
        try:
            loc = p.locator(sel)
            if loc.count() and loc.first.get_attribute("aria-pressed") != "true" and h.human_click(p, loc.first):
                return True
        except Exception:
            continue
    return False
