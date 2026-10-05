"""Thị trường / ngôn ngữ gán cho từng NHÓM mail.

Mỗi 'thị trường' quyết định: ngôn ngữ nhật ký, từ khoá YouTube, trang báo, vùng YouTube.
Gán ở cấp nhóm (groups.items."<tên>".market = "ja"...). Khi nuôi, mail sẽ hành xử như người
bản địa thị trường đó.

Lưu ý: nội dung tiếng nước ngoài (nhật ký) dùng AI (9router) — cần bật 9router + token.
Nếu không có AI, nhóm ngoài tiếng Việt sẽ BỎ QUA phần viết nhật ký (không ghi sai ngôn ngữ).
"""
from __future__ import annotations

MARKETS: dict[str, dict] = {
    "vi": {
        "name": "Việt Nam (Tiếng Việt)",
        "language": "vi", "region": "VN", "yt_lang": "vi",
        # (Bỏ vnexpress 30/09: liên tục hiện reCAPTCHA với các mail -> không đọc được bài.)
        "news_sites": ["https://tuoitre.vn", "https://dantri.com.vn", "https://thanhnien.vn"],
        "seeds": ["nhạc trẻ 2025", "công thức nấu ăn", "đánh giá điện thoại",
                  "bóng đá highlight", "review du lịch", "hài kịch", "tin tức hôm nay",
                  "mẹo vặt cuộc sống", "vlog hằng ngày", "phim hay", "học tiếng anh",
                  "chăm sóc da"],
    },
    "ja": {
        "name": "Nhật Bản (日本語)",
        "language": "ja", "region": "JP", "yt_lang": "ja",
        "news_sites": ["https://news.yahoo.co.jp", "https://www3.nhk.or.jp/news/",
                       "https://mainichi.jp", "https://www.asahi.com"],
        "seeds": ["音楽 2025", "簡単 レシピ", "スマホ レビュー", "サッカー ハイライト",
                  "旅行 vlog", "お笑い", "今日のニュース", "ゲーム実況", "自宅 トレーニング",
                  "節約 術", "映画 予告", "勉強法"],
    },
    "en": {
        "name": "Âu-Mỹ (English)",
        "language": "en", "region": "US", "yt_lang": "en",
        "news_sites": ["https://www.bbc.com/news", "https://edition.cnn.com",
                       "https://www.theguardian.com/international", "https://apnews.com"],
        "seeds": ["music 2025", "easy recipes", "phone review", "football highlights",
                  "travel vlog", "stand up comedy", "news today", "gameplay walkthrough",
                  "home workout", "money saving tips", "movie trailer", "study tips"],
    },
    "ko": {
        "name": "Hàn Quốc (한국어)",
        "language": "ko", "region": "KR", "yt_lang": "ko",
        "news_sites": ["https://news.naver.com", "https://www.yna.co.kr",
                       "https://www.donga.com", "https://www.hani.co.kr"],
        "seeds": ["음악 2025", "간단 요리 레시피", "스마트폰 리뷰", "축구 하이라이트",
                  "여행 브이로그", "예능 하이라이트", "오늘 뉴스", "게임 방송",
                  "홈트레이닝", "재테크 방법", "영화 예고편", "공부 방법"],
    },
    "es": {
        "name": "Mỹ Latinh (Español)",
        "language": "es", "region": "MX", "yt_lang": "es",
        "news_sites": ["https://www.eluniversal.com.mx", "https://www.milenio.com",
                       "https://www.clarin.com", "https://www.bbc.com/mundo"],
        "seeds": ["música 2025", "recetas fáciles", "reseña de celular", "goles de fútbol",
                  "vlog de viaje", "comedia stand up", "noticias de hoy", "gameplay español",
                  "rutina de ejercicio en casa", "cómo ahorrar dinero", "tráiler de película",
                  "consejos de estudio"],
    },
}

DEFAULT = "vi"


def get(key: str) -> dict:
    return MARKETS.get(key or DEFAULT, MARKETS[DEFAULT])


def options() -> list[tuple[str, str]]:
    """[(key, tên hiển thị)] cho giao diện."""
    return [(k, v["name"]) for k, v in MARKETS.items()]


def overrides_for(key: str) -> dict:
    """Chuyển preset thị trường thành các override chuẩn để trộn vào config nhóm."""
    m = MARKETS.get(key)
    if not m:
        return {}
    return {
        "content": {"language": m["language"]},
        "youtube_api": {"region": m["region"], "language": m["yt_lang"]},
        "news": {"sites": list(m["news_sites"])},
        "youtube_seeds": list(m["seeds"]),   # -> _persona_override
    }
