"""YouTube Data API v3 - tìm video thật theo từ khoá (search.list).

Dùng để chọn video liên quan/đang phổ biến theo sở thích persona, rồi cho trình duyệt
gõ tìm & click vào đúng video đó (giữ hành vi giống người, không nhảy thẳng watch URL).

API key lấy ở Google Cloud Console (bật 'YouTube Data API v3'). Không có key -> trả rỗng,
action youtube tự quay về cách chọn video ngẫu nhiên trong kết quả tìm kiếm.
"""
from __future__ import annotations

from dataclasses import dataclass

import requests

_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


@dataclass
class Video:
    video_id: str
    title: str
    channel: str


def search_videos(api_key: str, query: str, max_results: int = 8,
                  region: str = "VN", language: str = "vi") -> list[Video]:
    if not api_key:
        return []
    params = {
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max(1, min(max_results, 25)),
        "regionCode": region,
        "relevanceLanguage": language,
        "key": api_key,
    }
    try:
        r = requests.get(_SEARCH_URL, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception:
        return []
    out: list[Video] = []
    for item in data.get("items", []):
        vid = (item.get("id") or {}).get("videoId")
        sn = item.get("snippet") or {}
        if vid:
            out.append(Video(video_id=vid, title=sn.get("title", ""),
                             channel=sn.get("channelTitle", "")))
    return out
