"""YouTube Data API v3 - tìm video thật theo từ khoá (search.list).

Dùng để chọn video liên quan/đang phổ biến theo sở thích persona, rồi cho trình duyệt
gõ tìm & click vào đúng video đó (giữ hành vi giống người, không nhảy thẳng watch URL).

API key lấy ở Google Cloud Console (bật 'YouTube Data API v3'). Không có key -> trả rỗng,
action youtube tự quay về cách chọn video ngẫu nhiên trong kết quả tìm kiếm.

XOAY VÒNG NHIỀU KEY: mỗi key có hạn mức riêng ~10.000 đơn vị/ngày. Khai nhiều key
(youtube_api.api_keys) -> key nào HẾT HẠN MỨC hôm nay thì tự chuyển sang key kế tiếp.
Key bị đánh dấu hết hạn chỉ trong NGÀY hôm đó, sang ngày mới tự dùng lại.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import requests

_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
# Lý do lỗi (reason) của Google khi HẾT hạn mức / quá tốc độ -> coi như key này hết lượt hôm nay.
_QUOTA_REASONS = ("quotaexceeded", "dailylimitexceeded", "ratelimitexceeded", "userratelimitexceeded")

_lock = threading.Lock()
_exhausted: dict[str, str] = {}      # api_key -> 'YYYY-MM-DD' đã hết hạn mức (reset theo ngày)


@dataclass
class Video:
    video_id: str
    title: str
    channel: str
    published_at: str = ""      # ISO 8601, để tính VPH (view/giờ)


def keys_from_cfg(api_cfg: dict | None) -> list[str]:
    """Gộp key chính (api_key) + các key dự phòng (api_keys) thành 1 danh sách, bỏ trùng, giữ thứ tự."""
    api_cfg = api_cfg or {}
    out: list[str] = []
    for k in [api_cfg.get("api_key")] + list(api_cfg.get("api_keys") or []):
        k = (k or "").strip()
        if k and k not in out:
            out.append(k)
    return out


def _is_exhausted(key: str) -> bool:
    with _lock:
        return _exhausted.get(key) == date.today().isoformat()


def _mark_exhausted(key: str) -> None:
    with _lock:
        _exhausted[key] = date.today().isoformat()


def _extract(data: dict) -> list[Video]:
    out: list[Video] = []
    for item in (data or {}).get("items", []):
        vid = (item.get("id") or {}).get("videoId")
        sn = item.get("snippet") or {}
        if vid:
            out.append(Video(video_id=vid, title=sn.get("title", ""), channel=sn.get("channelTitle", ""),
                             published_at=sn.get("publishedAt", "")))
    return out


def _quota_status(r) -> str:
    """Phân loại phản hồi lỗi: 'quota' nếu hết hạn mức, 'error' nếu lỗi khác."""
    reason = ""
    try:
        errs = ((r.json().get("error") or {}).get("errors")) or []
        reason = (errs[0].get("reason") if errs else "").lower()
    except Exception:
        reason = (r.text or "").lower()
    if r.status_code in (403, 429) and any(q in reason for q in _QUOTA_REASONS):
        return "quota"
    return "error"


def _search_once(key: str, params: dict) -> tuple[list[Video], str]:
    """Gọi 1 key. Trả (videos, status) với status: 'ok' | 'quota' | 'error'."""
    try:
        r = requests.get(_SEARCH_URL, params={**params, "key": key}, timeout=20)
    except Exception:
        return [], "error"
    if r.status_code == 200:
        try:
            return _extract(r.json()), "ok"
        except Exception:
            return [], "error"
    return [], _quota_status(r)


def search_videos_rotating(keys: list[str], query: str, max_results: int = 8,
                           region: str = "VN", language: str = "vi",
                           extra: dict | None = None) -> list[Video]:
    """Thử lần lượt từng key (bỏ key đã hết hạn mức hôm nay). Key hết hạn -> đánh dấu + sang key sau.
    extra: tham số thêm cho search.list, vd {'order':'viewCount','publishedAfter':'...Z'}."""
    if not keys or not query:
        return []
    params = {
        "part": "snippet", "q": query, "type": "video",
        "maxResults": max(1, min(max_results, 25)),
        "regionCode": region, "relevanceLanguage": language,
        **(extra or {}),
    }
    for key in keys:
        if not key or _is_exhausted(key):
            continue
        vids, status = _search_once(key, params)
        if status == "ok":
            return vids
        if status == "quota":
            _mark_exhausted(key)        # key này hết lượt hôm nay -> thử key kế tiếp
        # 'error' (key sai/mạng lỗi...) -> cũng thử key kế tiếp
    return []


def _week_ago_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _video_views_once(key: str, ids: list[str]) -> tuple[dict, str]:
    """videos.list: {video_id: (view_count:int, published_iso:str)} (status 'ok'|'quota'|'error')."""
    try:
        r = requests.get(_VIDEOS_URL, params={"part": "statistics,snippet", "id": ",".join(ids),
                                              "key": key}, timeout=20)
    except Exception:
        return {}, "error"
    if r.status_code != 200:
        return {}, _quota_status(r)
    out = {}
    try:
        for it in (r.json() or {}).get("items", []):
            vid = it.get("id")
            if not vid:
                continue
            views = int(((it.get("statistics") or {}).get("viewCount")) or 0)
            pub = (it.get("snippet") or {}).get("publishedAt", "")
            out[vid] = (views, pub)
    except Exception:
        return {}, "error"
    return out, "ok"


def _video_views_rotating(keys: list[str], ids: list[str]) -> dict:
    for key in keys:
        if not key or _is_exhausted(key):
            continue
        data, status = _video_views_once(key, ids)
        if status == "ok":
            return data
        if status == "quota":
            _mark_exhausted(key)
    return {}


def _rank_by_vph(keys: list[str], vids: list[Video]) -> list[str]:
    """Xếp các video mới theo VPH (view/giờ) giảm dần. Không lấy được số liệu -> giữ thứ tự 'mới nhất'."""
    ids = [v.video_id for v in vids][:20]
    stats = _video_views_rotating(keys, ids)
    if not stats:
        return ids
    now = datetime.now(timezone.utc)
    scored = []
    for v in vids:
        views, pub = stats.get(v.video_id, (0, v.published_at))
        hours = 1.0
        try:
            if pub:
                dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                hours = max((now - dt).total_seconds() / 3600.0, 1.0)
        except Exception:
            hours = 1.0
        scored.append((views / hours, v.video_id))
    scored.sort(reverse=True)
    return [vid for _, vid in scored]


def ranked_video_ids(keys: list[str], query: str, region: str = "VN", language: str = "vi",
                     max_results: int = 12) -> list[str]:
    """Danh sách videoId theo ƯU TIÊN của người dùng:
      1) Nhiều view nhất trong ~1 tuần gần đây.
      2) Nhiều view nhất từ trước đến nay.
      3) Mới nhất, VPH (view/giờ) cao nhất.
    Trả rỗng nếu không có key / không gọi được API (lúc đó tự chọn trong kết quả tìm như cũ)."""
    if not keys or not query:
        return []
    # 1) view nhiều nhất trong 7 ngày
    vids = search_videos_rotating(keys, query, max_results, region, language,
                                  extra={"order": "viewCount", "publishedAfter": _week_ago_iso()})
    if vids:
        return [v.video_id for v in vids]
    # 2) view nhiều nhất mọi thời điểm
    vids = search_videos_rotating(keys, query, max_results, region, language, extra={"order": "viewCount"})
    if vids:
        return [v.video_id for v in vids]
    # 3) mới nhất -> xếp theo VPH
    vids = search_videos_rotating(keys, query, max_results, region, language, extra={"order": "date"})
    if not vids:
        return []
    return _rank_by_vph(keys, vids)


def search_videos(api_key: str, query: str, max_results: int = 8,
                  region: str = "VN", language: str = "vi") -> list[Video]:
    """Tương thích ngược: tìm bằng MỘT key."""
    return search_videos_rotating([api_key] if api_key else [], query, max_results, region, language)
