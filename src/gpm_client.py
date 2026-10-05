"""Wrapper cho API cục bộ của GPM Login v3.

Tài liệu API GPM Login v3 (chạy trên máy local, mặc định cổng 19995):
  - GET  /api/v3/profiles                     -> danh sách profile
  - GET  /api/v3/profiles/start/{id}          -> mở profile, trả remote_debugging_address
  - GET  /api/v3/profiles/close/{id}          -> đóng profile

Các endpoint được để cấu hình được (base_url/api_version) để dễ đổi khi GPM cập nhật.
Nếu response thực tế khác đôi chút, chỉnh lại hàm _extract_* bên dưới sau khi test live.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests


@dataclass
class Profile:
    id: str
    name: str
    group_id: str = ""
    group_name: str = ""
    raw: dict[str, Any] | None = None


@dataclass
class Group:
    id: str
    name: str
    raw: dict[str, Any] | None = None


@dataclass
class StartedProfile:
    profile_id: str
    remote_debugging_address: str   # ví dụ "127.0.0.1:53421" -> dùng cho Playwright CDP
    browser_location: str = ""
    driver_path: str = ""
    raw: dict[str, Any] | None = None

    @property
    def cdp_url(self) -> str:
        addr = self.remote_debugging_address
        if not addr.startswith("http"):
            addr = "http://" + addr
        return addr


class GPMError(RuntimeError):
    pass


class GPMClient:
    def __init__(self, base_url: str, api_version: str = "v3", timeout: int = 30):
        self.base = base_url.rstrip("/")
        self.api = f"{self.base}/api/{api_version}"
        self.timeout = timeout
        self.s = requests.Session()

    # ---- hạ tầng ----
    def _get(self, path: str, params: dict | None = None, timeout: float | None = None) -> dict:
        url = f"{self.api}{path}"
        t = timeout or self.timeout
        try:
            r = self.s.get(url, params=params, timeout=t)
        except requests.Timeout as e:
            # GPM đang chạy nhưng phản hồi chậm (vd profile có proxy khởi động lâu).
            raise GPMError(f"GPM phản hồi quá chậm ({t:.0f}s) khi gọi {path} — "
                           f"profile có thể khởi động lâu (proxy). Lỗi: {e}") from e
        except requests.RequestException as e:
            raise GPMError(
                f"Không kết nối được GPM Login tại {self.base}. "
                f"GPM đã mở và bật 'API' chưa? Lỗi: {e}"
            ) from e
        if r.status_code != 200:
            raise GPMError(f"GPM {path} trả HTTP {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
        except ValueError:
            raise GPMError(f"GPM {path} trả về không phải JSON: {r.text[:300]}")
        if isinstance(data, dict) and data.get("success") is False:
            raise GPMError(f"GPM {path} báo lỗi: {data.get('message') or data}")
        return data

    # ---- API ----
    def list_groups(self) -> list[Group]:
        """Lấy danh sách nhóm trong GPM."""
        data = self._get("/groups", params={"page": 1, "per_page": 200})
        items = self._extract_list(data)
        return [Group(id=str(it.get("id", "")), name=str(it.get("name", "")), raw=it)
                for it in items]

    def list_profiles(self, group_id: str = "", per_page: int = 200) -> list[Profile]:
        """Lấy toàn bộ profile (gộp các trang)."""
        out: list[Profile] = []
        page = 1
        while True:
            params = {"page": page, "per_page": per_page, "sort": 0}
            if group_id:
                params["group_id"] = group_id
            data = self._get("/profiles", params=params)
            items = self._extract_list(data)
            for it in items:
                out.append(
                    Profile(
                        id=str(it.get("id") or it.get("profile_id") or ""),
                        name=str(it.get("name") or it.get("profile_name") or ""),
                        group_id=str(it.get("group_id") or ""),
                        raw=it,
                    )
                )
            # Ưu tiên số trang GPM báo về (pagination.total_page); không có mới đoán theo số mục.
            total_page = None
            if isinstance(data, dict) and isinstance(data.get("pagination"), dict):
                total_page = data["pagination"].get("total_page") or data["pagination"].get("last_page")
            if not items:
                break
            if total_page:
                if page >= int(total_page):
                    break
            elif len(items) < per_page:
                break
            page += 1
            if page > 500:          # chặn vòng lặp vô tận nếu API trả dữ liệu lạ
                break
        return out

    def start_profile(self, profile_id: str, win_scale: float = 0.8,
                      wait_timeout: int = 60) -> StartedProfile:
        """Mở profile và trả về địa chỉ debug để Playwright bám vào."""
        params = {"win_scale": win_scale}
        # Lệnh mở chặn tới khi trình duyệt lên -> profile có proxy có thể mất ~30s+; cho chờ dài hơn.
        start_t = max(90.0, float(wait_timeout) + 30.0)
        data = self._get(f"/profiles/start/{profile_id}", params=params, timeout=start_t)
        started = self._extract_started(profile_id, data)
        # Chờ tới khi có địa chỉ debug (một số bản trả chậm).
        waited = 0.0
        while not started.remote_debugging_address and waited < wait_timeout:
            time.sleep(1.0)
            waited += 1.0
            data = self._get(f"/profiles/start/{profile_id}", params=params, timeout=start_t)
            started = self._extract_started(profile_id, data)
        if not started.remote_debugging_address:
            keys = list(started.raw.keys()) if isinstance(started.raw, dict) else started.raw
            raise GPMError(
                f"Profile {profile_id} mở nhưng không tìm thấy địa chỉ debug trong response. "
                f"Các key nhận được: {keys}. Hãy báo lại để chỉnh _extract_started()."
            )
        return started

    def close_profile(self, profile_id: str) -> None:
        self._get(f"/profiles/close/{profile_id}")

    # ---- bóc tách response (chỉnh ở đây nếu bản GPM khác cấu trúc) ----
    @staticmethod
    def _extract_list(data: Any) -> list[dict]:
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            d = data.get("data")
            if isinstance(d, list):
                return d
            if isinstance(d, dict) and isinstance(d.get("profiles"), list):
                return d["profiles"]
        return []

    @staticmethod
    def _extract_started(profile_id: str, data: Any) -> StartedProfile:
        d = data.get("data") if isinstance(data, dict) else None
        d = d if isinstance(d, dict) else (data if isinstance(data, dict) else {})
        addr = (
            d.get("remote_debugging_address")
            or d.get("remote_debug_address")
            or d.get("remoteDebuggingAddress")
            or d.get("debug_address")
            or d.get("debugAddress")
            or ""
        )
        return StartedProfile(
            profile_id=str(d.get("profile_id") or profile_id),
            remote_debugging_address=str(addr),
            browser_location=str(d.get("browser_location") or ""),
            driver_path=str(d.get("driver_path") or ""),
            raw=d,
        )
