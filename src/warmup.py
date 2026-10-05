"""Khởi động dần (warm-up): account càng 'mới' với automation thì càng đi nhẹ.

Chia giai đoạn theo SỐ PHIÊN đã nuôi thành công trước đó (robust hơn tính theo ngày lịch
vì có thể không chạy đều mỗi ngày):

  - seed    (0-6 phiên)  : YouTube + 1 việc (báo / ghé website).
  - warming (7-13 phiên) : YouTube + 1-2 việc, thêm gmail.
  - active  (>=14 phiên) : YouTube + 1-2 việc (báo/website/gmail).
  (YouTube luôn có trong mọi phiên — mỗi phiên xem 2–4 video. Thời lượng CẢ PHIÊN do cấu hình
   session.total_minutes quyết định, mặc định 15–20 phút; session_seconds ở đây chỉ còn để tham khảo.)
  Nhật ký ('diary') KHÔNG tính vào số hành động: runner viết nó như bước tổng kết cuối ngày
  (tối đa 1 lần/ngày, chỉ khi weight > 0).

Chỉ chọn trong các hành động đang bật (action_weights > 0). Nhờ vậy bạn có thể bật/tắt
từng việc chỉ bằng cách chỉnh trọng số trong config, không cần sửa code.
"""
from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass
class Plan:
    stage: str
    allowed: list[str]
    n_actions: int
    session_seconds: tuple[int, int]


# Danh sách hành động cho phép theo từng giai đoạn (trước khi lọc theo weight).
_STAGES = [
    # (ngưỡng phiên tối đa, tên giai đoạn, hành động cho phép, khoảng n, khoảng giây)
    (7,  "seed",    ["youtube", "news", "visit", "diary"],            (2, 2), (900, 1200)),
    (14, "warming", ["youtube", "news", "visit", "gmail", "diary"],   (2, 3), (900, 1200)),
]
_ACTIVE = ("active", ["youtube", "news", "visit", "gmail", "diary"], (2, 3), (900, 1200))


def plan_for(total_ok_sessions: int, cfg: dict, rnd: random.Random) -> Plan:
    weights = cfg.get("action_weights", {}) or {}
    enabled = {a for a, w in weights.items() if float(w) > 0}

    stage = _ACTIVE
    for threshold, name, allowed, nrange, secs in _STAGES:
        if total_ok_sessions < threshold:
            stage = (name, allowed, nrange, secs)
            break

    name, allowed, nrange, secs = stage
    allowed = [a for a in allowed if a in enabled] or ["news"]
    n = rnd.randint(nrange[0], nrange[1])
    n = min(n, len(allowed) * 2)
    return Plan(stage=name, allowed=allowed, n_actions=max(1, n), session_seconds=secs)
