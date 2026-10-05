"""Nhiệm vụ KIỂM TRA ĐĂNG NHẬP cho các mail.

Lần lượt mở từng profile KHÔNG bị khóa, chỉ đọc cookie phiên Google (KHÔNG vào trang web nào,
không tạo hoạt động trên tài khoản), rồi đóng lại. Kết quả lưu vào store (bảng login_checks):
  - Đã đăng xuất  -> gắn cờ NEED_LOGIN_REASON (mail bị bỏ qua khi nuôi, hiện "Cần đăng nhập").
  - Còn đăng nhập -> nếu trước đó bị gắn cờ DO KIỂM TRA COOKIE thì tự gỡ.
    (Cờ do gặp trang đăng nhập thật thì KHÔNG tự gỡ: còn cookie chưa chắc phiên còn hiệu lực —
     người dùng tự xác nhận bằng nút "Tôi đã đăng nhập lại".)
Nhóm Khóa: tuyệt đối không mở.
"""
from __future__ import annotations

import time

from . import groups, safety
from .browser import connect
from .config import load_config
from .gpm_client import GPMClient
from .shared import Shared
from .store import Store


def check_logins(cfg, profiles: list[dict], on_log=None, stop_event=None,
                 active: set | None = None) -> dict:
    """profiles: [{id, name, group}]. Trả {profile_id: True/False/None}.
    active: (tuỳ chọn) tập id profile đang mở — app dùng để đóng hộ khi người dùng tắt app giữa chừng."""
    def emit(msg: str) -> None:
        if on_log:
            try:
                on_log(msg)
            except Exception:
                pass

    targets = [p for p in profiles if not groups.is_locked(cfg.raw, p.get("group", ""))]
    skipped = len(profiles) - len(targets)
    emit(f"=== Kiểm tra đăng nhập {len(targets)} mail"
         + (f" (bỏ qua {skipped} mail nhóm Khóa)" if skipped else "") + " — chỉ đọc cookie ===")
    gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"))
    cooldown_h = float(cfg.get("safety.cooldown_hours", 48))
    res: dict = {}
    store = Store()
    try:
        for i, p in enumerate(targets, 1):
            if stop_event is not None and stop_event.is_set():
                emit("⏹ Đã dừng kiểm tra đăng nhập.")
                break
            pid, name = p["id"], p.get("name") or p["id"]
            tag = f"[{i}/{len(targets)}] {name}"
            # Đọc lại cấu hình MỚI NHẤT trước mỗi mail: nhóm có thể vừa bị đổi sang Khóa.
            try:
                fresh = load_config().raw
            except Exception as e:  # noqa: BLE001
                emit(f"⛔ Không đọc được cấu hình ({e}) — dừng kiểm tra cho an toàn.")
                break
            if groups.is_locked(fresh, p.get("group", "")):
                emit(f"  ⛔ {tag}: nhóm '{p.get('group', '')}' đang KHÓA — không mở.")
                continue
            # Chạy nhiều máy: KHÔNG mở mail mà máy khác đang nuôi (tránh 2 máy mở cùng 1 profile).
            sh = Shared(fresh)
            if sh.enabled:
                ok_dir, why_dir = sh.check()
                if not ok_dir:
                    emit(f"⛔ {why_dir} — dừng kiểm tra để tránh mở trùng với máy khác.")
                    break
                cinfo = sh.claim_info(pid) or {}
                if cinfo.get("status") == "running" and cinfo.get("machine") != sh.machine:
                    emit(f"  ✋ {tag}: máy “{cinfo.get('machine')}” đang nuôi mail này — không mở cùng lúc.")
                    continue
            ok = None
            attempted = False
            opened = False
            try:
                attempted = True
                if active is not None:
                    active.add(pid)
                st = gpm.start_profile(pid, win_scale=0.6,
                                       wait_timeout=cfg.get("gpm.start_timeout_sec", 60))
                time.sleep(1.5)
                with connect(st.cdp_url) as sess:
                    opened = True
                    store.clear_open_error(pid)          # mở được -> gỡ "Lỗi mở"
                    sess.minimize()
                    ok = safety.google_logged_in(sess.context)
            except Exception as e:  # noqa: BLE001
                emit(f"  ✖ {tag}: không kiểm tra được ({e})")
                if not opened:
                    err = f"{type(e).__name__}: {e}"
                    store.set_open_error(pid, err)
                    store.add_event("open_fail", err, "error", pid, name)
            finally:
                if attempted:
                    try:
                        gpm.close_profile(pid)
                    except Exception:
                        pass
                if active is not None:
                    active.discard(pid)
            res[pid] = ok

            if ok is True:
                store.set_login_status(pid, True)
                shared_login_flag = sh.enabled and (sh.flag_info(pid) or {}).get("reason") == safety.NEED_LOGIN_REASON
                if ((store.last_flag_reason(pid) == safety.NEED_LOGIN_REASON
                        and store.is_in_cooldown(pid, cooldown_h)) or shared_login_flag):
                    store.clear_flags(pid)
                    if shared_login_flag:
                        sh.clear_flag(pid)          # gỡ luôn cho các máy khác
                    emit(f"  ✅ {tag}: đang đăng nhập (đã gỡ nhãn “Cần đăng nhập”)")
                else:
                    emit(f"  ✅ {tag}: đang đăng nhập")
            elif ok is False:
                store.set_login_status(pid, False)
                if not (store.last_flag_reason(pid) == safety.NEED_LOGIN_REASON
                        and store.is_in_cooldown(pid, cooldown_h)):
                    store.flag_profile(pid, safety.NEED_LOGIN_REASON)
                    store.add_event("login_out", safety.NEED_LOGIN_REASON, "warn", pid, name)
                if sh.enabled:
                    sh.flag(pid, safety.NEED_LOGIN_REASON)   # báo cho các máy khác: mail này cần đăng nhập
                emit(f"  🔑 {tag}: ĐÃ ĐĂNG XUẤT — cần đăng nhập lại trong GPM")
            time.sleep(1.0)
    finally:
        store.close()

    n_ok = sum(1 for v in res.values() if v is True)
    n_out = sum(1 for v in res.values() if v is False)
    n_err = sum(1 for v in res.values() if v is None)
    emit(f"=== Kết quả: {n_ok} đang đăng nhập · {n_out} đã đăng xuất"
         + (f" · {n_err} không kiểm tra được" if n_err else "") + " ===")
    return res
