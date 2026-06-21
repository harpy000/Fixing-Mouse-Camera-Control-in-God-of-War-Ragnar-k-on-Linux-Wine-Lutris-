#!/usr/bin/env python3
"""
mouse2pad.py — Mouse-to-virtual-gamepad camera bridge

Why this exists:
God of War Ragnarok (and apparently some other UWP-style PC ports) read camera
look input through Microsoft's GameInput API. Under Wine, the GameInputSvc
service doesn't exist and the GameInput runtime has no working backend, so
mouse-look never reaches the game even though the game launches and runs fine
otherwise. Controller input does NOT go through that broken path, so this
script reads raw mouse movement and re-emits it as right-analog-stick input
on a virtual Xbox 360 controller. The game sees a controller moving the
camera, which already works.

This does NOT touch movement (WASD) or any other keys — keyboard still works
normally alongside this. Only the right stick (camera look) is driven by your
mouse. Toggle capture on/off with F9 so your mouse stays normal for desktop
use until you actually start the game.

Requires root (uinput device creation) and the 'evdev' package.

Usage:
    sudo python3 mouse2pad.py [--device /dev/input/eventX] [--sensitivity 4.0]

If you don't pass --device, it will list available mice and let you pick one.
"""

import argparse
import sys
import time
import threading

try:
    from evdev import InputDevice, UInput, ecodes, list_devices, categorize
except ImportError:
    print("Missing dependency. Install with:")
    print("  pip install evdev --break-system-packages")
    sys.exit(1)


STICK_MIN = -32768
STICK_MAX = 32767
STICK_CENTER = 0

# Cap how far the virtual stick can deflect from center. Set close to full
# range -- many games apply their own deadzone (often 10-20% of the stick's
# travel), so capping our OWN range too aggressively can leave us entirely
# inside the game's deadzone, making it feel like input does nothing.
STICK_DEFLECTION_CAP = 32000

# How long (seconds) the mouse must be idle before we start pulling the
# stick back toward center. While the mouse is actively moving, decay is
# skipped entirely so it never fights live input.
IDLE_BEFORE_DECAY = 0.05

# How quickly the virtual stick relaxes back to center once idle (since a
# real stick self-centers, a mouse doesn't). DECAY closer to 1.0 = slower.
DECAY = 0.85

# How much the written (output) position moves toward the raw target each
# tick. Lower = smoother/more lag, higher = snappier/more jittery. This is
# the main knob for fixing "shaky" camera movement.
SMOOTHING = 0.35

# How often the output thread ticks and actually writes to the virtual pad.
# This is decoupled from raw mouse event timing on purpose -- mice report
# movement in irregular bursts, but a real analog stick's position is read
# at a steady rate, so writing at a steady rate here is what makes it feel
# like a stick instead of looking jagged.
OUTPUT_TICK_INTERVAL = 1.0 / 250  # 250Hz

TOGGLE_KEY = ecodes.KEY_F9

# Mapping based on the in-game controller diagram:
#   R1     = Light attack              -> left click
#   R2     = Heavy attack              -> right click (analog trigger, see
#                                          RIGHT_CLICK_AS_TRIGGER handling below)
#   Square = Special weapon ability     -> M4 (mouse side button 1)
#   L1     = Block / dodge-parry        -> M5 (mouse side button 2)
#   Circle = Interact, X = Dodge, Triangle = Atreus command (unused mouse
#            buttons currently fall back to these if you have more buttons)
BUTTON_MAP = {
    ecodes.BTN_LEFT: ecodes.BTN_TR,      # R1 -> light attack
    ecodes.BTN_MIDDLE: ecodes.BTN_SOUTH,  # X -> dodge
    ecodes.BTN_SIDE: ecodes.BTN_WEST,     # M4 -> Square (axe special ability)
    ecodes.BTN_EXTRA: ecodes.BTN_TL,      # M5 -> L1 (block/dodge-parry)
}

# Right click is handled separately below since it maps to the R2 ANALOG
# TRIGGER (ABS_RZ), not a digital button -- the game expects R2 as a trigger
# axis even though you're pressing/releasing a mouse button.
RIGHT_CLICK_AS_TRIGGER = True


def pick_mouse_device():
    devices = [InputDevice(path) for path in list_devices()]
    mice = [d for d in devices if ecodes.EV_REL in d.capabilities()
            and ecodes.REL_X in d.capabilities().get(ecodes.EV_REL, [])]

    if not mice:
        print("No mouse-like devices found in /dev/input/. "
              "Try running with sudo, or pass --device explicitly.")
        sys.exit(1)

    print("Available mouse-like devices:")
    for i, d in enumerate(mice):
        print(f"  [{i}] {d.path}  {d.name}")

    if len(mice) == 1:
        print(f"Auto-selecting the only one found: {mice[0].path}")
        return mice[0]

    choice = input("Pick a device number: ").strip()
    try:
        return mice[int(choice)]
    except (ValueError, IndexError):
        print("Invalid choice.")
        sys.exit(1)


def make_virtual_gamepad():
    capabilities = {
        ecodes.EV_KEY: [
            ecodes.BTN_A, ecodes.BTN_B, ecodes.BTN_X, ecodes.BTN_Y,
            ecodes.BTN_TL, ecodes.BTN_TR, ecodes.BTN_SELECT, ecodes.BTN_START,
            ecodes.BTN_MODE, ecodes.BTN_THUMBL, ecodes.BTN_THUMBR,
            ecodes.BTN_DPAD_UP, ecodes.BTN_DPAD_DOWN,
            ecodes.BTN_DPAD_LEFT, ecodes.BTN_DPAD_RIGHT,
        ],
        ecodes.EV_ABS: [
            (ecodes.ABS_X, (0, STICK_MIN, STICK_MAX, 0, 0)),
            (ecodes.ABS_Y, (0, STICK_MIN, STICK_MAX, 0, 0)),
            (ecodes.ABS_RX, (0, STICK_MIN, STICK_MAX, 0, 0)),
            (ecodes.ABS_RY, (0, STICK_MIN, STICK_MAX, 0, 0)),
            (ecodes.ABS_Z, (0, 0, 255, 0, 0)),
            (ecodes.ABS_RZ, (0, 0, 255, 0, 0)),
            (ecodes.ABS_HAT0X, (0, -1, 1, 0, 0)),
            (ecodes.ABS_HAT0Y, (0, -1, 1, 0, 0)),
        ],
    }
    return UInput(capabilities, name="Mouse2Pad Virtual Xbox Controller",
                   vendor=0x045e, product=0x028e, version=0x110)


class CameraBridge:
    def __init__(self, mouse_path, sensitivity, debug=False):
        self.mouse = InputDevice(mouse_path)
        self.sensitivity = sensitivity
        self.pad = make_virtual_gamepad()
        self.active = True
        self.target_x = 0.0
        self.target_y = 0.0
        self.current_x = 0.0
        self.current_y = 0.0
        self.lock = threading.Lock()
        self._running = True
        self.debug = debug
        # Tracks when the mouse last actually moved. Decay only kicks in
        # after a short idle gap -- previously it ran every tick regardless,
        # which fought live input and made movement feel held-back/sluggish.
        self.last_move_time = 0.0

    def output_loop(self):
        """Runs at a steady tick rate, decoupled from raw mouse event timing.
        While the mouse is actively moving, the target is left alone (no
        decay fighting it). Once movement has been idle for IDLE_BEFORE_DECAY
        seconds, the target relaxes back toward center, mimicking a stick
        self-centering when released."""
        last_x_written = None
        last_y_written = None
        while self._running:
            with self.lock:
                if self.active:
                    idle_time = time.monotonic() - self.last_move_time
                    if idle_time > IDLE_BEFORE_DECAY:
                        self.target_x *= DECAY
                        self.target_y *= DECAY
                        if abs(self.target_x) < 50:
                            self.target_x = 0
                        if abs(self.target_y) < 50:
                            self.target_y = 0

                    self.current_x += (self.target_x - self.current_x) * SMOOTHING
                    self.current_y += (self.target_y - self.current_y) * SMOOTHING

                    x_int = int(self.current_x)
                    y_int = int(self.current_y)

                    if x_int != last_x_written:
                        self.pad.write(ecodes.EV_ABS, ecodes.ABS_RX, x_int)
                        last_x_written = x_int
                    if y_int != last_y_written:
                        self.pad.write(ecodes.EV_ABS, ecodes.ABS_RY, y_int)
                        last_y_written = y_int
                    if x_int != last_x_written or y_int != last_y_written:
                        self.pad.syn()
            time.sleep(OUTPUT_TICK_INTERVAL)

    def read_loop(self):
        print(f"Reading from {self.mouse.path} ({self.mouse.name})")
        print("Capture is ACTIVE. Press F9 to toggle pause/resume.")
        print("Ctrl+C to quit.")

        # Grab the device so the OS/desktop doesn't also consume these
        # mouse-move events while we're forwarding them to the virtual pad.
        # Comment out grab() if you want the mouse to ALSO keep moving your
        # normal desktop cursor at the same time (useful for menus).
        try:
            self.mouse.grab()
        except OSError:
            print("Warning: could not grab device exclusively "
                  "(continuing without exclusive grab).")

        for event in self.mouse.read_loop():
            if not self._running:
                break

            if event.type == ecodes.EV_KEY and event.code == TOGGLE_KEY and event.value == 1:
                with self.lock:
                    self.active = not self.active
                    state = "ACTIVE" if self.active else "PAUSED"
                    print(f"[mouse2pad] Capture {state}")
                    if not self.active:
                        self.target_x = 0
                        self.target_y = 0
                        self.current_x = 0
                        self.current_y = 0
                        self.pad.write(ecodes.EV_ABS, ecodes.ABS_RX, 0)
                        self.pad.write(ecodes.EV_ABS, ecodes.ABS_RY, 0)
                        self.pad.syn()
                continue

            if not self.active:
                continue

            if event.type == ecodes.EV_KEY and event.code == ecodes.BTN_RIGHT:
                # Right click -> R2 analog trigger, fully pressed or released.
                trigger_val = 255 if event.value == 1 else 0
                self.pad.write(ecodes.EV_ABS, ecodes.ABS_RZ, trigger_val)
                self.pad.syn()
                continue

            if event.type == ecodes.EV_KEY and event.code in BUTTON_MAP:
                pad_button = BUTTON_MAP[event.code]
                # event.value: 1 = press, 0 = release, 2 = autorepeat (ignore)
                if event.value in (0, 1):
                    self.pad.write(ecodes.EV_KEY, pad_button, event.value)
                    self.pad.syn()
                continue

            if event.type == ecodes.EV_REL:
                with self.lock:
                    self.last_move_time = time.monotonic()
                    if event.code == ecodes.REL_X:
                        self.target_x += event.value * self.sensitivity
                        self.target_x = max(-STICK_DEFLECTION_CAP,
                                             min(STICK_DEFLECTION_CAP, self.target_x))
                        if self.debug:
                            print(f"REL_X raw={event.value:+4d}  target_x={int(self.target_x):+6d}  "
                                  f"current_x={int(self.current_x):+6d}")
                    elif event.code == ecodes.REL_Y:
                        self.target_y += event.value * self.sensitivity
                        self.target_y = max(-STICK_DEFLECTION_CAP,
                                             min(STICK_DEFLECTION_CAP, self.target_y))
                        if self.debug:
                            print(f"REL_Y raw={event.value:+4d}  target_y={int(self.target_y):+6d}  "
                                  f"current_y={int(self.current_y):+6d}")

    def stop(self):
        self._running = False
        try:
            self.mouse.ungrab()
        except Exception:
            pass
        self.pad.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", help="Path to mouse event device, e.g. /dev/input/event5")
    parser.add_argument("--sensitivity", type=float, default=400.0,
                         help="Mouse-to-stick sensitivity multiplier (default: 400.0)")
    parser.add_argument("--debug", action="store_true",
                         help="Print live stick values as you move the mouse")
    args = parser.parse_args()

    if args.device:
        mouse_path = args.device
    else:
        mouse_path = pick_mouse_device().path

    bridge = CameraBridge(mouse_path, args.sensitivity, debug=args.debug)

    output_thread = threading.Thread(target=bridge.output_loop, daemon=True)
    output_thread.start()

    try:
        bridge.read_loop()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        bridge.stop()


if __name__ == "__main__":
    main()
