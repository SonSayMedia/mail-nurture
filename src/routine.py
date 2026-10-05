"""Hoạch định TRÌNH TỰ công việc mỗi ngày cho một mail — mỗi ngày một khác (tự học chống lặp).

Đọc các 'chữ ký trình tự' của những ngày gần nhất (từ nhật ký công việc trong store) rồi
chọn cho hôm nay một trình tự KHÁC đi. Nhờ vậy mail không lặp lại cùng một kiểu ngày qua ngày.
Chỉ xếp các hành động tương tác (youtube/news/gmail); nhật ký (diary) là bước 'chốt ngày' riêng.
"""
from __future__ import annotations

import random

from .humanize import weighted_choice

_INTERACT = ("youtube", "news", "gmail", "visit")


def _sample(inter: list[str], n: int, weights: dict, rnd: random.Random) -> list[str]:
    """Chọn n hành động (ưu tiên khác loại), rồi xáo trộn thứ tự."""
    pool = list(inter)
    seq: list[str] = []
    for _ in range(n):
        if pool:
            w = {k: float(weights.get(k, 1)) for k in pool}
            pick = weighted_choice(w)
            seq.append(pick)
            pool.remove(pick)          # ưu tiên đa dạng: không lặp cho tới khi hết loại
        else:
            w = {k: float(weights.get(k, 1)) for k in inter}
            seq.append(weighted_choice(w))
    rnd.shuffle(seq)
    return seq


def _sample_with(must: list[str], extras: list[str], n: int, weights: dict,
                 rnd: random.Random) -> list[str]:
    """Luôn có các việc `must` (mỗi việc 1 lần) + chọn thêm việc khác (không trùng) cho đủ n, rồi xáo thứ tự."""
    seq = list(must)
    pool = [a for a in extras if a not in seq]
    while len(seq) < n and pool:
        w = {k: float(weights.get(k, 1)) for k in pool}
        pick = weighted_choice(w)
        seq.append(pick)
        pool.remove(pick)
    rnd.shuffle(seq)
    return seq


def plan_routine(allowed: list[str], n_actions: int, weights: dict,
                 recent_sigs: list[str], rnd: random.Random,
                 must: tuple[str, ...] = ()) -> tuple[list[str], str]:
    """Trả (trình_tự, chữ_ký). Cố gắng cho chữ ký KHÁC các ngày gần đây.
    must: việc BẮT BUỘC có trong mọi phiên (vd 'youtube' — mỗi phiên xem 2–4 video)."""
    inter = [a for a in allowed if a in _INTERACT] or [a for a in allowed] or ["news"]
    # Việc bị kéo về 0 thì không bao giờ được chọn (kể cả khi thiếu việc để đủ số lượng).
    inter = [a for a in inter if float(weights.get(a, 1)) > 0] or inter
    must_l = [m for m in must if m in inter and float(weights.get(m, 1)) > 0]
    recent = set(recent_sigs or [])
    if must_l:
        extras = [a for a in inter if a not in must_l]
        n = max(len(must_l), min(n_actions, len(must_l) + len(extras)))
        sample = lambda: _sample_with(must_l, extras, n, weights, rnd)  # noqa: E731
    else:
        n = max(1, min(n_actions, len(inter) * 2))
        sample = lambda: _sample(inter, n, weights, rnd)  # noqa: E731

    best_seq = sample()
    for _ in range(16):
        seq = sample()
        sig = ">".join(seq)
        if sig not in recent:
            return seq, sig
        best_seq = seq
    # Không tránh được (ít lựa chọn) -> đành dùng, vẫn xáo thứ tự.
    return best_seq, ">".join(best_seq)
