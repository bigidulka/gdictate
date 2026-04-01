# gdictate

Free streaming voice dictation for Linux using Google Speech via Chrome's Web Speech API.

Press a hotkey, speak, release — text is pasted into any app.

## How it works

```
Hotkey (evdev) → Python daemon ↔ WebSocket ↔ Chrome (hidden) → Google Speech API
                                                                      ↓
                                              Text pasted via wl-copy + ydotool
```

Chrome runs minimized and hidden from taskbar/Alt+Tab. It only serves as a bridge to Google's free speech recognition — no API keys needed.

## Features

- **Streaming transcription** — see words as you speak (KDE OSD / notify-send on GNOME, Sway, Hyprland)
- **System tray icon** — colored status indicator + left-click to toggle recording
- **Global hotkey** — configurable key combo via evdev (default: Ctrl+Alt)
- **Auto-paste** — result pasted into focused window via clipboard
- **Auto-setup** — detects microphone, browser permissions, DE, and configures everything on first run
- **Zero cost** — uses Google Speech through Chrome, no API keys or accounts

## Requirements

- **OS:** Linux (Arch, Fedora, Ubuntu/Debian)
- **DE:** KDE Plasma 6 recommended (full OSD support); GNOME/Sway/Hyprland work with notify-send fallback
- **Display:** Wayland
- **Browser:** Google Chrome or Chromium
- **Python:** 3.10+

## Quick start

```bash
git clone https://github.com/bigidulka/gdictate.git
cd gdictate
./install.sh        # installs deps (Arch Linux)
python gdictate.py  # that's it
```

On first launch, Chrome opens automatically to request microphone permission. Click "Allow", close Chrome — it restarts hidden and you're ready to dictate. This only happens once.

## Usage

```bash
python gdictate.py                          # default: Ctrl+Alt, ru-RU
python gdictate.py --key CTRL+SUPER         # custom hotkey
python gdictate.py --lang en-US             # English
python gdictate.py --test                   # 5s test, no hotkey
python gdictate.py --no-ui                  # terminal only, no tray/OSD
python gdictate.py --debug                  # show WebSocket messages
python gdictate.py --setup                  # force re-setup browser permissions
```

### Hotkey

| Combo | Description |
|---|---|
| **Ctrl+Alt** (default) | Press to start recording, press again to stop and paste |
| `--key CTRL+SUPER` | Use Super instead of Alt |
| `--key CTRL+SHIFT` | Use Shift instead of Alt |

### Languages

Default is `ru-RU`. Pass `--lang` with any [BCP-47 language tag](https://www.techonthenet.com/js/language_tags.php):

```bash
python gdictate.py --lang en-US    # English
python gdictate.py --lang de-DE    # German
python gdictate.py --lang uk-UA    # Ukrainian
```

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
