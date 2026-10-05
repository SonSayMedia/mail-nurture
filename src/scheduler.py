"""Điều phối nuôi nhiều profile: chọn nhóm ngẫu nhiên trong ngày, chạy song song có kiểm soát."""
from __future__ import annotations

import random
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .config import Config
from .gpm_client import GPMClient, Profile
from .runner import run_session
from .shared import Shared
from .store import Store
from . import activity_log, groups

_print_lock = threading.Lock()


def _log(msg: str) -> None:
    with _print_lock:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)
    activity_log.write(msg)          # để app xem lại ở trang "Nhật ký & Lỗi"


def select_profiles(cfg: Config, gpm: GPMClient, store: Store) -> list[Profile]:
    """Lấy danh sách profile cần nuôi hôm nay (đã lọc theo cấu hình + giới hạn/ngày)."""
    return plan_today(cfg, gpm, store)["picked"]


def quota_reached(cfg: Config, plan: dict) -> tuple[bool, int]:
    """Chạy nhiều máy: CÁC MÁY CỘNG LẠI đã nuôi đủ tỉ lệ hôm nay chưa (đếm trong sổ trực dùng chung)."""
    sh = Shared(cfg.raw)
    if not sh.enabled or not plan.get("pool_ids"):
        return False, 0
    n = len(set(sh.claimed_today()) & set(plan["pool_ids"]))
    return n >= plan.get("target", 0), n


def plan_today(cfg: Config, gpm: GPMClient, store: Store) -> dict:
    """{picked, target, pool_ids, shared_msg}. Chạy nhiều máy thì tính cả việc các máy khác đã làm."""
    only = cfg.get("gpm.only_profile_ids") or []
    gmap = groups.group_map(gpm)
    if only:
        profiles = [p for p in gpm.list_profiles() if p.id in {str(x) for x in only}]
    else:
        profiles = gpm.list_profiles(group_id=cfg.get("gpm.group_id", ""))
    groups.annotate_profiles(profiles, gmap)

    # CHỈ nuôi profile thuộc nhóm ở chế độ 'Nuôi'. Nhóm 'Khóa'/'Để sau' bị loại hoàn toàn.
    profiles = [p for p in profiles if groups.is_nurture(cfg.raw, p.group_name)]

    # Chạy nhiều máy: đọc "sổ trực" chung. Không vào được -> KHÔNG chọn mail nào (tránh nuôi trùng).
    sh = Shared(cfg.raw)
    claimed: dict = {}
    last_idx: dict = {}
    shared_msg = ""
    if sh.enabled:
        ok_dir, shared_msg = sh.check()
        if not ok_dir:
            return {"picked": [], "target": 0, "pool_ids": [], "shared_msg": shared_msg, "shared_ok": False}
        claimed = sh.claimed_today()
        last_idx = sh.last_day_index()

    # Bỏ qua profile đang "nghỉ" do cảnh báo bảo mật trước đó (ở máy này HOẶC máy khác gắn cờ).
    cooldown_h = float(cfg.get("safety.cooldown_hours", 48))
    pool = [p for p in profiles if not store.is_in_cooldown(p.id, cooldown_h)
            and not (sh.enabled and sh.cooldown(p.id, cooldown_h)[0] > 0)]

    # Tỉ lệ nuôi/ngày tính theo CẢ NGÀY (không cộng dồn khi chạy nhiều lần/ngày):
    # mục tiêu = tổng mail đủ điều kiện × tỉ lệ; trừ số mail đã nuôi hôm nay (mọi máy) -> chọn phần còn thiếu.
    max_per_day = int(cfg.get("safety.max_sessions_per_profile_per_day", 1) or 1)
    done_today = [p for p in pool if store.sessions_today(p.id) >= 1 or p.id in claimed]
    remaining = [p for p in pool if store.sessions_today(p.id) < max_per_day and p.id not in claimed]
    frac = float(cfg.get("run.daily_fraction", 1.0) or 1.0)
    if not pool:
        target = 0
    elif 0 < frac < 1:
        target = max(1, int(round(len(pool) * frac)))
    else:
        target = len(pool)
    quota = max(0, target - len(done_today))
    # LUÂN PHIÊN công bằng: ưu tiên mail lâu nhất chưa được nuôi (chưa nuôi lần nào lên trước);
    # cùng ngày thì bốc thăm. Rồi xáo thứ tự chạy cho tự nhiên.
    remaining.sort(key=lambda p: (max(store.last_nurtured_day(p.id), last_idx.get(p.id, "")), random.random()))
    picked = remaining[:quota]
    random.shuffle(picked)
    return {"picked": picked, "target": target, "pool_ids": [p.id for p in pool],
            "shared_msg": shared_msg, "shared_ok": True}


def in_active_hours(cfg: Config, now: datetime | None = None) -> tuple[bool, int, int]:
    try:
        lo, hi = [int(x) for x in (cfg.get("run.active_hours", [0, 24]) or [0, 24])][:2]
    except Exception:
        lo, hi = 0, 24
    h = (now or datetime.now()).hour
    return lo <= h < hi, lo, hi


def _worker(cfg: Config, profile: Profile) -> tuple[bool, str]:
    # Mỗi luồng có GPM client + Store riêng (kết nối SQLite không dùng chung giữa luồng).
    gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"))
    store = Store()
    try:
        return run_session(cfg, gpm, store, profile.id, profile.name,
                           group_name=profile.group_name)
    finally:
        store.close()


def run_daily(cfg: Config, dry_run: bool = False) -> None:
    """Chạy một đợt nuôi cho hôm nay."""
    # Kiểm tra khung giờ hoạt động.
    ok_hours, lo, hi = in_active_hours(cfg)
    if not ok_hours:
        _log(f"Ngoài khung giờ hoạt động ({lo}h-{hi}h), bỏ qua đợt này.")
        return

    gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"))
    store = Store()
    try:
        plan = plan_today(cfg, gpm, store)
    finally:
        store.close()
    profiles = plan["picked"]
    if not plan.get("shared_ok", True):
        _log(f"⛔ {plan.get('shared_msg')} Không nuôi để tránh 2 máy trùng mail.")
        return

    if not profiles:
        _log("Không có profile nào cần nuôi hôm nay.")
        return

    _log(f"Chọn {len(profiles)} profile để nuôi.")
    if dry_run:
        for p in profiles:
            _log(f"  (dry-run) sẽ nuôi: {p.name or p.id}")
        return

    concurrency = int(cfg.get("run.concurrency", 2))
    stagger = cfg.get("run.stagger_seconds", [10, 40])
    max_err = int(cfg.get("safety.max_consecutive_errors", 5))
    err_streak = 0
    stop = threading.Event()

    def guarded(idx: int, p: Profile) -> tuple[bool, str]:
        if stop.is_set():
            return False, f"[BỎ QUA] {p.name or p.id} (đã dừng khẩn)"
        # So le thời điểm bắt đầu cho tự nhiên (mail đầu chạy ngay). Dừng khẩn -> thôi chờ ngay.
        if idx > 0 and stop.wait(random.uniform(stagger[0], stagger[1])):
            return False, f"[BỎ QUA] {p.name or p.id} (đã dừng khẩn)"
        # Mỗi mail mất 10–15 phút -> hết giờ hoạt động giữa đợt thì dừng, không mở mail mới.
        if not in_active_hours(cfg)[0]:
            return True, f"[BỎ QUA] {p.name or p.id} (đã hết giờ hoạt động, để mai)"
        # Chạy nhiều máy: các máy cộng lại đã đủ tỉ lệ hôm nay -> thôi.
        full, n = quota_reached(cfg, plan)
        if full:
            return True, f"[BỎ QUA] {p.name or p.id} (các máy đã nuôi đủ {n} mail hôm nay)"
        return _worker(cfg, p)

    ok_count = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = {ex.submit(guarded, i, p): p for i, p in enumerate(profiles)}
        for fut in as_completed(futures):
            success, summary = fut.result()
            _log(summary)
            if success:
                ok_count += 1
                err_streak = 0
            else:
                err_streak += 1
                if err_streak >= max_err and not stop.is_set():
                    stop.set()
                    msg = (f"{err_streak} mail lỗi/bị chặn liên tiếp — đã dừng, không mở thêm mail nào. "
                           f"Kiểm tra GPM, mạng, đăng nhập rồi mới chạy lại.")
                    _log(f"⚠ DỪNG KHẨN: {msg}")
                    try:
                        st = Store()
                        try:
                            st.add_event("emergency_stop", msg, "error")
                        finally:
                            st.close()
                    except Exception:
                        pass

    _log(f"Xong đợt: {ok_count}/{len(profiles)} phiên thành công.")
