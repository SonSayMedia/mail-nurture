"""Lá chắn an toàn: phát hiện trang Google 'nguy hiểm' để DỪNG ngay, không thao tác.

Hai loại rủi ro NGHIÊM TRỌNG (hard) — đều dừng phiên, gắn cờ, cho mail nghỉ:
  - ĐĂNG NHẬP LẠI: Google đòi đăng nhập (phiên hết hạn). Người dùng tự xử lý được
    (đăng nhập lại trong GPM) rồi gỡ nhãn "Cần đăng nhập".
  - THỬ THÁCH BẢO MẬT: "xác minh đó là bạn", hoạt động bất thường, captcha của Google,
    tài khoản bị vô hiệu... -> KHÔNG cho gỡ tay, phải nghỉ đủ thời gian cooldown.
Rủi ro NHẸ (soft): captcha của trang bên thứ 3 (báo...) -> chỉ rời trang, không phạt mail.

Quét chữ trên trang CHỈ áp dụng cho trang tài khoản Google (và trang không xác định được) —
tiêu đề bài báo, email trong hộp thư hay bình luận YouTube có chữ "hoạt động bất thường"
KHÔNG được làm mail bị phạt.
"""
from __future__ import annotations

SIGNIN_PREFIX = "Google yêu cầu đăng nhập lại"
CHALLENGE_PREFIX = "Google yêu cầu xác minh/cảnh báo bảo mật"
NEED_LOGIN_REASON = "Cần đăng nhập lại Google (mail đã bị đăng xuất)"

# Trang tài khoản Google (nơi Google hiện trang đăng nhập/thử thách). So khớp CHÍNH XÁC:
# không quét chữ ở Gmail/Docs/Keep/Drive vì đó là nội dung của người dùng (email, ghi chú...).
_AUTH_HOSTS = {"accounts.google.com", "myaccount.google.com", "gds.google.com",
               "www.google.com", "google.com"}
# Toàn bộ hệ sinh thái Google (captcha ở đây = rủi ro thật)
_GOOGLE_HOSTS = ("google.com", "youtube.com", "gstatic.com")
_YOUTUBE_HOSTS = ("youtube.com",)

# URL thử thách (kiểm tra TRƯỚC — vd .../v3/signin/challenge là thử thách, không phải đăng nhập thường)
_CHALLENGE_URL = ("/challenge", "speedbump", "deniedsigninrejected", "gds.google.com",
                  "myaccount.google.com/notifications", "google.com/sorry")
_SIGNIN_URL = ("accounts.google.com/signin", "accounts.google.com/v3/signin",
               "accounts.google.com/interactivelogin", "accounts.google.com/servicelogin",
               "accounts.google.com/accountchooser")

_SIGNIN_TEXT = ("đăng nhập vào tài khoản", "sign in to continue", "nhập lại mật khẩu",
                "enter your password", "wrong password", "sai mật khẩu")
_CHALLENGE_TEXT = ("xác minh rằng đó là bạn", "xác minh danh tính", "xác minh đó là bạn",
                   "verify it's you", "verify it’s you", "confirm it's you", "confirm it’s you",
                   "hoạt động bất thường", "unusual activity", "phát hiện hoạt động",
                   "we noticed unusual", "unusual traffic", "i'm not a robot", "i’m not a robot",
                   "tôi không phải là người máy", "tài khoản của bạn đã bị vô hiệu hóa",
                   "account disabled", "this account has been disabled")
# Trang chặn bot của chính YouTube (nằm trên youtube.com)
_YOUTUBE_BOT_TEXT = ("confirm you're not a bot", "confirm you’re not a bot",
                     "xác nhận bạn không phải là bot", "không phải là bot")


def _host(url: str) -> str:
    try:
        return url.split("//", 1)[-1].split("/", 1)[0].split(":")[0].lower()
    except Exception:
        return ""


def _is(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def assess_risk(page):
    """Trả (mức, lý_do): 'hard' | 'soft' | 'none'. Không bao giờ ném lỗi."""
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""
    host = _host(url)
    unknown = not host

    # 1) Địa chỉ trang (chỉ tin khi đúng là trang Google)
    if unknown or _is(host, _GOOGLE_HOSTS):
        for sig in _CHALLENGE_URL:
            if sig in url:
                return "hard", f"{CHALLENGE_PREFIX} ({sig})"
        for sig in _SIGNIN_URL:
            if sig in url:
                return "hard", f"{SIGNIN_PREFIX} ({sig})"

    # 2) Chữ trên trang — chỉ ở trang tài khoản Google / trang không xác định / trang chặn bot YouTube
    scan_full = unknown or host in _AUTH_HOSTS
    scan_yt = _is(host, _YOUTUBE_HOSTS)
    if scan_full or scan_yt:
        try:
            body = (page.inner_text("body", timeout=3000) or "").lower()
        except Exception:
            body = ""
        if scan_yt:
            for sig in _YOUTUBE_BOT_TEXT:
                if sig in body:
                    return "hard", f"{CHALLENGE_PREFIX} (YouTube chặn bot: '{sig}')"
        if scan_full:
            for sig in _CHALLENGE_TEXT:
                if sig in body:
                    return "hard", f"{CHALLENGE_PREFIX} ('{sig}')"
            for sig in _SIGNIN_TEXT:
                if sig in body:
                    return "hard", f"{SIGNIN_PREFIX} ('{sig}')"

    # 3) reCAPTCHA: của Google / không xác định -> hard; của trang ngoài -> soft
    try:
        has_captcha = page.locator("iframe[src*='recaptcha'], iframe[title*='recaptcha' i]").count() > 0
    except Exception:
        has_captcha = False
    if has_captcha:
        if unknown or _is(host, _GOOGLE_HOSTS):
            return "hard", f"{CHALLENGE_PREFIX} (reCAPTCHA trên {host or 'trang không xác định'})"
        return "soft", f"reCAPTCHA trên trang ngoài ({host})"
    return "none", None


def is_login_reason(reason: str) -> bool:
    """Cờ do bị ĐĂNG XUẤT / Google đòi ĐĂNG NHẬP LẠI (người dùng tự xử lý được).
    Thử thách bảo mật KHÔNG thuộc loại này (phải nghỉ đủ thời gian)."""
    r = (reason or "").strip()
    if r == NEED_LOGIN_REASON or r.startswith(SIGNIN_PREFIX):
        return True
    low = r.lower()
    if r.startswith(CHALLENGE_PREFIX):
        return False
    # Tương thích cờ cũ ("trang bảo mật/đăng nhập Google (accounts.google.com/v3/signin)")
    return "/signin" in low and not any(x in low for x in ("challenge", "speedbump", "captcha",
                                                             "xác minh", "sorry"))


# Cookie phiên đăng nhập Google (có ít nhất 1 cái = đang đăng nhập)
_GOOGLE_AUTH_COOKIES = {"SID", "__Secure-1PSID", "__Secure-3PSID", "SAPISID", "__Secure-3PAPISID"}


def google_logged_in(context):
    """Kiểm tra mail CÒN đăng nhập Google không, chỉ bằng cookie — KHÔNG mở trang nào.
    Trả True / False, hoặc None nếu không đọc được (khi đó cứ chạy bình thường).
    Lưu ý: còn cookie chưa chắc phiên còn hiệu lực (phiên hết hạn do lá chắn bắt khi gặp trang)."""
    try:
        cookies = context.cookies(["https://accounts.google.com", "https://www.google.com",
                                   "https://mail.google.com"])
    except Exception:
        try:
            cookies = context.cookies()
        except Exception:
            return None
    for c in cookies or []:
        if c.get("name") in _GOOGLE_AUTH_COOKIES and "google.com" in (c.get("domain") or ""):
            return True
    return False


def detect_risk(page) -> str | None:
    """Tương thích cũ: chỉ trả lý do khi rủi ro NGHIÊM TRỌNG (hard)."""
    level, reason = assess_risk(page)
    return reason if level == "hard" else None
