import json
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk


CONFIG_PATH = Path(__file__).with_name("config.json")
DEFAULT_POINT_ACTION_SCRIPTS = {
    "to_2_arrive": [{"type": "press_seq", "keys": ["skill_m"]}],
    "to_3_arrive": [{"type": "press_seq", "keys": ["skill_m"]}],
    "to_4_arrive": [{"type": "press_seq", "keys": ["skill_m"]}],
    "to_1_arrive": [{"type": "tap", "key": "left"}],
    "start_to_1_arrive": [{"type": "tap", "key": "left"}, {"type": "press_seq", "keys": ["n"]}],
    "pre50_return_to_1_arrive": [{"type": "tap", "key": "left"}, {"type": "press_seq", "keys": ["n"]}],
    "pre110_return_to_1_arrive": [
        {"type": "tap", "key": "left"},
        {"type": "press_seq", "keys": ["6"]},
        {"type": "press_seq", "keys": ["r"]},
        {"type": "press_seq", "keys": ["n"]},
    ],
}


class ConfigEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Maple Bot 參數編輯器")
        self.geometry("980x760")
        self.minsize(900, 680)

        self.cfg = self._load_config()
        self.vars = {}
        self.point_vars = {}

        self._build_ui()
        self._load_to_form()

    def _load_config(self):
        with CONFIG_PATH.open("r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
        scripts = cfg.setdefault("point_action_scripts", {})
        for k, v in DEFAULT_POINT_ACTION_SCRIPTS.items():
            scripts.setdefault(k, v)
        return cfg

    def _save_config(self):
        with CONFIG_PATH.open("w", encoding="utf-8") as f:
            json.dump(self.cfg, f, ensure_ascii=False, indent=2)

    def _entry(self, parent, label, key, width=12):
        row = ttk.Frame(parent)
        row.pack(fill="x", padx=6, pady=3)
        ttk.Label(row, text=label, width=28).pack(side="left")
        var = tk.StringVar()
        self.vars[key] = var
        ttk.Entry(row, textvariable=var, width=width).pack(side="left")
        return var

    def _build_points_table(self, parent):
        header = ttk.Frame(parent)
        header.pack(fill="x", padx=6, pady=(4, 2))
        ttk.Label(header, text="點位 ID", width=12).pack(side="left")
        ttk.Label(header, text="X", width=10).pack(side="left")
        ttk.Label(header, text="Y", width=10).pack(side="left")

        self.points_table = ttk.Frame(parent)
        self.points_table.pack(fill="x", padx=6, pady=2)

    def _render_points_rows(self):
        for child in self.points_table.winfo_children():
            child.destroy()
        self.point_vars.clear()

        points = self.cfg.setdefault("points", {})
        ids = sorted([int(k) for k in points.keys() if str(k).isdigit()])
        for idx in ids:
            k = str(idx)
            row = ttk.Frame(self.points_table)
            row.pack(fill="x", pady=1)
            ttk.Label(row, text=k, width=12).pack(side="left")
            xv = tk.StringVar(value=str(points[k][0]))
            yv = tk.StringVar(value=str(points[k][1]))
            self.point_vars[k] = (xv, yv)
            ttk.Entry(row, textvariable=xv, width=10).pack(side="left")
            ttk.Entry(row, textvariable=yv, width=10).pack(side="left", padx=(6, 0))

    def _build_ui(self):
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        content = ttk.Frame(canvas)
        content.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=content, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        sec_general = ttk.LabelFrame(content, text="一般設定")
        sec_general.pack(fill="x", pady=6)
        self._entry(sec_general, "螢幕編號 (monitor_index)", "monitor_index")
        self._entry(sec_general, "1點攻擊鍵 (point1_attack_key)", "point1_attack_key")
        self._entry(sec_general, "1點攻擊間隔(舊參數)", "point1_attack_interval_sec")
        self._entry(sec_general, "粉色偵測開關(true/false)", "pink_detection_enabled")

        sec_roi = ttk.LabelFrame(content, text="小地圖範圍 (ROI)")
        sec_roi.pack(fill="x", pady=6)
        self._entry(sec_roi, "左邊界 left", "roi_left")
        self._entry(sec_roi, "上邊界 top", "roi_top")
        self._entry(sec_roi, "寬度 width", "roi_width")
        self._entry(sec_roi, "高度 height", "roi_height")

        sec_keys = ttk.LabelFrame(content, text="動作按鍵")
        sec_keys.pack(fill="x", pady=6)
        for k in ("left", "right", "jump", "up", "down", "upward", "skill_m"):
            self._entry(sec_keys, k, f"key_{k}", width=16)

        sec_hotkeys = ttk.LabelFrame(content, text="快捷鍵")
        sec_hotkeys.pack(fill="x", pady=6)
        for k in ("stop", "pause", "mark_1", "mark_2", "mark_3", "mark_4"):
            self._entry(sec_hotkeys, k, f"hotkey_{k}", width=16)

        sec_timing = ttk.LabelFrame(content, text="時間參數")
        sec_timing.pack(fill="x", pady=6)
        for k in ("key_tap_sec", "between_key_sec", "poll_sec", "state_settle_sec", "arrival_pause_sec"):
            self._entry(sec_timing, k, f"timing_{k}")
        self._entry(sec_timing, "fast_poll_sec (僅 to_2 狀態)", "timing_fast_poll_sec")
        self._entry(sec_timing, "portal_retry_sec (傳送重試間隔)", "portal_retry_sec")

        sec_detect = ttk.LabelFrame(content, text="偵測 / 移動參數")
        sec_detect.pack(fill="x", pady=6)
        for k in (
            "arrival_radius_px",
            "arrival_radius_point1_px",
            "arrival_radius_other_points_strict_px",
            "arrival_radius_other_points_loose_px",
            "arrival_confirm_consecutive",
            "find_x_tolerance_px",
            "find_y_tolerance_px",
            "fine_nudge_x_px",
            "vertical_threshold_px",
            "stuck_seconds",
            "stuck_radius_px",
            "portal_align_x_tolerance_px",
        ):
            self._entry(sec_detect, k, k)

        sec_points = ttk.LabelFrame(content, text="點位設定")
        sec_points.pack(fill="x", pady=6)
        row = ttk.Frame(sec_points)
        row.pack(fill="x", padx=6, pady=3)
        ttk.Label(row, text="點位數量 point_count").pack(side="left")
        self.vars["point_count"] = tk.StringVar()
        ttk.Entry(row, textvariable=self.vars["point_count"], width=8).pack(side="left", padx=(8, 0))
        ttk.Button(row, text="套用點位數量", command=self._apply_point_count).pack(side="left", padx=8)
        ttk.Label(row, text="(目前主流程使用 1~4 點；多的點位會先保存)").pack(side="left")

        self._build_points_table(sec_points)

        sec_scripts = ttk.LabelFrame(content, text="到點動作腳本 (JSON 陣列)")
        sec_scripts.pack(fill="x", pady=6)
        script_hint = "格式範例: [{\"type\":\"tap\",\"key\":\"left\"},{\"type\":\"press_seq\",\"keys\":[\"skill_m\"]}]"
        ttk.Label(sec_scripts, text=script_hint).pack(anchor="w", padx=6, pady=(4, 2))
        for sk in (
            "to_2_arrive",
            "to_3_arrive",
            "to_4_arrive",
            "to_1_arrive",
            "start_to_1_arrive",
            "pre50_return_to_1_arrive",
            "pre110_return_to_1_arrive",
        ):
            self._entry(sec_scripts, sk, f"script_{sk}", width=95)

        btn_row = ttk.Frame(content)
        btn_row.pack(fill="x", pady=12)
        ttk.Button(btn_row, text="重新載入", command=self._reload).pack(side="left", padx=4)
        ttk.Button(btn_row, text="儲存", command=self._save_from_form).pack(side="left", padx=4)
        ttk.Button(btn_row, text="儲存並關閉", command=self._save_and_close).pack(side="left", padx=4)

    def _load_to_form(self):
        cfg = self.cfg
        roi = cfg.get("minimap_roi", {})
        keys = cfg.get("keys", {})
        hotkeys = cfg.get("hotkeys", {})
        timings = cfg.get("timings", {})

        self.vars["monitor_index"].set(str(cfg.get("monitor_index", 1)))
        self.vars["point1_attack_key"].set(str(cfg.get("point1_attack_key", "a")))
        self.vars["point1_attack_interval_sec"].set(str(cfg.get("point1_attack_interval_sec", 2)))
        self.vars["pink_detection_enabled"].set(str(cfg.get("pink_detection_enabled", False)).lower())

        self.vars["roi_left"].set(str(roi.get("left", 0)))
        self.vars["roi_top"].set(str(roi.get("top", 0)))
        self.vars["roi_width"].set(str(roi.get("width", 300)))
        self.vars["roi_height"].set(str(roi.get("height", 150)))

        for k in ("left", "right", "jump", "up", "down", "upward", "skill_m"):
            self.vars[f"key_{k}"].set(str(keys.get(k, "")))
        for k in ("stop", "pause", "mark_1", "mark_2", "mark_3", "mark_4"):
            self.vars[f"hotkey_{k}"].set(str(hotkeys.get(k, "")))
        for k in ("key_tap_sec", "between_key_sec", "poll_sec", "state_settle_sec", "arrival_pause_sec"):
            self.vars[f"timing_{k}"].set(str(timings.get(k, "")))
        self.vars["timing_fast_poll_sec"].set(str(timings.get("fast_poll_sec", 0.012)))
        self.vars["portal_retry_sec"].set(str(cfg.get("portal_retry_sec", 0.08)))
        scripts = cfg.get("point_action_scripts", {})
        for sk in (
            "to_2_arrive",
            "to_3_arrive",
            "to_4_arrive",
            "to_1_arrive",
            "start_to_1_arrive",
            "pre50_return_to_1_arrive",
            "pre110_return_to_1_arrive",
        ):
            self.vars[f"script_{sk}"].set(json.dumps(scripts.get(sk, []), ensure_ascii=False))

        for k in (
            "arrival_radius_px",
            "arrival_radius_point1_px",
            "arrival_radius_other_points_strict_px",
            "arrival_radius_other_points_loose_px",
            "arrival_confirm_consecutive",
            "find_x_tolerance_px",
            "find_y_tolerance_px",
            "fine_nudge_x_px",
            "vertical_threshold_px",
            "stuck_seconds",
            "stuck_radius_px",
            "portal_align_x_tolerance_px",
        ):
            self.vars[k].set(str(cfg.get(k, "")))

        points = cfg.setdefault("points", {})
        self.vars["point_count"].set(str(len(points)))
        self._render_points_rows()

    def _apply_point_count(self):
        try:
            n = int(self.vars["point_count"].get().strip())
            if n < 4:
                raise ValueError("point_count 必須 >= 4")
        except Exception as e:
            messagebox.showerror("點位數量錯誤", str(e))
            return

        points = self.cfg.setdefault("points", {})
        current_ids = sorted([int(k) for k in points.keys() if str(k).isdigit()])
        max_id = max(current_ids) if current_ids else 0

        for i in range(max_id + 1, n + 1):
            points[str(i)] = [0, 0]
        for i in range(n + 1, max_id + 1):
            points.pop(str(i), None)

        self._render_points_rows()

    def _save_from_form(self):
        try:
            cfg = self.cfg
            cfg["monitor_index"] = int(self.vars["monitor_index"].get().strip())
            cfg["point1_attack_key"] = self.vars["point1_attack_key"].get().strip()
            cfg["point1_attack_interval_sec"] = float(self.vars["point1_attack_interval_sec"].get().strip())
            pink_raw = self.vars["pink_detection_enabled"].get().strip().lower()
            cfg["pink_detection_enabled"] = pink_raw in ("true", "1", "yes", "y", "是", "開", "on")

            roi = cfg.setdefault("minimap_roi", {})
            roi["left"] = int(self.vars["roi_left"].get().strip())
            roi["top"] = int(self.vars["roi_top"].get().strip())
            roi["width"] = int(self.vars["roi_width"].get().strip())
            roi["height"] = int(self.vars["roi_height"].get().strip())

            keys = cfg.setdefault("keys", {})
            for k in ("left", "right", "jump", "up", "down", "upward", "skill_m"):
                keys[k] = self.vars[f"key_{k}"].get().strip()

            hotkeys = cfg.setdefault("hotkeys", {})
            for k in ("stop", "pause", "mark_1", "mark_2", "mark_3", "mark_4"):
                hotkeys[k] = self.vars[f"hotkey_{k}"].get().strip()

            timings = cfg.setdefault("timings", {})
            for k in ("key_tap_sec", "between_key_sec", "poll_sec", "state_settle_sec", "arrival_pause_sec"):
                timings[k] = float(self.vars[f"timing_{k}"].get().strip())
            timings["fast_poll_sec"] = float(self.vars["timing_fast_poll_sec"].get().strip())

            for k in (
                "arrival_radius_px",
                "arrival_radius_point1_px",
                "arrival_radius_other_points_strict_px",
                "arrival_radius_other_points_loose_px",
                "arrival_confirm_consecutive",
                "find_x_tolerance_px",
                "find_y_tolerance_px",
                "fine_nudge_x_px",
                "vertical_threshold_px",
                "stuck_seconds",
                "stuck_radius_px",
                "portal_align_x_tolerance_px",
            ):
                raw = self.vars[k].get().strip()
                if k in ("arrival_confirm_consecutive", "find_x_tolerance_px", "find_y_tolerance_px", "fine_nudge_x_px", "vertical_threshold_px", "portal_align_x_tolerance_px"):
                    cfg[k] = int(raw)
                else:
                    cfg[k] = float(raw)

            cfg["portal_retry_sec"] = float(self.vars["portal_retry_sec"].get().strip())

            scripts = cfg.setdefault("point_action_scripts", {})
            for sk in (
                "to_2_arrive",
                "to_3_arrive",
                "to_4_arrive",
                "to_1_arrive",
                "start_to_1_arrive",
                "pre50_return_to_1_arrive",
                "pre110_return_to_1_arrive",
            ):
                raw = self.vars[f"script_{sk}"].get().strip()
                parsed = json.loads(raw) if raw else []
                if not isinstance(parsed, list):
                    raise ValueError(f"{sk} 必須是 JSON 陣列")
                scripts[sk] = parsed

            self._apply_point_count()
            points = cfg.setdefault("points", {})
            for pid, (xv, yv) in self.point_vars.items():
                points[pid] = [int(xv.get().strip()), int(yv.get().strip())]

            for need in ("1", "2", "3", "4"):
                if need not in points:
                    raise ValueError("目前主流程需要 points 內至少包含 1,2,3,4")

            self._save_config()
            messagebox.showinfo("儲存成功", "config.json 已儲存。")
        except Exception as e:
            messagebox.showerror("儲存失敗", str(e))

    def _save_and_close(self):
        self._save_from_form()
        self.destroy()

    def _reload(self):
        self.cfg = self._load_config()
        self._load_to_form()


if __name__ == "__main__":
    app = ConfigEditor()
    app.mainloop()
