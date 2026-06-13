# gdictate

Version 0.3.0-dev

Free streaming voice dictation mini app using Google Speech via Chrome's Web Speech API.

Hold a hotkey, speak, release — text is pasted into any app.

## How it works

```
Tauri GUI / CLI / hotkeys
          ↓
Python core daemon ↔ WebSocket ↔ hidden Chrome/Chromium/Edge → Google Speech
          ↓
OS audio router + OS paste backend + live popup
```

Chrome runs minimized and hidden from taskbar/Alt+Tab. It only serves as a bridge to Google's free speech recognition — no API keys needed.

## Features

- **Two hold-to-talk channels** — `Alt+Left` records your microphone, `Alt+Right` records current speaker output
- **Streaming transcription** — live interim/final text events for popup/tray/GUI
- **Modular core** — audio, Chrome, paste, hotkeys, settings, platform diagnostics are separate modules
- **GUI scaffold** — Tauri 2 + React settings app for Windows/Linux
- **System tray icon** — colored status indicator + left-click to toggle recording
- **Global hotkey** — Tauri native shortcuts in GUI, evdev hold-mode in CLI, old toggle mode still available
- **Auto-paste** — result pasted into focused window via clipboard
- **OS diagnostics** — `--capabilities` reports Chrome/audio/hotkey/paste/overlay support
- **Zero cost** — uses Google Speech through Chrome, no API keys or accounts

## Requirements

- **OS:** Linux first; Windows core path started
- **DE:** GNOME/KDE/Sway/Hyprland via own popup/tray path, KDE window hiding rule supported
- **Display:** Wayland on Linux
- **Browser:** Google Chrome, Chromium, or Edge Chromium
- **Python:** 3.10+
- **Linux groups:** your user must be in `input` for evdev global hotkeys
- **GUI dev:** Node.js, npm, Rust, Tauri system deps

## Quick start

```bash
git clone https://github.com/bigidulka/gdictate.git
cd gdictate
./install.sh        # creates .venv + installs project deps
.venv/bin/python gdictate.py
```

`./install.sh` does not change system packages unless you pass `--install-system`.
Batch audio/video transcription deps are optional and heavy:

```bash
./install.sh --with-batch
python gdictate.py --apply-system-action install_batch_extras
```

Legacy PyQt tray/overlay CLI deps are optional:

```bash
.venv/bin/python -m pip install -r requirements-legacy-ui.txt
```

Linux OS deps:

```bash
./install.sh --install-system
```

Windows bootstrap:

```powershell
.\scripts\bootstrap-windows.ps1
```

Windows OS deps through winget:

```powershell
.\scripts\bootstrap-windows.ps1 -InstallSystem
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
python gdictate.py --engine chrome          # current speech engine
python gdictate.py --chrome-channel chromium
python gdictate.py --chrome-profile-dir ./tmp/chrome-profile
python gdictate.py --no-chrome-hidden       # show browser automation window
python gdictate.py --chrome-setup-required --save-settings
python gdictate.py --bind-mode toggle       # old toggle mode
python gdictate.py --bind-mode toggle --key CTRL+SUPER
python gdictate.py --lang en-US             # English
python gdictate.py --source both            # microphone + speaker output
python gdictate.py --linux-router manual    # do not switch Linux default input
python gdictate.py --windows-speaker-input vb-cable
python gdictate.py --test                   # 5s test, no hotkey
python gdictate.py --test --no-paste        # test without wl-copy/ydotool
python gdictate.py --live-paste             # paste final speech chunks while dictating
python gdictate.py --no-live-paste          # paste once on hotkey release
python gdictate.py --no-ui                  # terminal only, no tray/OSD
python gdictate.py --debug                  # show WebSocket messages
python gdictate.py --setup                  # force re-setup browser permissions
python gdictate.py --capabilities           # OS feature report
python gdictate.py --diagnostics            # OS/audio/paste device diagnostics
python gdictate.py --preflight              # aggregated readiness checks
python gdictate.py --live-report            # live popup/output backend report
python gdictate.py --apply-system-action enable_ydotool
python gdictate.py --user-install-plan      # user-level service/autostart files preview
python gdictate.py --install-user-assets    # write user-level service/autostart files
python gdictate.py --apply-system-action enable_daemon_service
python gdictate.py --apply-system-action install_batch_extras
python gdictate.py --shortcut-report        # DE shortcut commands + limitations
python gdictate.py --file-report            # batch file ASR/diarization pipeline readiness
python gdictate.py --file-report ./call.mp4 # media probe + pipeline plan
python gdictate.py --transcribe-file ./call.mp4 --model-size small --export-format all
python gdictate.py --transcribe-file ./call.mp4 --diarize --diarization-backend auto
python gdictate.py --file-start ./call.mp4  # daemon async job
python gdictate.py --file-jobs             # daemon job list
python gdictate.py --file-job <job-id>     # daemon job status
python gdictate.py --file-cancel <job-id>  # daemon job cancel
python gdictate.py --print-settings         # effective settings JSON
python gdictate.py --default-settings       # default settings JSON
python gdictate.py --settings-schema        # typed settings schema for GUI/tools
python gdictate.py --settings-snapshot      # path + current + defaults + schema
python gdictate.py --save-settings          # write ~/.config/gdictate/settings.json
python gdictate.py --reset-settings         # reset ~/.config/gdictate/settings.json
python gdictate.py --daemon --no-ui         # headless IPC daemon
python gdictate.py --daemon-hotkeys         # evdev hold hotkeys that control daemon
python gdictate.py --status                 # daemon status
python gdictate.py --start mic              # start mic channel through daemon
python gdictate.py --start speakers         # start speakers channel through daemon
python gdictate.py --stop                   # stop current daemon recording
python gdictate.py --shutdown               # stop daemon
```

## GUI development

```bash
npm run bootstrap:linux      # or: npm run bootstrap:windows
npm run tauri:dev
```

Run checks:

```bash
npm run test:python
npm run perf
npm run build
cargo check --manifest-path src-tauri/Cargo.toml
```

Build a desktop package:

```bash
npm run tauri:build
```

Linux release targets:

```bash
npm run tauri:build:linux   # deb + rpm + pacman pkg.tar.zst + AppImage
```

Arch Linux: Tauri has no native pacman bundle target, so `scripts/package-arch.sh` repacks the Tauri Linux payload into `src-tauri/target/release/bundle/pacman/*.pkg.tar.zst`.

Windows release targets:

```powershell
npm run tauri:build:windows # NSIS + MSI
```

Full platform target build:

```bash
npm run tauri:build:all
```

AppImage bundling may download Tauri's AppRun helper.

GitHub release artifacts are built by `.github/workflows/release.yml` on `workflow_dispatch` or tags matching `v*`.

The bundle includes the Python core resources under `resources/python/` (`gdictate.py`, `gdictate_core/`, `speech-proxy.html`, `requirements.txt`). Linux packages declare the required Python runtime, `aiohttp`, and `evdev` dependencies; browser, paste/audio-routing tools, legacy PyQt UI, and batch transcription packages remain OS/user optional. During development, the repo-local `.venv` is used automatically. Windows release builds vendor the pure Python runtime deps into `resources/python/vendor`.

Current GUI status: one-page app with two tabs: `App` for dictation/live/file jobs and `Настройки` for user settings. It reads/writes and resets the same JSON settings as the Python core, registers native hotkeys while the tray app is running, shows current-OS diagnostics/actions only, starts/stops recording channels, subscribes to live transcript events, opens a native always-on-top live popup, and can shut the daemon down.

The app also installs a tray menu. Closing the settings window hides it instead of exiting, so gdictate can keep running in the background. On GUI startup, Tauri supervises the local Python daemon and restarts it if it dies; using `Shutdown` disables that supervisor until `Daemon`, `Start`, or a hotkey start is used again.

Tray actions:

- Show settings
- Hide settings
- Open live popup
- Hide live popup
- Start mic
- Start speakers
- Stop recording
- Quit

## IPC

`--daemon` starts a local HTTP control server on `127.0.0.1:9877`.

Paste defaults:

- `paste.live=true`: final recognition chunks are pasted sequentially while the key is still held.
- On release, already-pasted final chunks are not pasted again.
- If Chrome only produced interim text, gdictate falls back to one paste on release.
- Linux paste uses `wl-copy` plus `ydotool`/`wtype`; failures are logged and auto mode falls back to the next backend.

| Endpoint | Role |
|---|---|
| `GET /status` | Current daemon state and active channel. |
| `POST /start {"source":"mic"}` | Start channel: `mic`, `speakers`, or `both`. |
| `POST /stop` | Stop recording and paste/finalize text. |
| `POST /toggle {"source":"speakers"}` | Toggle recording. |
| `GET /file-jobs` | List batch file transcription jobs. |
| `POST /file-jobs` | Start a batch transcription job. |
| `GET /file-jobs/{id}` | Read job state/result. |
| `POST /file-jobs/{id}/cancel` | Cancel queued/running job. |
| `POST /shutdown` | Stop daemon. |
| `GET /events` | WebSocket stream of engine/recording/transcript events. |

The GUI listens to `/events` directly and keeps live interim text, final text history, daemon state, and active channel in sync.

## Native live popup

The Tauri GUI can open a second window labeled `overlay`.

- transparent
- always-on-top
- frameless
- skipped from taskbar
- click-through when enabled
- monitor-aware positions: `bottom-center`, `top-center`, `bottom-right`
- fed by the same `/events` WebSocket as the settings window

Open it from the `Live` tab with `Open popup`. Hide it with `Hide popup`.
The `Live` tab also shows the live backend report, popup running/visible state, and compositor-specific warnings/actions.

## File transcription pipeline

`--file-report [path]` reports batch audio/video transcription readiness without starting Chrome:

- `ffprobe` media stream/duration inspection
- `ffmpeg` audio extraction stage
- local ASR backend readiness (`faster-whisper`)
- diarization readiness (`whisperx` or `pyannote.audio`)
- export stage: TXT/SRT/VTT/JSON with speaker labels

Install optional batch deps into the project `.venv`:

```bash
python gdictate.py --apply-system-action install_batch_extras
# or during bootstrap:
./install.sh --with-batch
```

`--transcribe-file <path>` runs the first executable batch path synchronously:

- extracts mono 16 kHz WAV with `ffmpeg`
- transcribes with local `faster-whisper`
- exports `transcript.json`, `transcript.txt`, `transcript.srt`, `transcript.vtt`
- with `--diarize`, assigns speaker labels through `--diarization-backend auto|whisperx|pyannote|off`

`pyannote.audio` and WhisperX diarization models usually require a Hugging Face token. Export one of these before starting the app/daemon:

```bash
export HUGGING_FACE_HUB_TOKEN=...
# or HF_TOKEN / PYANNOTE_AUTH_TOKEN
```

The GUI `Файлы` tab starts daemon-backed async jobs. Job status is delivered over `/events` as `file.job` events and can also be polled with the CLI job commands. Diarization backend, speaker count, warnings, and exported files are represented in the job result.

Cancel note: a queued job cancels immediately. A running `faster-whisper` job receives `cancel_requested`; Python cannot force-stop that backend thread safely, so it may finish and report a warning.

### Hotkey

| Combo | Description |
|---|---|
| **Alt+Left** (default) | Hold while you speak; records microphone. |
| **Alt+Right** (default) | Hold while the other side speaks; records current speaker output. |
| `--bind-mode toggle --key CTRL+ALT` | Old toggle mode: press to start, press again to stop and paste. |
| `--bind-mode toggle --key CTRL+SUPER` | Use Super instead of Alt in toggle mode. |

### Desktop shortcuts

The Tauri GUI registers shortcuts from the `Настройки` tab while the app/tray is running:

- `ALT+LEFT` press starts microphone capture, release stops.
- `ALT+RIGHT` press starts speaker-output capture, release stops.
- `toggle` mode registers one native toggle shortcut for the selected default source.

Linux Wayland caveat: Tauri native global shortcuts are X11-only here. On GNOME/KDE Wayland use `Start binds`; the GUI falls back to the evdev daemon listener. `Start evdev binds` does the same directly. It requires the current user to be in the `input` group and a fresh login after adding the group.

Use `python gdictate.py --shortcut-report` for ready-made commands for your current desktop.

GNOME caveat: Settings custom shortcuts launch a command on key press only. They do not expose key release, so real hold-to-talk is not possible there. Use toggle commands in GNOME:

```bash
python gdictate.py --toggle mic
python gdictate.py --toggle speakers
```

For CLI real hold-to-talk on Linux, use the evdev backend (`--bind-mode dual-hold`) after adding the user to the `input` group and logging in again. KDE/custom shortcut tools may support separate press/release workflows; if release is unavailable, use the same toggle commands.

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

Linux router:

- `--linux-router pipewire-pulse` / `pulse`: pactl-compatible automatic routing.
- `--linux-router manual`: leave the current default recording input unchanged.

Windows speaker input:

- `--windows-speaker-input auto`: expect Stereo Mix, VB-CABLE, Virtual Audio Cable, or Voicemeeter as default recording input.
- `--windows-speaker-input stereo-mix` / `vb-cable`: tailor warnings and setup intent.
- `--windows-speaker-input manual`: leave Windows input selection to the user.

## Compatibility

| System | Support |
|---|---|
| Arch + KDE Plasma 6 + Wayland | Tested, full OSD + tray |
| Fedora / Ubuntu + KDE + Wayland | `./install.sh --install-system` supported, full OSD + tray |
| GNOME / Sway / Hyprland + Wayland | GUI popup/tray works; native global shortcuts depend on compositor policy; CLI/DE fallback available |
| X11 | Not supported (uses wl-copy, ydotool on Wayland) |
| Windows | GUI/native shortcuts and microphone path supported through Chrome default input; speaker path requires Stereo Mix/VB-CABLE/Virtual Audio Cable as browser input |
| macOS | Not targeted |

## Diagnostics

Use:

```bash
python gdictate.py --diagnostics
```

It reports:

- Chrome/Chromium/Edge path
- paste backend
- hotkey backend
- microphone/input endpoints
- speaker/output endpoints
- speaker-capture readiness
- warnings and next actions
- structured OS actions with status, command, admin/manual flags for GNOME/KDE/Linux/Windows setup

`--apply-system-action <id>` only executes safe allowlisted user-level actions. Admin/manual actions return a JSON result with the required command or instruction; they are not executed automatically.

`--install-user-assets` writes current-user startup integration only:

- Linux: `~/.config/systemd/user/gdictate-daemon.service`
- Linux: `~/.local/share/applications/gdictate.desktop`
- Linux: `~/.config/autostart/gdictate.desktop`
- Windows source-tree mode: Startup `.cmd` for the Python daemon

It does not use sudo or write system paths. After Linux install, run the returned `systemctl --user` commands if you want the daemon enabled immediately. The GUI `Настройки` tab exposes the same current-OS user-level actions as `Copy`/`Apply`; `enable_daemon_service` and `disable_daemon_service` only touch the current user's systemd unit.

Windows note: Chrome Web Speech can only listen to browser recording inputs. Speaker transcription therefore requires a recording endpoint such as Stereo Mix, VB-CABLE, Virtual Audio Cable, or Voicemeeter. gdictate detects and explains this; it does not install audio drivers.

## Architecture

```
┌─────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│  hotkeys    │     │  gdictate_core       │     │  engine backend  │
│  GUI / CLI  │────>│  aiohttp IPC server  │<───>│  Chrome/WebSpeech│
│             │     │  state machine       │     │  first backend   │
└─────────────┘     └──────┬───────────────┘     └────────┬────────┘
                           │                              │
                    ┌──────┴──────┐               ┌───────┴────────┐
                    │ wl-copy +   │               │ Google Speech  │
                    │ ydotool     │               │ servers (free) │
                    │ (paste)     │               └────────────────┘
                    └─────────────┘
```

**Files:**
- `gdictate.py` — compatibility entrypoint
- `gdictate_core/` — Python core: app facade, Chrome bridge, audio routing, paste, hotkeys, settings, OS diagnostics
- `overlay.py` — OSD overlay (KDE native / notify-send fallback) + system tray icon with click-to-toggle
- `speech-proxy.html` — Chrome page running `webkitSpeechRecognition`
- `src/` — React settings UI
- `src-tauri/` — Tauri desktop shell and OS bridge

## Core modules

| Module | Role |
|---|---|
| `gdictate_core.app` | Dictation state machine and public facade |
| `gdictate_core.engines` | Speech engine protocol/factory; Chrome backend adapter |
| `gdictate_core.file_jobs` | Batch audio/video probing, dependency report, ASR/diarization job plan |
| `gdictate_core.chrome` | HTTPS/WS server, hidden Chrome launcher, Web Speech messages |
| `gdictate_core.audio` | Linux PipeWire/Pulse routing, Windows speaker-capture guidance |
| `gdictate_core.hotkeys` | Linux evdev toggle/dual-hold; DE shortcut mode lands via CLI IPC |
| `gdictate_core.paste` | Linux wl-copy + ydotool/wtype; Windows clipboard + Ctrl+V |
| `gdictate_core.settings` | Shared JSON settings schema |
| `gdictate_core.platforms` | OS capability report and dependency checks |
| `src-tauri` | Native tray, overlay window, global shortcuts, settings bridge |

## License

MIT
