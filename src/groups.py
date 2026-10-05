"""Quản lý theo NHÓM của GPM: khóa/nuôi/để sau + hành vi riêng cho từng nhóm.

An toàn là ưu tiên số 1: nhóm ở chế độ 'locked' (đang làm kênh) TUYỆT ĐỐI không bao giờ được mở.
Nhóm chưa cấu hình -> theo default_mode (mặc định 'later' = chưa nuôi), nên không lỡ đụng nhầm.

Cấu hình trong config.yaml:
  groups:
    default_mode: later          # later | nurture | locked
    items:
      "NHOM KENH":
        mode: locked             # nhóm kênh -> khóa hẳn
      "NUOI MAIL":
        mode: nurture
        overrides:               # hành vi riêng cho nhóm này (đè lên cấu hình chung)
          action_weights: { youtube: 50, news: 30, gmail: 20, diary: 0 }
          interests: ["công nghệ", "giải trí"]
          news: { sites: ["https://tuoitre.vn"] }
"""
from __future__ import annotations

import copy

from . import markets

MODE_NURTURE = "nurture"
MODE_LOCKED = "locked"
MODE_LATER = "later"
MODES = (MODE_NURTURE, MODE_LOCKED, MODE_LATER)


def group_map(gpm) -> dict[str, str]:
    """{group_id: group_name} từ GPM.
    KHÔNG nuốt lỗi: không lấy được danh sách nhóm thì phải dừng (nếu không, mail nhóm Khóa
    sẽ bị coi là 'không nhóm' và có thể bị mở nhầm)."""
    gmap = {g.id: g.name for g in gpm.list_groups()}
    if not gmap:
        from .gpm_client import GPMError
        raise GPMError("GPM trả về danh sách nhóm rỗng — không thể xác định nhóm Khóa, dừng cho an toàn.")
    return gmap


def annotate_profiles(profiles, gmap: dict[str, str]):
    """Gắn tên nhóm. Profile có group_id không có trong danh sách nhóm -> tên rỗng -> coi là KHÓA."""
    for p in profiles:
        p.group_name = gmap.get(str(p.group_id), "")
    return profiles


def _cfg(cfg_raw: dict) -> dict:
    g = cfg_raw.get("groups")
    return g if isinstance(g, dict) else {}


def _norm(mode) -> str:
    """Chuẩn hoá chế độ; giá trị lạ (gõ sai...) -> KHÓA cho an toàn."""
    m = str(mode or "").strip().lower()
    return m if m in MODES else MODE_LOCKED


def mode_for(cfg_raw: dict, group_name: str) -> str:
    # Không xác định được nhóm của mail -> coi là KHÓA (không bao giờ mở nhầm nhóm kênh).
    if not group_name:
        return MODE_LOCKED
    g = _cfg(cfg_raw)
    default = _norm(g.get("default_mode", MODE_LATER))
    items = g.get("items") if isinstance(g.get("items"), dict) else {}
    entry = items.get(group_name)
    if isinstance(entry, dict) and "mode" in entry:
        return _norm(entry.get("mode"))
    return default


def is_nurture(cfg_raw: dict, group_name: str) -> bool:
    return mode_for(cfg_raw, group_name) == MODE_NURTURE


def is_locked(cfg_raw: dict, group_name: str) -> bool:
    return mode_for(cfg_raw, group_name) == MODE_LOCKED


def overrides_for(cfg_raw: dict, group_name: str) -> dict:
    entry = (_cfg(cfg_raw).get("items") or {}).get(group_name)
    return entry.get("overrides", {}) if isinstance(entry, dict) else {}


def market_for(cfg_raw: dict, group_name: str) -> str:
    """Thị trường/ngôn ngữ gán cho nhóm (rỗng = mặc định tiếng Việt)."""
    entry = (_cfg(cfg_raw).get("items") or {}).get(group_name)
    return entry.get("market", "") if isinstance(entry, dict) else ""


def effective_cfg(cfg_raw: dict, group_name: str) -> dict:
    """Bản copy config đã trộn: preset thị trường (nền) -> override riêng của nhóm (đè lên)."""
    eff = copy.deepcopy(cfg_raw)
    layers = []
    mkey = market_for(cfg_raw, group_name)
    if mkey:
        layers.append(markets.overrides_for(mkey))   # nền theo thị trường
    layers.append(overrides_for(cfg_raw, group_name))  # override riêng đè lên

    persona_ov: dict = {}
    for ov in layers:
        for key in ("action_weights", "warmup", "run", "news", "diary", "content",
                    "youtube_api", "custom", "session", "youtube"):
            if key in ov and isinstance(ov[key], dict):
                base = eff.get(key)
                if isinstance(base, dict):
                    base.update(ov[key])
                    eff[key] = base
                else:
                    eff[key] = ov[key]
        if isinstance(ov.get("interests"), list):
            persona_ov["interests"] = ov["interests"]
        if isinstance(ov.get("youtube_seeds"), list):
            persona_ov["youtube_seeds"] = ov["youtube_seeds"]
    if persona_ov:
        eff["_persona_override"] = persona_ov
    return eff
