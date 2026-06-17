# gdictate

Desktop dictation, live transcription, and speaker capture for Linux and Windows.

gdictate turns speech into text where you already work. Hold a hotkey, speak, and the app sends recognized text to the focused window. It uses a local desktop shell, a Python daemon, and a browser Web Speech bridge, so there is no Google Cloud setup and no paid API key.

[Download latest](https://github.com/bigidulka/gdictate/releases/latest) · [Project page](https://bigidulka.github.io/gdictate/) · [Build workflow](.github/workflows/release.yml)

## What It Does

- **Two hold-to-talk channels:** one bind for your microphone, one bind for speaker output.
- **Live popup:** compact lower-center transcript overlay with timer and voice level strip.
- **Click-through overlay:** the popup can stay above apps without stealing clicks or focus.
- **Sequential paste:** confirmed chunks can be pasted while you dictate, with final fallback on release.
- **Compact GUI:** one main app screen with a focused settings tab.
- **Shared core:** GUI, tray, daemon, and CLI use the same settings and runtime modules.
- **Speaker capture:** Linux auto-detects the active speaker monitor; Windows supports loopback recording endpoints.
- **File transcription:** optional local audio/video transcription with subtitle and JSON exports.
- **Release packages:** AppImage, deb, rpm, Arch pacman package, Windows setup exe, and Windows MSI.

## Download

Use the [latest GitHub Release](https://github.com/bigidulka/gdictate/releases/latest).

| Platform | Asset to pick |
|---|---|
| Linux portable | AppImage |
| Debian / Ubuntu | deb package |
| Fedora / RPM distros | rpm package |
| Arch / CachyOS / pacman distros | pacman package |
| Windows | setup exe or MSI |

For Arch-style packages:

```bash
sudo pacman -U ./gdictate-*-x86_64.pkg.tar.zst
```

After install, run `gdictate-app` from the app launcher or terminal.

## How It Works

```text
Tauri app / tray / native controls
        |
        v
Python daemon and shared core
        |
        +--> hotkeys
        +--> audio routing
        +--> paste backend
        +--> live popup events
        +--> file transcription jobs
        |
        v
Hidden Chrome / Chromium / Edge Web Speech page
        |
        v
Streaming recognition results
```

The browser is only the speech bridge. gdictate runs a local HTTPS/WebSocket service, opens `speech-proxy.html`, receives interim and final recognition events, and routes text to the popup, GUI, daemon clients, and paste backend.

## Supported Systems

| System | Status |
|---|---|
| Linux + Wayland + GNOME/KDE | Primary target: GUI popup, tray, PipeWire/Pulse routing, clipboard paste, evdev hold hotkeys. |
| Arch / CachyOS | Main package path through the pacman release asset. |
| Debian / Ubuntu | deb package plus source bootstrap path. |
| Fedora / RPM distros | rpm package plus source bootstrap path. |
| Windows | Desktop package, microphone dictation, paste backend, and loopback-based speaker capture. |
| macOS | Not targeted yet. |

Linux notes:

- Hold hotkeys on Wayland need access to input devices.
- Add your user to the `input` group, log out, then log back in.
- Paste uses clipboard plus a key-injection backend such as `ydotool` or `wtype`.
- Speaker mode follows the current default PipeWire/Pulse speaker monitor.

Windows notes:

- Microphone dictation works through the default recording input.
- Speaker transcription needs a loopback input such as Stereo Mix, VB-CABLE, Virtual Audio Cable, or Voicemeeter.
- gdictate reports missing Windows audio setup instead of installing drivers.

## Quick Start From Source

Linux:

```bash
git clone https://github.com/bigidulka/gdictate.git
cd gdictate
./install.sh
.venv/bin/python gdictate.py --daemon --no-ui
```

Desktop app in development:

```bash
npm run bootstrap:linux
npm run tauri:dev
```

Windows:

```powershell
.\scripts\bootstrap-windows.ps1
npm run tauri:dev
```

Optional system dependencies:

```bash
./install.sh --install-system
```

Optional local file transcription dependencies:

```bash
./install.sh --with-batch
```

## Daily Use

Default controls:

| Action | Default |
|---|---|
| Hold microphone channel | `Alt+Left` |
| Hold speaker channel | `Alt+Right` |
| Stop recording | release the held key |
| Open app settings | tray menu or main window |
| Disable popup | Settings -> Live -> Live popup |

Useful daemon commands:

```bash
.venv/bin/python gdictate.py --status
.venv/bin/python gdictate.py --start mic
.venv/bin/python gdictate.py --start speakers
.venv/bin/python gdictate.py --stop
.venv/bin/python gdictate.py --shutdown
```

Diagnostics:

```bash
.venv/bin/python gdictate.py --capabilities
.venv/bin/python gdictate.py --diagnostics
.venv/bin/python gdictate.py --preflight
.venv/bin/python gdictate.py --live-report
.venv/bin/python gdictate.py --shortcut-report
.venv/bin/python gdictate.py --settings-snapshot
```

## Settings

The app stores settings in the user config directory:

| OS | Settings path |
|---|---|
| Linux | `~/.config/gdictate/settings.json` |
| Windows | `%APPDATA%\gdictate\settings.json` |

Main settings groups:

- **General:** language, engine, default channel.
- **Chrome:** browser channel, hidden window mode, profile path, setup flow.
- **Audio:** microphone source, speaker source, Linux routing, Windows loopback input.
- **Binds:** hold or toggle mode, mic bind, speakers bind, Linux hotkey backend.
- **Paste:** paste backend, terminal paste combo, live paste, clipboard-only mode.
- **Live:** popup enabled, click-through, interim text, position.
- **Files:** local transcription model, output formats, diarization options.

Live popup settings apply immediately. Daemon-bound settings are saved and applied through daemon restart when the runtime is idle.

## CLI Reference

```bash
.venv/bin/python gdictate.py --lang en-US
.venv/bin/python gdictate.py --source mic
.venv/bin/python gdictate.py --source speakers
.venv/bin/python gdictate.py --source both
.venv/bin/python gdictate.py --bind-mode dual-hold
.venv/bin/python gdictate.py --bind-mode toggle --key CTRL+ALT
.venv/bin/python gdictate.py --live-paste
.venv/bin/python gdictate.py --no-live-paste
.venv/bin/python gdictate.py --paste auto
.venv/bin/python gdictate.py --paste type --save-settings
.venv/bin/python gdictate.py --paste copy --save-settings
.venv/bin/python gdictate.py --linux-paste-key ctrl-v
.venv/bin/python gdictate.py --chrome-channel chromium
.venv/bin/python gdictate.py --chrome-profile-dir ./tmp/chrome-profile
.venv/bin/python gdictate.py --no-chrome-hidden
.venv/bin/python gdictate.py --setup
.venv/bin/python gdictate.py --save-settings
.venv/bin/python gdictate.py --reset-settings
```

Daemon IPC:

```bash
.venv/bin/python gdictate.py --daemon --no-ui
.venv/bin/python gdictate.py --daemon-hotkeys
.venv/bin/python gdictate.py --toggle mic
.venv/bin/python gdictate.py --toggle speakers
```

File transcription:

```bash
.venv/bin/python gdictate.py --file-report ./call.mp4
.venv/bin/python gdictate.py --transcribe-file ./call.mp4 --model-size small --export-format all
.venv/bin/python gdictate.py --transcribe-file ./call.mp4 --diarize --diarization-backend auto
.venv/bin/python gdictate.py --file-start ./call.mp4
.venv/bin/python gdictate.py --file-jobs
.venv/bin/python gdictate.py --file-job <job-id>
.venv/bin/python gdictate.py --file-cancel <job-id>
```

## Architecture

| Path | Role |
|---|---|
| `gdictate.py` | Compatibility entrypoint. |
| `gdictate_core/app.py` | Dictation state machine and transcript routing. |
| `gdictate_core/chrome.py` | Browser Web Speech bridge, local HTTPS server, WebSocket messages. |
| `gdictate_core/audio.py` | Linux PipeWire/Pulse routing and Windows speaker-input guidance. |
| `gdictate_core/paste.py` | Clipboard, direct typing, live paste, and platform paste backends. |
| `gdictate_core/hotkeys.py` | Linux evdev hold/toggle listeners. |
| `gdictate_core/ipc.py` | Local daemon HTTP/WebSocket control server. |
| `gdictate_core/settings.py` | Shared dataclass settings and schema. |
| `gdictate_core/platforms.py` | Capability reports, diagnostics, safe system actions. |
| `gdictate_core/file_jobs.py` | Batch ASR/diarization jobs and exports. |
| `src/` | React UI. |
| `src-tauri/` | Desktop shell, tray, native windows, popup window, daemon supervisor. |
| `speech-proxy.html` | Browser Web Speech page. |

## IPC API

The daemon listens on `127.0.0.1:9877`.

| Endpoint | Role |
|---|---|
| `GET /status` | Current daemon state and active channel. |
| `POST /start` | Start `mic`, `speakers`, or `both`. |
| `POST /stop` | Stop recording and finalize text. |
| `POST /toggle` | Toggle recording. |
| `GET /events` | WebSocket stream of engine, recording, transcript, audio level, and file-job events. |
| `GET /file-jobs` | List file transcription jobs. |
| `POST /file-jobs` | Start a file transcription job. |
| `GET /file-jobs/{id}` | Read job state/result. |
| `POST /file-jobs/{id}/cancel` | Cancel queued/running job. |
| `POST /shutdown` | Stop daemon. |

## Build And Test

```bash
npm run test:python
npm run perf
npm run build
cargo check --manifest-path src-tauri/Cargo.toml
```

Linux packages:

```bash
npm run tauri:build:linux
```

Windows packages:

```powershell
npm run tauri:build:windows
```

## Release

The release workflow builds package assets for tags matching `v*` and uploads them to the matching GitHub Release.

Release checklist:

```bash
# Update package manifests first.
git commit -m "Release <tag>"
git tag <tag>
git push origin main <tag>
```

Built assets:

- Linux AppImage
- Linux deb
- Linux rpm
- Arch pacman package
- Windows setup exe
- Windows MSI

## License

MIT
