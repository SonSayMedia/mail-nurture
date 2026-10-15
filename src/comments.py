"""Bình luận video ĐỐI THỦ — có DUYỆT TAY.

Luồng an toàn (KHÔNG BAO GIỜ tự đăng):
  1) Trong phiên, khi mail xem một video đối thủ, CUỐI phiên AI soạn 1 NHÁP bình luận -> lưu hàng chờ
     (status 'pending'). Chưa đăng gì cả.
  2) Người dùng mở trang "Duyệt bình luận" trong app: Sửa / Duyệt / Bỏ từng nháp.
  3) Phiên SAU của mail đó: nếu có nháp đã DUYỆT ('approved'), đăng TỐI ĐA 1 cái, rồi chuyển 'posted'.

Giới hạn cứng: tối đa 1 nháp/mail/ngày và 1 bình luận ĐĂNG/mail/ngày; mỗi mail một câu khác nhau
(AI sinh riêng theo tiêu đề video). Nháp dính link / mời sub bị loại ngay ở bước làm sạch (content.py).
"""
from __future__ import annotations

from .actions.base import ActionContext
from . import safety

# Ô nhập bình luận của YouTube (bản 2024–2026).
_PLACEHOLDER = ("ytd-comment-simplebox-renderer #placeholder-area", "#simplebox-placeholder",
                "#placeholder-area")
_EDITABLE = ("ytd-commentbox #contenteditable-root", "#contenteditable-root",
             "div#contenteditable-root[contenteditable='true']")
_SUBMIT = ("ytd-commentbox #submit-button button", "#submit-button button", "#submit-button",
           'button[aria-label*="Bình luận" i]', 'button[aria-label*="Comment" i]')


def commenting_on(cfg_raw: dict) -> bool:
    """Nhóm này có BẬT soạn bình luận đối thủ không (mặc định tắt)."""
    return bool((cfg_raw.get("custom") or {}).get("comments"))


def _comment_ratio(cfg_raw: dict) -> float:
    try:
        return max(0.0, min(1.0, float((cfg_raw.get("custom") or {}).get("comment_ratio", 0.5))))
    except Exception:
        return 0.5


def _niche(cfg_raw: dict, persona) -> str:
    from .actions.youtube import clean_queries
    qs = clean_queries((cfg_raw.get("custom") or {}).get("youtube_queries"))
    return ", ".join(qs[:4]) if qs else ", ".join(getattr(persona, "interests", []) or [])


def maybe_draft(ctx: ActionContext, profile_name: str, group_name: str, emit=None) -> str:
    """CUỐI phiên: nếu nhóm bật + đã xem video đối thủ + chưa soạn hôm nay -> AI soạn 1 NHÁP, lưu chờ duyệt.
    Trả chuỗi mô tả (hoặc '' nếu không làm gì). KHÔNG đăng."""
    cand = ctx.comment_candidate
    if not commenting_on(ctx.cfg) or not cand or ctx.store is None:
        return ""
    pid = ctx.profile_id
    if ctx.store.queued_comment_today(pid) > 0:
        return ""                                   # đã soạn 1 nháp hôm nay rồi
    if ctx.rnd.random() >= _comment_ratio(ctx.cfg):
        return ""                                   # không phải phiên nào cũng soạn
    if ctx.store.has_open_comment(pid, cand.get("video_id", "")):
        return ""                                   # đã có nháp cho đúng video này
    lang = (ctx.cfg.get("content") or {}).get("language", "vi")
    draft = ctx.content.comment_draft(ctx.persona, cand.get("title", ""), _niche(ctx.cfg, ctx.persona),
                                      cand.get("desc", ""))
    if not draft:
        return ""                                   # thiếu AI / nháp không hợp lệ -> bỏ qua, không lưu rác
    cid = ctx.store.queue_comment(
        pid, profile_name, group_name, cand.get("video_id", ""), cand.get("title", ""),
        cand.get("url", ""), cand.get("channel", ""), lang, draft)
    try:
        ctx.store.add_event("comment_draft", f"[{profile_name}] nháp bình luận chờ duyệt: {draft[:60]}",
                            "info", pid, profile_name)
    except Exception:
        pass
    line = (f"Soạn nháp bình luận (chờ bạn duyệt #{cid}) — kênh {cand.get('channel', '')}"
            f"\n• Video: {cand.get('url', '')}\n• Nháp: {draft}")
    if emit:
        emit(f"  📝 [{profile_name}] soạn nháp bình luận (chờ duyệt #{cid}): {draft[:60]}")
    return line


def post_one_approved(ctx: ActionContext, profile_name: str, emit=None) -> str:
    """Nếu mail có nháp ĐÃ DUYỆT và hôm nay chưa đăng -> đăng TỐI ĐA 1 cái. Trả mô tả ('' nếu không đăng).
    Chỉ đụng tới nháp status='approved' (người dùng đã bấm Duyệt)."""
    if not commenting_on(ctx.cfg) or ctx.store is None:
        return ""
    pid = ctx.profile_id
    if ctx.store.posted_comment_today(pid) > 0:
        return ""
    pending = ctx.store.approved_comments_for(pid, limit=1)
    if not pending:
        return ""
    item = pending[0]
    text, url = (item.get("draft") or "").strip(), (item.get("video_url") or "").strip()
    if not text or not url:
        ctx.store.set_comment_status(item["id"], "failed", note="thiếu nội dung/link video")
        return ""
    # Rời trạng thái 'approved' TRƯỚC khi đăng: nếu đăng xong mà ghi DB lỗi, lần sau cũng KHÔNG
    # chọn lại dòng này (approved_comments_for chỉ lấy 'approved') -> không bao giờ đăng trùng 1 bình luận.
    ctx.store.set_comment_status(item["id"], "posting")
    ok, detail = _post(ctx, url, text)
    if ok:
        ctx.store.set_comment_status(item["id"], "posted")
        line = (f"Đăng bình luận (đã duyệt #{item['id']}) — kênh {item.get('channel', '')}"
                f"\n• Video: {url}\n• Nội dung: {text}")
        if emit:
            emit(f"  💬 [{profile_name}] đăng bình luận (#{item['id']}): {text[:60]}")
        return line
    ctx.store.set_comment_status(item["id"], "failed", note=detail)
    if emit:
        emit(f"  ⚠ [{profile_name}] không đăng được bình luận (#{item['id']}): {detail} — giữ lại chờ thử sau")
    # giữ nguyên 'failed' để không tự thử lại vô hạn; người dùng có thể duyệt lại
    return ""


def _first(page, selectors):
    for sel in selectors:
        try:
            loc = page.locator(sel)
            if loc.count() and loc.first.is_visible():
                return loc.first
        except Exception:
            continue
    return None


def _post(ctx: ActionContext, url: str, text: str) -> tuple[bool, str]:
    """Mở video -> cuộn tới phần bình luận -> gõ nháp đã duyệt -> bấm Bình luận. Nhiều lớp phòng vệ."""
    p, h = ctx.page, ctx.human
    from .actions.base import goto
    try:
        goto(ctx, url)
    except Exception as e:  # noqa: BLE001
        return False, f"không mở được video ({type(e).__name__})"
    # An toàn: nếu lạc sang trang đăng nhập/xác minh của Google -> KHÔNG thao tác.
    lvl, reason = safety.assess_risk(p)
    if lvl == "hard":
        return False, f"gặp trang rủi ro ({reason})"
    try:
        p.wait_for_selector("#comments, ytd-comments", timeout=12000)
    except Exception:
        return False, "không thấy khu vực bình luận (bình luận có thể bị tắt)"
    for _ in range(6):                      # cuộn xuống cho phần bình luận tải (lazy-load)
        if _first(p, _PLACEHOLDER) or _first(p, _EDITABLE):
            break
        p.mouse.wheel(0, 700)
        h.pause(0.6, 1.4)
    ph = _first(p, _PLACEHOLDER)
    if ph is not None:
        if not h.human_click(p, ph):
            try:
                ph.click(timeout=4000)
            except Exception:
                return False, "không bấm được ô nhập bình luận"
        h.pause(0.6, 1.4)
    box = _first(p, _EDITABLE)
    if box is None:
        return False, "không mở được ô nhập bình luận"
    try:
        box.click(timeout=4000)
    except Exception:
        pass
    h.micro_pause()
    # GÕ TỪNG KÝ TỰ như người thật (giống viết nhật ký): tốc độ dao động, thỉnh thoảng gõ nhầm rồi xoá sửa.
    from .actions.diary import _typing_cfg, type_human_core

    def _refocus():
        try:
            box.click(timeout=2000)
        except Exception:
            pass

    lo, hi, typo = _typing_cfg(ctx)
    try:
        type_human_core(ctx, text, lo, hi, typo, refocus=_refocus, risk_label="viết bình luận")
    except RuntimeError as e:
        return False, str(e)                            # gặp trang rủi ro khi đang gõ -> không đăng
    except Exception:
        try:
            p.keyboard.type(text, delay=120)            # dự phòng nếu gõ từng phím lỗi
        except Exception:
            try:
                p.keyboard.insert_text(text)
            except Exception:
                return False, "không gõ được nội dung"
    h.pause(0.8, 1.8)
    btn = _first(p, _SUBMIT)
    if btn is None:
        return False, "không thấy nút Bình luận"
    if not h.human_click(p, btn):
        try:
            btn.click(timeout=4000)
        except Exception:
            return False, "không bấm được nút Bình luận"
    h.pause(1.5, 3.0)
    return True, "ok"
