# GNOME Wayland system integration

Validated machine profile:

- GNOME Shell 50.3 on Wayland
- PipeWire 1.6.7 / WirePlumber
- physical hotkeys: `/dev/input/event3` internal keyboard, `/dev/input/event4` Logitech G Pro
- microphone: `alsa_input.pci-0000_00_1f.3-platform-skl_hda_dsp_generic.HiFi__Mic1__source`
- STT bridge: `http://127.0.0.1:37182/v1/audio/transcriptions`

## Runtime ownership

Systemd user services own background lifecycle:

```text
gdictate-daemon.service  # includes evdev F8/F9 listener
chatgpt-transcribe-connect.service
ydotool.service
```

GUI autostart is intentionally absent. Running `gdictate-app` is optional; it reuses the active daemon. The evdev listener now lives inside that daemon, so no second listener process is needed.

## Wayland-first path

```text
physical F8/F9 via evdev
  -> native PipeWire capture via pw-record
  -> chatgpt-transcribe-connect on loopback
  -> wl-clipboard Unicode selection
  -> ydotool Shift+Insert fallback for layout-independent insertion
```

Native pieces: PipeWire audio, Wayland Qt overlay, Wayland clipboard. Applications use physical `Shift+Insert` through `ydotool`; regular and PRIMARY selections are published for terminal compatibility, with PRIMARY publication best-effort and non-blocking. GNOME does not expose a stable generic non-interactive text-injection protocol. RemoteDesktop portal input requires remote-control permission/session UI. `Shift+Insert` uses physical non-character keycodes and therefore works with both `us` and `ru` layouts; character-based `Ctrl+Shift+V` bindings can change with the active layout.

## Privacy

- Bridge and control server listen on loopback only and require separate per-user bearer tokens stored in mode-`0600` files.
- Normal logs contain transcript lengths, not transcript text.
- Status and retained event history do not expose transcript text.
- Captured WAV files are deleted after each request and on normal shutdown.
- Audio leaves the machine only for ChatGPT transcription; bridge is not local inference.

## Validation

From `/home/fsdf1234/Projects/gdictate`:

```bash
PYTHONPATH=. python3 -m unittest tests.test_core_smoke tests.test_openai_compatible
npm run build
(cd src-tauri && cargo check --locked)
git diff --check
```

Runtime:

```bash
systemctl --user status \
  gdictate-daemon.service \
  chatgpt-transcribe-connect.service \
  ydotool.service

PYTHONPATH=. python3 gdictate.py --status
curl -fsS http://127.0.0.1:37182/health
# Direct bridge transcription clients use the mode-0600 api-token as their OpenAI API key.
```

Manual acceptance still required in each target app: hold F8, speak, release, confirm overlay lifecycle and insertion in Ghostty/Bash, Pi, and Herdr.
