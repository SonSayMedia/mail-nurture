"""Sinh nội dung nhật ký. Hỗ trợ 3 chế độ:
  - template : ghép câu từ mẫu, KHÔNG cần API key (chạy được ngay).
  - gemini   : gọi Google Gemini (hoặc URL 9router tương thích).
  - claude   : gọi Anthropic Claude.

Nội dung là nhật ký cá nhân trung tính, theo giọng của persona, không nhắc gì tới automation.
"""
from __future__ import annotations

import json
import random
import re
from datetime import date, datetime

import requests

from .personas import Persona

# 9router → Antigravity (model "ag/gemini-..." có SUY LUẬN): phải cấp đủ token cho phần nghĩ + phần viết,
# nếu không model nghĩ hết lượt rồi trả content RỖNG. Và luôn gửi stream=false (tránh nhận dạng luồng SSE).
NINEROUTER_MAX_TOKENS = 8192          # đủ cho phần suy luận + bài nhật ký 500–800 từ
AI_PROVIDERS = ("9router", "ninerouter", "openai", "gemini", "claude")


def count_words(text: str, language: str = "vi") -> int:
    """Đếm số từ. Tiếng Nhật không có dấu cách -> ước lượng 2 ký tự = 1 từ."""
    text = text or ""
    if language == "ja":
        return len(re.sub(r"\s+", "", text)) // 2
    return len(text.split())


def _clean_ai_text(text: str) -> str:
    """Bỏ dấu markdown / dòng tiêu đề mà AI đôi khi tự thêm (nhật ký phải là văn xuôi thuần)."""
    lines = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if s.startswith("#"):
            continue
        lines.append(s.replace("**", "").replace("__", ""))
    out = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _as_text(c, strip: bool = True) -> str:
    """content có thể là chuỗi hoặc danh sách phần [{type:text, text:...}]."""
    if isinstance(c, list):
        s = " ".join((x if isinstance(x, str) else str((x or {}).get("text") or (x or {}).get("content") or ""))
                     for x in c if x)
    else:
        s = "" if c is None else str(c)
    return s.strip() if strip else s


def parse_chat_response(r) -> tuple[str, str]:
    """Đọc (nội_dung, finish_reason) từ /chat/completions — cả JSON thường lẫn dạng luồng (SSE).
    CHỈ lấy phần trả lời (content), KHÔNG lấy phần suy luận (reasoning) để khỏi ghi 'suy nghĩ' vào nhật ký."""
    try:
        data = r.json()
    except ValueError:
        data = None
    if isinstance(data, dict):
        ch = (data.get("choices") or [{}])[0] or {}
        msg = ch.get("message") or {}
        text = _as_text(msg.get("content")) or _as_text(ch.get("text")) or _as_text(data.get("output_text"))
        return text, str(ch.get("finish_reason") or "")
    parts: list[str] = []
    finish = ""
    for line in (getattr(r, "text", "") or "").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            js = json.loads(payload)
        except ValueError:
            continue
        ch = (js.get("choices") or [{}])[0] or {}
        delta = ch.get("delta") or ch.get("message") or {}
        parts.append(_as_text(delta.get("content"), strip=False))
        finish = ch.get("finish_reason") or finish
    return "".join(parts).strip(), str(finish or "")

_WEEKDAY = {0: "thứ Hai", 1: "thứ Ba", 2: "thứ Tư", 3: "thứ Năm",
            4: "thứ Sáu", 5: "thứ Bảy", 6: "Chủ nhật"}

_TEMPLATE_OPENERS = [
    "Hôm nay là một ngày khá bình thường.",
    "Sáng nay mình dậy hơi muộn một chút.",
    "Một ngày nữa lại trôi qua nhanh thật.",
    "Ngày hôm nay có vài chuyện đáng ghi lại.",
    "Cả ngày hôm nay khá bận rộn.",
    "Hôm nay mình thấy trong người thoải mái.",
    "Một ngày yên ả, không có gì đặc biệt.",
    "Hôm nay đầu óc hơi lơ đãng.",
    "Sáng dậy sớm nên có thời gian thong thả.",
    "Nay tự nhiên thấy có chút động lực.",
]
_WEATHER = [
    "Trời hôm nay nắng đẹp, dễ chịu.",
    "Trời hơi âm u, se se lạnh.",
    "Buổi chiều có cơn mưa nhỏ, mát mẻ hẳn.",
    "Thời tiết oi một chút nhưng vẫn chịu được.",
    "Gió nhẹ, ngồi đâu cũng thấy dễ chịu.",
    "",
]
_TEMPLATE_MIDDLES = {
    "âm nhạc": ["Nghe được vài bài hát hay nên tâm trạng thoải mái hơn.",
                 "Mở một playlist nhẹ nhàng để tập trung làm việc.",
                 "Tình cờ nghe lại một bài cũ, thấy bồi hồi.",
                 "Bật nhạc lên dọn nhà, tự nhiên thấy năng lượng."],
    "ẩm thực": ["Thử làm một món mới, kết quả cũng tạm ổn.",
                 "Ăn trưa ngon miệng, chiều làm việc hăng hơn.",
                 "Ghé thử một quán mới, đồ ăn cũng được.",
                 "Nấu một bữa đơn giản mà thấy ngon lạ."],
    "công nghệ": ["Đọc được tin công nghệ mới, thấy khá thú vị.",
                   "Mày mò một mẹo nhỏ cho máy tính, tiện hơn hẳn.",
                   "Cập nhật thêm vài thứ trên điện thoại cho gọn.",
                   "Xem review một món đồ công nghệ, khá hấp dẫn."],
    "thể thao": ["Tranh thủ vận động một chút cho khỏe người.",
                  "Xem lại vài pha bóng hay, đã mắt thật.",
                  "Đi bộ một vòng buổi tối cho thư giãn.",
                  "Theo dõi tin chuyển nhượng, cũng vui."],
    "du lịch": ["Lướt vài bài review du lịch, tự nhiên muốn đi đâu đó.",
                 "Lên kế hoạch cho một chuyến đi ngắn sắp tới.",
                 "Xem ảnh mấy nơi đẹp, thấy muốn xách balo lên.",
                 "Ghi lại vài địa điểm muốn ghé một ngày nào đó."],
    "giải trí": ["Xem mấy clip vui, cười được một lúc cho nhẹ đầu.",
                  "Giải trí một chút sau giờ làm cũng cần thiết.",
                  "Coi một tập phim ngắn trước khi ngủ.",
                  "Lướt xem vài trend mới cho biết."],
    "kiến thức": ["Học thêm được một điều mới, thấy ngày cũng có ích.",
                   "Đọc một bài dài nhưng bổ ích.",
                   "Tìm hiểu một chủ đề lạ, khá mở mang.",
                   "Ghi chú lại vài ý hay để dùng sau."],
    "làm đẹp": ["Chăm sóc bản thân một chút, thấy tự tin hơn.",
                 "Thử một cách phối đồ mới cho thay đổi.",
                 "Dành thời gian skincare buổi tối cho thư giãn.",
                 "Xem vài mẹo làm đẹp đơn giản, hữu ích."],
    "tài chính": ["Ngồi ghi lại chi tiêu trong ngày cho gọn.",
                   "Đọc chút kiến thức quản lý tiền bạc.",
                   "Rà lại kế hoạch tiết kiệm tháng này.",
                   "Xem một video về đầu tư cơ bản cho biết."],
    "gia đình": ["Dành thời gian cho việc nhà, thấy dễ chịu.",
                  "Chăm chút góc nhỏ trong nhà cho ngăn nắp.",
                  "Gọi hỏi thăm người thân một chút.",
                  "Tưới mấy chậu cây, thấy nhẹ nhàng."],
}
_REFLECTIONS = [
    "Nghĩ lại thì mọi thứ vẫn đang đi đúng hướng.",
    "Có những ngày bình yên như vầy cũng quý.",
    "Mình thấy cần giữ nhịp sống đều đặn hơn.",
    "Tối nay ngồi nghĩ vẩn vơ một lúc rồi thôi.",
    "Cảm giác hài lòng với những gì làm được hôm nay.",
]
_TEMPLATE_CLOSERS = [
    "Mong ngày mai mọi thứ suôn sẻ hơn.",
    "Nhìn chung là một ngày ổn.",
    "Cứ đều đặn như vậy là được.",
    "Ghi lại vài dòng để sau này đọc lại cho vui.",
    "Đi ngủ sớm để mai còn nhiều việc.",
    "Khép lại một ngày nhẹ nhàng.",
    "Mai cố gắng thêm một chút nữa.",
    "Vậy là hết một ngày, ngủ ngon.",
]


_LANG_NAME = {"vi": "tiếng Việt", "ja": "tiếng Nhật", "en": "tiếng Anh",
              "ko": "tiếng Hàn", "es": "tiếng Tây Ban Nha (giọng Mỹ Latinh)"}
_ES_MONTHS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
              "septiembre", "octubre", "noviembre", "diciembre"]
_EN_MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December"]


def _diary_title(language: str) -> str:
    """Tiêu đề nhật ký theo đúng ngôn ngữ thị trường."""
    d = date.today()
    if language == "ja":
        return f"日記 {d.year}年{d.month}月{d.day}日"
    if language == "ko":
        return f"일기 {d.year}년 {d.month}월 {d.day}일"
    if language == "en":
        return f"Diary — {_EN_MONTHS[d.month - 1]} {d.day}, {d.year}"
    if language == "es":
        return f"Diario — {d.day} de {_ES_MONTHS[d.month - 1]} de {d.year}"
    return f"Nhật ký ngày {d.strftime('%d/%m/%Y')}"


class ContentProvider:
    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.provider = self.cfg.get("provider", "template")
        self.language = self.cfg.get("language", "vi")

    def diary_entry(self, persona: Persona, rnd: random.Random | None = None,
                    activities: str = "", min_words: int = 0) -> tuple[str, str]:
        """Trả (tiêu_đề, nội_dung) cho nhật ký hôm nay. `activities` = tóm tắt việc đã làm.
        min_words > 0: bài BẮT BUỘC dài tối thiểu chừng đó từ (cần AI). Không đạt -> nội dung rỗng
        (bỏ qua hôm đó) chứ KHÔNG ghi bài ngắn."""
        rnd = rnd or random.Random()
        title = _diary_title(self.language)
        if min_words and min_words > 0:
            return title, self._long_entry(persona, activities, int(min_words))
        if self.provider in ("9router", "ninerouter", "openai"):
            body = self._ninerouter(persona, activities)
        elif self.provider == "gemini":
            body = self._gemini(persona, activities)
        elif self.provider == "claude":
            body = self._claude(persona, activities)
        else:
            body = ""
        # Không có nội dung từ AI: tiếng Việt -> dùng mẫu offline; ngôn ngữ khác -> BỎ QUA
        # (không ghi tiếng Việt vào nhật ký của mail thị trường khác).
        if not body:
            body = self._template(persona, rnd, activities) if self.language == "vi" else ""
        return title, body

    def generate(self, prompt: str) -> str:
        """Sinh văn bản tự do qua LLM đang cấu hình (rơi về chuỗi rỗng nếu lỗi/không có key)."""
        if self.provider in ("9router", "ninerouter", "openai"):
            return self._ninerouter_chat(prompt)
        if self.provider == "gemini":
            return self._gemini_text(prompt)
        if self.provider == "claude":
            return self._claude_text(prompt, max_tokens=3000)
        return ""

    # ---- nhật ký DÀI (tối thiểu min_words từ) ----
    def _long_entry(self, persona: Persona, activities: str, min_words: int) -> str:
        if self.provider not in AI_PROVIDERS:
            return ""                         # mẫu có sẵn không viết nổi bài dài tự nhiên -> cần AI
        body = _clean_ai_text(self.generate(self._prompt_long(persona, activities, min_words)))
        n = count_words(body, self.language)
        if body and n < min_words:
            # Còn ngắn -> nhờ AI viết TIẾP cho đủ (không viết lại từ đầu).
            more = _clean_ai_text(self.generate(self._prompt_continue(body, min_words - n + 150)))
            if more:
                body = body.rstrip() + "\n\n" + more
        return body if count_words(body, self.language) >= min_words else ""

    def _prompt_long(self, persona: Persona, activities: str, min_words: int) -> str:
        lang = _LANG_NAME.get(self.language, "tiếng Việt")
        did = f"Hôm nay có làm: {activities}. " if activities else ""
        return (
            f"Viết một bài nhật ký cá nhân bằng {lang} cho ngày hôm nay, DÀI khoảng {min_words + 20}–"
            f"{min_words + 120} từ (BẮT BUỘC tối thiểu {min_words} từ), chia 5–7 đoạn văn, các đoạn cách nhau "
            f"một dòng trống. Giọng văn {persona.diary_tone}. {did}Kể lại chi tiết từng việc đã làm trong ngày "
            f"kèm cảm nhận, suy nghĩ, một vài chuyện nhỏ đời thường, xen các chủ đề sở thích: "
            f"{', '.join(persona.interests)}. Viết tự nhiên như người bản xứ tự ghi nhật ký của mình: "
            f"không tiêu đề, không markdown, không gạch đầu dòng, không nhắc đến AI hay công cụ."
        )

    def _prompt_continue(self, body: str, need_words: int) -> str:
        lang = _LANG_NAME.get(self.language, "tiếng Việt")
        return (
            f"Dưới đây là phần đầu một bài nhật ký viết bằng {lang}:\n\n{body}\n\n"
            f"Hãy viết TIẾP bài nhật ký này thêm khoảng {need_words} từ nữa bằng {lang}, cùng giọng văn, "
            f"nối tiếp tự nhiên, không lặp lại ý đã viết, không tiêu đề, không markdown. "
            f"Chỉ trả về phần viết thêm."
        )

    # ---- template (offline) ----
    def _template(self, persona: Persona, rnd: random.Random, activities: str = "") -> str:
        parts: list[str] = []
        # Mở đầu, đôi khi có nhắc thứ trong tuần.
        opener = rnd.choice(_TEMPLATE_OPENERS)
        if rnd.random() < 0.4:
            wd = _WEEKDAY[datetime.now().weekday()]
            opener = f"Hôm nay là {wd}. " + opener
        parts.append(opener)
        # Nhắc lại việc đã làm hôm nay (nếu có).
        if activities:
            parts.append(f"Hôm nay mình có {activities}.")
        # Đôi khi thêm câu thời tiết.
        w = rnd.choice(_WEATHER)
        if w:
            parts.append(w)
        # Chọn 1-2 sở thích (không phải lúc nào cũng nhắc hết) để mỗi ngày mỗi khác.
        interests = list(persona.interests)
        rnd.shuffle(interests)
        k = 2 if persona.verbosity > 0.5 else 1
        for it in interests[:k]:
            if it in _TEMPLATE_MIDDLES:
                parts.append(rnd.choice(_TEMPLATE_MIDDLES[it]))
        # Đôi khi thêm một câu suy ngẫm.
        if rnd.random() < (0.6 if persona.verbosity > 0.5 else 0.3):
            parts.append(rnd.choice(_REFLECTIONS))
        parts.append(rnd.choice(_TEMPLATE_CLOSERS))
        return " ".join(parts)

    # ---- prompt chung cho LLM ----
    def _prompt(self, persona: Persona, activities: str = "") -> str:
        lang = _LANG_NAME.get(self.language, "tiếng Việt")
        did = f"Hôm nay có làm: {activities}. " if activities else ""
        return (
            f"Viết một đoạn nhật ký cá nhân bằng {lang} cho ngày hôm nay, "
            f"giọng văn {persona.diary_tone}, khoảng {'4-6' if persona.verbosity>0.5 else '2-3'} câu. "
            f"{did}Chủ đề xoay quanh sở thích: {', '.join(persona.interests)}. "
            f"Viết tự nhiên như người bản xứ ghi lại ngày của mình, "
            f"không tiêu đề, không markdown, không nhắc đến AI hay công cụ."
        )

    def _ninerouter(self, persona: Persona, activities: str = "") -> str:
        return self._ninerouter_chat(self._prompt(persona, activities))

    def _ninerouter_chat(self, prompt: str) -> str:
        """Gọi 9router (OpenAI-compatible /chat/completions) chạy local trên máy."""
        c = self.cfg.get("ninerouter") or self.cfg.get("9router") or {}
        base = str(c.get("base_url", "http://127.0.0.1:20128/v1")).rstrip("/")
        model = c.get("model", "gemini-3.8-flash-medium")
        token = c.get("api_key", "") or c.get("token", "")
        try:
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            r = requests.post(
                f"{base}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.9,
                    "max_tokens": NINEROUTER_MAX_TOKENS,
                    "stream": False,
                },
                timeout=240,
            )
            r.raise_for_status()
            text, _finish = parse_chat_response(r)
            return text
        except Exception:
            return ""

    def _gemini(self, persona: Persona, activities: str = "") -> str:
        return self._gemini_text(self._prompt(persona, activities))

    def _gemini_text(self, prompt: str) -> str:
        g = self.cfg.get("gemini", {})
        key = g.get("api_key", "")
        if not key:
            return ""
        base = g.get("base_url", "https://generativelanguage.googleapis.com").rstrip("/")
        model = g.get("model", "gemini-2.0-flash")
        url = f"{base}/v1beta/models/{model}:generateContent?key={key}"
        payload = {"contents": [{"parts": [{"text": prompt}]}]}
        try:
            r = requests.post(url, json=payload, timeout=120)
            r.raise_for_status()
            data = r.json()
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except Exception:
            return ""

    def _claude(self, persona: Persona, activities: str = "") -> str:
        return self._claude_text(self._prompt(persona, activities), max_tokens=400)

    def _claude_text(self, prompt: str, max_tokens: int = 400) -> str:
        c = self.cfg.get("claude", {})
        key = c.get("api_key", "")
        if not key:
            return ""
        base = c.get("base_url", "https://api.anthropic.com").rstrip("/")
        model = c.get("model", "claude-sonnet-5")
        try:
            r = requests.post(
                f"{base}/v1/messages",
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
                timeout=120,
            )
            r.raise_for_status()
            data = r.json()
            return "".join(b.get("text", "") for b in data.get("content", [])).strip()
        except Exception:
            return ""
