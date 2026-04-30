import ctypes
import json
import time
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
        return json.load(f)


def save_config(cfg: dict) -> None:
    with CONFIG_PATH.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class Bot:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.started = False
        self.stop = False

        self.mss_ctx = mss.MSS()
        self.monitor = self.mss_ctx.monitors[int(cfg["monitor_index"])]

        self.current_state = "hold_1"
        self.cycle_started = time.time()
        self.state_started = time.time()

        self.next_attack_t = 0.0
        self.next_idle_nudge_t = 0.0
        self.pause_until = 0.0

        self.event50_done = False
        self.event110_done = False

        self.state_jump_done = {"to_1": False, "to_2": False}
        self.arrival_counts = {}

        self.last_seen_pos = None
        self.last_heartbeat_t = 0.0
        self.prev_down = {k: False for k in VK}

        pydirectinput.PAUSE = 0
        pydirectinput.FAILSAFE = False

    def _state_name(self) -> str:
        return self.current_state

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

    def tap(self, key: str, with_gap: bool = True):
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

    def p(self, idx: int):
        v = self.cfg["points"].get(str(idx))
        return None if not v else Point(int(v[0]), int(v[1]))

    def dist(self, a: Point, b: Point) -> float:
        return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5

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
            sec = int(time.time() - self.cycle_started) if self.started else 0
            print(f"[BOT] sec={sec} arrived point={idx} mode={mode}")
            self.arrival_counts[k] = 0
        return ok

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
        dy = target.y - pos.y
        th = int(self.cfg.get("vertical_threshold_px", 16))
        if dy < -th:
            self.tap(self.cfg["keys"]["upward"])
            return True
        if dy > th:
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
        fine = int(self.cfg.get("fine_nudge_x_px", 18))
        keys = self.cfg["keys"]

        if abs(dx) > x_tol:
            direction = keys["right"] if dx > 0 else keys["left"]
            if abs(dy) <= y_tol and abs(dx) <= fine:
                self.tap(direction, with_gap=False)
            else:
                self.hold(direction, 0.06 if abs(dx) < 20 else 0.12)
            return

        if abs(dy) > y_tol:
            self.recover_vertical(pos, target)

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
        if not self.started:
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
        sec = time.time() - self.cycle_started
        if (not self.event110_done) and sec >= 110:
            self.event110_done = True
            self.state_jump_done["to_1"] = False
            self.current_state = "pre110_return_to_1"
            print("[BOT] trigger 110s")
            return
        if (not self.event50_done) and sec >= 50:
            self.event50_done = True
            self.state_jump_done["to_1"] = False
            self.current_state = "pre50_return_to_1"
            print("[BOT] trigger 50s")

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
                    self.cycle_started = time.time()
                    self.event20_done = False
                    self.event50_done = False
                    self.event80_done = False
                    self.event110_done = False
                    self.next_attack_t = time.time() + 0.1
                    self.next_idle_nudge_t = time.time() + 5.0
                    print("[BOT] start requested: go point1 then start timer")
            else:
                self.started = False
                self.current_state = "hold_1"
                print("[BOT] canceled")

    def heartbeat(self):
        now = time.time()
        if now - self.last_heartbeat_t < 1.0:
            return
        self.last_heartbeat_t = now
        sec = int(now - self.cycle_started) if self.started else 0
        if self.last_seen_pos is None:
            print(f"[BOT] sec={sec} state={self._state_name()} pos=none")
        else:
            print(f"[BOT] sec={sec} state={self._state_name()} pos=({self.last_seen_pos.x},{self.last_seen_pos.y})")

    def run_once(self):
        frame = self.capture()
        pos = self.detect_pos(frame)
        self.last_seen_pos = pos
        if pos is None:
            return

        self.on_hotkeys()
        if self.stop:
            return

        self.heartbeat()

        if not self.started:
            return

        self.check_timed_events()

        if time.time() >= self.next_attack_t:
            self.tap(self.cfg["point1_attack_key"])
            self.next_attack_t = time.time() + float(self.cfg["point1_attack_interval_sec"])

        # Core strict 0s route
        if self.current_state == "to_2":
            if self.confirm_arrived(pos, 2, "strict"):
                self.press_seq([self.cfg["keys"]["skill_m"], self.cfg["keys"]["up"]])
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
                self.press_seq([self.cfg["keys"]["skill_m"], self.cfg["keys"]["up"]])
                self.current_state = "to_4"
            else:
                t3 = self.p(3)
                if t3:
                    self.move_toward(pos, t3)
            return

        if self.current_state == "to_4":
            if self.confirm_arrived(pos, 4, "strict"):
                self.press_seq([self.cfg["keys"]["skill_m"]])
                self.current_state = "to_1"
                self.state_jump_done["to_1"] = False
            else:
                t4 = self.p(4)
                if t4:
                    self.move_toward(pos, t4)
            return

        if self.current_state == "to_1":
            if self.confirm_arrived(pos, 1, "strict"):
                self.tap(self.cfg["keys"]["left"])
                self.current_state = "hold_1"
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
            if self.confirm_arrived(pos, 1, "strict"):
                self.tap(self.cfg["keys"]["left"])
                self.cycle_started = time.time()
                self.event20_done = False
                self.event50_done = False
                self.event80_done = False
                self.event110_done = False
                self.press_seq(["n"])
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
                self.tap(self.cfg["keys"]["left"])
                self.press_seq(["n"])
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
            if self.confirm_arrived(pos, 1, "strict"):
                self.tap(self.cfg["keys"]["left"])
                self.current_state = "hold_1"
            else:
                t1 = self.p(1)
                if t1:
                    self.move_toward(pos, t1)
            return

        # 110s flow: return 1 then 6 then reset cycle and restart 0s flow
        if self.current_state == "pre110_return_to_1":
            if self.confirm_arrived(pos, 1, "strict"):
                self.tap(self.cfg["keys"]["left"])
                self.press_seq(["6"])
                self.cycle_started = time.time()
                self.event20_done = False
                self.event50_done = False
                self.event80_done = False
                self.event110_done = False
                self.press_seq(["n"])
                self.state_jump_done["to_2"] = False
                self.kickoff_to2()
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
            time.sleep(self.cfg["timings"]["poll_sec"])


if __name__ == "__main__":
    cfg = load_config()
    Bot(cfg).run()
