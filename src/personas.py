"""Mỗi tài khoản có một 'tính cách' ổn định (seed theo profile id) để hành vi nhất quán.

Persona quyết định: hay xem gì trên YouTube, đọc chủ đề báo nào, giọng viết nhật ký ra sao.
Nhờ seed cố định, cùng một account sẽ luôn có cùng sở thích qua các ngày -> giống người thật.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field

INTEREST_POOLS = {
    "âm nhạc": ["nhạc trẻ 2025", "acoustic cover", "playlist chill", "nhạc không lời làm việc", "liveshow"],
    "ẩm thực": ["công thức nấu ăn", "món ngon mỗi ngày", "review quán ăn", "làm bánh tại nhà", "street food"],
    "công nghệ": ["đánh giá điện thoại", "mẹo dùng máy tính", "tin công nghệ", "AI mới", "thủ thuật android"],
    "thể thao": ["bóng đá highlight", "ngoại hạng anh", "tin chuyển nhượng", "gym tại nhà", "yoga cơ bản"],
    "du lịch": ["review du lịch", "cẩm nang phượt", "ẩm thực vùng miền", "vlog du lịch", "kinh nghiệm đi biển"],
    "giải trí": ["hài kịch", "review phim", "gameshow", "clip vui", "trend tiktok"],
    "kiến thức": ["khám phá khoa học", "lịch sử việt nam", "tài liệu thiên nhiên", "học tiếng anh", "sách hay"],
    "làm đẹp": ["skincare cơ bản", "makeup hằng ngày", "review mỹ phẩm", "phối đồ", "chăm sóc tóc"],
    "tài chính": ["quản lý chi tiêu", "kiến thức đầu tư cơ bản", "tin kinh tế", "tiết kiệm", "khởi nghiệp"],
    "gia đình": ["chăm sóc trẻ", "mẹo nhà bếp", "trồng cây tại nhà", "dọn nhà", "sức khỏe gia đình"],
}

NEWS_TOPIC_BY_INTEREST = {
    "công nghệ": "so-hoa", "thể thao": "the-thao", "tài chính": "kinh-doanh",
    "giải trí": "giai-tri", "du lịch": "du-lich", "gia đình": "gia-dinh",
}

DIARY_TONES = ["mộc mạc, đời thường", "tích cực, lạc quan", "trầm lắng, suy tư",
               "hài hước nhẹ nhàng", "gọn gàng, liệt kê việc trong ngày"]


@dataclass
class Persona:
    profile_id: str
    interests: list[str]
    youtube_seeds: list[str]
    diary_tone: str
    active_bias: float          # 0..1: thiên về sáng (thấp) hay tối (cao)
    verbosity: float            # 0..1: viết nhật ký ngắn hay dài
    extra: dict = field(default_factory=dict)

    def pick_youtube_query(self, rnd: random.Random) -> str:
        return rnd.choice(self.youtube_seeds)

    def pick_interest(self, rnd: random.Random) -> str:
        return rnd.choice(self.interests)


def apply_override(persona: "Persona", override: dict | None) -> "Persona":
    """Đè sở thích/từ khoá YouTube theo cấu hình nhóm (nếu có)."""
    if not override:
        return persona
    interests = override.get("interests")
    if isinstance(interests, list) and interests:
        persona.interests = list(interests)
        # sinh lại từ khoá YouTube từ các sở thích được chỉ định (nếu nhóm không tự khai).
        seeds: list[str] = []
        for it in interests:
            if it in INTEREST_POOLS:
                seeds.extend(INTEREST_POOLS[it])
        if seeds:
            persona.youtube_seeds = seeds
    yt = override.get("youtube_seeds")
    if isinstance(yt, list) and yt:
        persona.youtube_seeds = list(yt)
    return persona


def build_persona(profile_id: str, name: str = "") -> Persona:
    """Tạo persona ổn định từ id (dùng hash làm seed)."""
    seed = int(hashlib.sha256(f"{profile_id}|{name}".encode()).hexdigest(), 16) % (2**32)
    rnd = random.Random(seed)

    interests = rnd.sample(list(INTEREST_POOLS.keys()), k=rnd.randint(2, 3))
    seeds: list[str] = []
    for it in interests:
        seeds.extend(rnd.sample(INTEREST_POOLS[it], k=min(3, len(INTEREST_POOLS[it]))))

    return Persona(
        profile_id=profile_id,
        interests=interests,
        youtube_seeds=seeds,
        diary_tone=rnd.choice(DIARY_TONES),
        active_bias=rnd.random(),
        verbosity=rnd.random(),
        extra={"seed": seed, "name": name},
    )
