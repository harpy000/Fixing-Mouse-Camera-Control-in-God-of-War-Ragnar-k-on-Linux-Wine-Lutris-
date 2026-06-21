# Fixing-Mouse-Camera-Control-in-God-of-War-Ragnar-k-on-Linux-Wine-Lutris-
python script that fix the issue of the camera control via mouse  

# mouse2pad

Fix mouse camera control in **God of War Ragnarök** (and other GameInput-based games) on Linux/Wine.

A small Python daemon that bridges raw mouse movement into a virtual Xbox controller, working around games whose camera-look is hard-wired to Microsoft's **GameInput API** — an API that doesn't work under Wine.

---

# The Problem

You installed God of War Ragnarök via Lutris/Wine.

It runs.

Audio works.

Keyboard works.

The mouse does nothing to the camera.

No response in menus, no response in gameplay — completely dead.

This isn't Wine-specific misery, either — some players hit this on real Windows too.

---

# The Cause

GoW Ragnarök's PC port reads camera-look through Microsoft's **GameInput API**, not the classic `WM_MOUSEMOVE` messages older games use.

GameInput depends on a Windows service:

```text
GameInputSvc
```

If that service isn't running, mouse-look silently dies — but controller input doesn't go through this path, so a gamepad's camera control keeps working fine.

On Windows, the fix is usually:

1. Install `GameInputRedist.msi`
2. Make sure `GameInputSvc` is set to **Automatic**
3. Restart the service

Under Wine, this fix doesn't work.

Proof:

```bash
wine sc query GameInputSvc

...
err:sc:query_service failed to open service 1060
```

Error 1060 means:

> The specified service does not exist.

The redistributable installs without complaint, but Wine's Service Control Manager never actually registers a working service.

Trying to manage it via `services.msc` doesn't help either — it crashes Wine outright with an unhandled page fault.

There is currently no way to make `GameInputSvc` actually run under Wine.

---

# The Fix

Don't fight GameInput.

Bypass it.

Controller-based camera input already works fine, so this script:

* Reads raw mouse movement from the Linux input layer (`evdev`)
* Creates a virtual Xbox 360 controller via `uinput`
* Translates mouse deltas into right-stick movement
* Smooths movement to feel like analog input instead of jagged mouse noise
* Forwards mouse clicks (including side buttons) to mapped gamepad buttons

The game sees a controller.

It has no idea a mouse is actually driving it.

---

# What Works

| Feature         | Status                                          |
| --------------- | ----------------------------------------------- |
| Camera Look     |  Fixed — driven by mouse → virtual right stick |
| Movement (WASD) | Untouched — keyboard works normally             |
| Mouse Clicks    | Re-routed to gamepad buttons                    |
| Desktop Cursor  | Frozen while active — toggle with F9            |

---

# Requirements

* Python 3
* evdev
* Root privileges (required for uinput)
* Any modern Linux distribution with uinput support

Install dependencies:

```bash
pip install evdev --break-system-packages
```

Arch / CachyOS:

```bash
sudo pacman -S python-evdev
```

---

# Usage

Start the daemon:

```bash
sudo python3 mouse2pad.py
```

If multiple mice are detected:

```text
Available mouse-like devices:
  [0] /dev/input/event25  AlpsPS/2 ALPS DualPoint Stick
  [1] /dev/input/event22  Touchpad
  [2] /dev/input/event5   INSTANT USB GAMING MOUSE

Pick a device number: 2
```

Launch the game through Lutris/Wine as usual.

The game automatically detects the virtual Xbox controller.

---

## Important

Start the script **after** you're past any menu that requires a real cursor.

When active:

* The desktop cursor stops moving.
* Mouse movement is exclusively used for stick emulation.
* Press **F9** at any time to pause/resume capture.

---

# Command-Line Options

```bash
sudo python3 mouse2pad.py --sensitivity 900 --debug
```

| Flag            | Default  | Description                     |
| --------------- | -------- | ------------------------------- |
| `--device`      | prompted | Specific mouse event device     |
| `--sensitivity` | 400      | Stick deflection per mouse unit |
| `--debug`       | off      | Print live stick values         |

---

# Feel Tuning

These values live near the top of the script.

Edit and re-run:

```python
SMOOTHING = 0.35        # Higher = snappier
                         # Lower = smoother but more lag

DECAY = 0.85            # Recentering speed

IDLE_BEFORE_DECAY = 0.05
                         # Seconds before recentering starts
```

---

# Button Mapping

Default mapping based on the in-game PS4-style scheme:

| Mouse Input        | Gamepad Button | Action             |
| ------------------ | -------------- | ------------------ |
| Left Click         | R1             | Light Attack       |
| Right Click        | R2             | Heavy Attack       |
| Middle Click       | X              | Dodge              |
| Side Button 1 (M4) | Square         | Axe Throw / Recall |
| Side Button 2 (M5) | L1             | Block / Parry      |

Configuration:

```python
BUTTON_MAP = {
    ecodes.BTN_LEFT:   ecodes.BTN_TR,
    ecodes.BTN_MIDDLE: ecodes.BTN_SOUTH,
    ecodes.BTN_SIDE:   ecodes.BTN_WEST,
    ecodes.BTN_EXTRA:  ecodes.BTN_TL,
}
```

Remap freely to match your own bindings.

---

# How It Works

```text
┌─────────────┐   evdev    ┌──────────────────┐   uinput   ┌──────────────────┐
│  Real Mouse │ ─────────▶ │   mouse2pad.py   │ ─────────▶ │ Virtual Xbox Pad │
│ /dev/input/*│  raw move  │ smooth + remap   │  ABS_RX/RY │ /dev/input/event*│
└─────────────┘  + clicks  └──────────────────┘ + buttons  └──────────────────┘
                                                                  │
                                                                  ▼
                                                       Wine sees a real
                                                       controller. The game's
                                                       working controller
                                                       camera path takes over.
```

Mouse input never touches GameInput's broken path.

Instead:

1. Mouse movement is captured at the Linux kernel level.
2. Converted into virtual right-stick movement.
3. Emitted through a virtual Xbox controller.
4. Consumed by the game's controller camera system.

---

# Caveats

* This is a workaround, not a system-wide fix.
* Requires root privileges each run.
* Relies entirely on the game's controller input path.
* If a game eventually requires GameInput for controller support too, this won't help.
* Adds a small smoothing layer compared to native mouse input.

Input latency is generally negligible but worth mentioning for highly sensitive players.

---

# Future

If Wine eventually gains proper support for registering and running `GameInputSvc`, the native Windows fix may begin working and this project will become unnecessary.

Until then, this works regardless of:

* Wine version
* GE-Proton build
* Staging patches

Because it operates entirely below the Wine compatibility layer.

---

# Contributing

Issues and pull requests are welcome.

Especially useful:

* Alternative button mapping presets
* Support for additional games
* Sensitivity tuning improvements

---

# License

MIT


The default mapping (based on the in-game PS4-style control scheme) is:

Mouse inputGamepad buttonActionLeft clickR1Light attackRight clickR2 (analog trigger)Heavy attackMiddle clickXDodgeSide button 1 (M4)SquareSpecial weapon ability (axe throw/recall)Side button 2 (M5)L1Block / parry

This is a plain Python dictionary (BUTTON_MAP) near the top of the script — straightforward to remap to your own bindings if yours differ.
