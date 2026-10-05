"""Chạy MỘT phiên nuôi cho MỘT profile: mở GPM -> bám Playwright -> làm vài hành động -> đóng."""
from __future__ import annotations

import random
import time
import traceback
from datetime import date

from .actions.base import ACTIONS, ActionContext
from .actions.youtube import ads_text as yt_ads_text, watch_more as yt_watch_more
from .browser import connect
from .config import Config, load_config
from .content import ContentProvider
from .gpm_client import GPMClient
from .humanize import Humanizer
from .personas import apply_override, build_persona
from .shared import Shared
from .store import Store
from . import groups, routine, safety, warmup

_ACT_PHRASE = {"youtube": "xem video YouTube", "news": "đọc báo", "gmail": "đọc email",
               "diary": "viết nhật ký", "visit": "ghé vài trang web quen"}

def _diary_estimate(cfg) -> float:
    """Thời gian (giây) bước viết nhật ký: AI viết + mở Docs + GÕ TỪNG PHÍM ~70–90 từ/phút."""
    try:
        min_words = int(cfg.get("diary.min_words", 500) or 0)
    except Exception:
        min_words = 500
    if min_words <= 0:
        return 120.0
    if str(cfg.get("diary.typing", "keys") or "keys") != "keys":
        return 150.0
    try:
        lo, hi = [float(x) for x in (cfg.get("diary.typing_wpm") or [70, 90])][:2]
    except Exception:
        lo, hi = 70.0, 90.0
    from .actions.diary import typing_seconds
    return 90.0 + typing_seconds(min_words + 70, (lo + hi) / 2)     # AI viết ~520–620 từ


def _session_minutes(cfg) -> tuple[float, float]:
    """Tổng thời lượng 1 phiên (phút), mặc định 15–20 (có gõ nhật ký từng phím)."""
    try:
        lo, hi = (cfg.get("session.total_minutes") or [15, 20])[:2]
        lo, hi = float(lo), float(hi)
        return (lo, hi) if 0 < lo <= hi else (15.0, 20.0)
    except Exception:
        return 15.0, 20.0


def _estimates(cfg) -> dict:
    try:
        lo, hi = (cfg.get("news.read_seconds") or [120, 180])[:2]
        news = (float(lo) + float(hi)) / 2 + 20
    except Exception:
        news = 170.0
    return {"news": news, "gmail": 60.0, "visit": 90.0, "youtube": 0.0}


def _flag(store, profile_id: str, reason: str, shared: Shared | None = None) -> None:
    """Gắn cờ nghỉ (cả ở máy này lẫn thư mục chung nếu chạy nhiều máy); lý do đăng xuất / trang đăng
    nhập thì ghi luôn trạng thái đăng nhập."""
    store.flag_profile(profile_id, reason)
    if safety.is_login_reason(reason):
        store.set_login_status(profile_id, False)
    if shared is not None and shared.enabled:
        try:
            shared.flag(profile_id, reason)
        except Exception:
            pass


def _summarize(done_actions) -> str:
    parts: list[str] = []
    for kind, _ in done_actions:
        ph = _ACT_PHRASE.get(kind, kind)
        if ph not in parts:
            parts.append(ph)
    return ", ".join(parts) if parts else "không làm gì đáng kể"


def run_session(cfg: Config, gpm: GPMClient, store: Store,
                profile_id: str, profile_name: str = "",
                force_actions: list[str] | None = None,
                on_log=None, group_name: str = "") -> tuple[bool, str]:
    """Trả (thành_công, mô_tả).

    force_actions: nếu truyền (vd ['youtube','gmail']), chạy đúng các hành động này theo thứ tự,
    mỗi cái 1 lần (dùng để test/tinh chỉnh). Nếu None -> chọn ngẫu nhiên theo trọng số như bình thường.
    on_log: hàm callback(str) để nhận từng dòng log ngay khi xảy ra (dùng cho giao diện app).
    group_name: tên nhóm của profile -> áp dụng cấu hình/persona riêng theo nhóm (nếu có).
    """
    def emit(line: str) -> None:
        if on_log:
            try:
                on_log(line)
            except Exception:
                pass

    name = profile_name or profile_id

    # 0a) AN TOÀN: đọc lại cấu hình MỚI NHẤT ngay trước khi mở — nhóm có thể vừa bị đổi sang Khóa
    #     giữa lúc đợt đang chạy. Không đọc được cấu hình / không xác định được nhóm -> KHÔNG mở.
    try:
        fresh = load_config().raw
    except Exception as e:  # noqa: BLE001
        msg = f"[DỪNG] {name} — không đọc được cấu hình ({e}); không mở cho an toàn."
        emit(f"⛔ {msg}")
        return False, msg
    if groups.is_locked(fresh, group_name):
        why = "nhóm đang KHÓA" if group_name else "không xác định được nhóm"
        msg = f"[BỎ QUA] {name} — {why}; không mở."
        emit(f"⛔ {msg}")
        return True, msg

    # 0b) Giới hạn số phiên/ngày cho MỌI đường chạy (trừ chế độ test có chỉ định hành động).
    if not force_actions:
        max_day = int((fresh.get("safety") or {}).get("max_sessions_per_profile_per_day", 1) or 1)
        done_today = store.sessions_today(profile_id)
        if done_today >= max_day:
            msg = f"[ĐỦ HÔM NAY] {name} — đã nuôi {done_today} phiên hôm nay (tối đa {max_day}/ngày), để mai."
            emit(f"✋ {msg}")
            return True, msg

    # Trộn cấu hình riêng của nhóm (đè lên config chung) trước khi chạy.
    persona_override = None
    if group_name:
        eff = groups.effective_cfg(cfg.raw, group_name)
        persona_override = eff.pop("_persona_override", None)
        cfg = Config(eff)

    # 0) An toàn: nếu profile đang trong thời gian "nghỉ" do cảnh báo trước đó -> bỏ qua, KHÔNG mở.
    cooldown_h = float(cfg.get("safety.cooldown_hours", 48))
    remain = store.cooldown_remaining_hours(profile_id, cooldown_h)
    if remain > 0:
        msg = f"[NGHỈ] {profile_name or profile_id} — đang cooldown còn ~{remain:.0f}h (lý do: {store.last_flag_reason(profile_id)})"
        emit(f"😴 {msg}")
        return True, msg

    # 0c) CHẠY NHIỀU MÁY (cùng bộ profile GPM): kiểm tra "sổ trực" ở thư mục chung trên mạng LAN.
    shared = Shared(fresh)
    claimed = False
    if shared.enabled:
        ok_dir, why_dir = shared.check()
        if not ok_dir:
            msg = f"[DỪNG] {name} — {why_dir} Không nuôi để tránh 2 máy trùng mail."
            emit(f"⛔ {msg}")
            return False, msg
        rem2, why2, who2 = shared.cooldown(profile_id, cooldown_h)
        if rem2 > 0:
            msg = f"[NGHỈ] {name} — máy “{who2}” đã gắn cờ, còn nghỉ ~{rem2:.0f}h (lý do: {why2})"
            emit(f"😴 {msg}")
            return True, msg
        if force_actions:
            info = shared.claim_info(profile_id) or {}
            if info.get("status") == "running" and info.get("machine") != shared.machine:
                msg = f"[BỎ QUA] {name} — máy “{info.get('machine')}” đang nuôi mail này, không mở cùng lúc."
                emit(f"✋ {msg}")
                return True, msg
        else:
            claimed, info = shared.claim(profile_id, name)
            if not claimed:
                who, st_ = (info or {}).get("machine", "?"), (info or {}).get("status", "")
                msg = (f"[ĐỦ HÔM NAY] {name} — máy “{who}” "
                       f"{'đang nuôi' if st_ == 'running' else 'đã nuôi'} mail này hôm nay, máy này bỏ qua.")
                emit(f"✋ {msg}")
                return True, msg

    session_id = store.start_session(profile_id, profile_name)
    started = None
    start_attempted = False
    opened = False              # đã mở + bám được trình duyệt chưa (để phân biệt lỗi MỞ profile)
    rnd = random.Random()
    log_lines: list[str] = []
    # Quỹ thời gian CẢ PHIÊN (từ lúc mở profile tới lúc đóng): mặc định 10–15 phút.
    t_start = time.time()
    tmin, tmax = _session_minutes(cfg)
    target_end = t_start + rnd.uniform(tmin * 60, tmax * 60)
    min_end = t_start + tmin * 60

    try:
        # 1) Mở profile qua GPM.
        start_attempted = True
        started = gpm.start_profile(
            profile_id,
            win_scale=cfg.get("gpm.win_scale", 0.8),
            wait_timeout=cfg.get("gpm.start_timeout_sec", 60),
        )
        time.sleep(rnd.uniform(1.5, 3.5))

        # 2) Bám Playwright vào và dựng bối cảnh.
        with connect(started.cdp_url) as sess:
            opened = True
            store.clear_open_error(profile_id)      # mở được rồi -> gỡ trạng thái "Lỗi mở"
            # Tự thu nhỏ cửa sổ GPM để chạy ngầm (không nhảy lên màn hình). Điều khiển vẫn chạy.
            if cfg.get("run.minimize_windows", True):
                sess.minimize()
            persona = apply_override(build_persona(profile_id, profile_name), persona_override)
            ctx = ActionContext(
                page=sess.page,
                human=Humanizer(cfg.get("humanize", {})),
                persona=persona,
                content=ContentProvider(cfg.get("content", {})),
                rnd=rnd,
                cfg=cfg.raw,
                store=store,
                profile_id=profile_id,
                session_end=target_end,
                shared=shared if shared.enabled else None,
            )

            # Kiểm tra CÒN ĐĂNG NHẬP Google không (chỉ đọc cookie, không mở trang).
            # Mail đã bị đăng xuất -> nuôi cũng vô ích và dễ vấp trang đăng nhập -> bỏ qua ngay.
            logged_in = safety.google_logged_in(sess.context)
            if logged_in is not None:
                store.set_login_status(profile_id, logged_in)
            if logged_in is False:
                reason = safety.NEED_LOGIN_REASON
                _flag(store, profile_id, reason, shared)
                store.end_session(session_id, "skip", reason)
                if claimed:
                    shared.finish(profile_id, "skip")
                store.add_event("login_out", reason, "warn", profile_id, name)
                msg = f"[CẦN ĐĂNG NHẬP] {profile_name or profile_id} — mail đã bị đăng xuất khỏi Google. " \
                      f"Không thao tác. Hãy đăng nhập lại trong GPM."
                emit(f"🔑 {msg}")
                return True, msg

            # An toàn: nếu ngay từ đầu tab đã ở trang bảo mật/đăng nhập -> dừng, gắn cờ, nghỉ.
            risk0 = safety.detect_risk(sess.page)
            if risk0:
                _flag(store, profile_id, risk0, shared)
                store.end_session(session_id, "flagged", risk0)
                if claimed:
                    shared.finish(profile_id, "flagged")
                msg = f"[CẢNH BÁO] {profile_name or profile_id} — {risk0}. Đã gắn cờ, cho nghỉ {cooldown_h:.0f}h, KHÔNG thao tác."
                emit(f"🛑 {msg}")
                # Trả False: nhiều mail liền gặp trang bảo mật -> đợt chạy tính vào chuỗi lỗi và dừng khẩn.
                return False, msg

            # 3) Chọn số hành động + TRÌNH TỰ (mỗi ngày mỗi khác).
            all_weights = dict(cfg.get("action_weights", {"youtube": 1}) or {"youtube": 1})
            # Chưa khai báo website chỉ định -> không lên lịch việc "ghé website" (tránh báo thành công giả).
            if not [s for s in ((cfg.get("custom") or {}).get("sites") or []) if str(s).strip()]:
                all_weights.pop("visit", None)
            stage = ""
            routine_sig = ""
            if force_actions:
                plan = list(force_actions)
            else:
                if cfg.get("warmup.enabled", True):
                    total_ok = store.total_ok_sessions(profile_id)
                    wp = warmup.plan_for(total_ok, cfg.raw, rnd)
                    stage = f" [{wp.stage}, phiên #{total_ok + 1}]"
                    allowed = [a for a in wp.allowed if a in all_weights] or ["news"]
                    n_plan = wp.n_actions
                else:
                    allowed = [a for a, w in all_weights.items() if float(w) > 0] or ["youtube"]
                    n_plan = rnd.randint(2, 3)
                # Đọc lại nhật ký hôm trước -> chọn trình tự KHÁC đi (tự học chống lặp).
                # YouTube LUÔN có trong mọi phiên (xem 2–4 video), thêm 1–2 việc khác.
                recent = store.recent_routines(profile_id, days=7)
                plan, routine_sig = routine.plan_routine(allowed, n_plan, all_weights, recent, rnd,
                                                         must=("youtube",))
                lastj = store.last_journal(profile_id)
                if lastj and lastj.get("routine"):
                    emit(f"📖 [{profile_name or profile_id}] hôm qua ({lastj['day']}) làm [{lastj['routine']}] "
                         f"→ hôm nay đổi thành [{routine_sig}]")
            n_actions = len(plan)

            # Chia quỹ thời gian: YouTube tự tính số video để CẢ PHIÊN vừa 10–15 phút.
            will_diary = (float(all_weights.get("diary", 0)) > 0
                          and not store.did_action_today(profile_id, "diary"))
            diary_est = _diary_estimate(cfg) if will_diary else 0
            est = _estimates(cfg)
            emit(f"▶ {profile_name or profile_id}{stage} — bắt đầu {n_actions} việc [{' > '.join(plan)}]"
                 f"{' + nhật ký' if will_diary else ''}, phiên ~{int((target_end - t_start) // 60)} phút")

            done_actions: list[tuple[str, str]] = []
            flagged = False
            flag_reason = ""

            def do_action(kind: str, fn) -> bool:
                """Chạy 1 việc + kiểm tra an toàn sau đó. Trả True nếu Google chặn (phải dừng phiên)."""
                nonlocal flagged, flag_reason
                try:
                    detail = fn()
                    store.log_action(session_id, profile_id, kind, detail, ok=True)
                    log_lines.append(f"  ✓ {kind}: {detail}")
                    done_actions.append((kind, detail))
                    emit(f"  ✓ [{profile_name or profile_id}] {kind}: {detail}")
                except Exception as e:
                    store.log_action(session_id, profile_id, kind, f"{type(e).__name__}: {e}", ok=False)
                    log_lines.append(f"  ✗ {kind}: {e}")
                    emit(f"  ✗ [{profile_name or profile_id}] {kind}: {e}")

                # An toàn: sau MỖI hành động, đánh giá rủi ro trang hiện tại.
                level, reason = safety.assess_risk(ctx.page)
                if level == "hard":
                    # Cảnh báo/xác minh/captcha TRÊN GOOGLE -> dừng, gắn cờ, cho mail nghỉ.
                    _flag(store, profile_id, reason, shared)
                    flagged = True
                    flag_reason = reason
                    log_lines.append(f"  🛑 rủi ro Google: {reason} -> dừng, nghỉ {cooldown_h:.0f}h")
                    emit(f"  🛑 [{profile_name or profile_id}] {reason} -> DỪNG + nghỉ {cooldown_h:.0f}h")
                    return True
                if level == "soft":
                    # Captcha trang ngoài (báo...) -> chỉ rời trang, KHÔNG phạt mail.
                    emit(f"  ⚠ [{profile_name or profile_id}] {reason} -> bỏ qua trang này")
                    try:
                        ctx.page.goto("about:blank", timeout=8000)
                    except Exception:
                        pass
                return False

            for i, kind in enumerate(plan):
                fn = ACTIONS.get(kind)
                if not fn:
                    continue
                ctx.reserve_after = sum(est.get(k, 60) for k in plan[i + 1:]) + diary_est
                # Không để phiên vượt quá xa 15 phút: việc phụ (không phải YouTube) hết giờ thì bỏ.
                if kind != "youtube" and not force_actions and time.time() + est.get(kind, 60) > target_end + 60:
                    emit(f"  – [{profile_name or profile_id}] bỏ {kind}: đã hết quỹ thời gian phiên")
                    continue
                if do_action(kind, lambda f=fn: f(ctx)):
                    break
                ctx.human.pause(2.0, 6.0)   # nghỉ giữa các hành động

            # Phiên kết thúc SỚM hơn 10 phút -> xem thêm video / lướt YouTube cho đủ (trước bước nhật ký).
            gap = min_end - time.time() - diary_est
            if (not flagged and not force_actions and gap > 45
                    and float(all_weights.get("youtube", 0)) > 0):
                do_action("youtube", lambda: "thêm cho đủ giờ — " + yt_watch_more(ctx, gap))

            # ===== CHỐT NGÀY: ghi nhật ký 2 NƠI =====
            day = date.today().isoformat()
            # tóm tắt việc tương tác (để nhật ký nhắc lại); không làm được gì thì không nhắc
            activities = _summarize(done_actions) if done_actions else ""
            # Nơi 1: Google Docs — 1 mục/ngày, nối vào doc 'Nhật ký' của mail, nhắc lại việc đã làm.
            if not flagged and float(all_weights.get("diary", 0)) > 0 \
                    and not store.did_action_today(profile_id, "diary"):
                ctx.day_summary = activities
                dfn = ACTIONS.get("diary")
                if dfn:
                    try:
                        detail = dfn(ctx)
                        if detail.startswith("hôm nay đã"):
                            emit(f"  – [{name}] diary: {detail}")
                        elif detail.startswith("bỏ qua"):
                            # Không viết được (vd thiếu AI) -> ghi là CHƯA làm, để lần sau còn thử lại.
                            store.log_action(session_id, profile_id, "diary", detail, ok=False)
                            emit(f"  – [{name}] diary: {detail}")
                        else:
                            store.log_action(session_id, profile_id, "diary", detail, ok=True)
                            done_actions.append(("diary", detail))
                            emit(f"  ✓ [{name}] diary: {detail}")
                    except Exception as e:
                        store.log_action(session_id, profile_id, "diary", f"{type(e).__name__}: {e}", ok=False)
                        emit(f"  ✗ [{profile_name or profile_id}] diary: {e}")
                    # Luôn kiểm tra rủi ro sau khi mở Docs (kể cả khi viết thất bại).
                    lvl, rs = safety.assess_risk(ctx.page)
                    if lvl == "hard":
                        _flag(store, profile_id, rs, shared)
                        flagged = True
                        flag_reason = rs
                        emit(f"  🛑 [{profile_name or profile_id}] {rs} -> DỪNG + nghỉ {cooldown_h:.0f}h")
            # Dòng đếm quảng cáo YouTube của CẢ PHIÊN (đã bấm "Bỏ qua" bao nhiêu lần).
            if any(k == "youtube" for k, _ in done_actions):
                ad_line = f"⏭ Quảng cáo YouTube cả phiên — {yt_ads_text(ctx.ads_seen, ctx.ads_skipped, ctx.ads_waited)}"
                log_lines.append(f"  {ad_line}")
                emit(f"  {ad_line.replace('⏭ ', '⏭ [' + name + '] ', 1)}")
            # Nơi 2: NHẬT KÝ CÔNG VIỆC của mail (bộ nhớ tự học) — luôn lưu.
            sig = routine_sig or ">".join(k for k, _ in done_actions if k != "diary") or "—"
            store.save_journal(profile_id, day, sig, _summarize(done_actions))

        # Phiên bị Google chặn giữa chừng -> 'flagged' (không tính "tuổi" mail, tính vào chuỗi lỗi).
        store.end_session(session_id, "flagged" if flagged else "ok", flag_reason if flagged else None)
        if claimed:
            try:
                shared.finish(profile_id, "flagged" if flagged else "ok")
            except Exception:
                pass
        mins = (time.time() - t_start) / 60
        emit(f"✔ {name} — xong ({len(done_actions)} việc, trình tự [{routine_sig or sig}], "
             f"cả phiên ~{mins:.1f} phút)"
             + (" — ⚠ dừng sớm do Google chặn, mail sẽ nghỉ" if flagged else ""))
        tag = "[BỊ CHẶN]" if flagged else "[OK]"
        summary = f"{tag} {name}{stage} — {len(done_actions)} việc\n" + "\n".join(log_lines)
        # Bị Google chặn -> trả False để đợt chạy tính vào chuỗi lỗi (nhiều mail bị chặn liền -> dừng khẩn).
        return (not flagged), summary

    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        store.end_session(session_id, "error", err)
        if not opened:
            # Hỏng ngay ở bước MỞ profile (GPM quá giờ, proxy chết...) -> hiện "Lỗi mở" trên app.
            try:
                store.set_open_error(profile_id, err)
                store.add_event("open_fail", err, "error", profile_id, name)
            except Exception:
                pass
        if claimed:
            try:
                # Chưa mở được profile -> nhả mail để máy khác thử; đã làm dở -> ghi 'error' (không nuôi lại).
                shared.release(profile_id) if not opened else shared.finish(profile_id, "error")
            except Exception:
                pass
        emit(f"✖ {profile_name or profile_id} — LỖI: {err}")
        return False, f"[LỖI] {profile_name or profile_id} — {err}\n{traceback.format_exc(limit=2)}"

    finally:
        # 4) Luôn đóng profile — KỂ CẢ khi lệnh mở bị lỗi/quá giờ (GPM có thể vẫn mở xong sau đó,
        #    không đóng thì trình duyệt bị bỏ lơ lửng).
        if start_attempted:
            try:
                gpm.close_profile(profile_id)
            except Exception:
                pass
