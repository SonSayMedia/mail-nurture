"""Mail Nurture - App desktop (CustomTkinter, giao diện tối, thân thiện người dùng).

Vận hành: tự kết nối GPM + tải danh sách mail khi mở; chọn mail bằng ô tích; tìm/lọc; nuôi ngay
hoặc chạy đợt nhẹ; theo dõi log. Tab Nhóm: khóa/nuôi (tự lưu) + tuỳ chỉnh riêng từng nhóm.
Tab Cấu hình: thanh trượt/công tắc dễ hiểu thay cho số thô.

Chạy: bấm đúp icon "Mail Nurture" trên Desktop hoặc file "MAIL NURTURE APP.bat".
"""
from __future__ import annotations

import os
import queue
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from tkinter import messagebox

import customtkinter as ctk
import yaml

from src import activity_log, conn_check, groups, login_check, markets, monitor, safety, warmup
from src.config import ROOT, load_config
from src.gpm_client import GPMClient, GPMError
from src.personas import INTEREST_POOLS
from src.runner import run_session
from src.shared import Shared
from src.store import Store

CONFIG_PATH = ROOT / "config.yaml"
EXAMPLE_PATH = ROOT / "config.example.yaml"
ICON_PATH = ROOT / "assets" / "app_icon.ico"

# Bảng màu (hệ thống nhất quán, tương phản dễ đọc)
BG_MAIN = "#15171C"      # nền trang
BG_SIDE = "#0F1116"      # sidebar / ô nhập
CARD = "#1E222A"         # thẻ/card
CARD_HI = "#262B34"      # hover
LOGBG = "#0E1013"        # nền log console
TXT = "#E8EAED"          # chữ chính
MUTED = "#9AA0A8"        # chữ phụ
FAINT = "#6B7178"        # chữ mờ
BORDER = "#2C313A"       # viền
ACCENT = "#22A06B"       # xanh chủ đạo (nút chính)
ACCENT_HOVER = "#1B8557"
GREEN = "#37C871"        # Nuôi
RED = "#F0655F"          # Khóa
SLATE = "#7C838D"        # Để sau
BLUE = "#5AA0EE"         # thông tin
AMBER = "#E0A438"        # cảnh báo/nghỉ
SEL_BG = "#20362B"       # nền dòng được chọn
PILL_BG = {"nurture": "#1F3A2B", "locked": "#3A2422", "later": "#2B3038", "rest": "#39301C",
           "login": "#3A2422"}
PILL_FG = {"nurture": GREEN, "locked": RED, "later": "#AEB4BC", "rest": AMBER, "login": RED}
PILL_TXT = {"nurture": "Nuôi", "locked": "Khóa", "later": "Để sau", "rest": "Nghỉ",
            "login": "Cần đăng nhập"}

STAGE_VI = {"seed": "Mới", "warming": "Đang ấm", "active": "Ổn định", "-": "—"}
ACT_VI = {"youtube": "Xem YouTube", "news": "Đọc báo", "gmail": "Đọc Gmail", "diary": "Viết nhật ký"}
PROVIDERS = [("template", "Mẫu có sẵn (không cần AI)"), ("9router", "9router (AI) — khuyên dùng"),
             ("gemini", "Google Gemini (AI)"), ("claude", "Claude (AI)")]

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")     # checkbox/công tắc/thanh trượt đồng bộ màu xanh lá


def level_word(v) -> str:
    v = int(float(v))
    if v <= 0:
        return "Tắt"
    return "Ít" if v < 20 else ("Vừa" if v < 45 else "Nhiều")


# ----------------------------------------------------------------------------
# Engine: chạy phiên ở luồng nền, đẩy log qua queue cho giao diện.
# ----------------------------------------------------------------------------
class Engine:
    def __init__(self):
        self.log_q: "queue.Queue[str]" = queue.Queue()
        self.stop_event = threading.Event()
        self.running = False
        self.job = ""            # "nurture" | "login" — việc đang chạy
        self.active_ids: set[str] = set()   # profile GPM đang mở (để đóng hộ khi tắt app giữa chừng)
        # Kết quả việc vừa xong (để app hiện thông báo tổng kết đúng màu): {"kind": ok|warn|err, "text"}
        self.last_result: dict = {}

    def log(self, msg: str) -> None:
        self.log_q.put(msg)

    def _cfg(self):
        return load_config()

    def check_gpm(self):
        cfg = self._cfg()
        gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"))
        return gpm.list_profiles(group_id=cfg.get("gpm.group_id", ""))

    def list_profiles_with_stage(self):
        cfg = self._cfg()
        gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"))
        gmap = groups.group_map(gpm)
        profiles = gpm.list_profiles(group_id=cfg.get("gpm.group_id", ""))
        groups.annotate_profiles(profiles, gmap)
        store = Store()
        rnd = random.Random()
        rows = []
        cooldown_h = float(cfg.get("safety.cooldown_hours", 48))
        # Chạy nhiều máy: gộp thêm việc máy khác đã làm hôm nay + cờ cảnh báo dùng chung.
        sh = Shared(cfg.raw)
        claimed = sh.claimed_today() if sh.enabled and sh.check()[0] else {}
        try:
            open_errs = store.open_errors()
            for p in profiles:
                total = store.total_ok_sessions(p.id)
                today = store.sessions_today(p.id)
                cd = store.cooldown_remaining_hours(p.id, cooldown_h)
                flag = store.last_flag_reason(p.id) if cd > 0 else ""
                other = ""
                if sh.enabled:
                    rem2, why2, who2 = sh.cooldown(p.id, cooldown_h)
                    if rem2 > cd:
                        cd, flag = rem2, why2 + (f" (máy {who2})" if who2 and who2 != sh.machine else "")
                    info = claimed.get(p.id)
                    if info and info.get("machine") != sh.machine:
                        today = max(today, 1)
                        other = info.get("machine", "")
                stage = warmup.plan_for(total, cfg.raw, rnd).stage if cfg.get("warmup.enabled", True) else "-"
                rows.append({"id": p.id, "name": p.name, "group": p.group_name,
                             "raw_mode": groups.mode_for(cfg.raw, p.group_name),
                             "stage": stage, "today": today, "total": total, "cooldown": cd,
                             "flag": flag, "other_machine": other,
                             "login": store.login_status(p.id),
                             "open_err": open_errs.get(p.id)})
        finally:
            store.close()
        return rows

    def list_groups_with_mode(self):
        cfg = self._cfg()
        gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"))
        counts: dict[str, int] = {}
        try:
            for p in gpm.list_profiles():
                counts[str(p.group_id)] = counts.get(str(p.group_id), 0) + 1
        except Exception:
            pass
        out = []
        for g in gpm.list_groups():
            out.append({"id": g.id, "name": g.name, "mode": groups.mode_for(cfg.raw, g.name),
                        "count": counts.get(str(g.id), 0),
                        "market": markets.get(groups.market_for(cfg.raw, g.name))["name"]})
        return out

    def run_profiles(self, profiles: list[dict], force_actions=None, respect_hours: bool = False,
                     plan: dict | None = None):
        """respect_hours=True (Chạy đợt nhẹ): hết giờ hoạt động giữa đợt thì không mở thêm mail.
        plan (Chạy đợt nhẹ): chạy nhiều máy thì dừng khi các máy cộng lại đã đủ tỉ lệ hôm nay."""
        if self.running:
            self.log("⚠ Đang có việc chạy, bỏ qua.")
            return
        self.running = True
        self.job = "nurture"
        self.stop_event.clear()
        self._respect_hours = respect_hours
        self._plan = plan
        threading.Thread(target=self._worker, args=(profiles, force_actions), daemon=True).start()

    def run_login_check(self, profiles: list[dict]):
        """Chạy nền nhiệm vụ kiểm tra đăng nhập (chỉ đọc cookie)."""
        if self.running:
            self.log("⚠ Đang có việc chạy, bỏ qua.")
            return
        self.running = True
        self.job = "login"
        self.stop_event.clear()
        threading.Thread(target=self._login_worker, args=(profiles,), daemon=True).start()

    def _login_worker(self, profiles: list[dict]):
        self.last_result = {}
        try:
            res = login_check.check_logins(self._cfg(), profiles, on_log=self.log,
                                           stop_event=self.stop_event, active=self.active_ids)
            n_ok = sum(1 for v in res.values() if v is True)
            n_out = sum(1 for v in res.values() if v is False)
            n_err = sum(1 for v in res.values() if v is None)
            parts = [f"{n_ok} còn đăng nhập"]
            if n_out:
                parts.append(f"{n_out} đã đăng xuất")
            if n_err:
                parts.append(f"{n_err} không mở được")
            self.last_result = {"kind": "warn" if (n_out or n_err) else "ok",
                                "text": "Kiểm tra đăng nhập xong: " + " · ".join(parts)}
        except Exception as e:  # noqa: BLE001
            self.log(f"✖ Lỗi khi kiểm tra đăng nhập: {e}")
            self.last_result = {"kind": "err", "text": f"Kiểm tra đăng nhập bị lỗi: {e}"}
        finally:
            self.running = False

    def close_active_profiles(self, timeout: float = 15.0) -> None:
        """Đóng các profile GPM đang mở (dùng khi tắt app giữa lúc đang chạy)."""
        ids = list(self.active_ids)
        if not ids:
            return

        def closer():
            try:
                cfg = self._cfg()
                gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"), timeout=8)
            except Exception:
                return
            for pid in ids:
                try:
                    gpm.close_profile(pid)
                except Exception:
                    pass

        t = threading.Thread(target=closer, daemon=True)
        t.start()
        t.join(timeout=timeout)

    def stop(self):
        if self.running:
            self.stop_event.set()
            self.log("⏹ Sẽ dừng sau khi các phiên đang chạy hoàn tất (không mở profile mới).")

    @staticmethod
    def _tally_key(summary: str) -> str:
        """Phân loại kết quả 1 mail theo nhãn đầu dòng mà run_session trả về."""
        s = summary or ""
        if s.startswith("[OK]"):
            return "ok"
        if s.startswith(("[BỊ CHẶN]", "[CẢNH BÁO]")):
            return "blocked"
        if s.startswith("[CẦN ĐĂNG NHẬP]"):
            return "login"
        if s.startswith(("[LỖI]", "[DỪNG]")):
            return "error"
        return "skip"                        # ĐỦ HÔM NAY / NGHỈ / BỎ QUA

    def _worker(self, profiles: list[dict], force_actions):
        lock = threading.Lock()
        state = {"streak": 0, "emergency": False}
        tally = {"ok": 0, "blocked": 0, "login": 0, "error": 0, "skip": 0}
        self.last_result = {}
        try:
            cfg = self._cfg()
            conc = max(1, int(cfg.get("run.concurrency", 1) or 1))
            stagger = cfg.get("run.stagger_seconds", [30, 90]) or [30, 90]
            max_err = max(1, int(cfg.get("safety.max_consecutive_errors", 5) or 5))
            self.log(f"=== Bắt đầu nuôi {len(profiles)} mail (song song {conc}) ===")

            def work(item):
                idx, pf = item
                if self.stop_event.is_set():
                    return
                if groups.is_locked(cfg.raw, pf.get("group", "")):
                    self.log(f"⛔ Bỏ qua {pf['name'] or pf['id']} — nhóm '{pf.get('group')}' đang KHÓA.")
                    return
                # Mail đầu tiên chạy ngay; các mail sau mới giãn cách cho tự nhiên.
                if idx > 0 and not force_actions:
                    wait = random.uniform(stagger[0], stagger[1])
                    self.log(f"… chờ {wait:.0f}s rồi mới mở mail tiếp theo (giãn cách cho tự nhiên)")
                    # Chờ theo từng nhịp ngắn để bấm Dừng / đóng app có hiệu lực ngay.
                    if self.stop_event.wait(wait):
                        return
                if self.stop_event.is_set():
                    return
                if getattr(self, "_respect_hours", False):
                    from src.scheduler import in_active_hours
                    ok_h, lo, hi = in_active_hours(self._cfg())
                    if not ok_h:
                        if not self.stop_event.is_set():
                            self.stop_event.set()
                            self.log(f"⏰ Đã hết giờ hoạt động ({lo}h–{hi}h) — không mở thêm mail, "
                                     f"các mail còn lại để mai.")
                        return
                if getattr(self, "_plan", None):
                    from src.scheduler import quota_reached
                    full, n_done = quota_reached(cfg, self._plan)
                    if full:
                        if not self.stop_event.is_set():
                            self.stop_event.set()
                            self.log(f"✋ Các máy cộng lại đã nuôi đủ {n_done} mail hôm nay — máy này dừng đợt.")
                        return
                gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"))
                store = Store()
                self.active_ids.add(pf["id"])
                try:
                    ok, summary = run_session(cfg, gpm, store, pf["id"], pf["name"],
                                              force_actions=force_actions, on_log=self.log,
                                              group_name=pf.get("group", ""))
                except Exception as e:  # noqa: BLE001
                    ok, summary = False, f"[LỖI] {e}"
                    self.log(f"✖ {pf['name'] or pf['id']} — LỖI: {e}")
                finally:
                    self.active_ids.discard(pf["id"])
                    store.close()
                # Chuỗi lỗi / bị chặn liên tiếp -> DỪNG KHẨN (GPM/mạng hỏng hoặc Google đang siết).
                with lock:
                    tally[self._tally_key(summary)] += 1
                    state["streak"] = 0 if ok else state["streak"] + 1
                    if state["streak"] >= max_err and not self.stop_event.is_set():
                        self.stop_event.set()
                        state["emergency"] = True
                        msg = (f"{state['streak']} mail lỗi/bị chặn liên tiếp — đã dừng, không mở thêm mail nào. "
                               f"Kiểm tra GPM, mạng, đăng nhập rồi mới chạy lại.")
                        self.log(f"⚠ DỪNG KHẨN: {msg}")
                        try:
                            st = Store()
                            try:
                                st.add_event("emergency_stop", msg, "error")
                            finally:
                                st.close()
                        except Exception:
                            pass

            with ThreadPoolExecutor(max_workers=conc) as ex:
                list(ex.map(work, enumerate(profiles)))
        except Exception as e:  # noqa: BLE001
            self.log(f"✖ Không chạy được đợt nuôi: {e}")
            state["emergency"] = True
            self.last_result = {"kind": "err", "text": f"Không chạy được đợt nuôi: {e}"}
        finally:
            labels = [("ok", "xong"), ("blocked", "bị Google chặn"), ("login", "cần đăng nhập"),
                      ("error", "lỗi"), ("skip", "bỏ qua")]
            parts = [f"{tally[k]} {t}" for k, t in labels if tally[k]] or ["không mail nào chạy"]
            text = "Kết thúc đợt: " + " · ".join(parts)
            if state["emergency"] and not self.last_result:
                text = "DỪNG KHẨN — " + text
            if not self.last_result:
                bad = tally["blocked"] or tally["login"] or tally["error"]
                self.last_result = {"kind": "err" if state["emergency"] else ("warn" if bad else "ok"),
                                    "text": text}
            self.log(f"=== {text} ===")
            self.running = False


# ----------------------------------------------------------------------------
# Giao diện
# ----------------------------------------------------------------------------
class App(ctk.CTk):
    SEG_LABELS = {"nurture": "Nuôi", "locked": "Khóa", "later": "Để sau"}
    SEG2MODE = {v: k for k, v in SEG_LABELS.items()}
    MODE_HEX = {"Nuôi": GREEN, "Khóa": RED, "Để sau": SLATE}
    # Cột bảng: (tiêu đề, bề rộng px, canh lề)
    _COLS = [("", 34, "center"), ("Tên mail (profile)", 214, "w"), ("Nhóm", 124, "w"),
             ("Đăng nhập", 82, "center"), ("Chế độ", 112, "center"), ("Giai đoạn", 98, "center"),
             ("Hôm nay", 62, "center"), ("Tổng phiên", 78, "center")]

    def __init__(self):
        super().__init__()
        self.title("Mail Nurture")
        self.geometry("1180x760")
        self.minsize(1000, 640)
        self.configure(fg_color=BG_MAIN)
        try:
            self.iconbitmap(str(ICON_PATH))
        except Exception:
            pass
        self.engine = Engine()
        self._ensure_config()

        self._all_rows: list[dict] = []
        self._rows_by_iid: dict[str, dict] = {}
        self._row_widgets: dict[str, ctk.CTkFrame] = {}
        self._row_checks: dict[str, ctk.CTkCheckBox] = {}
        self._selected: set[str] = set()
        self.group_mode_vars: dict[str, ctk.StringVar] = {}
        self._gpm_ok = False
        self._was_running = False
        self._issues: list[dict] = []        # việc cần xử lý (trang Nhật ký & Lỗi)
        self._cur_page = "dash"
        self._hist_rows: list[dict] = []
        self._hist_shown = 0
        activity_log.cleanup()               # xoá file log quá cũ

        self.f_h1 = ctk.CTkFont(size=22, weight="bold")
        self.f_h2 = ctk.CTkFont(size=15, weight="bold")
        self.f_sm = ctk.CTkFont(size=13)
        self.f_xs = ctk.CTkFont(size=12)
        self.f_stat = ctk.CTkFont(size=24, weight="bold")
        self.f_mono = ctk.CTkFont(family="Consolas", size=12)

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()

        self.content = ctk.CTkFrame(self, fg_color=BG_MAIN, corner_radius=0)
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)

        self.pages: dict[str, ctk.CTkFrame] = {}
        self._build_dashboard()
        self._build_monitor()
        self._build_groups()
        self._build_config()
        self._build_toast()
        self.show_page("dash")
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._cleanup_stale_sessions()
        self.after(200, self._poll_logs)
        self.after(500, lambda: self._refresh_all(quiet=True))   # tự kết nối + tải khi mở

    def _ensure_config(self):
        if not CONFIG_PATH.exists() and EXAMPLE_PATH.exists():
            CONFIG_PATH.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")

    @staticmethod
    def _cleanup_stale_sessions():
        """Phiên còn 'đang chạy' từ lần mở app trước bị tắt ngang -> đánh dấu lỗi."""
        try:
            s = Store()
            try:
                s.cleanup_stale_running()
            finally:
                s.close()
        except Exception:
            pass

    def _on_close(self):
        """Bấm X: lưu nốt ô đang tự lưu; còn thay đổi cấu hình chưa lưu thì hỏi; đang chạy thì hỏi lại,
        dừng việc và ĐÓNG các profile GPM đang mở rồi mới thoát."""
        try:
            self._flush_autosave()
            dirty = self._cfg_dirty()
        except Exception:
            dirty = {}
        if dirty:
            names = "\n".join(f"• {self._KEY_LABEL.get(k, k)}" for k in list(dirty)[:10])
            ans = messagebox.askyesnocancel(
                "Cấu hình chưa lưu",
                f"Bạn có thay đổi ở tab Cấu hình CHƯA LƯU:\n\n{names}\n\nLưu trước khi thoát?\n"
                f"(Có = lưu rồi thoát · Không = bỏ thay đổi · Huỷ = quay lại app)")
            if ans is None:
                return
            if ans and not self._save_config():
                return
        if self.engine.running:
            if not messagebox.askyesno(
                    "Đang chạy",
                    "App đang làm việc với các mail.\n\nĐóng app bây giờ sẽ dừng ngay và đóng các "
                    "cửa sổ GPM đang mở. Đóng app?"):
                return
            self.engine.stop_event.set()
            self.engine.close_active_profiles()
        self.destroy()

    # ================= sidebar =================
    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=210, corner_radius=0, fg_color=BG_SIDE)
        sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(False)

        brand = ctk.CTkFrame(sb, fg_color="transparent")
        brand.pack(fill="x", padx=16, pady=(20, 22))
        ctk.CTkLabel(brand, text="Mail Nurture", font=ctk.CTkFont(size=18, weight="bold"),
                     text_color=TXT).pack(anchor="w")
        ctk.CTkLabel(brand, text="Nuôi mail như người thật", font=self.f_xs,
                     text_color=FAINT).pack(anchor="w")

        self.nav_btns = {}
        for key, text in [("dash", "Bảng điều khiển"), ("monitor", "Nhật ký & Lỗi"),
                          ("groups", "Nhóm mail"), ("config", "Cấu hình")]:
            b = ctk.CTkButton(sb, text=text, anchor="w", height=42, corner_radius=8,
                              fg_color="transparent", text_color=MUTED, hover_color=CARD,
                              font=self.f_sm, command=lambda k=key: self.show_page(k))
            b.pack(fill="x", padx=10, pady=3)
            self.nav_btns[key] = b
        # Số đỏ trên mục "Nhật ký & Lỗi" = số việc cần bạn chú ý
        self.nav_badge = ctk.CTkLabel(self.nav_btns["monitor"], text="", fg_color=RED, text_color="#FFFFFF",
                                      corner_radius=9, width=26, height=20, bg_color=BG_SIDE,
                                      font=ctk.CTkFont(size=11, weight="bold"))
        self.nav_badge.bind("<Button-1>", lambda e: self.show_page("monitor"))

        ctk.CTkFrame(sb, fg_color="transparent").pack(fill="both", expand=True)

        ctk.CTkLabel(sb, text="TRỢ GIÚP", font=self.f_xs, text_color=FAINT).pack(anchor="w", padx=18, pady=(0, 4))
        for label, fname in [("Hướng dẫn sử dụng", "HƯỚNG DẪN SỬ DỤNG.txt"),
                             ("Quy trình tương tác", "QUY TRÌNH TƯƠNG TÁC MAIL.txt")]:
            ctk.CTkButton(sb, text=label, anchor="w", height=32, corner_radius=8,
                          fg_color="transparent", text_color=MUTED, hover_color=CARD, font=self.f_xs,
                          command=lambda f=fname: self._open_doc(f)).pack(fill="x", padx=10, pady=1)

        status = ctk.CTkFrame(sb, fg_color=CARD, corner_radius=10)
        status.pack(fill="x", padx=12, pady=(12, 16))
        self.side_dot = ctk.CTkLabel(status, text="●", text_color=AMBER, font=self.f_sm, width=14)
        self.side_dot.pack(side="left", padx=(12, 4), pady=10)
        self.side_status = ctk.CTkLabel(status, text="Đang kết nối GPM…", text_color=MUTED,
                                        font=self.f_xs, anchor="w")
        self.side_status.pack(side="left", fill="x", expand=True, pady=10)

    def _open_doc(self, fname: str):
        path = ROOT / fname
        try:
            os.startfile(str(path))
        except Exception:
            self.toast(f"Không mở được {fname}", "err")

    def show_page(self, name: str):
        for p in self.pages.values():
            p.grid_remove()
        self.pages[name].grid(row=0, column=0, sticky="nsew")
        self._cur_page = name
        for k, b in self.nav_btns.items():
            if k == name:
                b.configure(fg_color=ACCENT, text_color="#ffffff", hover_color=ACCENT_HOVER)
            else:
                b.configure(fg_color="transparent", text_color=MUTED, hover_color=CARD)
        self.nav_badge.configure(bg_color=ACCENT if name == "monitor" else BG_SIDE)
        if name == "groups" and self._gpm_ok:
            self._load_groups()
        if name == "monitor":
            self._load_monitor()

    def _set_status(self, ok: bool, text: str):
        self._gpm_ok = ok
        self.side_dot.configure(text_color=GREEN if ok else RED)
        self.side_status.configure(text=text, text_color=TXT if ok else MUTED)

    # ================= toast (thông báo nhỏ, tự ẩn) =================
    def _build_toast(self):
        self._toast = ctk.CTkLabel(self, text="", corner_radius=8, font=self.f_sm, height=38)
        self._toast_job = None

    def toast(self, msg: str, kind: str = "ok", ms: int = 3000):
        bg, fg = {"ok": ("#1F3A2B", GREEN), "warn": ("#39301C", AMBER),
                  "err": ("#3A2422", RED)}.get(kind, ("#1F3A2B", GREEN))
        self._toast.configure(text=f"   {msg}   ", fg_color=bg, text_color=fg)
        self._toast.place(relx=0.985, rely=0.975, anchor="se")
        self._toast.lift()
        if self._toast_job:
            self.after_cancel(self._toast_job)
        self._toast_job = self.after(ms, self._toast.place_forget)

    # ================= dashboard =================
    def _build_dashboard(self):
        page = ctk.CTkFrame(self.content, fg_color=BG_MAIN, corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(5, weight=1)
        self.pages["dash"] = page

        # Tiêu đề + nút hành động chính
        head = ctk.CTkFrame(page, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 8))
        head.grid_columnconfigure(0, weight=1)
        left = ctk.CTkFrame(head, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(left, text="Bảng điều khiển", font=self.f_h1, text_color=TXT).pack(anchor="w")
        ctk.CTkLabel(left, text="Tích chọn mail ở bảng rồi bấm Nuôi — hoặc để tool tự chọn bằng “Chạy đợt nhẹ”.",
                     font=self.f_xs, text_color=MUTED).pack(anchor="w", pady=(2, 0))
        btns = ctk.CTkFrame(head, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="e")
        self.btn_run = ctk.CTkButton(btns, text="▶  Nuôi ngay", width=170, height=38, font=self.f_sm,
                                     fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self._run_selected)
        self.btn_run.pack(side="left", padx=4)
        self.btn_daily = ctk.CTkButton(btns, text="Chạy đợt nhẹ", width=120, height=38, font=self.f_sm,
                                       fg_color="transparent", border_width=1, border_color=BORDER,
                                       text_color=TXT, hover_color=CARD, command=self._run_daily)
        self.btn_daily.pack(side="left", padx=4)
        ctk.CTkButton(btns, text="Dừng", width=76, height=38, font=self.f_sm, fg_color="transparent",
                      border_width=1, border_color=BORDER, text_color=RED, hover_color=CARD,
                      command=self._stop).pack(side="left", padx=4)

        # Thanh tiến trình khi đang chạy (ẩn khi rảnh)
        self.progress = ctk.CTkProgressBar(page, mode="indeterminate", height=4, progress_color=ACCENT,
                                           fg_color=BG_MAIN)
        self.progress.grid(row=1, column=0, sticky="ew", padx=24)
        self.progress.grid_remove()

        # Banner hướng dẫn theo ngữ cảnh (ẩn khi mọi thứ ổn)
        self.banner = ctk.CTkFrame(page, fg_color="#2E2718", corner_radius=10, border_width=1,
                                   border_color="#5A4A22")
        self.banner.grid(row=2, column=0, sticky="ew", padx=24, pady=(6, 4))
        self.banner.grid_columnconfigure(0, weight=1)
        self.banner_lbl = ctk.CTkLabel(self.banner, text="", text_color=AMBER, font=self.f_sm,
                                       anchor="w", justify="left", wraplength=720)
        self.banner_lbl.grid(row=0, column=0, sticky="w", padx=16, pady=12)
        self.banner_btn = ctk.CTkButton(self.banner, text="", width=150, height=32, font=self.f_sm,
                                        fg_color=AMBER, hover_color="#C48C2C", text_color="#1a1407")
        self.banner_btn.grid(row=0, column=1, padx=(12, 4), pady=10)
        # Nút phụ: sang trang "Nhật ký & Lỗi" khi còn nhiều việc khác cần xem
        self.banner_btn2 = ctk.CTkButton(self.banner, text="", width=120, height=32, font=self.f_sm,
                                         fg_color="transparent", border_width=1, border_color="#5A4A22",
                                         hover_color="#3A3120", text_color=AMBER,
                                         command=lambda: self.show_page("monitor"))
        self.banner_btn2.grid(row=0, column=2, padx=(4, 12), pady=10)
        self.banner_btn2.grid_remove()
        self.banner.grid_remove()

        # Thẻ số liệu
        stats = ctk.CTkFrame(page, fg_color="transparent")
        stats.grid(row=3, column=0, sticky="ew", padx=20, pady=(8, 4))
        for i in range(5):
            stats.grid_columnconfigure(i, weight=1)
        self.stat_total = self._stat_card(stats, 0, "Tổng mail", TXT)
        self.stat_ready = self._stat_card(stats, 1, "Đang được nuôi", GREEN)
        self.stat_locked = self._stat_card(stats, 2, "Đang khóa (kênh)", RED)
        self.stat_today = self._stat_card(stats, 3, "Phiên hôm nay", BLUE)
        self.stat_cooldown = self._stat_card(stats, 4, "Đang nghỉ (cảnh báo)", AMBER)

        # Thanh công cụ bảng: tìm kiếm + lọc + chọn nhanh
        tool = ctk.CTkFrame(page, fg_color="transparent")
        tool.grid(row=4, column=0, sticky="ew", padx=24, pady=(10, 6))
        self.search_entry = ctk.CTkEntry(tool, width=210, height=34, font=self.f_sm,
                                         placeholder_text="Tìm theo tên mail hoặc nhóm…",
                                         fg_color=BG_SIDE, border_color=BORDER)
        self.search_entry.pack(side="left")
        self.search_entry.bind("<KeyRelease>", lambda e: self._render_rows())
        self.filter_var = ctk.StringVar(value="Tất cả")
        ctk.CTkSegmentedButton(tool, values=["Tất cả", "Nuôi", "Khóa", "Để sau", "Nghỉ"],
                               variable=self.filter_var, height=34, font=self.f_xs,
                               fg_color=BG_SIDE, unselected_color=BG_SIDE, unselected_hover_color=CARD,
                               selected_color=ACCENT, selected_hover_color=ACCENT_HOVER, text_color=TXT,
                               command=lambda v: self._render_rows()).pack(side="left", padx=10)
        ctk.CTkButton(tool, text="⟳  Làm mới", width=92, height=34, font=self.f_sm, fg_color=CARD,
                      hover_color=CARD_HI, text_color=TXT,
                      command=lambda: self._refresh_all(quiet=False)).pack(side="right")
        self.btn_login = ctk.CTkButton(tool, text="Kiểm tra đăng nhập", width=146, height=34, font=self.f_sm,
                                       fg_color=CARD, hover_color=CARD_HI, text_color=TXT,
                                       command=self._check_logins)
        self.btn_login.pack(side="right", padx=4)
        ctk.CTkButton(tool, text="Bỏ chọn", width=72, height=34, font=self.f_sm, fg_color="transparent",
                      hover_color=CARD, text_color=MUTED, command=self._clear_selection).pack(side="right", padx=2)
        ctk.CTkButton(tool, text="Chọn mail Nuôi", width=118, height=34, font=self.f_sm,
                      fg_color=CARD, hover_color=CARD_HI, text_color=TXT,
                      command=self._select_all_nurture).pack(side="right", padx=4)

        # Bảng mail (chiếm phần lớn màn hình)
        self._build_table(page)

        # Log gọn phía dưới
        logwrap = ctk.CTkFrame(page, fg_color=LOGBG, corner_radius=10, height=120)
        logwrap.grid(row=6, column=0, sticky="ew", padx=24, pady=(8, 18))
        logwrap.grid_propagate(False)
        logwrap.grid_columnconfigure(0, weight=1)
        logwrap.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(logwrap, text="Nhật ký hoạt động", font=self.f_xs, text_color=MUTED).grid(
            row=0, column=0, sticky="w", padx=12, pady=(8, 0))
        ctk.CTkButton(logwrap, text="Xoá log", width=64, height=24, font=self.f_xs, fg_color="transparent",
                      hover_color=CARD, text_color=FAINT, command=self._clear_log).grid(
            row=0, column=1, sticky="e", padx=8, pady=(6, 0))
        self.logbox = ctk.CTkTextbox(logwrap, fg_color=LOGBG, text_color="#CBD3DE",
                                     font=self.f_mono, border_width=0)
        self.logbox.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=8, pady=(0, 6))
        self.logbox.configure(state="disabled")

    def _stat_card(self, parent, col, label, color):
        c = ctk.CTkFrame(parent, fg_color=CARD, corner_radius=10)
        c.grid(row=0, column=col, sticky="ew", padx=4)
        ctk.CTkLabel(c, text=label, font=self.f_xs, text_color=MUTED).pack(anchor="w", padx=14, pady=(8, 0))
        val = ctk.CTkLabel(c, text="—", font=self.f_stat, text_color=color, height=30)
        val.pack(anchor="w", padx=14, pady=(0, 6))
        return val

    def _build_table(self, page):
        wrap = ctk.CTkFrame(page, fg_color=CARD, corner_radius=10)
        wrap.grid(row=5, column=0, sticky="nsew", padx=24)

        hdr = ctk.CTkFrame(wrap, fg_color=BG_SIDE, corner_radius=8)
        hdr.pack(fill="x", padx=6, pady=(6, 0))
        for i, (t, w, a) in enumerate(self._COLS):
            ctk.CTkLabel(hdr, text=t, width=w, anchor="w" if a == "w" else "center",
                         text_color=MUTED, font=self.f_xs).grid(
                row=0, column=i, padx=(10 if i == 0 else 4, 4), pady=8)

        self.table_body = ctk.CTkScrollableFrame(wrap, fg_color=CARD)
        self.table_body.pack(fill="both", expand=True, padx=4, pady=4)
        self._empty_note("Đang tải danh sách mail…")

    def _empty_note(self, text: str):
        for w in self.table_body.winfo_children():
            w.destroy()
        ctk.CTkLabel(self.table_body, text=text, text_color=FAINT, font=self.f_sm).pack(pady=28)

    # ---- dữ liệu bảng ----
    def _row_visible(self, r: dict) -> bool:
        q = self.search_entry.get().strip().lower()
        if q and q not in (r["name"] or "").lower() and q not in (r["group"] or "").lower():
            return False
        f = self.filter_var.get()
        rest = r.get("cooldown", 0) > 0
        if f == "Nuôi":
            return r["raw_mode"] == "nurture" and not rest
        if f == "Khóa":
            return r["raw_mode"] == "locked"
        if f == "Để sau":
            return r["raw_mode"] == "later"
        if f == "Nghỉ":
            return rest
        return True

    def _render_rows(self):
        for w in self.table_body.winfo_children():
            w.destroy()
        self._row_widgets, self._row_checks = {}, {}
        shown = [r for r in self._all_rows if self._row_visible(r)]
        # Mail có thể nuôi lên đầu, mail đang nghỉ tiếp theo, mail Khóa xuống cuối.
        rank = {"nurture": 0, "later": 1, "locked": 3}
        shown.sort(key=lambda r: (2 if (r.get("cooldown", 0) > 0 and r["raw_mode"] != "locked")
                                  else rank.get(r["raw_mode"], 2), (r["name"] or "").lower()))
        if not self._all_rows:
            self._empty_note("Chưa có dữ liệu — kiểm tra kết nối GPM rồi bấm “Làm mới”.")
        elif not shown:
            self._empty_note("Không có mail nào khớp bộ lọc.")
        for r in shown:
            self._add_row(r)
        self._update_sel_ui()

    def _add_row(self, r: dict):
        pid = r["id"]
        locked = r["raw_mode"] == "locked"
        fr = ctk.CTkFrame(self.table_body, fg_color=SEL_BG if pid in self._selected else CARD,
                          corner_radius=7)
        fr.pack(fill="x", pady=1)
        w = [c[1] for c in self._COLS]

        chk = None
        if locked:
            # Mail nhóm Khóa: không có ô tích (không thể chọn để nuôi).
            ctk.CTkLabel(fr, text="", width=w[0]).grid(row=0, column=0, padx=(10, 4), pady=6)
        else:
            chk = ctk.CTkCheckBox(fr, text="", width=w[0], checkbox_width=18, checkbox_height=18,
                                  command=lambda p=pid: self._toggle_row(p, from_check=True))
            chk.grid(row=0, column=0, padx=(10, 4), pady=6)
            if pid in self._selected:
                chk.select()
            self._row_checks[pid] = chk

        name_col = FAINT if locked else TXT
        ctk.CTkLabel(fr, text=r["name"], width=w[1], anchor="w", text_color=name_col,
                     font=self.f_sm).grid(row=0, column=1, padx=4, pady=6)
        ctk.CTkLabel(fr, text=r["group"] or "—", width=w[2], anchor="w", text_color=MUTED,
                     font=self.f_xs).grid(row=0, column=2, padx=4, pady=6)

        # Cột Đăng nhập: kết quả kiểm tra cookie gần nhất
        li = r.get("login")
        if r.get("open_err"):
            li_txt, li_col = "● Lỗi mở", AMBER          # profile không mở được (proxy/GPM)
        elif li is None:
            li_txt, li_col = "—", FAINT
        elif li[0]:
            li_txt, li_col = "● Có", GREEN
        else:
            li_txt, li_col = "● Không", RED
        ctk.CTkLabel(fr, text=li_txt, width=w[3], anchor="center", text_color=li_col,
                     font=self.f_xs).grid(row=0, column=3, padx=4, pady=6)

        rest = r.get("cooldown", 0) > 0
        need_login = rest and safety.is_login_reason(r.get("flag", ""))
        pk = "login" if need_login else ("rest" if rest else r["raw_mode"])
        pk = pk if pk in PILL_BG else "later"
        pillwrap = ctk.CTkFrame(fr, width=w[4], height=26, fg_color="transparent")
        pillwrap.grid(row=0, column=4, padx=4, pady=5)
        pillwrap.grid_propagate(False)
        ctk.CTkLabel(pillwrap, text=PILL_TXT[pk], fg_color=PILL_BG[pk], text_color=PILL_FG[pk],
                     corner_radius=11, font=self.f_xs, width=104 if pk == "login" else 74, height=24).place(
            relx=0.5, rely=0.5, anchor="center")

        if need_login:
            stage_txt = "Đăng nhập lại"
        elif rest:
            stage_txt = f"Nghỉ {r['cooldown']:.0f}h"
        else:
            stage_txt = STAGE_VI.get(r["stage"], r["stage"])
        ctk.CTkLabel(fr, text=stage_txt, width=w[5], anchor="center",
                     text_color=AMBER if rest else MUTED, font=self.f_xs).grid(row=0, column=5, padx=4, pady=6)
        other = r.get("other_machine")
        ctk.CTkLabel(fr, text="máy khác" if other else str(r["today"]), width=w[6], anchor="center",
                     text_color=BLUE if other else MUTED,
                     font=self.f_xs).grid(row=0, column=6, padx=4, pady=6)
        ctk.CTkLabel(fr, text=str(r["total"]), width=w[7], anchor="center", text_color=MUTED,
                     font=self.f_xs).grid(row=0, column=7, padx=4, pady=6)

        if not locked:
            for wd in [fr] + [c for c in fr.winfo_children() if c is not chk]:
                wd.bind("<Button-1>", lambda e, p=pid: self._toggle_row(p))
            fr.bind("<Enter>", lambda e, p=pid: self._hover_row(p, True))
            fr.bind("<Leave>", lambda e, p=pid: self._hover_row(p, False))
        self._row_widgets[pid] = fr

    def _paint_row(self, pid, hover=False):
        fr = self._row_widgets.get(pid)
        if fr:
            fr.configure(fg_color=SEL_BG if pid in self._selected else (CARD_HI if hover else CARD))

    def _toggle_row(self, pid, from_check=False):
        r = self._rows_by_iid.get(pid, {})
        if r.get("raw_mode") == "locked":
            self.toast("Mail thuộc nhóm KHÓA — không thể chọn để nuôi.", "warn")
            return
        if pid in self._selected:
            self._selected.discard(pid)
        else:
            self._selected.add(pid)
        chk = self._row_checks.get(pid)
        if chk:
            chk.select() if pid in self._selected else chk.deselect()
        self._paint_row(pid)
        self._update_sel_ui()

    def _hover_row(self, pid, entering):
        if pid not in self._selected:
            self._paint_row(pid, hover=entering)

    def _select_all_nurture(self):
        n = 0
        for r in self._all_rows:
            if r["raw_mode"] == "nurture" and r.get("cooldown", 0) <= 0:
                self._selected.add(r["id"])
                n += 1
        self._render_rows()
        self.toast(f"Đã chọn {n} mail đang Nuôi." if n else "Chưa có mail nào ở nhóm Nuôi.",
                   "ok" if n else "warn")

    def _clear_selection(self):
        self._selected.clear()
        self._render_rows()

    def _update_sel_ui(self):
        if self.engine.running:
            return
        n = len(self._selected)
        self.btn_run.configure(text=f"▶  Nuôi {n} mail đã chọn" if n else "▶  Nuôi ngay")

    # ---- tải / làm mới ----
    def _refresh_all(self, quiet=False):
        if self._check_gpm(quiet=quiet):
            self._load_profiles()
        else:
            self._all_rows = []
            self._render_rows()
        self._refresh_issues()

    def _check_gpm(self, quiet=False) -> bool:
        try:
            profiles = self.engine.check_gpm()
            self._set_status(True, f"Đã kết nối GPM · {len(profiles)} mail")
            return True
        except GPMError as e:
            self._set_status(False, "Chưa kết nối GPM")
            if not quiet:
                messagebox.showerror("Chưa kết nối được GPM", str(e))
            return False

    def _load_profiles(self):
        try:
            rows = self.engine.list_profiles_with_stage()
        except GPMError as e:
            self.toast("Không tải được danh sách mail", "err")
            self._append_log(f"✖ {e}")
            return
        self._all_rows = rows
        self._rows_by_iid = {r["id"]: r for r in rows}
        self._selected &= {r["id"] for r in rows if r["raw_mode"] != "locked"}
        nurture = sum(1 for r in rows if r["raw_mode"] == "nurture")
        self.stat_total.configure(text=str(len(rows)))
        self.stat_ready.configure(text=str(nurture))
        self.stat_locked.configure(text=str(sum(1 for r in rows if r["raw_mode"] == "locked")))
        self.stat_today.configure(text=str(sum(r["today"] for r in rows)))
        self.stat_cooldown.configure(text=str(sum(1 for r in rows if r.get("cooldown", 0) > 0)))
        self._render_rows()
        self._refresh_issues()

    def _refresh_issues(self, render: bool = True):
        """Tính lại 'việc cần xử lý' -> cập nhật số đỏ trên menu, dải vàng và trang Nhật ký & Lỗi."""
        try:
            store = Store()
            try:
                self._issues = monitor.build_issues(self._all_rows, store, self._gpm_ok)
            finally:
                store.close()
        except Exception as e:  # noqa: BLE001
            self._issues = []
            self._append_log(f"✖ Không tổng hợp được danh sách lỗi: {e}")
        n = monitor.count_attention(self._issues)
        if n:
            self.nav_badge.configure(text=str(n) if n < 100 else "99+")
            self.nav_badge.place(relx=0.93, rely=0.5, anchor="e")
        else:
            self.nav_badge.place_forget()
        if hasattr(self, "mon_tabs"):
            self.mon_tabs["issues"].configure(text=f"Cần xử lý ({n})" if n else "Cần xử lý")
            if render and self._cur_page == "monitor" and self._mon_tab == "issues":
                self._render_issues()
        self._update_banner()

    def _update_banner(self):
        n_att = monitor.count_attention(self._issues)
        shown = 0                              # số việc mà thông báo chính đã nói tới
        if not self._gpm_ok:
            self.banner_lbl.configure(text="Chưa kết nối được GPM Login. Hãy mở GPM, vào Cài đặt → API "
                                           "và BẬT API (cổng 19995), rồi bấm “Thử lại”.")
            self.banner_btn.configure(text="Thử lại", command=lambda: self._refresh_all(quiet=False))
            shown = 1
        elif self._login_rows():
            n = len(self._login_rows())
            self.banner_lbl.configure(text=f"Có {n} mail đã bị ĐĂNG XUẤT khỏi Google nên tool tạm bỏ qua "
                                           f"(nhãn “Cần đăng nhập”). Hãy mở GPM đăng nhập lại các mail này, "
                                           f"xong bấm nút bên cạnh — hoặc bấm “Kiểm tra đăng nhập” để tool tự kiểm tra.")
            self.banner_btn.configure(text="Tôi đã đăng nhập lại", command=self._clear_login_flags)
            shown = n
        elif self._all_rows and not any(r["raw_mode"] == "nurture" for r in self._all_rows):
            self.banner_lbl.configure(text="Chưa có nhóm nào được đặt “Nuôi”, nên tool chưa biết nuôi mail nào. "
                                           "Vào tab Nhóm mail và chọn Nuôi cho nhóm cần nuôi.")
            self.banner_btn.configure(text="Mở tab Nhóm mail", command=lambda: self.show_page("groups"))
        elif n_att:
            kinds = {}
            for it in self._issues:
                if it["sev"] in ("high", "medium"):
                    kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
            words = {"open": "mail không mở được", "challenge": "mail bị Google cảnh báo (đang nghỉ)",
                     "error": "mail có phiên bị lỗi", "emergency": "đợt chạy bị dừng khẩn",
                     "login": "mail cần đăng nhập", "gpm": "lỗi kết nối GPM"}
            desc = ", ".join(f"{c} {words.get(k, k)}" for k, c in kinds.items())
            self.banner_lbl.configure(text=f"Có {n_att} việc cần xem: {desc}. Mở trang “Nhật ký & Lỗi” để "
                                           f"xem chi tiết và cách xử lý.")
            self.banner_btn.configure(text="Xem Nhật ký & Lỗi", command=lambda: self.show_page("monitor"))
            shown = n_att
        else:
            self.banner.grid_remove()
            return
        extra = n_att - shown
        if extra > 0:
            self.banner_btn2.configure(text=f"+{extra} việc khác")
            self.banner_btn2.grid()
            self.banner_lbl.configure(wraplength=580)
        else:
            self.banner_btn2.grid_remove()
            self.banner_lbl.configure(wraplength=720)
        self.banner.grid()

    @staticmethod
    def _shared_clear_flags(pids: list[str]):
        """Chạy nhiều máy: gỡ cờ "Cần đăng nhập" ở thư mục chung (cờ cảnh báo bảo mật thì KHÔNG gỡ)."""
        try:
            sh = Shared(load_config().raw)
            if not sh.enabled or not sh.check()[0]:
                return
            for pid in pids:
                if safety.is_login_reason((sh.flag_info(pid) or {}).get("reason", "")):
                    sh.clear_flag(pid)
        except Exception:
            pass

    def _login_rows(self) -> list[dict]:
        return [r for r in self._all_rows
                if r.get("cooldown", 0) > 0 and safety.is_login_reason(r.get("flag", ""))]

    def _clear_login_flags(self):
        rows = self._login_rows()
        if not rows:
            return
        names = "\n".join(f"• {r['name']}" for r in rows[:12])
        if not messagebox.askyesno("Gỡ trạng thái “Cần đăng nhập”",
                                   f"Bạn đã đăng nhập lại Google cho các mail này trong GPM chưa?\n\n{names}\n\n"
                                   f"Bấm Có để tool nuôi lại các mail này."):
            return
        store = Store()
        try:
            for r in rows:
                store.clear_flags(r["id"])
        finally:
            store.close()
        self._shared_clear_flags([r["id"] for r in rows])
        self.toast(f"Đã gỡ trạng thái “Cần đăng nhập” cho {len(rows)} mail.")
        self._append_log(f"🔑 Gỡ cờ đăng nhập cho {len(rows)} mail.")
        self._load_profiles()

    # ---- log + trạng thái chạy ----
    def _append_log(self, msg: str):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")
        activity_log.write(msg)              # lưu ra file theo ngày (tắt app vẫn còn)
        # Trang "Nhật ký tool" đang xem hôm nay -> thêm dòng mới ngay
        if hasattr(self, "toollog") and self._cur_log_day() == date.today().isoformat():
            stamp = datetime.now().strftime("%H:%M:%S")
            for ln in str(msg).rstrip("\n").split("\n"):
                self._toollog_insert(f"[{stamp}] {ln}")

    def _clear_log(self):
        self.logbox.configure(state="normal")
        self.logbox.delete("1.0", "end")
        self.logbox.configure(state="disabled")

    def _poll_logs(self):
        try:
            while True:
                self._append_log(self.engine.log_q.get_nowait())
        except queue.Empty:
            pass
        running = self.engine.running
        if running != self._was_running:
            self._was_running = running
            is_login = self.engine.job == "login"
            if running:
                self.btn_run.configure(text="⏳  Đang kiểm tra…" if is_login else "⏳  Đang chạy…",
                                       state="disabled")
                self.btn_daily.configure(state="disabled")
                self.btn_login.configure(state="disabled")
                self.progress.grid()
                self.progress.start()
            else:
                self.progress.stop()
                self.progress.grid_remove()
                self.btn_run.configure(state="normal")
                self.btn_daily.configure(state="normal")
                self.btn_login.configure(state="normal")
                self._update_sel_ui()
                res = self.engine.last_result or {}
                default = ("Kiểm tra đăng nhập xong — xem cột “Đăng nhập”." if is_login
                           else "Đợt nuôi đã xong — đã cập nhật bảng.")
                # Tổng kết đúng màu (đỏ khi dừng khẩn, vàng khi có mail lỗi) và hiện lâu hơn để kịp đọc.
                self.toast(res.get("text") or default, res.get("kind", "ok"), ms=9000)
                if self._gpm_ok:
                    self._load_profiles()
                else:
                    self._refresh_issues()
                if self._cur_page == "monitor":
                    self._load_monitor()
        self.after(200, self._poll_logs)

    # ---- chạy ----
    def _selected_profiles(self) -> list[dict]:
        out = []
        for pid in self._selected:
            r = self._rows_by_iid.get(pid, {})
            out.append({"id": pid, "name": r.get("name", ""),
                        "group": r.get("group", ""), "raw_mode": r.get("raw_mode", "")})
        return out

    def _run_selected(self):
        sel = self._selected_profiles()
        if not sel:
            self.toast("Hãy tích chọn ít nhất 1 mail trong bảng (hoặc dùng “Chạy đợt nhẹ”).", "warn")
            return
        run = [p for p in sel if p.get("raw_mode") != "locked"]
        if len(run) < len(sel):
            self.toast(f"Bỏ qua {len(sel) - len(run)} mail thuộc nhóm Khóa.", "warn")
        if run:
            self.engine.run_profiles(run)

    def _run_daily(self):
        if self.engine.running:
            self.toast("Đang có việc chạy — đợi xong rồi chạy đợt mới.", "warn")
            return
        try:
            cfg = load_config()
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Cấu hình bị lỗi", str(e))
            return
        # Đợt nuôi tự chọn mail chỉ chạy trong khung giờ hoạt động (người thật không lướt web lúc 3h sáng).
        try:
            lo, hi = [int(x) for x in (cfg.get("run.active_hours", [0, 24]) or [0, 24])][:2]
        except Exception:
            lo, hi = 0, 24
        if not (lo <= datetime.now().hour < hi):
            self.toast(f"Ngoài giờ hoạt động ({lo}h–{hi}h) — chưa chạy đợt. Đổi giờ ở tab Cấu hình.", "warn")
            return
        try:
            from src.scheduler import plan_today
            gpm = GPMClient(cfg.get("gpm.base_url"), cfg.get("gpm.api_version", "v3"))
            store = Store()
            try:
                plan = plan_today(cfg, gpm, store)
            finally:
                store.close()
        except GPMError as e:
            messagebox.showerror("Chưa kết nối được GPM", str(e))
            return
        if not plan.get("shared_ok", True):
            messagebox.showerror("Không vào được thư mục chung",
                                 f"{plan.get('shared_msg')}\n\nĐang cài chạy nhiều máy nên máy này KHÔNG nuôi "
                                 f"(tránh 2 máy trùng mail). Kiểm tra máy chủ đang bật và thư mục còn chia sẻ, "
                                 f"hoặc xoá đường dẫn ở tab Cấu hình nếu chỉ chạy 1 máy.")
            return
        profiles = plan["picked"]
        if not profiles:
            self.toast("Hôm nay không có mail nào cần nuôi (chưa có nhóm Nuôi, hoặc đã nuôi đủ).", "warn")
            self._update_banner()
            return
        self.engine.run_profiles([{"id": p.id, "name": p.name, "group": p.group_name} for p in profiles],
                                 respect_hours=True, plan=plan)

    def _check_logins(self, only_ids: list[str] | None = None):
        """Nhiệm vụ kiểm tra đăng nhập: only_ids (từ trang Nhật ký & Lỗi), hoặc mail đang tích chọn,
        hoặc tất cả mail không bị khóa."""
        if self.engine.running:
            self.toast("Đang có việc chạy — đợi xong rồi kiểm tra.", "warn")
            return
        if not self._gpm_ok:
            self.toast("Chưa kết nối GPM — mở GPM rồi bấm Làm mới.", "err")
            self._update_banner()
            return
        pool = [r for r in self._all_rows if r["raw_mode"] != "locked"]
        if only_ids is not None:
            sel = [r for r in pool if r["id"] in set(only_ids)]
        else:
            sel = [r for r in pool if r["id"] in self._selected]
        targets = sel or ([] if only_ids is not None else pool)
        if not targets:
            self.toast("Không có mail nào để kiểm tra (tất cả thuộc nhóm Khóa).", "warn")
            return
        self.toast(f"Đang kiểm tra đăng nhập {len(targets)} mail{' đã chọn' if sel else ''}… (chỉ đọc cookie)")
        self.engine.run_login_check([{"id": r["id"], "name": r["name"], "group": r["group"]}
                                     for r in targets])

    def _stop(self):
        if self.engine.running:
            self.engine.stop()
            self.toast("Sẽ dừng sau khi mail đang xử lý xong.", "warn")
        else:
            self.toast("Hiện không có việc nào đang chạy.", "warn")

    # ================= trang Nhật ký & Lỗi =================
    _RANGES = {"Hôm nay": 0, "7 ngày": 6, "30 ngày": 29}
    # trạng thái phiên -> (chữ, màu chữ, màu nền pill)
    _ST = {"ok": ("Thành công", GREEN, "#1F3A2B"), "flagged": ("Bị Google chặn", RED, "#3A2422"),
           "error": ("Lỗi", RED, "#3A2422"), "skip": ("Bỏ qua", "#AEB4BC", "#2B3038"),
           "running": ("Đang chạy", BLUE, "#1C2C40"), "aborted": ("Bị ngắt giữa chừng", AMBER, "#39301C")}
    _HIST_FILTER = {"Tất cả": None, "Thành công": {"ok"}, "Bị chặn": {"flagged"},
                    "Lỗi": {"error", "aborted"}, "Bỏ qua": {"skip"}}
    _ACT_SHORT = {"youtube": "YouTube", "news": "Báo", "gmail": "Gmail", "diary": "Nhật ký", "visit": "Website"}
    _HIST_COLS = [("Thời gian", 100, "w"), ("Mail", 210, "w"), ("Kết quả", 130, "center"),
                  ("Thời lượng", 74, "center"), ("Việc đã làm / lý do", 330, "w")]
    _HIST_PAGE = 60

    def _build_monitor(self):
        page = ctk.CTkFrame(self.content, fg_color=BG_MAIN, corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        page.grid_columnconfigure(0, weight=1)
        page.grid_rowconfigure(3, weight=1)
        self.pages["monitor"] = page

        head = ctk.CTkFrame(page, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 8))
        head.grid_columnconfigure(0, weight=1)
        left = ctk.CTkFrame(head, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(left, text="Nhật ký & Lỗi", font=self.f_h1, text_color=TXT).pack(anchor="w")
        ctk.CTkLabel(left, text="Tool đã làm gì, mail nào gặp lỗi — và bạn cần xử lý việc gì.",
                     font=self.f_xs, text_color=MUTED).pack(anchor="w", pady=(2, 0))
        right = ctk.CTkFrame(head, fg_color="transparent")
        right.grid(row=0, column=1, sticky="e")
        self.mon_range = ctk.StringVar(value="Hôm nay")
        ctk.CTkSegmentedButton(right, values=list(self._RANGES), variable=self.mon_range, height=34,
                               font=self.f_xs, fg_color=BG_SIDE, unselected_color=BG_SIDE,
                               unselected_hover_color=CARD, selected_color=ACCENT,
                               selected_hover_color=ACCENT_HOVER, text_color=TXT,
                               command=lambda v: self._load_monitor()).pack(side="left", padx=(0, 8))
        ctk.CTkButton(right, text="⟳  Làm mới", width=92, height=34, font=self.f_sm, fg_color=CARD,
                      hover_color=CARD_HI, text_color=TXT, command=self._load_monitor).pack(side="left")

        stats = ctk.CTkFrame(page, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", padx=20, pady=(4, 4))
        for i in range(5):
            stats.grid_columnconfigure(i, weight=1)
        self.mst_ok = self._stat_card(stats, 0, "Phiên thành công", GREEN)
        self.mst_flag = self._stat_card(stats, 1, "Bị Google chặn", RED)
        self.mst_err = self._stat_card(stats, 2, "Phiên lỗi", AMBER)
        self.mst_skip = self._stat_card(stats, 3, "Bỏ qua (đăng xuất…)", "#AEB4BC")
        self.mst_act = self._stat_card(stats, 4, "Việc đã làm", BLUE)

        # Thanh chọn mục (kiểu tab)
        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.grid(row=2, column=0, sticky="ew", padx=24, pady=(12, 6))
        self.mon_tabs: dict[str, ctk.CTkButton] = {}
        for key, text in [("issues", "Cần xử lý"), ("history", "Lịch sử phiên"), ("log", "Nhật ký tool")]:
            b = ctk.CTkButton(bar, text=text, width=150, height=34, corner_radius=8, font=self.f_sm,
                              fg_color=CARD, hover_color=CARD_HI, text_color=MUTED,
                              command=lambda k=key: self._mon_show(k))
            b.pack(side="left", padx=(0, 6))
            self.mon_tabs[key] = b

        body = ctk.CTkFrame(page, fg_color="transparent")
        body.grid(row=3, column=0, sticky="nsew", padx=24, pady=(0, 18))
        body.grid_columnconfigure(0, weight=1)
        body.grid_rowconfigure(0, weight=1)
        self.mon_views: dict[str, ctk.CTkFrame] = {}

        # A) Cần xử lý
        self.mon_issues = ctk.CTkScrollableFrame(body, fg_color="transparent")
        self.mon_views["issues"] = self.mon_issues

        # B) Lịch sử phiên
        vb = ctk.CTkFrame(body, fg_color=CARD, corner_radius=10)
        self.mon_views["history"] = vb
        tb = ctk.CTkFrame(vb, fg_color="transparent")
        tb.pack(fill="x", padx=10, pady=(10, 6))
        self.hist_filter = ctk.StringVar(value="Tất cả")
        ctk.CTkSegmentedButton(tb, values=list(self._HIST_FILTER), variable=self.hist_filter, height=30,
                               font=self.f_xs, fg_color=BG_SIDE, unselected_color=BG_SIDE,
                               unselected_hover_color=CARD_HI, selected_color=ACCENT,
                               selected_hover_color=ACCENT_HOVER, text_color=TXT,
                               command=lambda v: self._render_history()).pack(side="left")
        self.hist_count = ctk.CTkLabel(tb, text="", text_color=FAINT, font=self.f_xs)
        self.hist_count.pack(side="left", padx=12)
        ctk.CTkLabel(tb, text="Bấm vào một dòng để xem chi tiết", text_color=FAINT,
                     font=self.f_xs).pack(side="right", padx=4)
        hdr = ctk.CTkFrame(vb, fg_color=BG_SIDE, corner_radius=8)
        hdr.pack(fill="x", padx=6)
        for i, (t, w, a) in enumerate(self._HIST_COLS):
            ctk.CTkLabel(hdr, text=t, width=w, anchor="w" if a == "w" else "center", text_color=MUTED,
                         font=self.f_xs).grid(row=0, column=i, padx=(12 if i == 0 else 4, 4), pady=8)
        self.hist_body = ctk.CTkScrollableFrame(vb, fg_color=CARD)
        self.hist_body.pack(fill="both", expand=True, padx=4, pady=4)
        self._hist_filtered: list[dict] = []
        self._hist_more_btn = None

        # C) Nhật ký tool (file log theo ngày)
        vc = ctk.CTkFrame(body, fg_color=LOGBG, corner_radius=10)
        self.mon_views["log"] = vc
        tc = ctk.CTkFrame(vc, fg_color="transparent")
        tc.pack(fill="x", padx=12, pady=(10, 4))
        ctk.CTkLabel(tc, text="Ngày", text_color=MUTED, font=self.f_sm).pack(side="left", padx=(0, 6))
        today = date.today().isoformat()
        self._log_day_map = {self._day_label(today): today}
        self.log_day = ctk.StringVar(value=self._day_label(today))
        self.log_day_menu = ctk.CTkOptionMenu(tc, values=list(self._log_day_map), variable=self.log_day,
                                              width=170, height=30, font=self.f_sm,
                                              command=lambda v: self._render_toollog())
        self.log_day_menu.pack(side="left")
        self.log_only_bad = ctk.CTkSwitch(tc, text="Chỉ hiện lỗi & cảnh báo", font=self.f_sm,
                                          command=self._render_toollog)
        self.log_only_bad.pack(side="left", padx=16)
        ctk.CTkButton(tc, text="Mở thư mục log", width=120, height=30, font=self.f_xs, fg_color=CARD,
                      hover_color=CARD_HI, text_color=TXT, command=self._open_log_dir).pack(side="right")
        self.toollog_count = ctk.CTkLabel(tc, text="", text_color=FAINT, font=self.f_xs)
        self.toollog_count.pack(side="right", padx=10)
        self.toollog = ctk.CTkTextbox(vc, fg_color=LOGBG, text_color="#CBD3DE", font=self.f_mono,
                                      border_width=0, wrap="word")
        self.toollog.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        for tag, col in [("error", RED), ("warn", AMBER), ("ok", "#7FD6A4"), ("head", BLUE),
                         ("info", "#CBD3DE")]:
            self.toollog.tag_config(tag, foreground=col)
        self.toollog.configure(state="disabled")
        self._toollog_empty = True

        self._mon_tab = "issues"
        self._mon_show("issues", render=False)

    def _mon_show(self, key: str, render: bool = True):
        self._mon_tab = key
        for k, v in self.mon_views.items():
            if k == key:
                v.grid(row=0, column=0, sticky="nsew")
            else:
                v.grid_remove()
        for k, b in self.mon_tabs.items():
            on = k == key
            b.configure(fg_color=ACCENT if on else CARD, text_color="#FFFFFF" if on else MUTED,
                        hover_color=ACCENT_HOVER if on else CARD_HI)
        if render:
            if key == "issues":
                self._render_issues()
            elif key == "history":
                self._render_history()
            else:
                self._refresh_log_days()
                self._render_toollog()

    def _load_monitor(self):
        """Đọc lại thống kê + lịch sử theo khoảng thời gian đang chọn rồi vẽ mục đang mở."""
        since = monitor.since_day(self._RANGES.get(self.mon_range.get(), 0))
        st: dict = {}
        try:
            store = Store()
            try:
                st = store.session_stats(since)
                self._hist_rows = store.session_history(since, limit=500)
            finally:
                store.close()
        except Exception as e:  # noqa: BLE001
            self._hist_rows = []
            self._append_log(f"✖ Không đọc được lịch sử phiên: {e}")
        self.mst_ok.configure(text=str(st.get("ok", 0)))
        self.mst_flag.configure(text=str(st.get("flagged", 0)))
        self.mst_err.configure(text=str(st.get("error", 0) + st.get("aborted", 0)))
        self.mst_skip.configure(text=str(st.get("skip", 0)))
        self.mst_act.configure(text=str(st.get("actions_ok", 0)))
        self._refresh_issues(render=False)
        self._mon_show(self._mon_tab)

    # ---- A) Cần xử lý ----
    def _render_issues(self):
        for w in self.mon_issues.winfo_children():
            w.destroy()
        if not self._issues:
            box = ctk.CTkFrame(self.mon_issues, fg_color=CARD, corner_radius=12)
            box.pack(fill="x", pady=6, padx=(0, 4))
            ctk.CTkLabel(box, text="✓  Mọi thứ đang ổn", font=self.f_h2, text_color=GREEN).pack(pady=(28, 4))
            ctk.CTkLabel(box, text="Không có mail nào gặp lỗi hay cần bạn xử lý.", font=self.f_sm,
                         text_color=MUTED).pack(pady=(0, 28))
            return
        for it in self._issues:
            self._issue_card(it)

    def _issue_card(self, it: dict):
        color = {"high": RED, "medium": AMBER}.get(it["sev"], BLUE)
        card = ctk.CTkFrame(self.mon_issues, fg_color=CARD, corner_radius=12)
        card.pack(fill="x", pady=5, padx=(0, 4))
        card.grid_columnconfigure(1, weight=1)
        ctk.CTkFrame(card, width=6, height=10, fg_color=color, corner_radius=3).grid(
            row=0, column=0, sticky="ns", padx=(10, 0), pady=12)

        info = ctk.CTkFrame(card, fg_color="transparent")
        info.grid(row=0, column=1, sticky="ew", padx=14, pady=10)
        ctk.CTkLabel(info, text=it["title"], font=self.f_h2, text_color=color, anchor="w").pack(anchor="w")
        meta = []
        if it.get("name"):
            meta.append(f"Mail: {it['name']}")
        if it.get("group"):
            meta.append(f"Nhóm: {it['group']}")
        if it.get("at"):
            meta.append(monitor.ago(it["at"]))
        if meta:
            ctk.CTkLabel(info, text="   ·   ".join(meta), font=self.f_xs, text_color=TXT,
                         anchor="w").pack(anchor="w", pady=(2, 0))
        det = (it.get("detail") or "").strip().splitlines()
        if det:
            ctk.CTkLabel(info, text="Lý do: " + _clip(det[0], 200), font=self.f_xs, text_color=FAINT,
                         anchor="w", justify="left", wraplength=560).pack(anchor="w", pady=(2, 0))
        if it.get("hint"):
            ctk.CTkLabel(info, text="→ " + it["hint"], font=self.f_xs, text_color=MUTED, anchor="w",
                         justify="left", wraplength=560).pack(anchor="w", pady=(4, 0))

        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=0, column=2, sticky="e", padx=(4, 14), pady=10)

        def mk(text, cmd, primary=False):
            ctk.CTkButton(btns, text=text, width=156, height=32, font=self.f_xs,
                          fg_color=ACCENT if primary else "transparent",
                          hover_color=ACCENT_HOVER if primary else CARD_HI,
                          text_color="#FFFFFF" if primary else TXT, border_width=0 if primary else 1,
                          border_color=BORDER, command=cmd).pack(pady=3)

        act = it.get("action")
        if act == "login":
            mk("Kiểm tra lại", lambda: self._issue_recheck(it), primary=True)
            mk("Tôi đã đăng nhập lại", lambda: self._issue_login_done(it))
        elif act == "recheck":
            mk("Kiểm tra lại", lambda: self._issue_recheck(it), primary=True)
        elif act == "ack":
            mk("Đã hiểu", lambda: self._issue_ack(it))
        elif act == "retry":
            mk("Thử lại", lambda: self._refresh_all(quiet=False), primary=True)
        elif act == "groups":
            mk("Mở tab Nhóm mail", lambda: self.show_page("groups"))

    def _issue_recheck(self, it: dict):
        """Mở lại đúng mail đó (chỉ đọc cookie) — mở được thì tự gỡ 'Lỗi mở'."""
        if it.get("pid"):
            self._check_logins(only_ids=[it["pid"]])

    def _issue_login_done(self, it: dict):
        if not messagebox.askyesno("Gỡ trạng thái “Cần đăng nhập”",
                                   f"Bạn đã đăng nhập lại Google cho mail này trong GPM chưa?\n\n"
                                   f"• {it.get('name')}\n\nBấm Có để tool nuôi lại mail này."):
            return
        store = Store()
        try:
            store.clear_flags(it["pid"])
        finally:
            store.close()
        self._shared_clear_flags([it["pid"]])
        self.toast(f"Đã gỡ “Cần đăng nhập” cho {it.get('name')}.")
        self._append_log(f"🔑 Gỡ cờ đăng nhập cho {it.get('name')}.")
        if self._gpm_ok:
            self._load_profiles()
        self._load_monitor()

    def _issue_ack(self, it: dict):
        store = Store()
        try:
            store.add_event("ack_emergency", "Người dùng đã xem thông báo dừng khẩn", "info")
        finally:
            store.close()
        self._refresh_issues()

    # ---- B) Lịch sử phiên ----
    def _render_history(self):
        for w in self.hist_body.winfo_children():
            w.destroy()
        self._hist_more_btn = None
        allowed = self._HIST_FILTER.get(self.hist_filter.get())
        self._hist_filtered = [s for s in self._hist_rows if allowed is None or s["status"] in allowed]
        self.hist_count.configure(text=f"{len(self._hist_filtered)} phiên")
        self._hist_shown = 0
        if not self._hist_filtered:
            ctk.CTkLabel(self.hist_body, text="Chưa có phiên nào trong khoảng thời gian này.",
                         text_color=FAINT, font=self.f_sm).pack(pady=28)
            return
        self._append_hist_rows()

    def _append_hist_rows(self):
        if self._hist_more_btn is not None:
            self._hist_more_btn.destroy()
            self._hist_more_btn = None
        with_date = self.mon_range.get() != "Hôm nay"
        chunk = self._hist_filtered[self._hist_shown:self._hist_shown + self._HIST_PAGE]
        for s in chunk:
            self._hist_row(s, with_date)
        self._hist_shown += len(chunk)
        remain = len(self._hist_filtered) - self._hist_shown
        if remain > 0:
            self._hist_more_btn = ctk.CTkButton(self.hist_body, text=f"Hiện thêm ({remain} phiên)", width=180,
                                                height=32, font=self.f_xs, fg_color=CARD_HI,
                                                hover_color=BORDER, text_color=TXT,
                                                command=self._append_hist_rows)
            self._hist_more_btn.pack(pady=8)

    def _session_desc(self, s: dict) -> str:
        acts = s.get("actions") or []
        err = (s.get("error") or "").strip().splitlines()
        parts = [f"{'✓' if a['ok'] else '✗'} {self._ACT_SHORT.get(a['kind'], a['kind'])}" for a in acts]
        txt = "  ".join(parts)
        if err and s.get("status") != "ok":
            txt = f"{txt}  ·  {err[0]}" if txt else err[0]
        if not txt:
            txt = "đang chạy…" if s.get("status") == "running" else "không có việc nào"
        return _clip(txt, 58)

    def _hist_row(self, s: dict, with_date: bool):
        w = [c[1] for c in self._HIST_COLS]
        fr = ctk.CTkFrame(self.hist_body, fg_color=CARD, corner_radius=7)
        fr.pack(fill="x", pady=1)
        txt, fg, bg = self._ST.get(s["status"], (s["status"], MUTED, CARD_HI))
        ctk.CTkLabel(fr, text=monitor.fmt_time(s["started_at"], with_date), width=w[0], anchor="w",
                     text_color=MUTED, font=self.f_xs).grid(row=0, column=0, padx=(12, 4), pady=6)
        ctk.CTkLabel(fr, text=_clip(s.get("profile_name") or s["profile_id"], 30), width=w[1], anchor="w",
                     text_color=TXT, font=self.f_sm).grid(row=0, column=1, padx=4, pady=6)
        pw = ctk.CTkFrame(fr, width=w[2], height=26, fg_color="transparent")
        pw.grid(row=0, column=2, padx=4, pady=5)
        pw.grid_propagate(False)
        ctk.CTkLabel(pw, text=txt, fg_color=bg, text_color=fg, corner_radius=11, font=self.f_xs,
                     width=124 if len(txt) > 12 else 96, height=24).place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(fr, text=monitor.duration(s["started_at"], s.get("ended_at")), width=w[3],
                     anchor="center", text_color=MUTED, font=self.f_xs).grid(row=0, column=3, padx=4, pady=6)
        ctk.CTkLabel(fr, text=self._session_desc(s), width=w[4], anchor="w", text_color=MUTED,
                     font=self.f_xs).grid(row=0, column=4, padx=4, pady=6)

        def walk(wd):
            yield wd
            for c in wd.winfo_children():
                yield from walk(c)

        for wd in walk(fr):
            wd.bind("<Button-1>", lambda e, ss=s: self._show_session(ss))
        fr.bind("<Enter>", lambda e: fr.configure(fg_color=CARD_HI))
        fr.bind("<Leave>", lambda e: fr.configure(fg_color=CARD))

    def _show_session(self, s: dict):
        name = s.get("profile_name") or s["profile_id"]
        win = ctk.CTkToplevel(self)
        win.title(f"Chi tiết phiên — {name}")
        win.geometry("660x560")
        win.minsize(560, 440)
        win.configure(fg_color=BG_MAIN)
        win.after(250, lambda: self._set_icon(win))
        win.after(60, win.lift)
        win.after(120, win.grab_set)
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(win, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        ctk.CTkLabel(head, text=name, font=self.f_h2, text_color=TXT).pack(anchor="w")
        line = ctk.CTkFrame(head, fg_color="transparent")
        line.pack(anchor="w", pady=(6, 0))
        txt, fg, bg = self._ST.get(s["status"], (s["status"], MUTED, CARD_HI))
        ctk.CTkLabel(line, text=txt, fg_color=bg, text_color=fg, corner_radius=11, font=self.f_xs,
                     width=124 if len(txt) > 12 else 100, height=24).pack(side="left")
        end = monitor.fmt_time(s["ended_at"]) if s.get("ended_at") else "đang chạy"
        ctk.CTkLabel(line, text=f"{monitor.fmt_time(s['started_at'], True)} → {end}   ·   "
                                f"{monitor.duration(s['started_at'], s.get('ended_at'))}   ·   "
                                f"ID {s['profile_id']}",
                     font=self.f_xs, text_color=MUTED).pack(side="left", padx=10)

        body = ctk.CTkScrollableFrame(win, fg_color=CARD, corner_radius=12)
        body.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 8))
        self._field_title(body, "Việc đã làm trong phiên")
        acts = s.get("actions") or []
        if not acts:
            ctk.CTkLabel(body, text="Không có việc nào được thực hiện trong phiên này.", font=self.f_xs,
                         text_color=FAINT).pack(anchor="w", padx=16, pady=(0, 6))
        for a in acts:
            r = ctk.CTkFrame(body, fg_color="transparent")
            r.pack(fill="x", padx=16, pady=3)
            ok = bool(a.get("ok"))
            ctk.CTkLabel(r, text=("✓  " if ok else "✗  ") + self._ACT_SHORT.get(a["kind"], a["kind"]),
                         width=104, anchor="w", text_color=GREEN if ok else RED,
                         font=self.f_sm).pack(side="left", anchor="n")
            ctk.CTkLabel(r, text=f"{monitor.fmt_time(a.get('at', ''))}   {a.get('detail') or ''}",
                         anchor="w", justify="left", wraplength=450, text_color=MUTED,
                         font=self.f_xs).pack(side="left", fill="x")
        if s.get("error"):
            self._field_title(body, "Lý do / lỗi", "Có thể bôi đen để sao chép gửi cho người hỗ trợ.")
            eb = ctk.CTkTextbox(body, height=96, fg_color=BG_SIDE, border_width=1, border_color=BORDER,
                                font=self.f_mono, wrap="word")
            eb.pack(fill="x", padx=16, pady=(0, 8))
            eb.insert("1.0", s["error"])
            eb.configure(state="disabled")
        try:
            store = Store()
            try:
                j = store.journal_for(s["profile_id"], s["day"])
            finally:
                store.close()
        except Exception:
            j = None
        if j and (j.get("summary") or j.get("routine")):
            self._field_title(body, "Nhật ký công việc của mail hôm đó")
            ctk.CTkLabel(body, text=f"Trình tự: {j.get('routine') or '—'}\nTóm tắt: {j.get('summary') or '—'}",
                         font=self.f_xs, text_color=MUTED, anchor="w", justify="left",
                         wraplength=560).pack(anchor="w", padx=16, pady=(0, 10))

        foot = ctk.CTkFrame(win, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 16))
        ctk.CTkButton(foot, text="Đóng", width=100, height=36, font=self.f_sm, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, command=win.destroy).pack(side="right")
        ctk.CTkButton(foot, text="Xem nhật ký tool ngày này", width=190, height=36, font=self.f_sm,
                      fg_color="transparent", border_width=1, border_color=BORDER, text_color=TXT,
                      hover_color=CARD,
                      command=lambda: (win.destroy(), self._goto_log_day(s["day"]))).pack(side="right", padx=8)

    # ---- C) Nhật ký tool ----
    @staticmethod
    def _day_label(iso: str) -> str:
        try:
            d = date.fromisoformat(iso)
        except Exception:
            return iso
        return d.strftime("%d/%m/%Y") + ("  (hôm nay)" if d == date.today() else "")

    def _cur_log_day(self) -> str:
        return self._log_day_map.get(self.log_day.get(), date.today().isoformat())

    def _refresh_log_days(self):
        cur = self._cur_log_day()
        days = activity_log.days()
        today = date.today().isoformat()
        if today not in days:
            days.insert(0, today)
        self._log_day_map = {self._day_label(d): d for d in days}
        self.log_day_menu.configure(values=list(self._log_day_map))
        self.log_day.set(self._day_label(cur if cur in days else today))

    def _render_toollog(self):
        lines = activity_log.read(self._cur_log_day())[-4000:]
        only_bad = bool(self.log_only_bad.get())
        tb = self.toollog
        tb.configure(state="normal")
        tb.delete("1.0", "end")
        n = 0
        for ln in lines:
            lv = activity_log.level(ln)
            if only_bad and lv not in ("error", "warn"):
                continue
            tb.insert("end", ln + "\n", lv)
            n += 1
        self._toollog_empty = n == 0
        if not n:
            tb.insert("end", "(Chưa có dòng nào" + (" là lỗi/cảnh báo" if only_bad else "") + ")\n", "info")
        tb.configure(state="disabled")
        tb.see("end")
        self.toollog_count.configure(text=f"{n} dòng")

    def _toollog_insert(self, line: str):
        lv = activity_log.level(line)
        if self.log_only_bad.get() and lv not in ("error", "warn"):
            return
        tb = self.toollog
        tb.configure(state="normal")
        if self._toollog_empty:
            tb.delete("1.0", "end")
            self._toollog_empty = False
        tb.insert("end", line + "\n", lv)
        tb.see("end")
        tb.configure(state="disabled")

    def _goto_log_day(self, iso: str):
        self._refresh_log_days()
        label = self._day_label(iso)
        if label not in self._log_day_map:          # ngày đó chưa có file log -> vẫn cho chọn (sẽ trống)
            self._log_day_map[label] = iso
            self.log_day_menu.configure(values=list(self._log_day_map))
        self.log_day.set(label)
        self._mon_show("log", render=False)
        self._render_toollog()

    def _open_log_dir(self):
        try:
            activity_log.LOG_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(activity_log.LOG_DIR))
        except Exception:
            self.toast("Không mở được thư mục log", "err")

    # ================= tab Nhóm =================
    def _build_groups(self):
        page = ctk.CTkFrame(self.content, fg_color=BG_MAIN, corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        self.pages["groups"] = page

        head = ctk.CTkFrame(page, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(20, 4))
        ctk.CTkLabel(head, text="Nhóm mail", font=self.f_h1, text_color=TXT).pack(side="left")
        ctk.CTkButton(head, text="⟳  Làm mới", width=100, height=34, font=self.f_sm, fg_color=CARD,
                      hover_color=CARD_HI, text_color=TXT, command=self._load_groups).pack(side="right")

        ctk.CTkLabel(page, text="Chọn chế độ cho từng nhóm GPM — thay đổi được LƯU TỰ ĐỘNG.",
                     font=self.f_xs, text_color=MUTED).pack(anchor="w", padx=24)
        legend = ctk.CTkFrame(page, fg_color="transparent")
        legend.pack(anchor="w", padx=22, pady=(8, 6))
        for mode, desc in [("nurture", "được tool tương tác"), ("locked", "nhóm kênh — KHÔNG BAO GIỜ đụng"),
                           ("later", "tạm chưa nuôi")]:
            ctk.CTkLabel(legend, text=PILL_TXT[mode], fg_color=PILL_BG[mode], text_color=PILL_FG[mode],
                         corner_radius=10, font=self.f_xs, width=64, height=24).pack(side="left", padx=(2, 6))
            ctk.CTkLabel(legend, text=desc, text_color=MUTED, font=self.f_xs).pack(side="left", padx=(0, 18))

        self.groups_area = ctk.CTkScrollableFrame(page, fg_color="transparent")
        self.groups_area.pack(fill="both", expand=True, padx=18, pady=(4, 16))
        ctk.CTkLabel(self.groups_area, text="Đang tải danh sách nhóm…", text_color=FAINT,
                     font=self.f_sm).pack(pady=28)

    def _load_groups(self):
        try:
            gs = self.engine.list_groups_with_mode()
        except GPMError as e:
            self.toast("Không tải được danh sách nhóm — kiểm tra GPM.", "err")
            self._append_log(f"✖ {e}")
            return
        for w in self.groups_area.winfo_children():
            w.destroy()
        self.group_mode_vars = {}
        for g in gs:
            cur_label = self.SEG_LABELS.get(g["mode"], "Để sau")
            color = self.MODE_HEX.get(cur_label, SLATE)

            row = ctk.CTkFrame(self.groups_area, fg_color=CARD, corner_radius=12)
            row.pack(fill="x", pady=6)
            row.grid_columnconfigure(1, weight=1)
            bar = ctk.CTkFrame(row, width=6, height=10, fg_color=color, corner_radius=3)
            bar.grid(row=0, column=0, sticky="ns", padx=(10, 0), pady=12)

            info = ctk.CTkFrame(row, fg_color="transparent")
            info.grid(row=0, column=1, sticky="w", padx=16, pady=14)
            ctk.CTkLabel(info, text=g["name"], font=self.f_h2, text_color=TXT, anchor="w").pack(anchor="w")
            ctk.CTkLabel(info, text=f"{g['count']} mail  ·  Thị trường: {g['market']}", font=self.f_xs,
                         text_color=FAINT, anchor="w").pack(anchor="w", pady=(2, 0))

            var = ctk.StringVar(value=cur_label)
            seg = ctk.CTkSegmentedButton(
                row, values=["Nuôi", "Khóa", "Để sau"], variable=var, width=240, height=36,
                font=self.f_sm, fg_color=BG_SIDE, unselected_color=BG_SIDE,
                unselected_hover_color=CARD_HI, text_color=TXT,
                selected_color=color, selected_hover_color=color)
            seg.grid(row=0, column=2, padx=6)
            seg.configure(command=lambda v, s=seg, b=bar, n=g["name"]: self._on_group_mode(n, v, s, b))
            self.group_mode_vars[g["name"]] = var

            ctk.CTkButton(row, text="Tuỳ chỉnh nhóm", width=140, height=36, corner_radius=8, font=self.f_sm,
                          fg_color="transparent", border_width=1, border_color=BORDER,
                          text_color=TXT, hover_color=CARD_HI,
                          command=lambda n=g["name"]: self._edit_group_behavior(n)).grid(
                row=0, column=3, padx=(8, 16))

    def _on_group_mode(self, name, value, seg, bar):
        """Đổi màu theo nghĩa + TỰ LƯU ngay."""
        cfg = self._load_cfg_file()
        if cfg is None:
            self._load_groups()                 # trả nút về chế độ đang lưu thật
            return
        color = self.MODE_HEX.get(value, SLATE)
        seg.configure(selected_color=color, selected_hover_color=color)
        bar.configure(fg_color=color)
        items = self._group_items(cfg)
        entry = items.get(name) if isinstance(items.get(name), dict) else {}
        entry["mode"] = self.SEG2MODE.get(value, "later")
        items[name] = entry
        if self._write_cfg_file(cfg):
            self.toast(f"Đã lưu: nhóm “{name}” → {value}", "warn" if value == "Khóa" else "ok")
            self._append_log(f"💾 Nhóm '{name}' → {value}")
            if self._gpm_ok:
                self._load_profiles()

    def _save_groups(self):
        cfg = self._load_cfg_file()
        if cfg is None:
            return
        items = self._group_items(cfg)
        for name, var in self.group_mode_vars.items():
            entry = items.get(name) if isinstance(items.get(name), dict) else {}
            entry["mode"] = self.SEG2MODE.get(var.get(), "later")
            items[name] = entry
        if self._write_cfg_file(cfg):
            self.toast("Đã lưu chế độ các nhóm.")

    def _current_overrides(self, name: str) -> dict | None:
        cfg = self._load_cfg_file()
        if cfg is None:
            return None
        entry = self._group_items(cfg).get(name)
        ov = entry.get("overrides") if isinstance(entry, dict) else None
        return ov if isinstance(ov, dict) else {}

    def _write_group_overrides(self, name: str, overrides: dict) -> bool:
        cfg = self._load_cfg_file()
        if cfg is None:
            return False
        items = self._group_items(cfg)
        entry = items.get(name) if isinstance(items.get(name), dict) else {}
        if overrides:
            entry["overrides"] = overrides
        else:
            entry.pop("overrides", None)
        items[name] = entry
        if self._write_cfg_file(cfg):
            self._append_log(f"💾 Lưu tuỳ chỉnh nhóm '{name}'.")
            return True
        return False

    def _current_market(self, name: str) -> str:
        cfg = self._load_cfg_file()
        if cfg is None:
            return ""
        entry = self._group_items(cfg).get(name)
        return entry.get("market", "") if isinstance(entry, dict) else ""

    def _write_group_market(self, name: str, key: str) -> bool:
        cfg = self._load_cfg_file()
        if cfg is None:
            return False
        items = self._group_items(cfg)
        entry = items.get(name) if isinstance(items.get(name), dict) else {}
        if key and key != "vi":
            entry["market"] = key
        else:
            entry.pop("market", None)
        items[name] = entry
        return self._write_cfg_file(cfg)

    # ---- hộp "Tuỳ chỉnh nhóm" (chia tab, nút Lưu luôn hiện) ----
    def _field_title(self, parent, title, desc=""):
        ctk.CTkLabel(parent, text=title, font=self.f_sm, text_color=TXT, anchor="w").pack(
            anchor="w", padx=16, pady=(14, 0))
        if desc:
            ctk.CTkLabel(parent, text=desc, font=self.f_xs, text_color=FAINT, anchor="w", justify="left",
                         wraplength=520).pack(anchor="w", padx=16, pady=(2, 6))

    def _edit_group_behavior(self, name: str):
        ov = self._current_overrides(name)
        if ov is None:                       # config.yaml lỗi -> đã báo, không mở hộp sửa
            return
        try:
            base = load_config().raw
        except Exception as e:  # noqa: BLE001
            messagebox.showerror("Cấu hình bị lỗi", str(e))
            return
        win = ctk.CTkToplevel(self)
        win.title(f"Tuỳ chỉnh nhóm — {name}")
        win.geometry("620x640")
        win.minsize(560, 560)
        win.configure(fg_color=BG_MAIN)
        win.after(250, lambda: self._set_icon(win))
        win.after(60, win.lift)
        win.after(120, win.grab_set)
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(win, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 6))
        ctk.CTkLabel(head, text=f"Tuỳ chỉnh nhóm: {name}", font=self.f_h2, text_color=TXT).pack(anchor="w")
        ctk.CTkLabel(head, text="Chỉ áp dụng cho nhóm này. Ô để trống = dùng cấu hình chung.",
                     font=self.f_xs, text_color=MUTED).pack(anchor="w", pady=(2, 0))

        tabs = ctk.CTkTabview(win, fg_color=CARD, corner_radius=12, text_color=TXT,
                              segmented_button_fg_color=BG_SIDE,
                              segmented_button_selected_color=ACCENT,
                              segmented_button_selected_hover_color=ACCENT_HOVER,
                              segmented_button_unselected_color=BG_SIDE,
                              segmented_button_unselected_hover_color=CARD_HI)
        tabs.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 8))
        t_basic = tabs.add("Cơ bản")
        t_task = tabs.add("Nhiệm vụ chỉ định")
        t_content = tabs.add("Nội dung")
        t_adv = tabs.add("Nâng cao")

        # --- Cơ bản ---
        self._field_title(t_basic, "Thị trường / ngôn ngữ",
                          "Quyết định ngôn ngữ nhật ký, từ khoá YouTube, trang báo và vùng YouTube. "
                          "Ngoài tiếng Việt cần bật 9router (AI) để viết nhật ký.")
        _m2k = {n: k for k, n in markets.options()}
        _k2m = {k: n for k, n in markets.options()}
        mvar = ctk.StringVar(value=_k2m.get(self._current_market(name) or "vi", _k2m["vi"]))
        ctk.CTkOptionMenu(t_basic, values=list(_m2k.keys()), variable=mvar, width=300, height=34,
                          font=self.f_sm).pack(anchor="w", padx=16)
        self._field_title(t_basic, "Khởi động dần (warm-up)",
                          "Mail mới đi nhẹ rồi tăng dần. “Theo chung” = dùng thiết lập ở tab Cấu hình.")
        wm = ctk.StringVar(value="Theo chung")
        if isinstance(ov.get("warmup"), dict) and "enabled" in ov["warmup"]:
            wm.set("Bật" if ov["warmup"]["enabled"] else "Tắt")
        ctk.CTkSegmentedButton(t_basic, values=["Theo chung", "Bật", "Tắt"], variable=wm, height=34,
                               font=self.f_sm, text_color=TXT).pack(anchor="w", padx=16)

        # --- Nhiệm vụ chỉ định ---
        cust = ov.get("custom") or {}
        self._field_title(t_task, "Website ghé thăm",
                          "Mỗi dòng 1 địa chỉ. Mail sẽ thỉnh thoảng ghé vào, cuộn xem như người thật. "
                          "Ví dụ: https://blog-cua-ban.com")
        site_text = ctk.CTkTextbox(t_task, height=84, fg_color=BG_SIDE, border_width=1, border_color=BORDER)
        site_text.pack(fill="x", padx=16)
        site_text.insert("1.0", "\n".join(cust.get("sites", [])))
        self._field_title(t_task, "Mức độ ghé website", "Kéo sang phải để ghé thường xuyên hơn. Kéo về 0 = tắt.")
        vrow = ctk.CTkFrame(t_task, fg_color="transparent")
        vrow.pack(fill="x", padx=16)
        vf = ctk.DoubleVar(value=float((ov.get("action_weights") or {}).get("visit", 15)))
        vlbl = ctk.CTkLabel(vrow, text=level_word(vf.get() * 2), width=60, text_color=GREEN, font=self.f_sm)
        ctk.CTkSlider(vrow, from_=0, to=50, number_of_steps=50, variable=vf, width=340,
                      command=lambda x: vlbl.configure(text=level_word(float(x) * 2))).pack(side="left")
        vlbl.pack(side="left", padx=10)
        self._field_title(t_task, "Từ khoá YouTube chỉ định",
                          "Mỗi dòng 1 từ khoá. Thỉnh thoảng mail sẽ tìm đúng các từ khoá này. "
                          "Ví dụ: review sản phẩm X")
        q_text = ctk.CTkTextbox(t_task, height=84, fg_color=BG_SIDE, border_width=1, border_color=BORDER)
        q_text.pack(fill="x", padx=16, pady=(0, 12))
        q_text.insert("1.0", "\n".join(cust.get("youtube_queries", [])))

        # --- Nội dung ---
        cscroll = ctk.CTkScrollableFrame(t_content, fg_color="transparent")
        cscroll.pack(fill="both", expand=True)
        self._field_title(cscroll, "Sở thích của nhóm",
                          "Nhóm sẽ xem YouTube / đọc báo thiên về các mảng được chọn. Không chọn = ngẫu nhiên.")
        grid = ctk.CTkFrame(cscroll, fg_color="transparent")
        grid.pack(fill="x", padx=16)
        cur_i = set(ov.get("interests", []))
        ivars = {}
        for idx, it in enumerate(INTEREST_POOLS.keys()):
            v = ctk.BooleanVar(value=it in cur_i)
            ctk.CTkCheckBox(grid, text=it, variable=v, font=self.f_sm, checkbox_width=20,
                            checkbox_height=20).grid(row=idx // 3, column=idx % 3, sticky="w", padx=6, pady=5)
            ivars[it] = v
        self._field_title(cscroll, "Trang báo riêng",
                          "Mỗi dòng 1 địa chỉ báo. Để trống = dùng báo theo thị trường của nhóm.")
        ntext = ctk.CTkTextbox(cscroll, height=84, fg_color=BG_SIDE, border_width=1, border_color=BORDER)
        ntext.pack(fill="x", padx=16, pady=(0, 12))
        ntext.insert("1.0", "\n".join((ov.get("news") or {}).get("sites", [])))

        # --- Nâng cao ---
        self._field_title(t_adv, "Mức ưu tiên hoạt động riêng",
                          "Bật để nhóm này dùng mức ưu tiên khác cấu hình chung. Kéo về 0 = không làm việc đó.")
        w_on = ctk.CTkSwitch(t_adv, text="Dùng mức ưu tiên riêng cho nhóm này", font=self.f_sm)
        w_on.pack(anchor="w", padx=16, pady=(0, 6))
        if "action_weights" in ov and any(k in ov["action_weights"] for k in ACT_VI):
            w_on.select()
        cur_w = ov.get("action_weights", {})
        wvars = {}
        adv_sliders: list = []
        for a in ("youtube", "news", "gmail", "diary"):
            default = cur_w.get(a, _dig(base, f"action_weights.{a}", 0))
            wvars[a] = self._slider_row(t_adv, ACT_VI[a], float(default), store=adv_sliders)

        def sync_adv():
            st = "normal" if w_on.get() else "disabled"
            for s in adv_sliders:
                s.configure(state=st)

        w_on.configure(command=sync_adv)
        sync_adv()

        # --- Chân hộp: nút luôn hiện ---
        foot = ctk.CTkFrame(win, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=20, pady=(0, 16))

        def save():
            new_ov: dict = {}
            if w_on.get():
                new_ov["action_weights"] = {a: int(v.get()) for a, v in wvars.items()}
            chosen = [it for it, v in ivars.items() if v.get()]
            if chosen:
                new_ov["interests"] = chosen
            sites = [s.strip() for s in ntext.get("1.0", "end").splitlines() if s.strip()]
            if sites:
                new_ov["news"] = {"sites": sites}
            if wm.get() != "Theo chung":
                new_ov["warmup"] = {"enabled": wm.get() == "Bật"}
            sites_c = [s.strip() for s in site_text.get("1.0", "end").splitlines() if s.strip()]
            # Bỏ dấu phẩy thừa / tách nhiều từ khoá trên 1 dòng (vd "lịch sử, người cổ đại,").
            from src.actions.youtube import clean_queries
            queries_c = clean_queries(q_text.get("1.0", "end").splitlines())
            custom_c: dict = {}
            if sites_c:
                custom_c["sites"] = sites_c
                new_ov.setdefault("action_weights", {})["visit"] = int(vf.get())
            if queries_c:
                custom_c["youtube_queries"] = queries_c
            if custom_c:
                new_ov["custom"] = custom_c
            if not self._write_group_market(name, _m2k.get(mvar.get(), "vi")):
                return                           # lỗi đã được báo; giữ hộp mở để không mất dữ liệu nhập
            if not self._write_group_overrides(name, new_ov):
                return
            win.destroy()
            self.toast(f"Đã lưu tuỳ chỉnh cho nhóm “{name}”.")
            if self._gpm_ok:
                self._load_groups()

        ctk.CTkButton(foot, text="Lưu thay đổi", width=150, height=38, font=self.f_sm, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, command=save).pack(side="right")
        ctk.CTkButton(foot, text="Huỷ", width=90, height=38, font=self.f_sm, fg_color="transparent",
                      border_width=1, border_color=BORDER, text_color=TXT, hover_color=CARD,
                      command=win.destroy).pack(side="right", padx=8)

    def _slider_row(self, parent, label, value, store: list | None = None) -> ctk.DoubleVar:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=16, pady=5)
        ctk.CTkLabel(row, text=label, width=120, anchor="w", text_color=TXT, font=self.f_sm).pack(side="left")
        var = ctk.DoubleVar(value=value)
        lbl = ctk.CTkLabel(row, text=f"{level_word(value)} ({int(value)})", width=90, anchor="w",
                           text_color=GREEN, font=self.f_xs)
        sl = ctk.CTkSlider(row, from_=0, to=100, number_of_steps=100, variable=var, width=300,
                           command=lambda x: lbl.configure(text=f"{level_word(x)} ({int(float(x))})"))
        sl.pack(side="left")
        lbl.pack(side="left", padx=10)
        if store is not None:
            store.append(sl)
        return var

    def _set_icon(self, win):
        try:
            win.iconbitmap(str(ICON_PATH))
        except Exception:
            pass

    # ================= tab Cấu hình =================
    def _build_config(self):
        page = ctk.CTkFrame(self.content, fg_color=BG_MAIN, corner_radius=0)
        page.grid(row=0, column=0, sticky="nsew")
        self.pages["config"] = page

        head = ctk.CTkFrame(page, fg_color="transparent")
        head.pack(fill="x", padx=24, pady=(20, 6))
        ctk.CTkLabel(head, text="Cấu hình", font=self.f_h1, text_color=TXT).pack(side="left")
        ctk.CTkButton(head, text="Lưu cấu hình", width=140, height=36, font=self.f_sm, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, command=self._save_config).pack(side="right")
        self.btn_conn = ctk.CTkButton(head, text="Kiểm tra kết nối", width=150, height=36, font=self.f_sm,
                                      fg_color="transparent", border_width=1, border_color=BORDER,
                                      text_color=TXT, hover_color=CARD, command=self._test_connections)
        self.btn_conn.pack(side="right", padx=8)

        scroll = ctk.CTkScrollableFrame(page, fg_color="transparent")
        scroll.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        self.cfg_scroll = scroll
        self.cfg_entries: dict[str, ctk.CTkEntry] = {}
        self._autosave_jobs: dict[str, str] = {}
        self._secret_status: dict[str, ctk.CTkLabel] = {}
        self._stored_secrets: dict[str, str] = {}
        self._cfg_initial: dict = {}
        self._build_conn_card(scroll)
        try:
            cfg = load_config().raw
        except Exception as e:  # noqa: BLE001 — config lỗi: vẫn mở app; nút Lưu sẽ tự chặn ghi đè
            cfg = {}
            err_msg = str(e)        # Python xoá biến 'e' khi ra khỏi except -> giữ lại chuỗi lỗi
            self.after(800, lambda: messagebox.showerror(
                "Cấu hình bị lỗi", f"Không đọc được config.yaml — app sẽ KHÔNG lưu đè cho tới khi sửa.\n\n{err_msg}"))
        self.cfg_get: dict = {}

        def section(title, desc=""):
            s = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=12)
            s.pack(fill="x", pady=7)
            if self._cfg_first_section is None:
                self._cfg_first_section = s      # thẻ kết quả kiểm tra kết nối chèn lên trên thẻ này
            ctk.CTkLabel(s, text=title, font=self.f_h2, text_color=TXT).pack(anchor="w", padx=16, pady=(14, 0))
            if desc:
                ctk.CTkLabel(s, text=desc, font=self.f_xs, text_color=FAINT, anchor="w",
                             justify="left", wraplength=760).pack(anchor="w", padx=16, pady=(2, 4))
            return s

        def line(parent, label):
            fr = ctk.CTkFrame(parent, fg_color="transparent")
            fr.pack(fill="x", padx=16, pady=6)
            ctk.CTkLabel(fr, text=label, width=210, anchor="w", text_color=MUTED,
                         font=self.f_sm).pack(side="left")
            return fr

        # --- Nhịp độ nuôi ---
        s1 = section("Nhịp độ nuôi", "Càng chậm, càng an toàn cho tài khoản.")
        conc_opts = ["1 mail mỗi lần (an toàn nhất)", "2 mail mỗi lần", "3 mail mỗi lần"]
        cur = min(3, max(1, int(_dig(cfg, "run.concurrency", 1) or 1)))
        cv = ctk.StringVar(value=conc_opts[cur - 1])
        ctk.CTkOptionMenu(line(s1, "Chạy cùng lúc"), values=conc_opts, variable=cv, width=280,
                          font=self.f_sm).pack(side="left")
        self.cfg_get["run.concurrency"] = lambda: conc_opts.index(cv.get()) + 1

        fr = line(s1, "Số mail nuôi mỗi ngày")
        dv = ctk.DoubleVar(value=round(float(_dig(cfg, "run.daily_fraction", 0.4) or 0.4) * 100))
        dl = ctk.CTkLabel(fr, text=f"{int(dv.get())}% số mail", width=120, anchor="w", text_color=GREEN,
                          font=self.f_sm)
        ctk.CTkSlider(fr, from_=10, to=100, number_of_steps=18, variable=dv, width=280,
                      command=lambda x: dl.configure(text=f"{int(float(x))}% số mail")).pack(side="left")
        dl.pack(side="left", padx=10)
        self.cfg_get["run.daily_fraction"] = lambda: round(dv.get() / 100, 2)

        ah = _dig(cfg, "run.active_hours", [8, 22]) or [8, 22]
        fr = line(s1, "Giờ hoạt động trong ngày")
        hf = ctk.StringVar(value=f"{int(ah[0])}h")
        ht = ctk.StringVar(value=f"{int(ah[1])}h")
        ctk.CTkLabel(fr, text="Từ", text_color=MUTED, font=self.f_sm).pack(side="left", padx=(0, 6))
        ctk.CTkOptionMenu(fr, values=[f"{i}h" for i in range(0, 24)], variable=hf, width=86,
                          font=self.f_sm).pack(side="left")
        ctk.CTkLabel(fr, text="đến", text_color=MUTED, font=self.f_sm).pack(side="left", padx=8)
        ctk.CTkOptionMenu(fr, values=[f"{i}h" for i in range(1, 25)], variable=ht, width=86,
                          font=self.f_sm).pack(side="left")
        self.cfg_get["run.active_hours"] = lambda: [int(hf.get()[:-1]), int(ht.get()[:-1])]

        sw_warm = ctk.CTkSwitch(s1, text="Khởi động dần — mail mới đi nhẹ, tăng dần theo thời gian",
                                font=self.f_sm)
        sw_warm.pack(anchor="w", padx=16, pady=(8, 4))
        if _dig(cfg, "warmup.enabled", True):
            sw_warm.select()
        self.cfg_get["warmup.enabled"] = lambda: bool(sw_warm.get())
        sw_min = ctk.CTkSwitch(s1, text="Chạy ngầm — tự thu nhỏ cửa sổ GPM, không che màn hình",
                               font=self.f_sm)
        sw_min.pack(anchor="w", padx=16, pady=(4, 14))
        if _dig(cfg, "run.minimize_windows", True):
            sw_min.select()
        self.cfg_get["run.minimize_windows"] = lambda: bool(sw_min.get())

        # --- Mức ưu tiên hoạt động ---
        s2 = section("Mức ưu tiên hoạt động",
                     "YouTube > 0 = LUÔN có trong mọi phiên (2–4 video). Báo / Gmail: kéo cao thì hay "
                     "được chọn đi kèm YouTube hơn. Nhật ký > 0 = có viết (≥500 từ, cần 9router). "
                     "Kéo về 0 = tắt việc đó. (Mỗi nhóm có thể chỉnh riêng trong “Tuỳ chỉnh nhóm”.)")
        for a in ("youtube", "news", "gmail", "diary"):
            var = self._slider_row(s2, ACT_VI[a], float(_dig(cfg, f"action_weights.{a}", 0) or 0))
            self.cfg_get[f"action_weights.{a}"] = lambda v=var: int(v.get())
        ctk.CTkLabel(s2, text="").pack(pady=2)

        # --- AI & YouTube ---
        s3 = section("Nội dung AI & YouTube",
                     "Nhật ký tiếng Việt chạy được ngay với “Mẫu có sẵn”. Ngôn ngữ khác cần 9router (AI). "
                     "Token, key và nguồn viết TỰ LƯU ngay khi dán/chọn — tắt app mở lại vẫn còn.")
        prov_map = dict(PROVIDERS)
        prov_inv = {v: k for k, v in PROVIDERS}
        cur_prov = str(_dig(cfg, "content.provider", "template") or "template")
        # Tên cũ/đồng nghĩa trong file (ninerouter/openai) đều là 9router.
        shown_key = {"ninerouter": "9router", "openai": "9router"}.get(cur_prov, cur_prov)
        init_label = prov_map.get(shown_key, prov_map["template"])
        pv = ctk.StringVar(value=init_label)
        ctk.CTkOptionMenu(line(s3, "Nguồn viết nhật ký"), values=list(prov_map.values()), variable=pv,
                          width=300, font=self.f_sm,
                          command=lambda v: self._autosave("content.provider")).pack(side="left")

        def _prov_value():
            # Người dùng không đổi lựa chọn -> giữ nguyên giá trị đang có trong file (không ghi đè).
            if pv.get() == init_label:
                return cur_prov
            return prov_inv.get(pv.get(), "template")

        self.cfg_get["content.provider"] = _prov_value
        self._secret_row(s3, line, "Token 9router", "content.ninerouter.api_key", cfg)
        self._secret_row(s3, line, "YouTube API key (tuỳ chọn)", "youtube_api.api_key", cfg)
        ctk.CTkLabel(s3, text="").pack(pady=2)

        # --- Chạy nhiều máy (mạng LAN, cùng bộ profile GPM đồng bộ) ---
        s5 = section("Chạy nhiều máy (mạng LAN)",
                     "Chỉ cần khi NHIỀU MÁY dùng chung 1 bộ profile GPM. Mọi máy trỏ về CÙNG 1 thư mục chia sẻ "
                     "trên mạng LAN → không bao giờ 2 máy nuôi trùng 1 mail, cờ cảnh báo và file nhật ký dùng chung. "
                     "Để trống = chỉ chạy 1 máy. Đã đặt mà không vào được thư mục → máy này KHÔNG nuôi (tránh trùng).")
        for label, key, ph in [("Thư mục chung", "shared.dir", r"vd \\MAY-CHINH\MailNurture"),
                               ("Tên máy này", "shared.machine_name", "để trống = tên máy Windows")]:
            ent = ctk.CTkEntry(line(s5, label), width=380, height=34, font=self.f_sm, fg_color=BG_SIDE,
                               border_color=BORDER, placeholder_text=ph)
            val = str(_dig(cfg, key, "") or "")
            if val:
                ent.insert(0, val)
            ent.pack(side="left")
            self.cfg_get[key] = lambda e=ent: e.get().strip()
            self.cfg_entries[key] = ent
        fr = line(s5, "")
        ctk.CTkButton(fr, text="Kiểm tra thư mục chung", width=190, height=32, font=self.f_sm, fg_color=CARD_HI,
                      hover_color=BORDER, text_color=TXT, command=self._test_shared).pack(side="left")
        self.shared_result = ctk.CTkLabel(s5, text="", font=self.f_xs, text_color=MUTED, anchor="w",
                                          justify="left", wraplength=720)
        self.shared_result.pack(anchor="w", padx=16, pady=(4, 12))

        # --- Nâng cao ---
        s4 = section("Nâng cao", "Thường không cần đổi.")
        for label, key, default in [("Địa chỉ API của GPM", "gpm.base_url", "http://127.0.0.1:19995"),
                                    ("Địa chỉ 9router", "content.ninerouter.base_url", "http://127.0.0.1:20128/v1"),
                                    ("Model 9router", "content.ninerouter.model", "gemini-3.8-flash-medium")]:
            ent = ctk.CTkEntry(line(s4, label), width=380, height=34, font=self.f_sm,
                               fg_color=BG_SIDE, border_color=BORDER)
            ent.insert(0, str(_dig(cfg, key, default) or default))
            ent.pack(side="left")
            self.cfg_get[key] = lambda e=ent: e.get().strip()
            self.cfg_entries[key] = ent
            if key in self._AUTOSAVE_KEYS:
                self._bind_autosave(ent, key)
        ctk.CTkLabel(s4, text="").pack(pady=2)

        # Ghi nhớ giá trị ĐANG LƯU lúc mở app -> chỉ lưu những mục bạn thật sự đổi, và biết cái gì chưa lưu.
        self._cfg_initial = self._cfg_snapshot()
        self._stored_secrets = {k: self._cfg_initial.get(k, "") for k in self._SECRET_KEYS}
        for k in self._SECRET_KEYS:
            self._set_key_status(k)

    def _test_shared(self):
        """Thử thư mục chung bằng đường dẫn ĐANG NHẬP (chưa cần Lưu)."""
        d = self.cfg_get["shared.dir"]()
        m = self.cfg_get["shared.machine_name"]()
        if not d:
            self.shared_result.configure(text="Chưa nhập thư mục chung → app đang chạy kiểu 1 máy.",
                                         text_color=MUTED)
            return
        self.shared_result.configure(text="Đang kiểm tra…", text_color=MUTED)
        self.update_idletasks()
        sh = Shared({"shared": {"dir": d, "machine_name": m}})
        ok, msg = sh.check()
        if not ok:
            self.shared_result.configure(
                text=f"✖ {msg}\nKiểm tra: máy chủ đang bật, thư mục đã chia sẻ (Share) với quyền GHI cho "
                     f"các máy, đường dẫn gõ đúng dạng \\\\TÊN-MÁY\\TÊN-THƯ-MỤC.", text_color=RED)
            return
        ms = sh.machines()
        names = ", ".join(f"{x.get('machine')} ({monitor.ago(x.get('at', ''))})" for x in ms[:8]) or "—"
        today = sh.claimed_today()
        self.shared_result.configure(
            text=f"✓ {msg} Máy này tên “{sh.machine}”.\nCác máy đã dùng thư mục này: {names}\n"
                 f"Hôm nay các máy đã nhận nuôi {len(today)} mail.  (Nhớ bấm “Lưu cấu hình”.)",
            text_color=GREEN)

    # ---- nút "Kiểm tra kết nối" ----
    _CONN_ITEMS = [("gpm", "GPM Login"), ("ai", "9router (AI viết nhật ký)"), ("yt", "YouTube Data API")]

    def _build_conn_card(self, scroll):
        """Thẻ kết quả (ẩn cho tới khi bấm nút), chèn ở đầu trang Cấu hình."""
        self._cfg_first_section = None
        self._conn_shown = False
        self._conn_running = False
        card = ctk.CTkFrame(scroll, fg_color=CARD, corner_radius=12, border_width=1, border_color=BORDER)
        self.conn_card = card
        hdr = ctk.CTkFrame(card, fg_color="transparent")
        hdr.pack(fill="x", padx=16, pady=(12, 4))
        ctk.CTkLabel(hdr, text="Kết quả kiểm tra kết nối", font=self.f_h2, text_color=TXT).pack(side="left")
        self.conn_note = ctk.CTkLabel(hdr, text="", font=self.f_xs, text_color=FAINT)
        self.conn_note.pack(side="left", padx=10)
        ctk.CTkButton(hdr, text="Ẩn", width=50, height=26, font=self.f_xs, fg_color="transparent",
                      hover_color=CARD_HI, text_color=MUTED, command=self._hide_conn_card).pack(side="right")
        self.conn_rows: dict[str, tuple] = {}
        for key, name in self._CONN_ITEMS:
            row = ctk.CTkFrame(card, fg_color="transparent")
            row.pack(fill="x", padx=16, pady=4)
            dot = ctk.CTkLabel(row, text="●", width=18, text_color=MUTED, font=self.f_sm)
            dot.pack(side="left", anchor="n")
            ctk.CTkLabel(row, text=name, width=200, anchor="w", text_color=TXT,
                         font=self.f_sm).pack(side="left", anchor="n")
            msg = ctk.CTkLabel(row, text="", anchor="w", justify="left", wraplength=520, text_color=MUTED,
                               font=self.f_xs)
            msg.pack(side="left", fill="x", anchor="n", pady=(3, 0))
            self.conn_rows[key] = (dot, msg)
        ctk.CTkLabel(card, text="Dùng đúng giá trị đang nhập trên trang này (chưa cần bấm Lưu). "
                                "Không đụng tới tài khoản Google nào.",
                     font=self.f_xs, text_color=FAINT, anchor="w").pack(anchor="w", padx=16, pady=(4, 12))

    def _hide_conn_card(self):
        self.conn_card.pack_forget()
        self._conn_shown = False

    def _set_conn_row(self, key: str, level: str, text: str):
        dot, msg = self.conn_rows[key]
        col = {"ok": GREEN, "warn": AMBER, "err": RED}.get(level, MUTED)
        dot.configure(text_color=col)
        msg.configure(text=text, text_color=TXT if level in ("ok", "warn", "err") else MUTED)

    def _test_connections(self):
        if self._conn_running:
            return
        self._flush_autosave()          # token/key vừa dán -> lưu ngay trước khi thử
        g = self.cfg_get
        vals = {k: (g[k]() if k in g else "") for k in (
            "gpm.base_url", "content.ninerouter.base_url", "content.ninerouter.model",
            "content.ninerouter.api_key", "youtube_api.api_key", "content.provider")}
        if not self._conn_shown:
            if self._cfg_first_section is not None:
                self.conn_card.pack(fill="x", pady=7, before=self._cfg_first_section)
            else:
                self.conn_card.pack(fill="x", pady=7)
            self._conn_shown = True
        try:
            self.cfg_scroll._parent_canvas.yview_moveto(0)
        except Exception:
            pass
        for key, _ in self._CONN_ITEMS:
            self._set_conn_row(key, "pending", "Đang kiểm tra…")
        self.conn_note.configure(text="")
        self._conn_running = True
        self.btn_conn.configure(state="disabled", text="Đang kiểm tra…")
        q: "queue.Queue" = queue.Queue()

        def work():
            for key, fn in (("gpm", lambda: conn_check.check_gpm(vals["gpm.base_url"])),
                            ("ai", lambda: conn_check.check_9router(vals["content.ninerouter.base_url"],
                                                                    vals["content.ninerouter.api_key"],
                                                                    vals["content.ninerouter.model"])),
                            ("yt", lambda: conn_check.check_youtube(vals["youtube_api.api_key"]))):
                try:
                    res = fn()
                except Exception as e:  # noqa: BLE001
                    res = {"level": "err", "msg": f"Lỗi không mong đợi: {e}"}
                q.put((key, res))
            q.put(("done", None))

        threading.Thread(target=work, daemon=True).start()
        self.after(150, lambda: self._poll_conn(q, vals, {}))

    def _poll_conn(self, q, vals: dict, results: dict):
        try:
            while True:
                key, res = q.get_nowait()
                if key == "done":
                    self._conn_running = False
                    self.btn_conn.configure(state="normal", text="Kiểm tra kết nối")
                    self.conn_note.configure(text=f"lúc {datetime.now():%H:%M:%S}")
                    bad = [n for k, n in self._CONN_ITEMS if results.get(k) == "err"]
                    warn = [n for k, n in self._CONN_ITEMS if results.get(k) == "warn"]
                    if bad:
                        self.toast("Có lỗi kết nối: " + ", ".join(bad) + " — xem hướng dẫn trên thẻ.", "err", ms=6000)
                    elif warn:
                        self.toast(f"Kết nối được — có {len(warn)} lưu ý (chấm vàng) trên thẻ.", "warn", ms=5000)
                    else:
                        self.toast("Tất cả kết nối đều ổn.", "ok")
                    return
                text = res.get("msg", "")
                level = res.get("level", "err")
                if key == "ai":
                    sug = res.get("suggested_model")
                    ent = self.cfg_entries.get("content.ninerouter.model")
                    if sug and ent is not None:
                        ent.delete(0, "end")
                        ent.insert(0, sug)
                        self._autosave("content.ninerouter.model")     # tự lưu luôn model chạy được
                        text = text.replace("bấm “Lưu cấu hình”", "đã TỰ LƯU")
                    uses_ai = vals.get("content.provider") in ("9router", "ninerouter", "openai")
                    if not uses_ai and not (vals.get("content.ninerouter.api_key") or "").strip() \
                            and "Không kết nối" not in text:
                        # Đang dùng "Mẫu có sẵn": chưa có token chỉ là lưu ý, không phải lỗi.
                        level = "warn"
                        text = ("Đang dùng “Mẫu có sẵn” (không cần AI) — nhật ký tiếng Việt vẫn chạy. Muốn AI "
                                "viết (bắt buộc cho nhóm ngoại ngữ): dán Token 9router + chọn Nguồn = 9router.")
                    elif level == "ok" and not uses_ai:
                        level = "warn"
                        text += ("  Lưu ý: “Nguồn viết nhật ký” đang KHÔNG phải 9router — chọn "
                                 "“9router (AI)” ở ô đó (tự lưu) để app dùng AI viết nhật ký.")
                results[key] = level
                self._set_conn_row(key, level, text)
                name = dict(self._CONN_ITEMS)[key]
                mark = {"ok": "✓", "warn": "⚠", "err": "✖"}.get(level, "•")
                self._append_log(f"🔌 Kiểm tra kết nối — {mark} {name}: {text}")
        except queue.Empty:
            pass
        self.after(150, lambda: self._poll_conn(q, vals, results))

    def _secret_row(self, parent, line, label, key, cfg):
        fr = line(parent, label)
        ent = ctk.CTkEntry(fr, width=300, height=34, show="•", font=self.f_sm, fg_color=BG_SIDE,
                           border_color=BORDER, placeholder_text="Dán vào đây (tự lưu)")
        val = str(_dig(cfg, key, "") or "")
        if val:
            ent.insert(0, val)
        ent.pack(side="left")

        def toggle(e=ent):
            e.configure(show="" if e.cget("show") else "•")

        ctk.CTkButton(fr, text="Hiện", width=50, height=34, font=self.f_xs, fg_color=CARD_HI,
                      hover_color=BORDER, text_color=TXT, command=toggle).pack(side="left", padx=(6, 2))
        ctk.CTkButton(fr, text="Xoá", width=46, height=34, font=self.f_xs, fg_color="transparent",
                      border_width=1, border_color=BORDER, hover_color=CARD_HI, text_color=RED,
                      command=lambda k=key: self._clear_secret(k)).pack(side="left", padx=2)
        st = ctk.CTkLabel(fr, text="", font=self.f_xs, text_color=FAINT, anchor="w")
        st.pack(side="left", padx=(8, 0))
        self._secret_status[key] = st
        self.cfg_get[key] = lambda e=ent: e.get().strip()
        self.cfg_entries[key] = ent
        self._bind_autosave(ent, key)

    # ---- TỰ LƯU token/key/nguồn AI/model: dán xong là nhớ, tắt app mở lại vẫn còn ----
    _SECRET_KEYS = ("content.ninerouter.api_key", "youtube_api.api_key")
    _AUTOSAVE_KEYS = ("content.provider", "content.ninerouter.api_key", "youtube_api.api_key",
                      "content.ninerouter.model", "content.ninerouter.base_url")
    _KEY_LABEL = {"content.provider": "Nguồn viết nhật ký", "content.ninerouter.api_key": "Token 9router",
                  "youtube_api.api_key": "YouTube API key", "content.ninerouter.model": "Model 9router",
                  "content.ninerouter.base_url": "Địa chỉ 9router", "gpm.base_url": "Địa chỉ API của GPM",
                  "run.concurrency": "Chạy cùng lúc", "run.daily_fraction": "Số mail nuôi mỗi ngày",
                  "run.active_hours": "Giờ hoạt động", "warmup.enabled": "Khởi động dần",
                  "run.minimize_windows": "Chạy ngầm", "action_weights.youtube": "Ưu tiên Xem YouTube",
                  "action_weights.news": "Ưu tiên Đọc báo", "action_weights.gmail": "Ưu tiên Đọc Gmail",
                  "action_weights.diary": "Ưu tiên Viết nhật ký"}

    def _bind_autosave(self, ent, key: str):
        for seq in ("<KeyRelease>", "<FocusOut>"):
            ent.bind(seq, lambda e, k=key: self._schedule_autosave(k), add="+")

    def _schedule_autosave(self, key: str):
        job = self._autosave_jobs.pop(key, None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        if key in self._secret_status:
            self._set_key_status(key, "pending")
        self._autosave_jobs[key] = self.after(900, lambda k=key: self._autosave(k))

    def _flush_autosave(self):
        """Lưu ngay mọi ô đang chờ tự lưu (trước khi kiểm tra kết nối / thoát app)."""
        for key in list(self._autosave_jobs):
            job = self._autosave_jobs.pop(key, None)
            if job:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
            self._autosave(key)

    def _autosave(self, key: str):
        self._autosave_jobs.pop(key, None)
        try:
            val = self.cfg_get[key]()
        except Exception:
            return
        # Ô TRỐNG không bao giờ xoá giá trị đã lưu (muốn xoá token/key phải bấm nút "Xoá").
        if not val or val == self._cfg_initial.get(key):
            self._set_key_status(key)
            return
        cfg = self._load_cfg_file(silent=True)
        if cfg is None:
            self._set_key_status(key, "error")
            return
        _set(cfg, key, val)
        if self._write_cfg_file(cfg):
            self._cfg_initial[key] = val
            if key in self._SECRET_KEYS:
                self._stored_secrets[key] = val
            label = self._KEY_LABEL.get(key, key)
            self._set_key_status(key)
            self._append_log(f"💾 Đã tự lưu: {label}")
            if key in self._SECRET_KEYS:
                self.toast(f"Đã lưu {label} — tắt app mở lại vẫn còn.")
        else:
            self._set_key_status(key, "error")

    def _set_key_status(self, key: str, state: str = ""):
        lbl = self._secret_status.get(key)
        if lbl is None:
            return
        try:
            cur = self.cfg_get[key]()
        except Exception:
            cur = ""
        stored = self._stored_secrets.get(key, "")
        if state == "pending":
            text, col = "Đang lưu…", AMBER
        elif state == "error":
            text, col = "⚠ Chưa lưu được", RED
        elif stored and cur == stored:
            text, col = "✓ Đã lưu", GREEN
        elif stored and not cur:
            text, col = "✓ Vẫn giữ key đã lưu", GREEN
        elif not stored and not cur:
            text, col = "Chưa có", FAINT
        else:
            text, col = "Chưa lưu", AMBER
        lbl.configure(text=text, text_color=col)

    def _clear_secret(self, key: str):
        label = self._KEY_LABEL.get(key, key)
        ent = self.cfg_entries.get(key)
        if not self._stored_secrets.get(key):
            if ent is not None:
                ent.delete(0, "end")
            self._set_key_status(key)
            return
        if not messagebox.askyesno(f"Xoá {label}", f"Xoá {label} đã lưu?\n\nSau khi xoá, app sẽ không dùng được "
                                                   f"{label} cho tới khi bạn dán lại."):
            return
        cfg = self._load_cfg_file()
        if cfg is None:
            return
        _set(cfg, key, "")
        if self._write_cfg_file(cfg):
            self._stored_secrets[key] = ""
            self._cfg_initial[key] = ""
            if ent is not None:
                ent.delete(0, "end")
            self._set_key_status(key)
            self._append_log(f"🗑 Đã xoá {label} đã lưu.")
            self.toast(f"Đã xoá {label}.", "warn")

    def _cfg_snapshot(self) -> dict:
        out = {}
        for key, getter in self.cfg_get.items():
            try:
                out[key] = getter()
            except Exception:
                pass
        return out

    def _cfg_dirty(self) -> dict:
        """Các mục trên tab Cấu hình đã ĐỔI so với lúc lưu gần nhất (chỉ ghi những mục này)."""
        if not hasattr(self, "_cfg_initial"):
            return {}
        out = {}
        for key, val in self._cfg_snapshot().items():
            if key in self._SECRET_KEYS and not val:
                continue                        # ô trống không xoá token/key đã lưu
            if val != self._cfg_initial.get(key):
                out[key] = val
        return out

    def _save_config(self) -> bool:
        self._flush_autosave()
        dirty = self._cfg_dirty()
        if not dirty:
            self.toast("Mọi thay đổi đã được lưu.")
            return True
        cfg = self._load_cfg_file()
        if cfg is None:
            return False
        for key, val in dirty.items():
            _set(cfg, key, val)
        if self._write_cfg_file(cfg):
            self._cfg_initial.update(dirty)
            for key in self._SECRET_KEYS:
                if key in dirty:
                    self._stored_secrets[key] = dirty[key]
                self._set_key_status(key)
            self.toast("Đã lưu cấu hình.")
            self._append_log("💾 Đã lưu cấu hình: " + ", ".join(self._KEY_LABEL.get(k, k) for k in dirty))
            return True
        return False

    # ================= tiện ích file cấu hình =================
    def _load_cfg_file(self, silent: bool = False) -> dict | None:
        """Đọc config.yaml để SỬA. Lỗi đọc -> báo lỗi và trả None (người gọi phải huỷ việc ghi),
        tuyệt đối không trả {} rồi ghi đè làm mất toàn bộ cấu hình (kể cả nhóm Khóa).
        silent=True: không bật hộp báo lỗi (dùng cho tự lưu — lỗi hiện ngay cạnh ô nhập)."""
        def err(title, text):
            if not silent:
                messagebox.showerror(title, text)
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except FileNotFoundError:
            err("Không tìm thấy cấu hình", f"Không thấy {CONFIG_PATH.name}. Chưa lưu thay đổi nào.")
            return None
        except Exception as e:  # noqa: BLE001
            err("Cấu hình bị lỗi",
                f"{CONFIG_PATH.name} đang lỗi nên CHƯA lưu thay đổi (tránh mất cấu hình).\n\n{e}")
            return None
        if data is None:
            data = {}
        if not isinstance(data, dict):
            err("Cấu hình bị lỗi", f"{CONFIG_PATH.name} không đúng định dạng — chưa lưu.")
            return None
        return data

    @staticmethod
    def _group_items(cfg: dict) -> dict:
        """Trả về cfg['groups']['items'] (tạo mới nếu thiếu hoặc đang là null)."""
        if not isinstance(cfg.get("groups"), dict):
            cfg["groups"] = {}
        if not isinstance(cfg["groups"].get("items"), dict):
            cfg["groups"]["items"] = {}
        return cfg["groups"]["items"]

    def _write_cfg_file(self, cfg: dict) -> bool:
        """Ghi an toàn: ghi ra file tạm rồi thay thế -> không bao giờ để config.yaml ghi dở."""
        tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
        try:
            text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
            yaml.safe_load(text)                      # tự kiểm tra đọc lại được
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, CONFIG_PATH)
            return True
        except Exception as e:  # noqa: BLE001
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            messagebox.showerror("Lỗi lưu", str(e))
            return False


def _clip(text: str, n: int) -> str:
    """Cắt chữ dài cho vừa ô (thêm '…')."""
    text = str(text or "")
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def _dig(d: dict, path: str, default=None):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def _set(d: dict, path: str, value):
    parts = path.split(".")
    cur = d
    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]
    cur[parts[-1]] = value


if __name__ == "__main__":
    App().mainloop()
