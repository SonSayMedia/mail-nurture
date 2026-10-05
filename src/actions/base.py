"""Khung chung cho các hành động + nơi đăng ký (registry)."""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable

from playwright.sync_api import Page

from ..content import ContentProvider
from ..humanize import Humanizer
from ..personas import Persona

# registry: tên -> hàm chạy hành động. Dùng @register("youtube") để thêm.
ACTIONS: dict[str, "ActionFn"] = {}


@dataclass
class ActionContext:
    page: Page
    human: Humanizer
    persona: Persona
    content: ContentProvider
    rnd: random.Random
    cfg: dict           # toàn bộ config.raw
    store: object = None        # Store: để nhớ đã xem/đọc gì (tránh lặp)
    profile_id: str = ""
    day_summary: str = ""       # tóm tắt việc đã làm hôm nay (để nhật ký nhắc lại)
    # Quỹ thời gian của PHIÊN (tổng 10–15 phút): mốc nên kết thúc + số giây cần dành cho các việc SAU
    # việc đang làm (để YouTube tự tính xem mấy video cho vừa). 0 = không giới hạn.
    session_end: float = 0.0
    reserve_after: float = 0.0
    yt_videos: int = 0          # số video YouTube đã xem trong phiên (tối đa theo cấu hình)
    ads_seen: int = 0           # số LƯỢT quảng cáo YouTube gặp trong phiên
    ads_skipped: int = 0        # số lần đã bấm "Bỏ qua quảng cáo"
    ads_waited: int = 0         # số lượt quảng cáo KHÔNG có nút bỏ qua (phải chờ hết)
    shared: object = None       # Shared: "sổ trực" dùng chung khi chạy nhiều máy (None = 1 máy)

    def seen(self, kind: str) -> set:
        """Các key (video_id / url) profile này đã xem/đọc trước đây."""
        if self.store is None:
            return set()
        try:
            return self.store.recent_seen_keys(self.profile_id, kind)
        except Exception:
            return set()

    def mark_seen(self, kind: str, key: str) -> None:
        if self.store is not None and key:
            try:
                self.store.mark_seen(self.profile_id, kind, key)
            except Exception:
                pass


# Một hành động nhận ActionContext, trả về chuỗi mô tả ngắn những gì đã làm.
ActionFn = Callable[[ActionContext], str]


def register(name: str):
    def deco(fn: ActionFn) -> ActionFn:
        ACTIONS[name] = fn
        return fn
    return deco


def goto(ctx: ActionContext, url: str, wait: str = "domcontentloaded") -> None:
    ctx.page.goto(url, wait_until=wait, timeout=45000)
    ctx.human.pause(1.0, 3.0)
