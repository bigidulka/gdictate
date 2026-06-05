# gdictate

Version 0.2.0

Free streaming voice dictation for Linux using Google Speech via Chrome's Web Speech API.

Hold a hotkey, speak, release — text is pasted into any app.

## How it works

```
Hotkey (evdev) → Python daemon ↔ WebSocket ↔ Chrome (hidden) → Google Speech API
                                                                      ↓
                                              Text pasted via wl-copy + ydotool
```

Chrome runs minimized and hidden from taskbar/Alt+Tab. It only serves as a bridge to Google's free speech recognition — no API keys needed.

## Features

- **Two hold-to-talk channels** — `Alt+Left` records your microphone, `Alt+Right` records the current speaker output
- **Streaming transcription** — see words as you speak (KDE OSD / notify-send on GNOME, Sway, Hyprland)
- **System tray icon** — colored status indicator + left-click to toggle recording
- **Global hotkey** — hold-mode via evdev, with old toggle mode still available
- **Auto-paste** — result pasted into focused window via clipboard
- **Auto-setup** — detects microphone, speaker monitor, browser permissions, DE, and configures everything on first run
- **Zero cost** — uses Google Speech through Chrome, no API keys or accounts

## Requirements

- **OS:** Linux (Arch, Fedora, Ubuntu/Debian)
- **DE:** KDE Plasma 6 recommended (full OSD support); GNOME/Sway/Hyprland work with notify-send fallback
- **Display:** Wayland
- **Browser:** Google Chrome or Chromium
- **Python:** 3.10+
- **Groups:** your user must be in `input` for global hotkeys

## Quick start

```bash
git clone https://github.com/bigidulka/gdictate.git
cd gdictate
./install.sh        # installs deps (Arch Linux)
python gdictate.py  # that's it
```

For Arch, if global hotkeys or paste do not work:

```bash
sudo usermod -aG input "$USER"
systemctl --user enable --now ydotool.service
```

Log out and log back in after changing groups. Check:

```bash
id -nG | tr ' ' '\n' | grep '^input$'
python - <<'PY'
import evdev
print(evdev.list_devices())
PY
```

On first launch, Chrome opens automatically to request microphone permission. Click "Allow", close Chrome — it restarts hidden and you're ready to dictate. This only happens once.

## Usage

```bash
python gdictate.py                          # default: hold Alt+Left/Right, ru-RU
python gdictate.py --version                # show version
python gdictate.py --bind-mode toggle       # old toggle mode
python gdictate.py --bind-mode toggle --key CTRL+SUPER
python gdictate.py --lang en-US             # English
python gdictate.py --source both            # microphone + speaker output
python gdictate.py --test                   # 5s test, no hotkey
python gdictate.py --test --no-paste        # test without wl-copy/ydotool
python gdictate.py --no-ui                  # terminal only, no tray/OSD
python gdictate.py --debug                  # show WebSocket messages
python gdictate.py --setup                  # force re-setup browser permissions
```

### Hotkey

| Combo | Description |
|---|---|
| **Alt+Left** (default) | Hold while you speak; records microphone. |
| **Alt+Right** (default) | Hold while the other side speaks; records current speaker output. |
| `--bind-mode toggle --key CTRL+ALT` | Old toggle mode: press to start, press again to stop and paste. |
| `--bind-mode toggle --key CTRL+SUPER` | Use Super instead of Alt in toggle mode. |

### Languages

Default is `ru-RU`. Pass `--lang` with any [BCP-47 language tag](https://www.techonthenet.com/js/language_tags.php):

```bash
python gdictate.py --lang en-US    # English
python gdictate.py --lang de-DE    # German
python gdictate.py --lang uk-UA    # Ukrainian
```

### Audio source

| Source | Description |
|---|---|
| `--source mic` | Default. Use the best real microphone source. |
| `--source speakers` | Use the monitor of the current default speaker output. |
| `--source both` | Create a temporary PulseAudio/PipeWire mixed source from microphone + speaker monitor. |

In default dual-hold mode, `Alt+Right` auto-detects the current default speaker sink every time recording starts. It listens to that sink's monitor source only; apps manually routed to another sink are not captured until that sink becomes default.

`--source both` routes Chrome to `gdictate_mix_source` while gdictate runs, then restores the previous default source and unloads its temporary modules on exit.

## Compatibility

| System | Support |
|---|---|
| Arch + KDE Plasma 6 + Wayland | Tested, full OSD + tray |
| Fedora / Ubuntu + KDE + Wayland | `./install.sh` supported, full OSD + tray |
| GNOME / Sway / Hyprland + Wayland | Works — tray icon + notify-send for streaming & final text |
| X11 | Not supported (uses wl-copy, ydotool on Wayland) |
| macOS / Windows | Not supported |

## Architecture

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  evdev      │     │  Python daemon       │     │  Chrome (hidden) │
│  hotkey     │────>│  aiohttp WS server   │<───>│  Web Speech API  │
│  listener   │     │  state machine       │     │  (speech-proxy)  │
└─────────────┘     └──────┬───────────────┘     └────────┬────────┘
                           │                              │
                    ┌──────┴──────┐               ┌───────┴────────┐
                    │ wl-copy +   │               │ Google Speech  │
                    │ ydotool     │               │ servers (free) │
                    │ (paste)     │               └────────────────┘
                    └─────────────┘
```

**Files:**
- `gdictate.py` — main daemon: WebSocket server, Chrome launcher, hotkey listener, paste
- `overlay.py` — OSD overlay (KDE native / notify-send fallback) + system tray icon with click-to-toggle
- `speech-proxy.html` — Chrome page running `webkitSpeechRecognition`

## License

MIT
