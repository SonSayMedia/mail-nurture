"""Mail Nurture - CLI.

Ví dụ:
    python main.py check                 # kiểm tra kết nối GPM Login
    python main.py profiles              # liệt kê profile trong GPM
    python main.py run-once <profile_id> # nuôi thử 1 profile (để test)
    python main.py daily --dry-run       # xem hôm nay sẽ nuôi những profile nào
    python main.py daily                 # chạy một đợt nuôi
"""
from __future__ import annotations

import argparse
import sys
import time

# Ép stdout/stderr sang UTF-8 để không crash khi in tiếng Việt/emoji lúc chạy nền hoặc qua Task Scheduler.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

from src import activity_log
from src.config import load_config
from src.gpm_client import GPMClient, GPMError


def cmd_check(cfg) -> int:
    gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"))
    try:
        profiles = gpm.list_profiles(group_id=cfg.get("gpm.group_id", ""))
    except GPMError as e:
        print(f"❌ {e}")
        return 1
    print(f"✅ Kết nối GPM OK. Thấy {len(profiles)} profile.")
    return 0


def cmd_profiles(cfg) -> int:
    gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"))
    try:
        profiles = gpm.list_profiles(group_id=cfg.get("gpm.group_id", ""))
    except GPMError as e:
        print(f"❌ {e}")
        return 1
    if not profiles:
        print("Không có profile nào.")
        return 0
    print(f"{'ID':<38}  TÊN")
    print("-" * 70)
    for p in profiles:
        print(f"{p.id:<38}  {p.name}")
    return 0


def cmd_test_attach(cfg, profile_id: str) -> int:
    """Mở profile, bám Playwright, đọc thông tin trang rồi đóng. KHÔNG tác động tài khoản."""
    from src.browser import connect
    gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"))
    print(f"→ Mở profile {profile_id} qua GPM...")
    try:
        started = gpm.start_profile(
            profile_id,
            win_scale=cfg.get("gpm.win_scale", 0.8),
            wait_timeout=cfg.get("gpm.start_timeout_sec", 60),
        )
    except GPMError as e:
        print(f"❌ {e}")
        try:
            gpm.close_profile(profile_id)   # phòng GPM vẫn mở xong sau khi quá giờ
        except Exception:
            pass
        return 1
    print(f"   remote_debugging_address = {started.remote_debugging_address}")
    print(f"   driver_path = {started.driver_path or '(không có)'}")
    try:
        time.sleep(2)
        with connect(started.cdp_url) as sess:
            page = sess.page
            print(f"✅ Bám Playwright OK. Tab hiện tại: {page.url}")
            try:
                print(f"   Tiêu đề: {page.title()}")
            except Exception:
                pass
            print("→ Thử điều hướng tới google.com (chỉ đọc)...")
            page.goto("https://www.google.com", wait_until="domcontentloaded", timeout=30000)
            time.sleep(2)
            print(f"✅ Điều khiển được trình duyệt. URL: {page.url} | Tiêu đề: {page.title()}")
        print("✅ TEST ATTACH THÀNH CÔNG — mắt xích GPM + Playwright chạy tốt.")
        return 0
    except Exception as e:
        print(f"❌ Lỗi khi bám/điều khiển: {type(e).__name__}: {e}")
        return 1
    finally:
        print(f"→ Đóng profile {profile_id}...")
        try:
            gpm.close_profile(profile_id)
        except Exception:
            pass


def cmd_check_login(cfg, ids: list[str]) -> int:
    """Nhiệm vụ kiểm tra đăng nhập (chỉ đọc cookie). Không truyền id = mọi mail không bị khóa."""
    from src import groups, login_check
    gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"))
    try:
        gmap = groups.group_map(gpm)
        profs = gpm.list_profiles()
    except GPMError as e:
        print(f"❌ {e}")
        return 1
    groups.annotate_profiles(profs, gmap)
    if ids:
        profs = [p for p in profs if p.id in set(ids)]
    rows = [{"id": p.id, "name": p.name, "group": p.group_name} for p in profs]

    def out(m: str) -> None:
        print(m)
        activity_log.write(m)            # app xem lại được ở trang "Nhật ký & Lỗi"

    login_check.check_logins(cfg, rows, on_log=out)
    return 0


def cmd_run_once(cfg, profile_id: str, action: str | None = None) -> int:
    from src.runner import run_session
    from src.store import Store
    from src import groups
    force = [a.strip() for a in action.split(",") if a.strip()] if action else None
    gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"))

    # Tìm tên + nhóm của profile; CHẶN nếu nhóm đang bị khóa hoặc không xác định được nhóm.
    found = None
    try:
        gmap = groups.group_map(gpm)
        found = next((p for p in gpm.list_profiles() if p.id == str(profile_id)), None)
    except GPMError as e:
        print(f"❌ {e}")
        return 1
    if found is None:
        print(f"❌ Không tìm thấy profile {profile_id} trong GPM.")
        return 1
    name = found.name
    group_name = gmap.get(str(found.group_id), "")
    if not group_name:
        print(f"⛔ Không xác định được nhóm của profile {profile_id} — không nuôi cho an toàn.")
        return 1
    if groups.is_locked(cfg.raw, group_name):
        print(f"⛔ Profile {profile_id} thuộc nhóm '{group_name}' đang KHÓA — không nuôi.")
        return 1

    store = Store()
    try:
        ok, summary = run_session(cfg, gpm, store, profile_id, name,
                                  force_actions=force, group_name=group_name)
    finally:
        store.close()
    print(summary)
    activity_log.write(summary)
    return 0 if ok else 1


def cmd_daily(cfg, dry_run: bool) -> int:
    from src.scheduler import run_daily
    from src.store import Store
    s = Store()
    try:
        s.cleanup_stale_running()        # phiên kẹt do lần trước bị ngắt
    finally:
        s.close()
    try:
        run_daily(cfg, dry_run=dry_run)
    except GPMError as e:
        print(f"❌ {e}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mail Nurture - nuôi tài khoản qua GPM Login")
    parser.add_argument("--config", help="đường dẫn config.yaml", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="kiểm tra kết nối GPM")
    sub.add_parser("profiles", help="liệt kê profile")
    cl = sub.add_parser("check-login", help="kiểm tra đăng nhập các mail (chỉ đọc cookie)")
    cl.add_argument("ids", nargs="*", help="id profile cần kiểm tra (bỏ trống = mọi mail không bị khóa)")
    t = sub.add_parser("test-attach", help="test mở + bám profile (không tác động tài khoản)")
    t.add_argument("profile_id")
    r = sub.add_parser("run-once", help="nuôi thử 1 profile")
    r.add_argument("profile_id")
    r.add_argument("--action", default=None,
                   help="chỉ chạy hành động chỉ định, cách nhau bởi dấu phẩy "
                        "(vd: youtube  hoặc  youtube,news). Bỏ trống = random theo trọng số.")
    d = sub.add_parser("daily", help="chạy một đợt nuôi hôm nay")
    d.add_argument("--dry-run", action="store_true", help="chỉ xem sẽ nuôi profile nào")

    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except FileNotFoundError as e:
        print(f"❌ {e}")
        return 1

    if args.cmd == "check":
        return cmd_check(cfg)
    if args.cmd == "profiles":
        return cmd_profiles(cfg)
    if args.cmd == "check-login":
        return cmd_check_login(cfg, args.ids)
    if args.cmd == "test-attach":
        return cmd_test_attach(cfg, args.profile_id)
    if args.cmd == "run-once":
        return cmd_run_once(cfg, args.profile_id, args.action)
    if args.cmd == "daily":
        return cmd_daily(cfg, args.dry_run)
    return 1


if __name__ == "__main__":
    sys.exit(main())
