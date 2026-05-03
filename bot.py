import ctypes
import json
import random
import time
import winsound
from dataclasses import dataclass
from pathlib import Path

import cv2
import mss
import numpy as np
import pydirectinput

CONFIG_PATH = Path(__file__).with_name("config.json")
VK = {
    "F1": 0x70,
    "F2": 0x71,
    "F3": 0x72,
    "F4": 0x73,
    "F7": 0x76,
    "F8": 0x77,
}


@dataclass
class Point:
    x: int
    y: int


def load_config() -> dict:
    with CONFIG_PATH.open("r", encoding="utf-8-sig") as f:
        cfg = json.load(f)
    cfg.setdefault(
        "point_action_scripts",
        {
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
        },
    )
    return cfg


def save_config(cfg: dict) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class Bot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.started = False
        self.stop = False
        self.pink_enabled = bool(cfg.get("pink_detection_enabled", True))

        self.mss_ctx = mss.MSS()
        monitors = self.mss_ctx.monitors
        monitor_count = len(monitors)
        raw_index = int(cfg.get("monitor_index", 1))
        # mss.monitors[0] is the virtual bounding monitor; prefer real monitor index >= 1.
        fallback_index = 1 if monitor_count > 1 else 0
        selected_index = raw_index if 0 <= raw_index < monitor_count else fallback_index
        if selected_index != raw_index:
            cfg["monitor_index"] = selected_index
            save_config(cfg)
            print(
                f"[monitor] monitor_index={raw_index} out of range (0..{monitor_count - 1}), "
                f"fallback to {selected_index}."
            )
        self.monitor = monitors[selected_index]

        self.current_state = "hold_1"
        self.cycle_started = time.time()
        self.cycle_active = False
        self.timer_pause_started = None
        self.timer_pause_total = 0.0
        self.state_started = time.time()

        self.next_attack_t = 0.0
        self.next_idle_nudge_t = 0.0
        self.next_portal_try_t = 0.0
        self.portal_check_required_23 = False
        self.pause_until = 0.0

        self.event50_done = False
        self.event110_done = False
        self.event_d_done = False
        self.event_f_done = False
        self.event_d_sec = 0.0
        self.event_f_sec = 0.0

        self.state_jump_done = {"to_1": False, "to_2": False}
        self.arrival_counts = {}

        self.last_seen_pos = None
        self.last_frame = None
        self.last_pink_pos = None
        self.pending_pink_task = False
        self.last_pink_action_t = 0.0
        self.pink_missing_count = 0
        self.manual_pink_alert_pending = False
        self.manual_pink_target_event = None
        self.last_heartbeat_t = 0.0
        self.prev_down = {k: False for k in VK}

        pydirectinput.PAUSE = 0
        pydirectinput.FAILSAFE = False
        self._reset_cycle_random_events()

    def _random_fixed_key_delay(self) -> float:
        return random.uniform(0.1, 0.5)

    def _schedule_next_attack(self):
        self.next_attack_t = time.time() + random.uniform(1.5, 4.0)

    def _reset_cycle_random_events(self):
        # Pick two distinct whole seconds inside one 0~110s cycle.
        t1, t2 = sorted(random.sample(range(1, 111), 2))
        self.event_d_sec = float(t1)
        self.event_f_sec = float(t2)
        self.event_d_done = False
        self.event_f_done = False
        print(f"[BOT] random schedule: D@{self.event_d_sec:.0f}s F@{self.event_f_sec:.0f}s")

    def current_poll_sec(self) -> float:
        normal = float(self.cfg["timings"].get("poll_sec", 0.03))
        fast = float(self.cfg["timings"].get("fast_poll_sec", 0.012))
        if self.started and self.current_state == "to_2":
            return max(0.001, fast)
        return max(0.001, normal)

    def _nudge_roi(self, dx=0, dy=0, dw=0, dh=0):
        roi = self.cfg["minimap_roi"]
        mon_w = int(self.monitor["width"])
        mon_h = int(self.monitor["height"])

        left = int(roi["left"]) + int(dx)
        top = int(roi["top"]) + int(dy)
        width = int(roi["width"]) + int(dw)
        height = int(roi["height"]) + int(dh)

        width = max(20, min(width, mon_w))
        height = max(20, min(height, mon_h))
        left = max(0, min(left, mon_w - width))
        top = max(0, min(top, mon_h - height))

        changed = (
            left != int(roi["left"])
            or top != int(roi["top"])
            or width != int(roi["width"])
            or height != int(roi["height"])
        )
        if changed:
            roi["left"] = left
            roi["top"] = top
            roi["width"] = width
            roi["height"] = height
            save_config(self.cfg)
            print(f"[ROI] left={left} top={top} width={width} height={height}")
        return changed

    def _handle_debug_key(self, key: int):
        move_step = int(self.cfg.get("roi_adjust_step_px", 5))
        size_step = int(self.cfg.get("roi_resize_step_px", 10))
        # Toggle pink detection with P (no Ctrl required).
        k = key & 0xFF
        if k in (ord("p"), ord("P")):
            self.pink_enabled = not self.pink_enabled
            self.cfg["pink_detection_enabled"] = self.pink_enabled
            if not self.pink_enabled:
                self.pending_pink_task = False
                if self.current_state == "to_pink":
                    self.current_state = "hold_1"
            save_config(self.cfg)
            print(f"[PINK] detection enabled = {self.pink_enabled}")
            return

        ctrl_down = (ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000) != 0
        if not ctrl_down:
            return
        # Move ROI with Ctrl + arrow keys.
        if key == 2424832:  # Left arrow
            self._nudge_roi(dx=-move_step)
        elif key == 2555904:  # Right arrow
            self._nudge_roi(dx=move_step)
        elif key == 2490368:  # Up arrow
            self._nudge_roi(dy=-move_step)
        elif key == 2621440:  # Down arrow
            self._nudge_roi(dy=move_step)
        # Q/E: width -/+
        elif k in (ord("q"), ord("Q"), 17):
            self._nudge_roi(dw=-size_step)
        elif k in (ord("e"), ord("E"), 5):
            self._nudge_roi(dw=size_step)
        # Z/C/X: height -/+  (Ctrl+C may be intercepted on some systems, so add Ctrl+X fallback)
        elif k in (ord("z"), ord("Z"), 26):
            self._nudge_roi(dh=-size_step)
        elif k in (ord("c"), ord("C"), 3, ord("x"), ord("X"), 24):
            self._nudge_roi(dh=size_step)

    def _state_name(self) -> str:
        return self.current_state

    def cycle_elapsed_sec(self) -> float:
        if not (self.started and self.cycle_active):
            return 0.0
        now = time.time()
        paused = self.timer_pause_total
        if self.timer_pause_started is not None:
            paused += now - self.timer_pause_started
        return max(0.0, now - self.cycle_started - paused)

    def pause_timer_for_54_flow(self):
        if self.timer_pause_started is None:
            self.timer_pause_started = time.time()
            print("[TIMER] paused at 54s, waiting for N")

    def resume_timer_after_54_n(self):
        if self.timer_pause_started is not None:
            self.timer_pause_total += time.time() - self.timer_pause_started
            self.timer_pause_started = None
            print("[TIMER] resumed after N")

    def next_timed_event_name(self):
        if not self.event50_done:
            return "54"
        if not self.event110_done:
            return "111"
        return None

    def pause_for_manual_pink_alert(self):
        self.started = False
        self.cycle_active = False
        self.current_state = "hold_1"
        self.manual_pink_alert_pending = False
        self.manual_pink_target_event = None
        self.pending_pink_task = False
        self.timer_pause_started = None
        try:
            winsound.Beep(1200, 220)
            winsound.Beep(1600, 260)
        except Exception:
            pass
        print("[PINK] next timed flow finished, bot paused (same as F7). Pink marker appeared.")

    def maybe_set_manual_pink_alert(self, pink_found: bool):
        if not pink_found or self.pink_enabled or not self.started:
            return
        if self.manual_pink_alert_pending:
            return
        target = self.next_timed_event_name()
        if target is None:
            return
        self.manual_pink_alert_pending = True
        self.manual_pink_target_event = target
        print(f"[PINK] detected while PINK_ON=false. Will pause after next {target}s flow.")

    def maybe_finish_manual_pink_alert(self, finished_event: str):
        if self.manual_pink_alert_pending and self.manual_pink_target_event == finished_event:
            self.pause_for_manual_pink_alert()

    def _roi_abs(self) -> dict:
        roi = self.cfg["minimap_roi"]
        return {
            "left": self.monitor["left"] + int(roi["left"]),
            "top": self.monitor["top"] + int(roi["top"]),
            "width": int(roi["width"]),
            "height": int(roi["height"]),
        }

    def capture(self):
        shot = self.mss_ctx.grab(self._roi_abs())
        arr = np.array(shot)
        return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)

    def detect_pos(self, bgr):
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        lo = np.array(self.cfg["yellow_hsv"]["lower"], dtype=np.uint8)
        hi = np.array(self.cfg["yellow_hsv"]["upper"], dtype=np.uint8)
        mask = cv2.inRange(hsv, lo, hi)
        k = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best, score = None, -1.0
        for c in contours:
            area = cv2.contourArea(c)
            if area < 8 or area > 600:
                continue
            peri = cv2.arcLength(c, True)
            if peri <= 0:
                continue
            circ = 4 * np.pi * area / (peri * peri)
            if circ < 0.35:
                continue
            x, y, w, h = cv2.boundingRect(c)
            s = circ * area
            if s > score:
                score = s
                best = Point(x + w // 2, y + h // 2)
        return best

    def detect_pink_target(self, bgr):
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        # Magenta mask (HSV) + anti-red constraints (BGR), for better stability.
        lo = np.array(self.cfg.get("pink_hsv", {}).get("lower", [135, 90, 90]), dtype=np.uint8)
        hi = np.array(self.cfg.get("pink_hsv", {}).get("upper", [179, 255, 255]), dtype=np.uint8)
        mask_hsv = cv2.inRange(hsv, lo, hi)
        b, g, r = cv2.split(bgr)
        mask_bgr = (
            (r > int(self.cfg.get("pink_bgr_r_min", 120)))
            & (b > int(self.cfg.get("pink_bgr_b_min", 90)))
            & (g < int(self.cfg.get("pink_bgr_g_max", 190)))
            & ((r.astype(np.int16) - g.astype(np.int16)) > int(self.cfg.get("pink_bgr_rg_min_diff", 20)))
            & ((b.astype(np.int16) - g.astype(np.int16)) > int(self.cfg.get("pink_bgr_bg_min_diff", 5)))
        )
        mask = cv2.bitwise_and(mask_hsv, (mask_bgr.astype(np.uint8) * 255))
        k = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best, best_area = None, 0.0
        for c in contours:
            area = cv2.contourArea(c)
            if area < 5 or area > 420:
                continue
            x, y, w, h = cv2.boundingRect(c)
            if w <= 1 or h <= 1:
                continue
            ratio = w / max(1.0, float(h))
            if ratio < 0.5 or ratio > 1.8:
                continue
            # Marker should be compact.
            if abs(w - h) > 6:
                continue
            if area > best_area:
                best_area = area
                best = Point(x + w // 2, y + h // 2)
        return best

    def is_cycle_busy(self) -> bool:
        return self.current_state in {
            "to_2", "to_3", "to_4", "to_1",
            "start_to_1",
            "pre50_return_to_1", "pre110_return_to_1",
            "loose_loop_to_2", "loose_loop_wait_2",
            "loose_loop_to_3", "loose_loop_wait_3",
            "loose_loop_to_4", "loose_loop_wait_4",
            "loose_loop_to_1",
        }

    def maybe_handle_pink(self):
        if self.last_pink_pos is None:
            return
        if self.current_state == "to_pink":
            return
        if time.time() - self.last_pink_action_t < float(self.cfg.get("pink_action_cooldown_sec", 2.5)):
            return
        if self.is_cycle_busy():
            if not self.pending_pink_task:
                self.pending_pink_task = True
                print("[PINK] detected during cycle, queued until current cycle finishes")
            return
        self.pending_pink_task = False
        self.current_state = "to_pink"
        print("[PINK] detected, start moving to pink target now")

    def maybe_start_queued_pink(self):
        if not self.pending_pink_task:
            return
        if self.last_pink_pos is None:
            return
        self.pending_pink_task = False
        self.pink_missing_count = 0
        self.current_state = "to_pink"
        print("[PINK] cycle finished, now moving to pink target")

    def tap(self, key: str, with_gap: bool = True):
        time.sleep(self._random_fixed_key_delay())
        t = self.cfg["timings"]["key_tap_sec"]
        pydirectinput.keyDown(key)
        time.sleep(t)
        pydirectinput.keyUp(key)
        if with_gap:
            time.sleep(self.cfg["timings"]["between_key_sec"])

    def hold(self, key: str, sec: float):
        pydirectinput.keyDown(key)
        time.sleep(max(0.01, sec))
        pydirectinput.keyUp(key)

    def press_seq(self, keys):
        for k in keys:
            self.tap(k, with_gap=True)

    def _resolve_key_alias(self, token: str) -> str:
        keys = self.cfg.get("keys", {})
        return keys.get(token, token)

    def run_action_script(self, script_name: str):
        scripts = self.cfg.get("point_action_scripts", {})
        actions = scripts.get(script_name)
        if not isinstance(actions, list):
            return
        for action in actions:
            if not isinstance(action, dict):
                continue
            typ = str(action.get("type", "")).strip().lower()
            if typ == "tap":
                key = self._resolve_key_alias(str(action.get("key", "")).strip())
                if key:
                    self.tap(key, with_gap=bool(action.get("with_gap", True)))
            elif typ == "press_seq":
                raw_keys = action.get("keys", [])
                if isinstance(raw_keys, list):
                    keys = [self._resolve_key_alias(str(k).strip()) for k in raw_keys if str(k).strip()]
                    if keys:
                        self.press_seq(keys)
            elif typ == "hold":
                key = self._resolve_key_alias(str(action.get("key", "")).strip())
                sec = float(action.get("sec", 0.05))
                if key:
                    self.hold(key, sec)
            elif typ == "wait":
                sec = float(action.get("sec", 0.0))
                if sec > 0:
                    time.sleep(sec)

    def p(self, idx: int):
        v = self.cfg["points"].get(str(idx))
        return None if not v else Point(int(v[0]), int(v[1]))

    def dist(self, a: Point, b: Point) -> float:
        return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5

    def strictest_arrival_radius(self) -> float:
        candidates = [
            float(self.cfg.get("arrival_radius_point1_px", 9999)),
            float(self.cfg.get("arrival_radius_other_points_strict_px", 9999)),
            float(self.cfg.get("arrival_radius_other_points_loose_px", 9999)),
            float(self.cfg.get("arrival_radius_px", 9999)),
        ]
        positives = [v for v in candidates if v > 0]
        return min(positives) if positives else 3.0

    def radius(self, idx: int, mode: str = "strict") -> float:
        if idx == 1:
            return float(self.cfg.get("arrival_radius_point1_px", 8))
        if mode == "loose":
            return float(self.cfg.get("arrival_radius_other_points_loose_px", 18))
        return float(self.cfg.get("arrival_radius_other_points_strict_px", 12))

    def arrived(self, pos: Point, idx: int, mode: str = "strict") -> bool:
        t = self.p(idx)
        return False if t is None else self.dist(pos, t) <= self.radius(idx, mode)

    def confirm_arrived(self, pos: Point, idx: int, mode: str = "strict") -> bool:
        need = int(self.cfg.get("arrival_confirm_consecutive", 2))
        k = f"{idx}:{mode}"
        if self.arrived(pos, idx, mode):
            self.arrival_counts[k] = self.arrival_counts.get(k, 0) + 1
        else:
            self.arrival_counts[k] = 0
        ok = self.arrival_counts.get(k, 0) >= need
        if ok:
            sec = int(self.cycle_elapsed_sec()) if (self.started and self.cycle_active) else 0
            print(f"[BOT] sec={sec} arrived point={idx} mode={mode}")
            self.arrival_counts[k] = 0
        return ok

    def near_point(self, pos: Point, idx: int) -> bool:
        t = self.p(idx)
        if t is None:
            return False
        x_tol = int(self.cfg.get("find_x_tolerance_px", 5))
        y_tol = int(self.cfg.get("find_y_tolerance_px", 8))
        slack = int(self.cfg.get("point_near_slack_px", 4))
        return abs(pos.x - t.x) <= (x_tol + slack) and abs(pos.y - t.y) <= (y_tol + slack)

    def reached_point1(self, pos: Point) -> bool:
        # Robust point1 arrival to avoid being stuck near point1 due strict radius jitter.
        return self.confirm_arrived(pos, 1, "strict") or self.arrived(pos, 1, "loose") or self.near_point(pos, 1)

    def down_jump_once(self):
        keys = self.cfg["keys"]
        pydirectinput.keyDown(keys["down"])
        time.sleep(0.06)
        pydirectinput.keyDown(keys["jump"])
        time.sleep(max(0.05, self.cfg["timings"]["key_tap_sec"]))
        pydirectinput.keyUp(keys["jump"])
        time.sleep(0.08)
        pydirectinput.keyUp(keys["down"])
        time.sleep(self.cfg["timings"]["between_key_sec"])

    def recover_vertical(self, pos: Point, target: Point) -> bool:
        # Screen/minimap y grows downward:
        # pos.y > target.y => character is below target => should move up.
        pos_minus_target = pos.y - target.y
        th = int(self.cfg.get("vertical_threshold_px", 16))
        if pos_minus_target > th:
            print(f"[BOT] vertical adjust: up (pos.y={pos.y} > target.y={target.y})")
            self.tap(self.cfg["keys"]["upward"])
            return True
        if pos_minus_target < -th:
            print(f"[BOT] vertical adjust: down (pos.y={pos.y} < target.y={target.y})")
            self.down_jump_once()
            return True
        return False

    def move_toward(self, pos: Point, target: Point):
        # Use wrap-aware X delta on minimap (cyclic horizontal map).
        raw_dx = target.x - pos.x
        wrap_w = int(self.cfg["minimap_roi"]["width"])
        if raw_dx > 0:
            alt_dx = raw_dx - wrap_w
        else:
            alt_dx = raw_dx + wrap_w
        dx = raw_dx if abs(raw_dx) <= abs(alt_dx) else alt_dx
        dy = target.y - pos.y
        x_tol = int(self.cfg.get("find_x_tolerance_px", 5))
        y_tol = int(self.cfg.get("find_y_tolerance_px", 8))
        hold_threshold = int(self.cfg.get("hold_move_x_threshold_px", 10))
        keys = self.cfg["keys"]

        if abs(dx) > x_tol:
            direction = keys["right"] if dx > 0 else keys["left"]
            # Human-like movement: hold while far, tap when close.
            if abs(dx) > hold_threshold:
                self.hold(direction, 0.12)
            elif abs(dy) <= y_tol:
                self.tap(direction, with_gap=False)
            else:
                self.hold(direction, 0.06)
            return

        if abs(dy) > y_tol:
            self.recover_vertical(pos, target)
            return

        # Dead-zone guard:
        # When inside x/y tolerance but not truly arrived (strict radius), keep nudging.
        # This avoids getting stuck near points while state cannot advance.
        if dx != 0:
            self.tap(keys["right"] if dx > 0 else keys["left"], with_gap=False)

    def align_x_for_portal(self, pos: Point, source_idx: int) -> bool:
        src = self.p(source_idx)
        if src is None:
            return False
        x_tol = int(self.cfg.get("portal_align_x_tolerance_px", 1))
        dx = src.x - pos.x
        if abs(dx) <= x_tol:
            return True
        key = self.cfg["keys"]["right"] if dx > 0 else self.cfg["keys"]["left"]
        print(f"[BOT] portal align p{source_idx}: pos.x={pos.x} target.x={src.x} dx={dx}")
        self.tap(key, with_gap=False)
        return False

    def kickoff_to2(self):
        keys = self.cfg["keys"]
        self.hold(keys["left"], 1.0)
        pydirectinput.keyDown(keys["left"])
        self.tap(keys["jump"], with_gap=False)
        self.tap(keys["jump"], with_gap=False)
        pydirectinput.keyUp(keys["left"])
        self.state_jump_done["to_2"] = True
        self.current_state = "to_2"

    def move_to1_with_kickoff(self, pos: Point):
        t1 = self.p(1)
        if t1 is None:
            return
        if not self.state_jump_done["to_1"]:
            keys = self.cfg["keys"]
            self.hold(keys["left"], 1.0)
            pydirectinput.keyDown(keys["left"])
            self.tap(keys["jump"], with_gap=False)
            self.tap(keys["jump"], with_gap=False)
            pydirectinput.keyUp(keys["left"])
            self.state_jump_done["to_1"] = True
            return
        self.move_toward(pos, t1)

    def check_timed_events(self):
        if not self.started or not self.cycle_active:
            return
        if self.current_state in {
            "start_to_1",
            "pre50_return_to_1", "pre110_return_to_1",
            "loose_loop_to_2", "loose_loop_wait_2",
            "loose_loop_to_3", "loose_loop_wait_3",
            "loose_loop_to_4", "loose_loop_wait_4",
            "loose_loop_to_1",
        }:
            return
        sec = self.cycle_elapsed_sec()
        if (not self.event110_done) and sec >= 111:
            self.event110_done = True
            self.state_jump_done["to_1"] = False
            self.current_state = "pre110_return_to_1"
            print("[BOT] trigger 111s")
            return
        if (not self.event50_done) and sec >= 54:
            self.event50_done = True
            self.pause_timer_for_54_flow()
            if self.last_seen_pos is not None and self.arrived(self.last_seen_pos, 1, "strict"):
                self.tap(self.cfg["keys"]["left"])
                self.press_seq(["n"])
                self.resume_timer_after_54_n()
                self.current_state = "loose_loop_to_2"
                print("[BOT] trigger 54s (already at point1, enter loose loop directly)")
            else:
                self.state_jump_done["to_1"] = False
                self.current_state = "pre50_return_to_1"
                print("[BOT] trigger 54s")

    def on_hotkeys(self):
        down = {k: (ctypes.windll.user32.GetAsyncKeyState(v) & 0x8000) != 0 for k, v in VK.items()}
        pressed = {k: down[k] and not self.prev_down[k] for k in VK}
        self.prev_down = down

        if pressed["F8"]:
            self.stop = True
            return
        if pressed["F1"] and self.last_seen_pos:
            self.cfg["points"]["1"] = [self.last_seen_pos.x, self.last_seen_pos.y]
            save_config(self.cfg)
            print("[BOT] saved point1")
        if pressed["F2"] and self.last_seen_pos:
            self.cfg["points"]["2"] = [self.last_seen_pos.x, self.last_seen_pos.y]
            save_config(self.cfg)
            print("[BOT] saved point2")
        if pressed["F3"] and self.last_seen_pos:
            self.cfg["points"]["3"] = [self.last_seen_pos.x, self.last_seen_pos.y]
            save_config(self.cfg)
            print("[BOT] saved point3")
        if pressed["F4"] and self.last_seen_pos:
            self.cfg["points"]["4"] = [self.last_seen_pos.x, self.last_seen_pos.y]
            save_config(self.cfg)
            print("[BOT] saved point4")

        if pressed["F7"]:
            print("[BOT] F7 detected")
            if not self.started:
                if self.last_seen_pos is None:
                    print("[BOT] no yellow dot yet")
                else:
                    self.started = True
                    self.current_state = "start_to_1"
                    self.cycle_active = False
                    self.manual_pink_alert_pending = False
                    self.manual_pink_target_event = None
                    self.timer_pause_total = 0.0
                    self.timer_pause_started = None
                    self.event20_done = False
                    self.event50_done = False
                    self.event80_done = False
                    self.event110_done = False
                    self._reset_cycle_random_events()
                    self._schedule_next_attack()
                    self.next_idle_nudge_t = time.time() + 5.0
                    print("[BOT] start requested: go point1 then start timer")
            else:
                self.started = False
                self.cycle_active = False
                self.timer_pause_started = None
                self.manual_pink_alert_pending = False
                self.manual_pink_target_event = None
                self.current_state = "hold_1"
                print("[BOT] canceled")

    def heartbeat(self):
        now = time.time()
        if now - self.last_heartbeat_t < 1.0:
            return
        self.last_heartbeat_t = now
        sec = int(self.cycle_elapsed_sec()) if (self.started and self.cycle_active) else 0
        if self.last_seen_pos is None:
            print(f"[BOT] sec={sec} state={self._state_name()} pos=none")
        else:
            print(f"[BOT] sec={sec} state={self._state_name()} pos=({self.last_seen_pos.x},{self.last_seen_pos.y})")

    def _draw_labeled_point(self, canvas, idx: int, color):
        pt = self.p(idx)
        if pt is None:
            return
        cv2.circle(canvas, (pt.x, pt.y), 6, color, 2)
        cv2.putText(
            canvas,
            str(idx),
            (pt.x + 8, pt.y - 8),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            color,
            2,
            cv2.LINE_AA,
        )

    def render_debug_panel(self):
        if self.last_frame is None:
            return

        map_view = self.last_frame.copy()
        self._draw_labeled_point(map_view, 1, (0, 255, 255))
        self._draw_labeled_point(map_view, 2, (255, 200, 0))
        self._draw_labeled_point(map_view, 3, (255, 200, 0))
        self._draw_labeled_point(map_view, 4, (255, 200, 0))

        if self.last_seen_pos is not None:
            cv2.circle(map_view, (self.last_seen_pos.x, self.last_seen_pos.y), 5, (0, 255, 0), -1)
            cv2.putText(
                map_view,
                "NOW",
                (self.last_seen_pos.x + 8, self.last_seen_pos.y + 12),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 255, 0),
                1,
                cv2.LINE_AA,
            )
        if self.last_pink_pos is not None:
            cv2.circle(map_view, (self.last_pink_pos.x, self.last_pink_pos.y), 7, (255, 0, 255), 2)
            cv2.putText(
                map_view,
                "PINK",
                (self.last_pink_pos.x + 8, self.last_pink_pos.y - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 0, 255),
                1,
                cv2.LINE_AA,
            )

        info_w = 420
        h = map_view.shape[0]
        info_h = max(h, 720)
        info = np.zeros((info_h, info_w, 3), dtype=np.uint8)
        info[:] = (24, 24, 24)

        sec = int(self.cycle_elapsed_sec()) if (self.started and self.cycle_active) else 0
        pos_text = "none" if self.last_seen_pos is None else f"({self.last_seen_pos.x}, {self.last_seen_pos.y})"
        lines = [
            f"STATE: {self._state_name()}",
            f"SECONDS: {sec}",
            f"CURRENT: {pos_text}",
            f"STARTED: {self.started}",
            f"PINK_ON: {self.pink_enabled}",
            f"PINK_QUEUED: {self.pending_pink_task}",
            f"PINK_MISS: {self.pink_missing_count}",
            f"TIMER_PAUSED: {self.timer_pause_started is not None}",
            f"MANUAL_PINK_WAIT: {self.manual_pink_target_event if self.manual_pink_alert_pending else 'no'}",
            "",
            "POINTS:",
        ]
        for i in (1, 2, 3, 4):
            pt = self.p(i)
            lines.append(f"  {i}: none" if pt is None else f"  {i}: ({pt.x}, {pt.y})")

        y = 34
        for line in lines:
            cv2.putText(info, line, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (220, 220, 220), 1, cv2.LINE_AA)
            y += 28

        roi = self.cfg["minimap_roi"]
        roi_lines = [
            "",
            "ROI:",
            f"  left={roi['left']} top={roi['top']}",
            f"  width={roi['width']} height={roi['height']}",
            "",
            "Adjust in window:",
            "  Hold Ctrl + Arrow Keys (move)",
            "  Hold Ctrl + Q / E (width)",
            "  Hold Ctrl + Z / C or X (height)",
            "  P: toggle pink detection",
        ]
        for line in roi_lines:
            cv2.putText(info, line, (16, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (170, 220, 255), 1, cv2.LINE_AA)
            y += 24

        cv2.imshow("Maple Bot Monitor - Map", map_view)
        cv2.imshow("Maple Bot Monitor - Info", info)
        # If user closes either window via X button, stop bot loop cleanly.
        if cv2.getWindowProperty("Maple Bot Monitor - Map", cv2.WND_PROP_VISIBLE) < 1:
            self.stop = True
            return
        if cv2.getWindowProperty("Maple Bot Monitor - Info", cv2.WND_PROP_VISIBLE) < 1:
            self.stop = True
            return
        key = cv2.waitKeyEx(1)
        if key != -1:
            self._handle_debug_key(key)

    def run_once(self):
        frame = self.capture()
        self.last_frame = frame
        pos = self.detect_pos(frame)
        pink = None
        if self.started or self.current_state == "to_pink":
            pink = self.detect_pink_target(frame)
            if pink is not None:
                self.last_pink_pos = pink
                if self.current_state != "to_pink":
                    print(f"[PINK] detected at ({pink.x},{pink.y})")
        self.last_seen_pos = pos
        if pos is None:
            return

        self.on_hotkeys()
        if self.stop:
            return

        self.heartbeat()
        self.maybe_set_manual_pink_alert(pink is not None)
        if self.pink_enabled:
            self.maybe_handle_pink()

        if self.current_state == "to_pink":
            if not self.pink_enabled:
                self.current_state = "hold_1"
                return
            if self.last_pink_pos is None:
                self.pink_missing_count = 0
                self.current_state = "hold_1"
                return
            if pink is None:
                self.pink_missing_count += 1
            else:
                self.pink_missing_count = 0
                self.last_pink_pos = pink

            if self.pink_missing_count >= 3:
                self.tap("space")
                self.last_pink_action_t = time.time()
                self.pink_missing_count = 0
                self.current_state = "hold_1"
                print("[PINK] marker missing 3 times in a row, pressed space")
            else:
                self.move_toward(pos, self.last_pink_pos)
            return

        if not self.started:
            return

        self.check_timed_events()
        cycle_sec = self.cycle_elapsed_sec()
        keys = self.cfg["keys"]
        d_key = keys.get("skill_d", "d")
        f_key = keys.get("skill_f", "f")

        if self.cycle_active:
            if (not self.event_d_done) and cycle_sec >= self.event_d_sec:
                self.tap(d_key)
                self.event_d_done = True
                print(f"[BOT] random D at {cycle_sec:.1f}s (target {self.event_d_sec:.1f}s)")

            if (not self.event_f_done) and cycle_sec >= self.event_f_sec:
                self.tap(f_key)
                self.event_f_done = True
                print(f"[BOT] random F at {cycle_sec:.1f}s (target {self.event_f_sec:.1f}s)")

        if time.time() >= self.next_attack_t:
            self.tap(self.cfg["point1_attack_key"])
            self._schedule_next_attack()

        # Core strict 0s route
        if self.current_state == "to_2":
            if self.confirm_arrived(pos, 2, "strict"):
                self.run_action_script("to_2_arrive")
                self.portal_check_required_23 = False
                self.next_portal_try_t = time.time()
                self.current_state = "to_3"
            else:
                t2 = self.p(2)
                if t2:
                    if not self.state_jump_done["to_2"]:
                        self.kickoff_to2()
                    else:
                        self.move_toward(pos, t2)
            return

        if self.current_state == "to_3":
            if self.confirm_arrived(pos, 3, "strict"):
                self.run_action_script("to_3_arrive")
                self.portal_check_required_23 = False
                self.current_state = "to_4"
            else:
                # After each UP attempt, force at least one coordinate-check cycle
                # before allowing the next UP, to avoid accidental double-portal.
                if self.portal_check_required_23:
                    self.portal_check_required_23 = False
                    return
                if time.time() < self.next_portal_try_t:
                    return
                if self.align_x_for_portal(pos, 2):
                    self.tap(self.cfg["keys"]["up"])
                    self.portal_check_required_23 = True
                    self.next_portal_try_t = time.time() + float(self.cfg.get("portal_retry_sec", 0.08))
            return

        if self.current_state == "to_4":
            if self.confirm_arrived(pos, 4, "strict"):
                self.run_action_script("to_4_arrive")
                self.current_state = "to_1"
                self.state_jump_done["to_1"] = False
            else:
                if time.time() < self.next_portal_try_t:
                    return
                if self.align_x_for_portal(pos, 3):
                    self.tap(self.cfg["keys"]["up"])
                    self.next_portal_try_t = time.time() + float(self.cfg.get("portal_retry_sec", 0.2))
            return

        if self.current_state == "to_1":
            if self.reached_point1(pos):
                self.run_action_script("to_1_arrive")
                self.current_state = "hold_1"
                self.maybe_start_queued_pink()
            else:
                self.move_to1_with_kickoff(pos)
            return

        if self.current_state == "hold_1":
            if not self.arrived(pos, 1, "strict"):
                t1 = self.p(1)
                if t1:
                    self.move_toward(pos, t1)
            elif time.time() >= self.next_idle_nudge_t:
                self.tap(self.cfg["keys"]["left"])
                self.tap(self.cfg["keys"]["right"])
                self.next_idle_nudge_t = time.time() + 5.0
            return

        if self.current_state == "start_to_1":
            if self.reached_point1(pos):
                self.run_action_script("start_to_1_arrive")
                self.cycle_started = time.time()
                self.cycle_active = True
                self.timer_pause_total = 0.0
                self.timer_pause_started = None
                self.event20_done = False
                self.event50_done = False
                self.event80_done = False
                self.event110_done = False
                self._reset_cycle_random_events()
                self.state_jump_done["to_2"] = False
                self.kickoff_to2()
                print("[BOT] reached point1, timer started from 0s")
            else:
                t1 = self.p(1)
                if t1:
                    self.move_toward(pos, t1)
            return

        # 50s flow: return 1 then N then loose big loop with 5s stops at 2/3/4
        if self.current_state == "pre50_return_to_1":
            if self.confirm_arrived(pos, 1, "strict"):
                self.run_action_script("pre50_return_to_1_arrive")
                self.resume_timer_after_54_n()
                self.maybe_finish_manual_pink_alert("54")
                if not self.started:
                    return
                self.current_state = "loose_loop_to_2"
            else:
                t1 = self.p(1)
                if t1:
                    self.move_toward(pos, t1)
            return

        if self.current_state == "loose_loop_to_2":
            if self.confirm_arrived(pos, 2, "loose"):
                self.pause_until = time.time() + 5.0
                self.current_state = "loose_loop_wait_2"
            else:
                t2 = self.p(2)
                if t2:
                    self.move_toward(pos, t2)
            return

        if self.current_state == "loose_loop_wait_2":
            if time.time() >= self.pause_until:
                self.current_state = "loose_loop_to_3"
            return

        if self.current_state == "loose_loop_to_3":
            if self.confirm_arrived(pos, 3, "loose"):
                self.pause_until = time.time() + 5.0
                self.current_state = "loose_loop_wait_3"
            else:
                t3 = self.p(3)
                if t3:
                    self.move_toward(pos, t3)
            return

        if self.current_state == "loose_loop_wait_3":
            if time.time() >= self.pause_until:
                self.current_state = "loose_loop_to_4"
            return

        if self.current_state == "loose_loop_to_4":
            if self.confirm_arrived(pos, 4, "loose"):
                self.pause_until = time.time() + 5.0
                self.current_state = "loose_loop_wait_4"
            else:
                t4 = self.p(4)
                if t4:
                    self.move_toward(pos, t4)
            return

        if self.current_state == "loose_loop_wait_4":
            if time.time() >= self.pause_until:
                self.current_state = "loose_loop_to_1"
                self.state_jump_done["to_1"] = False
            return

        if self.current_state == "loose_loop_to_1":
            if self.reached_point1(pos):
                self.tap(self.cfg["keys"]["left"])
                self.current_state = "hold_1"
                self.maybe_start_queued_pink()
                self.maybe_finish_manual_pink_alert("54")
            else:
                t1 = self.p(1)
                if t1:
                    self.move_toward(pos, t1)
            return

        # 110s flow: return 1 then 6 then reset cycle and restart 0s flow
        if self.current_state == "pre110_return_to_1":
            if self.reached_point1(pos):
                self.run_action_script("pre110_return_to_1_arrive")
                self.cycle_started = time.time()
                self.cycle_active = True
                self.timer_pause_total = 0.0
                self.timer_pause_started = None
                self.event20_done = False
                self.event50_done = False
                self.event80_done = False
                self.event110_done = False
                self._reset_cycle_random_events()
                self.state_jump_done["to_2"] = False
                self.kickoff_to2()
                self.maybe_finish_manual_pink_alert("111")
            else:
                t1 = self.p(1)
                if t1:
                    self.move_toward(pos, t1)

    def run(self):
        print("[BOT] start in 3s")
        time.sleep(3)
        while not self.stop:
            try:
                self.run_once()
            except Exception as e:
                print(f"[ERR] {e}")
            self.render_debug_panel()
            time.sleep(self.current_poll_sec())
        cv2.destroyAllWindows()


if __name__ == "__main__":
    cfg = load_config()
    Bot(cfg).run()





