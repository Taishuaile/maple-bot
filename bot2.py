import ctypes
import random
import time
import winsound

import pydirectinput


VK_F7 = 0x76


def is_key_down(vk: int) -> bool:
    return (ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000) != 0


def beep_toggle():
    try:
        winsound.Beep(1200, 120)
        winsound.Beep(1600, 140)
    except Exception:
        pass


def tap(key: str, hold_sec: float = 0.05):
    pydirectinput.keyDown(key)
    time.sleep(hold_sec)
    pydirectinput.keyUp(key)


def run():
    pydirectinput.PAUSE = 0
    pydirectinput.FAILSAFE = False

    running = False
    prev_f7_down = False
    step = 0
    next_step_t = 0.0

    # 0: left, 1: ctrl, 2: right, 3: ctrl
    seq = ["left", "ctrl", "right", "ctrl"]

    print("[BOT2] Ready. Press F7 to start/stop.")
    while True:
        f7_down = is_key_down(VK_F7)
        f7_pressed = f7_down and not prev_f7_down
        prev_f7_down = f7_down

        if f7_pressed:
            running = not running
            beep_toggle()
            if running:
                step = 0
                next_step_t = time.time()
                print("[BOT2] Started.")
            else:
                print("[BOT2] Stopped.")
            # Debounce
            time.sleep(0.15)

        if running and time.time() >= next_step_t:
            tap(seq[step], hold_sec=0.05)
            step = (step + 1) % len(seq)
            next_step_t = time.time() + random.uniform(0.5, 0.8)

        time.sleep(0.01)


if __name__ == "__main__":
    run()
