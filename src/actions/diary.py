"""Hành động: viết nhật ký vào Google Docs (mặc định) hoặc Google Keep.

CÁCH GÕ GOOGLE DOCS (đã kiểm nghiệm trên GPM v4.3.6 / Chromium 142):
Docs render bằng canvas nên gõ từng phím (keyboard.type) bị bỏ qua. Cách hoạt động là:
  1) focus vào contenteditable ẩn trong iframe `.docs-texteventtarget-iframe`
  2) dùng `keyboard.insert_text(...)` (chèn cả cụm như dán/IME)
  3) xuống dòng bằng ký tự "\n" NGAY TRONG insert_text (KHÔNG dùng phím Enter rời — mất focus)
Gõ theo cụm + re-focus trước mỗi cụm để có nhịp giống người và giữ chắc focus.
Sau khi viết, tự tải bản .txt (export) qua phiên đăng nhập để xác minh chữ đã vào.
"""
from __future__ import annotations

import re
import time

from .. import safety
from ..content import count_words
from .base import ActionContext, goto, register

_EDITOR_IFRAME = ".docs-texteventtarget-iframe"
DEFAULT_MIN_WORDS = 500      # nhật ký tối thiểu 500 từ (cấu hình diary.min_words; 0 = không bắt buộc)


@register("diary")
def write_diary(ctx: ActionContext) -> str:
    # Mỗi ngày tối đa 1 lần viết nhật ký.
    if ctx.store is not None and ctx.store.did_action_today(ctx.profile_id, "diary"):
        return "hôm nay đã viết nhật ký rồi (bỏ qua)"
    target = ctx.cfg.get("diary", {}).get("target", "google_docs")
    if target == "google_keep":
        return _write_keep(ctx)
    return _write_docs(ctx)


def _focus_editor(ctx: ActionContext) -> bool:
    try:
        ce = ctx.page.frame_locator(_EDITOR_IFRAME).locator("[contenteditable=true]")
        ce.focus()
        return True
    except Exception:
        return False


def _write_docs(ctx: ActionContext) -> str:
    """Ghi nhật ký hằng ngày, NỐI vào MỘT doc 'Nhật ký' riêng của mỗi mail.

    Doc id được nhớ trong store (kind 'diary_doc'). Lần đầu tạo doc mới; lần sau mở lại doc đó
    và ghi thêm 1 mục có ngày tháng ở CUỐI (theo thứ tự thời gian) -> giống người viết nhật ký thật.
    (Đã kiểm chứng live 2026-09-25: tạo + nối doc đều vào đúng, doc có đủ các mục.)
    """
    p, h = ctx.page, ctx.human
    min_words = _min_words(ctx)
    from .youtube import clean_queries
    niche = ", ".join(clean_queries((ctx.cfg.get("custom") or {}).get("youtube_queries"))[:6])
    title, body = ctx.content.diary_entry(ctx.persona, ctx.rnd, ctx.day_summary,
                                          min_words=min_words, niche=niche)   # title đã có ngày tháng
    if not body:
        if min_words:
            return (f"bỏ qua nhật ký (cần AI 9router viết bài ≥{min_words} từ — AI chưa bật hoặc chưa viết "
                    f"đủ dài; sẽ thử lại lần sau)")
        return "bỏ qua nhật ký (chưa có nội dung — nhóm ngoài tiếng Việt cần bật 9router/AI)"

    # Dùng đúng doc nhật ký dùng GẦN NHẤT (không lấy ngẫu nhiên -> tránh nhật ký bị tách nhiều doc).
    # Chạy nhiều máy: lấy mã file nhật ký ở thư mục chung trước -> mọi máy ghi tiếp đúng 1 file.
    doc_id = ""
    if ctx.shared is not None:
        try:
            doc_id = ctx.shared.diary_doc(ctx.profile_id)
        except Exception:
            doc_id = ""
    if not doc_id and ctx.store is not None:
        doc_id = ctx.store.latest_seen_key(ctx.profile_id, "diary_doc")
    is_new = False

    # Mở doc cũ nếu đã có. Chỉ tạo doc mới khi doc cũ thật sự hỏng — KHÔNG tạo khi Google đang
    # chặn/đòi đăng nhập (khi đó dừng luôn, để lá chắn an toàn xử lý).
    if doc_id:
        goto(ctx, f"https://docs.google.com/document/d/{doc_id}/edit")
        try:
            p.wait_for_selector(".kix-appview-editor", timeout=30000)
        except Exception:
            level, reason = safety.assess_risk(p)
            if level == "hard":
                raise RuntimeError(f"không mở được doc nhật ký — {reason}")
            doc_id = ""
    if not doc_id:
        goto(ctx, "https://docs.google.com/document/create")
        try:
            p.wait_for_selector(".kix-appview-editor", timeout=20000)
        except Exception:
            raise RuntimeError("không mở được trình soạn thảo Google Docs (mail có thể đã bị đăng xuất)")
        try:
            doc_id = p.url.split("/document/d/")[1].split("/")[0]
            ctx.mark_seen("diary_doc", doc_id)
        except Exception:
            doc_id = ""
        is_new = True
        h.pause(2.0, 4.0)                  # doc mới tải xong hẳn rồi mới thao tác (lần chạy 30/09 thao tác quá sớm)
        _name_new_doc(ctx)

    h.pause(1.5, 3.0)
    if not _focus_editor(ctx):
        raise RuntimeError("không focus được vùng nhập Google Docs (iframe ẩn)")

    # Doc cũ: đưa con trỏ về cuối rồi ghi thêm mục mới (nhật ký theo thứ tự thời gian).
    if not is_new:
        try:
            p.keyboard.press("Control+End")
        except Exception:
            pass
        _focus_editor(ctx)

    lang = (ctx.cfg.get("content") or {}).get("language", "vi")
    n_words = count_words(body, lang)
    sep = "" if is_new else "\n\n"
    paras = [x.strip() for x in re.split(r"\n\s*\n|\n", body) if x.strip()]
    text = sep + f"{title}\n" + "\n".join(paras)

    # Mở bảng đếm từ (Ctrl+Shift+C) như người viết hay làm: vừa lấy số từ ban đầu, vừa THỬ xem Docs có
    # nhận phím gõ không. Không mở được -> KHÔNG gõ phím (tránh ra chữ lỗi), chèn từng câu như cũ.
    before = _doc_word_count(ctx) if _typing_mode(ctx) == "keys" else None
    t0 = time.time()
    if before is None:
        mode = "chèn từng câu"
        chunks = ([sep] if sep else []) + [f"{title}\n"] + _body_chunks(body)
        for c in chunks:
            _focus_editor(ctx)
            p.keyboard.insert_text(c)
            h.pause(0.6, 1.8)
    else:
        mode = "gõ phím"
        typos = type_like_human(ctx, text)
        after = _doc_word_count(ctx)
        if before >= 0 and after is not None and after >= 0 and after - before < max(5, int(n_words * 0.5)):
            raise RuntimeError(f"gõ phím chưa vào đủ Google Docs (Docs chỉ tăng {after - before} từ trên "
                               f"{n_words} từ đã gõ)")
        mode += f", sửa {typos} lỗi gõ"
    mins = (time.time() - t0) / 60

    h.pause(2.5, 4.0)   # chờ Docs tự lưu
    if doc_id:
        ctx.mark_seen("diary_doc", doc_id)   # làm mới "gần nhất" = doc vừa ghi thành công
        if ctx.shared is not None:
            try:
                ctx.shared.set_diary_doc(ctx.profile_id, doc_id)
            except Exception:
                pass
    where = "tạo doc mới" if is_new else "nối thêm vào doc nhật ký cũ"
    out = [f"Viết nhật ký — {where}.",
           f"• Số từ: {n_words} từ",
           f"• Cách viết: {mode}",
           f"• Thời gian: ~{mins:.1f} phút"]
    if niche:
        out.append(f"• Chủ đề: {niche}")
    if doc_id:
        out.append(f"• Doc: docs.google.com/document/d/{doc_id}/edit")
    return "\n".join(out)


def _write_keep(ctx: ActionContext) -> str:
    p, h = ctx.page, ctx.human
    min_words = _min_words(ctx)
    title, body = ctx.content.diary_entry(ctx.persona, ctx.rnd, ctx.day_summary, min_words=min_words)
    if not body:
        return (f"bỏ qua nhật ký (cần AI 9router viết bài ≥{min_words} từ)" if min_words
                else "bỏ qua nhật ký (chưa có nội dung — nhóm ngoài tiếng Việt cần bật 9router/AI)")
    goto(ctx, "https://keep.google.com")
    h.pause(2.0, 4.0)
    opened = False
    for sel in ['[aria-label="Ghi chú…"]', '[aria-label="Take a note…"]',
                'div[role="button"]:has-text("Ghi chú")', 'div[role="button"]:has-text("Take a note")']:
        if p.locator(sel).count() and h.human_click(p, sel):
            opened = True
            break
    if not opened:
        raise RuntimeError("mở Google Keep nhưng không thấy ô ghi chú (cần tinh chỉnh selector)")
    h.micro_pause()
    p.keyboard.insert_text(f"{title}\n{body}")
    h.pause(1.0, 2.0)
    for sel in ['button:has-text("Đóng")', 'button:has-text("Close")']:
        if p.locator(sel).count() and h.human_click(p, sel):
            break
    return f"viết nhật ký vào Google Keep ({len(body)} ký tự)"


def _split_sentences(text: str) -> list[str]:
    # Chữ Latin: tách sau . ! ? khi có dấu cách (không tách "3.5"); chữ Nhật: tách ngay sau 。！？
    parts = re.split(r"(?<=[.!?])\s+|(?<=[。！？])", text.strip())
    return [s for s in parts if s]


# ---------------------------------------------------------------- gõ phím như người thật
# Phím gần nhau trên bàn phím QWERTY (để thỉnh thoảng gõ nhầm rồi xoá sửa).
_NEAR = {
    "q": "wa", "w": "qes", "e": "wrd", "r": "etf", "t": "ryg", "y": "tuh", "u": "yij", "i": "uok",
    "o": "ipl", "p": "ol", "a": "qsz", "s": "adw", "d": "sfe", "f": "dgr", "g": "fht", "h": "gjy",
    "j": "hku", "k": "jli", "l": "ko", "z": "xa", "x": "zcs", "c": "xvd", "v": "cbf", "b": "vng",
    "n": "bmh", "m": "nj",
}


def _typing_mode(ctx: ActionContext) -> str:
    return str((ctx.cfg.get("diary") or {}).get("typing", "keys") or "keys")


def _typing_cfg(ctx: ActionContext) -> tuple[float, float, float]:
    d = ctx.cfg.get("diary") or {}
    try:
        lo, hi = [float(x) for x in (d.get("typing_wpm") or [70, 90])][:2]
    except Exception:
        lo, hi = 70.0, 90.0
    try:
        typo = float(d.get("typo_rate", 0.02))
    except Exception:
        typo = 0.02
    return max(20.0, lo), max(lo, hi), min(max(typo, 0.0), 0.1)


def type_human_core(ctx: ActionContext, text: str, lo: float, hi: float, typo: float,
                    refocus=None, risk_label: str = "gõ") -> int:
    """Gõ TỪNG PHÍM như người thật: tốc độ dao động, thỉnh thoảng gõ nhầm phím bên cạnh rồi xoá sửa,
    dừng nghĩ sau dấu câu và giữa các đoạn. Dùng chung cho nhật ký Docs VÀ bình luận YouTube.
    refocus(): hàm giữ con trỏ trong ô đang gõ — gọi lúc đầu, sau mỗi xuống dòng và ~mỗi 120 ký tự.
    Trả số lỗi gõ đã sửa."""
    p, rnd = ctx.page, ctx.rnd
    wpm = rnd.uniform(lo, hi)
    base = 60.0 / (wpm * 5)                 # giây / ký tự (1 "từ" chuẩn = 5 ký tự)
    typos = 0
    since_focus = 0
    if refocus:
        refocus()
    for i, ch in enumerate(text):
        if ch == "\n":
            if refocus:
                refocus()
            p.keyboard.insert_text("\n")
            time.sleep(rnd.uniform(1.5, 4.5))            # ngẫm trước khi viết đoạn mới
            if refocus:
                refocus()
            since_focus = 0
            continue
        if since_focus >= 120 and refocus:                # giữ chắc focus vùng gõ
            refocus()
            since_focus = 0
        low = ch.lower()
        if typo and low in _NEAR and rnd.random() < typo:
            wrong = rnd.choice(_NEAR[low])
            p.keyboard.type(wrong.upper() if ch.isupper() else wrong)
            time.sleep(rnd.uniform(0.12, 0.45))           # nhận ra gõ sai
            p.keyboard.press("Backspace")
            time.sleep(rnd.uniform(0.08, 0.25))
            typos += 1
        p.keyboard.type(ch)
        since_focus += 1
        d = base * rnd.uniform(0.55, 1.6)
        if ch == " " and rnd.random() < 0.12:
            d += rnd.uniform(0.15, 0.5)                   # khựng lại nghĩ từ tiếp theo
        elif ch in ",;:":
            d += rnd.uniform(0.15, 0.5)
        elif ch in ".!?…":
            d += rnd.uniform(0.6, 1.8)                    # hết câu: nghĩ câu sau
        time.sleep(d)
        if i and i % 300 == 0:                            # đang gõ mà Google chặn/đòi đăng nhập -> dừng
            level, reason = safety.assess_risk(p)
            if level == "hard":
                raise RuntimeError(f"dừng {risk_label} — {reason}")
    return typos


def type_like_human(ctx: ActionContext, text: str) -> int:
    """Gõ nhật ký vào Google Docs từng phím như người thật (70–90 từ/phút). Trả số lỗi gõ đã sửa."""
    lo, hi, typo = _typing_cfg(ctx)
    return type_human_core(ctx, text, lo, hi, typo,
                           refocus=lambda: _focus_editor(ctx), risk_label="viết nhật ký")


def typing_seconds(words: int, wpm: float, chars_per_word: float = 5.3) -> float:
    """Ước lượng thời gian gõ `words` từ ở tốc độ phím `wpm`, KỂ CẢ dừng nghĩ (sau dấu cách, dấu phẩy,
    hết câu, sang đoạn) và gõ nhầm rồi sửa. Khớp với mô phỏng: ~60 từ/phút thực tế ở tốc độ phím 80."""
    chars = words * chars_per_word
    keys = chars * 60.0 / (wpm * 5) * 1.075
    pauses = words * 0.12 * 0.325 + (words / 12) * 0.325 + (words / 15) * 1.2 + (words / 90) * 3.0
    typos = chars * 0.8 * 0.02 * 0.45
    return keys + pauses + typos


_DOC_NAMES = {"vi": "Nhật ký", "ja": "日記", "ko": "일기", "en": "Diary", "es": "Diario"}


def _name_new_doc(ctx: ActionContext) -> None:
    """Đặt tên file nhật ký mới (thay cho 'Tài liệu không có tiêu đề') — gõ vào ô tên như người thật."""
    p, h = ctx.page, ctx.human
    lang = (ctx.cfg.get("content") or {}).get("language", "vi")
    name = _DOC_NAMES.get(lang, _DOC_NAMES["vi"])
    try:
        box = p.locator("input.docs-title-input").first
        if box.count() and h.human_click(p, box):
            h.micro_pause()
            p.keyboard.press("Control+A")
            h.press_type(p, name)
            h.pause(0.4, 1.0)
            p.keyboard.press("Enter")                     # xác nhận tên, con trỏ quay lại trang viết
            h.pause(0.8, 1.6)
    except Exception:
        pass                                              # không đặt được tên thì thôi, không ảnh hưởng nhật ký


_CLOSE_WORDS = ("huỷ", "hủy", "cancel", "ok", "đóng", "close")


def _close_dialogs(ctx: ActionContext) -> bool:
    """Đóng hộp thoại đang mở trên Docs bằng cách BẤM NÚT (Huỷ/OK) — đã thử trên Docs thật 30/09:
    phím Escape lúc đóng được lúc không, bảng kẹt lại sẽ nuốt mất phím gõ."""
    p = ctx.page
    for _ in range(3):
        try:
            dl = p.locator("[role=dialog]:visible")
            if not dl.count():
                return True
            btns = dl.last.locator("button")
            target = None
            for i in range(min(btns.count(), 8)):
                t = (btns.nth(i).inner_text() or "").strip().lower()
                if t in _CLOSE_WORDS:
                    target = btns.nth(i)
                    if t in ("huỷ", "hủy", "cancel"):
                        break                      # ưu tiên Huỷ: không đổi tuỳ chọn nào của Docs
            if target is None or not ctx.human.human_click(p, target):
                p.keyboard.press("Escape")
        except Exception:
            try:
                p.keyboard.press("Escape")
            except Exception:
                pass
        p.wait_for_timeout(800)
    try:
        return p.locator("[role=dialog]:visible").count() == 0
    except Exception:
        return False


def _doc_word_count(ctx: ActionContext) -> int | None:
    """Mở bảng 'Số từ' của Docs (Ctrl+Shift+C), đọc số từ rồi đóng (bấm Huỷ).
    Trả: số từ; -1 = bảng mở được (Docs nhận phím) nhưng không đọc được số; None = không mở được.
    Bảng Docs mới (30/09) dạng chữ: 'Số từ Trang 1 Từ 678 Ký tự 3030 …' -> số THỨ 2 là số từ."""
    p = ctx.page
    if not _close_dialogs(ctx):                           # hộp thoại có sẵn (vd giới thiệu lần đầu)
        return None
    dlg = None
    for _attempt in range(2):                             # doc vừa tạo có thể chưa sẵn sàng -> thử lại
        _focus_editor(ctx)
        try:
            p.keyboard.press("Control+Shift+C")
        except Exception:
            return None
        end = time.time() + 6
        while time.time() < end:
            try:
                loc = p.locator("[role=dialog]:visible")
                if loc.count() and re.search(r"\d", loc.last.inner_text() or ""):
                    dlg = loc.last
                    break
            except Exception:
                pass
            p.wait_for_timeout(300)
        if dlg is not None:
            break
        ctx.human.pause(1.5, 3.0)
    if dlg is None:
        return None
    ctx.human.pause(0.8, 2.0)                             # nhìn số từ
    words = -1
    try:
        nums = [int(re.sub(r"\D", "", x)) for x in re.findall(r"\d[\d., ]*", dlg.inner_text() or "")]
        if len(nums) >= 2:
            words = nums[1]                               # [trang, TỪ, ký tự, ký tự bỏ dấu cách]
    except Exception:
        pass
    if not _close_dialogs(ctx):
        raise RuntimeError("không đóng được bảng Số từ của Google Docs")
    _focus_editor(ctx)
    return words


def _min_words(ctx: ActionContext) -> int:
    try:
        return max(0, int((ctx.cfg.get("diary") or {}).get("min_words", DEFAULT_MIN_WORDS)))
    except Exception:
        return DEFAULT_MIN_WORDS


def _body_chunks(body: str) -> list[str]:
    """Chia bài thành từng câu để gõ; giữa các đoạn văn chèn xuống dòng."""
    paras = [x.strip() for x in re.split(r"\n\s*\n|\n", body) if x.strip()]
    chunks: list[str] = []
    for i, para in enumerate(paras):
        sents = _split_sentences(para)
        for j, s in enumerate(sents):
            gap = "" if (j == len(sents) - 1 or re.search(r"[。！？]$", s)) else " "
            chunks.append(s + gap)
        if i < len(paras) - 1:
            chunks.append("\n")
    return chunks
