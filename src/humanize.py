"""Các tiện ích mô phỏng hành vi người thật: gõ phím, cuộn, di chuột, nghỉ."""
from __future__ import annotations

import math
import random
import time
from typing import Sequence

from playwright.sync_api import Page


class Humanizer:
    def __init__(self, cfg: dict | None = None):
        cfg = cfg or {}
        self.type_delay = tuple(cfg.get("type_delay", [0.05, 0.22]))
        self.read_pause = tuple(cfg.get("read_pause", [1.5, 6.0]))
        self.scroll_back_prob = float(cfg.get("scroll_back_prob", 0.25))

    # ---- thời gian ----
    @staticmethod
    def rand(a: float, b: float) -> float:
        return random.uniform(a, b)

    def pause(self, lo: float | None = None, hi: float | None = None) -> None:
        """Nghỉ như đang đọc/suy nghĩ."""
        lo = self.read_pause[0] if lo is None else lo
        hi = self.read_pause[1] if hi is None else hi
        time.sleep(self.rand(lo, hi))

    def micro_pause(self) -> None:
        time.sleep(self.rand(0.2, 0.8))

    # ---- gõ phím ----
    def type_text(self, page: Page, selector_or_locator, text: str) -> None:
        """Gõ từng ký tự với độ trễ ngẫu nhiên, thỉnh thoảng khựng lại."""
        loc = page.locator(selector_or_locator) if isinstance(selector_or_locator, str) else selector_or_locator
        loc.click()
        self.micro_pause()
        for ch in text:
            loc.type(ch, delay=self.rand(*self.type_delay) * 1000)
            if random.random() < 0.04:      # thỉnh thoảng dừng nghĩ giữa câu
                time.sleep(self.rand(0.4, 1.4))

    def press_type(self, page: Page, text: str) -> None:
        """Gõ vào phần tử đang focus (dùng cho ô tìm kiếm đã click sẵn)."""
        for ch in text:
            page.keyboard.type(ch, delay=self.rand(*self.type_delay) * 1000)
            if random.random() < 0.04:
                time.sleep(self.rand(0.4, 1.2))

    # ---- cuộn ----
    def scroll_read(self, page: Page, steps: int | None = None) -> None:
        """Cuộn xuống đọc trang, có lúc cuộn ngược lên như người thật."""
        steps = steps if steps is not None else random.randint(3, 8)
        for _ in range(steps):
            dy = random.randint(250, 700)
            page.mouse.wheel(0, dy)
            self.pause(0.8, 3.0)
            if random.random() < self.scroll_back_prob:
                page.mouse.wheel(0, -random.randint(120, 350))
                self.pause(0.6, 1.8)

    def scroll_for(self, page: Page, seconds: float) -> None:
        """Đọc/lướt trang trong đúng khoảng `seconds`: cuộn từng đoạn, dừng đọc, thỉnh thoảng cuộn lên."""
        end = time.time() + max(0.0, seconds)
        while True:
            left = end - time.time()
            if left <= 0.3:
                break
            try:
                page.mouse.wheel(0, random.randint(160, 520))
            except Exception:
                pass
            time.sleep(min(left, random.uniform(2.0, 7.0)))
            if random.random() < self.scroll_back_prob * 0.6 and end - time.time() > 2.5:
                try:
                    page.mouse.wheel(0, -random.randint(120, 320))
                except Exception:
                    pass
                time.sleep(min(max(0.0, end - time.time()), random.uniform(0.6, 1.6)))

    # ---- di chuột ----
    def move_mouse_curve(self, page: Page, x: float, y: float, steps: int = 18) -> None:
        """Di chuột theo đường cong nhẹ tới (x, y) thay vì nhảy thẳng."""
        try:
            start_x = random.randint(100, 400)
            start_y = random.randint(100, 400)
            for i in range(1, steps + 1):
                t = i / steps
                ease = 0.5 - 0.5 * math.cos(math.pi * t)   # ease-in-out
                cx = start_x + (x - start_x) * ease + random.uniform(-3, 3)
                cy = start_y + (y - start_y) * ease + random.uniform(-3, 3)
                page.mouse.move(cx, cy)
                time.sleep(self.rand(0.008, 0.03))
        except Exception:
            pass

    def human_click(self, page: Page, locator) -> bool:
        """Di chuột tới phần tử rồi click. Nhận selector (str), Locator, hoặc ElementHandle.
        Trả False nếu không click được."""
        try:
            if isinstance(locator, str):
                loc = page.locator(locator).first
            elif hasattr(locator, "first"):     # Locator
                loc = locator.first
            else:                                # ElementHandle
                loc = locator
            loc.scroll_into_view_if_needed(timeout=5000)
            box = loc.bounding_box()
            if box:
                tx = box["x"] + box["width"] * random.uniform(0.3, 0.7)
                ty = box["y"] + box["height"] * random.uniform(0.3, 0.7)
                self.move_mouse_curve(page, tx, ty)
            self.micro_pause()
            loc.click(timeout=5000)
            return True
        except Exception:
            return False


def weighted_choice(weights: dict[str, float]) -> str:
    """Chọn ngẫu nhiên một key theo trọng số."""
    keys = list(weights.keys())
    vals = [max(0.0, float(weights[k])) for k in keys]
    total = sum(vals) or 1.0
    r = random.uniform(0, total)
    acc = 0.0
    for k, v in zip(keys, vals):
        acc += v
        if r <= acc:
            return k
    return keys[-1]


def sample_int_range(rng: Sequence[float]) -> int:
    return random.randint(int(rng[0]), int(rng[1]))
