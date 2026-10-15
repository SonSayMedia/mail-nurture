"""Nút "Kiểm tra kết nối" (tab Cấu hình): thử GPM, 9router, YouTube Data API bằng giá trị đang nhập.

Mỗi hàm trả dict: {"level": "ok" | "warn" | "err", "msg": "<giải thích tiếng Việt>", ...}
Không đụng tài khoản Google nào; YouTube chỉ gọi 1 lệnh rẻ nhất (i18nRegions = 1 đơn vị hạn mức).
"""
from __future__ import annotations

import time

import requests

from .content import ContentProvider, parse_chat_response
from .gpm_client import GPMClient, GPMError
from .personas import build_persona

YT_TEST_URL = "https://www.googleapis.com/youtube/v3/i18nRegions"


def check_gpm(base_url: str, api_version: str = "v3") -> dict:
    try:
        gpm = GPMClient(base_url or "http://127.0.0.1:19995", api_version or "v3", timeout=8)
        n_groups = len(gpm.list_groups())
        n_profiles = len(gpm.list_profiles())
    except GPMError as e:
        return {"level": "err", "msg": f"Không kết nối được GPM — mở GPM, vào Cài đặt → API, bật API. ({e})"}
    except Exception as e:  # noqa: BLE001
        return {"level": "err", "msg": f"Lỗi khi gọi GPM: {e}"}
    return {"level": "ok", "msg": f"Đã kết nối — {n_profiles} mail, {n_groups} nhóm."}


def _err_text(r) -> str:
    try:
        err = r.json().get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)
        if err:
            return str(err)
    except Exception:
        pass
    return (r.text or "")[:160]


def _chat(base: str, token: str, model: str, timeout: float):
    """Gửi 1 câu hỏi rất ngắn (tiếng Anh, stream=false, đủ token cho model suy luận).
    Trả (http_code, nội_dung_trả_lời, lỗi_hoặc_finish_reason)."""
    try:
        r = requests.post(
            f"{base}/chat/completions", timeout=timeout,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            json={"model": model, "temperature": 0, "max_tokens": 1024, "stream": False,
                  "messages": [{"role": "user", "content": "Reply exactly with the single word OK."}]},
        )
    except requests.Timeout:
        return 0, "", f"quá {timeout:.0f}s không trả lời"
    except requests.RequestException as e:
        return -1, "", str(e)
    if r.status_code != 200:
        return r.status_code, "", _err_text(r)
    try:
        text, finish = parse_chat_response(r)
    except Exception:
        text, finish = "", ""
    return 200, text, (f"finish_reason: {finish}" if finish else "")


def _diary_probe(base: str, token: str, model: str) -> str:
    """Cho AI viết thử 1 đoạn nhật ký tiếng Việt bằng ĐÚNG đường code app dùng khi chạy thật."""
    cp = ContentProvider({"provider": "9router", "language": "vi",
                          "ninerouter": {"base_url": base, "model": model, "api_key": token}})
    return cp._ninerouter(build_persona("KIEM-TRA", "kiem-tra"), "đọc báo, xem YouTube")


def _alternates(model: str, models: list[str], exclude: set[str]) -> list[str]:
    """Model cùng họ để thử khi model đang chọn trả lời rỗng (vd ag/gemini-3.8-flash, -low, -high)."""
    base = model.split("/", 1)[-1]
    fam = base.rsplit("-", 1)[0] if base.endswith(("-high", "-medium", "-low")) else base
    rank = {"": 0, "-low": 1, "-high": 2}
    same = [m for m in models if m not in exclude and fam in m.split("/", 1)[-1]]
    same.sort(key=lambda m: rank.get(m.split("/", 1)[-1][len(fam):], 3))
    other = [m for m in models if m not in exclude and m not in same
             and "gemini" in m.lower() and "flash" in m.lower()]
    return (same + other)[:3]


def check_9router(base_url: str, token: str, model: str, timeout: float = 60) -> dict:
    """Kiểm tra 9router: đang chạy? token đúng? model trả lời được + VIẾT THỬ được nhật ký?
    Model đang nhập không dùng được mà có model khác chạy được -> trả 'suggested_model' để app điền sẵn."""
    base = (base_url or "http://127.0.0.1:20128/v1").strip().rstrip("/")
    model = (model or "").strip()
    token = (token or "").strip()

    # 1) 9router có đang chạy không + danh sách model
    models: list[str] = []
    try:
        r = requests.get(f"{base}/models", timeout=8,
                         headers={"Authorization": f"Bearer {token}"} if token else {})
        if r.status_code == 200:
            models = [str(m.get("id")) for m in (r.json().get("data") or []) if m.get("id")]
    except requests.RequestException:
        return {"level": "err", "msg": f"Không kết nối được 9router tại {base} — hãy mở app 9router "
                                       f"(Start-9Router) rồi thử lại."}
    except Exception:
        pass
    if not token:
        return {"level": "err", "msg": "9router đang chạy nhưng CHƯA có token — dán token vào ô "
                                       "“Token 9router” rồi kiểm tra lại.", "models": models}
    if not model:
        return {"level": "err", "msg": "Chưa nhập Model 9router (mục Nâng cao).", "models": models}

    # 2) thử model đang nhập (+ tên đầy đủ nếu thiếu tiền tố 'ag/'); trả lời rỗng -> thử model cùng họ.
    candidates = [model]
    if models and model not in models:
        candidates += [m for m in models if m.split("/", 1)[-1] == model and m != model]
    candidates += _alternates(model, models, set(candidates))
    tried: list[str] = []
    for m in candidates[:4]:
        t0 = time.time()
        code, text, err = _chat(base, token, m, timeout)
        if code in (401, 403):
            return {"level": "err", "models": models,
                    "msg": f"Token 9router SAI hoặc hết hạn (HTTP {code}: {err[:100]}). "
                           f"Lấy lại token trong app 9router."}
        if code != 200:
            what = f"HTTP {code}" if code > 0 else "không phản hồi"
            tried.append(f"{m}: {what} {err[:70]}".strip())
            continue
        if not text:
            tried.append(f"{m}: trả lời rỗng" + (f" ({err})" if err else ""))
            continue
        # Trả lời được câu ngắn -> cho viết thử 1 đoạn nhật ký thật.
        sample = _diary_probe(base, token, m)
        secs = time.time() - t0
        if not sample:
            tried.append(f"{m}: trả lời câu ngắn được nhưng viết nhật ký bị rỗng")
            continue
        show = sample if len(sample) <= 120 else sample[:119].rstrip() + "…"
        if m != model:
            return {"level": "ok", "suggested_model": m, "models": models, "sample": sample,
                    "msg": f"Token đúng. Model “{model}” không dùng được, nhưng “{m}” chạy tốt ({secs:.0f}s) — "
                           f"đã ĐIỀN SẴN vào ô Model, bấm “Lưu cấu hình”. AI viết thử: “{show}”"}
        return {"level": "ok", "models": models, "sample": sample,
                "msg": f"Token đúng, model “{m}” chạy tốt ({secs:.0f}s). AI viết thử: “{show}”"}
    detail = "; ".join(tried) if tried else "không có model nào để thử"
    return {"level": "err", "models": models,
            "msg": f"Token đúng nhưng chưa model nào viết được. Đã thử — {detail}. Mở trang quản lý 9router "
                   f"(http://127.0.0.1:20128/dashboard) kiểm tra kết nối Antigravity, rồi thử lại."}


def _check_one_youtube(key: str, timeout: float) -> str:
    """Thử 1 key, trả mã: ok | invalid | disabled | quota | restricted | neterr | httpNNN."""
    try:
        r = requests.get(YT_TEST_URL, params={"part": "snippet", "hl": "vi", "key": key}, timeout=timeout)
    except requests.RequestException:
        return "neterr"
    if r.status_code == 200:
        return "ok"
    low = (r.text or "").lower()
    if "api_key_invalid" in low or "keyinvalid" in low or "api key not valid" in low:
        return "invalid"
    if ("accessnotconfigured" in low or "service_disabled" in low
            or "has not been used in project" in low or "is disabled" in low):
        return "disabled"
    if "quotaexceeded" in low or "dailylimitexceeded" in low or "ratelimitexceeded" in low:
        return "quota"
    if "referer" in low or "ip_address" in low or "ipreferer" in low or "blocked" in low:
        return "restricted"
    return f"http{r.status_code}"


def check_youtube(api_key, timeout: float = 12) -> dict:
    """api_key: 1 key (str) hoặc NHIỀU key (list) để xoay vòng. Báo tổng hợp bao nhiêu key dùng được."""
    keys = api_key if isinstance(api_key, (list, tuple)) else [api_key]
    keys = [(k or "").strip() for k in keys if (k or "").strip()]
    # bỏ trùng, giữ thứ tự
    seen: set = set()
    keys = [k for k in keys if not (k in seen or seen.add(k))]
    if not keys:
        return {"level": "warn", "msg": "Chưa dán key (không bắt buộc) — YouTube vẫn chạy, chỉ chọn video "
                                        "ngẫu nhiên trong kết quả tìm kiếm."}
    codes = [_check_one_youtube(k, timeout) for k in keys]
    nok = codes.count("ok")
    n = len(keys)
    if n == 1:
        c = codes[0]
        if c == "ok":
            return {"level": "ok", "msg": "Key hợp lệ, YouTube Data API v3 đã bật."}
        if c == "quota":
            return {"level": "warn", "msg": "Key đúng nhưng đã HẾT hạn mức hôm nay — mai tự có lại. "
                                            "Muốn chạy tiếp hôm nay: thêm key dự phòng để xoay vòng."}
        if c == "invalid":
            return {"level": "err", "msg": "Key SAI — kiểm tra lại (copy thiếu hoặc thừa ký tự?)."}
        if c == "disabled":
            return {"level": "err", "msg": "Key đúng nhưng chưa BẬT “YouTube Data API v3” — Google Cloud "
                                           "Console → APIs & Services → Library → bật YouTube Data API v3."}
        if c == "restricted":
            return {"level": "err", "msg": "Key đang bị GIỚI HẠN (IP/website/ứng dụng) — bỏ giới hạn trong "
                                           "Google Cloud Console hoặc cho phép máy này."}
        if c == "neterr":
            return {"level": "err", "msg": "Không kết nối được Google (kiểm tra mạng)."}
        return {"level": "err", "msg": f"Lỗi {c}."}
    # Nhiều key
    if nok:
        extra = []
        for code, name in (("quota", "hết hạn mức hôm nay"), ("invalid", "sai"),
                           ("disabled", "chưa bật API"), ("restricted", "bị giới hạn")):
            c = codes.count(code)
            if c:
                extra.append(f"{c} {name}")
        tail = f" (còn: {', '.join(extra)})" if extra else ""
        return {"level": "ok", "msg": f"{nok}/{n} key dùng được — tự xoay vòng khi key hết hạn mức{tail}."}
    if codes.count("quota") == n:
        return {"level": "warn", "msg": f"Cả {n} key đều HẾT hạn mức hôm nay — mai tự có lại."}
    bad = []
    if "invalid" in codes:
        bad.append("có key SAI")
    if "disabled" in codes:
        bad.append("có key chưa bật YouTube Data API v3")
    if "restricted" in codes:
        bad.append("có key bị giới hạn IP/website")
    if "neterr" in codes and not bad:
        return {"level": "err", "msg": "Không kết nối được Google (kiểm tra mạng)."}
    return {"level": "err", "msg": f"Không key nào dùng được: {', '.join(bad) or 'lỗi không rõ'}."}
