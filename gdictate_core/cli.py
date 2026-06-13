from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import sys
from dataclasses import asdict
from pathlib import Path

import aiohttp

from .app import Dictation
from .chrome import is_browser_configured
from .constants import VERSION
from .desktop import ensure_kwin_rule
from .file_jobs import FileTranscriptionOptions, pipeline_report, transcribe_file
from .hotkeys import run_dual_hold_evdev, run_dual_hold_evdev_actions, run_evdev, run_stdin_toggle
from .install_assets import install_user_assets, user_install_plan
from .ipc import ControlServer, get_control, get_status, post_control
from .platforms import apply_system_action, capability_report, check_dependencies, diagnostics_report, live_report
from .preflight import preflight_report
from .settings import AppSettings, default_settings, load_settings, reset_settings, save_settings, settings_schema, settings_snapshot
from .shortcuts import shortcut_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Google Speech Dictation")
    parser.add_argument("--version", action="version", version=f"gdictate {VERSION}")
    parser.add_argument("--lang", default=None)
    parser.add_argument("--engine", default=None, choices=["chrome"])
    parser.add_argument("--key", default=None)
    parser.add_argument("--bind-mode", default=None, choices=["dual-hold", "toggle", "enter"])
    parser.add_argument("--paste", default=None, choices=["auto", "ydotool", "wtype", "none"])
    parser.add_argument("--live-paste", default=None, action=argparse.BooleanOptionalAction, help="Paste final chunks while dictating")
    parser.add_argument("--linux-paste-key", default=None, choices=["ctrl-v", "ctrl-shift-v"], help="Linux paste shortcut sent by ydotool/wtype")
    parser.add_argument("--no-paste", action="store_const", const="none", dest="paste")
    parser.add_argument("--source", default=None, choices=["mic", "speakers", "both"])
    parser.add_argument("--linux-router", default=None, choices=["pipewire-pulse", "pulse", "manual"])
    parser.add_argument("--windows-speaker-input", default=None, choices=["auto", "stereo-mix", "vb-cable", "manual"])
    parser.add_argument("--chrome-channel", default=None, choices=["auto", "stable", "beta", "dev", "chromium", "edge"])
    parser.add_argument("--chrome-profile-dir", default=None)
    parser.add_argument("--chrome-hidden", default=None, action=argparse.BooleanOptionalAction)
    parser.add_argument("--chrome-setup-required", default=None, action=argparse.BooleanOptionalAction)
    parser.add_argument("--setup", action="store_true", help="Force browser setup")
    parser.add_argument("--test", action="store_true", help="5s test recording")
    parser.add_argument("--debug", action="store_true", help="Show all WS messages")
    parser.add_argument("--no-ui", action="store_true", help="Disable overlay/tray")
    parser.add_argument("--capabilities", action="store_true", help="Print OS capability report")
    parser.add_argument("--diagnostics", action="store_true", help="Print OS/audio/paste diagnostics")
    parser.add_argument("--preflight", action="store_true", help="Print aggregated readiness report")
    parser.add_argument("--live-report", action="store_true", help="Print live popup/output backend report")
    parser.add_argument("--apply-system-action", help="Apply a safe OS diagnostic action by id")
    parser.add_argument("--user-install-plan", action="store_true", help="Print user-level service/autostart install plan")
    parser.add_argument("--install-user-assets", action="store_true", help="Write user-level service/autostart files")
    parser.add_argument("--print-settings", action="store_true", help="Print effective settings JSON")
    parser.add_argument("--save-settings", action="store_true", help="Write effective settings JSON")
    parser.add_argument("--default-settings", action="store_true", help="Print default settings JSON")
    parser.add_argument("--settings-schema", action="store_true", help="Print settings schema JSON")
    parser.add_argument("--settings-snapshot", action="store_true", help="Print settings path/current/defaults/schema JSON")
    parser.add_argument("--reset-settings", action="store_true", help="Reset settings file to defaults")
    parser.add_argument("--shortcut-report", action="store_true", help="Print desktop shortcut command report")
    parser.add_argument("--file-report", nargs="?", const="", help="Print file transcription/diarization pipeline report")
    parser.add_argument("--transcribe-file", help="Transcribe an audio/video file with local faster-whisper")
    parser.add_argument("--file-start", help="Start daemon file transcription job")
    parser.add_argument("--file-jobs", action="store_true", help="List daemon file transcription jobs")
    parser.add_argument("--file-job", help="Get daemon file transcription job status")
    parser.add_argument("--file-cancel", help="Cancel daemon file transcription job")
    parser.add_argument("--output-dir", help="Output directory for file transcription exports")
    parser.add_argument("--model-size", default="small", help="faster-whisper model size for --transcribe-file")
    parser.add_argument("--device", default="auto", help="faster-whisper device: auto/cpu/cuda")
    parser.add_argument("--compute-type", default="default", help="faster-whisper compute type")
    parser.add_argument("--diarize", action="store_true", help="Request speaker diarization for file transcription")
    parser.add_argument(
        "--diarization-backend",
        default="auto",
        choices=["auto", "whisperx", "pyannote", "off"],
        help="Speaker diarization backend for file transcription",
    )
    parser.add_argument(
        "--export-format",
        action="append",
        choices=["all", "json", "txt", "srt", "vtt"],
        help="Export format for --transcribe-file; repeatable",
    )
    parser.add_argument("--daemon", action="store_true", help="Run headless IPC daemon")
    parser.add_argument("--daemon-hotkeys", action="store_true", help="Run Linux evdev hold listener that controls IPC daemon")
    parser.add_argument("--parent-pid", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--status", action="store_true", help="Print IPC daemon status")
    parser.add_argument("--start", choices=["mic", "speakers", "both"], help="Start daemon recording channel")
    parser.add_argument("--stop", action="store_true", help="Stop daemon recording")
    parser.add_argument("--toggle", choices=["mic", "speakers", "both"], nargs="?", const="mic", help="Toggle daemon recording")
    parser.add_argument("--shutdown", action="store_true", help="Shutdown IPC daemon")
    return parser


def effective_settings(args: argparse.Namespace) -> AppSettings:
    settings = load_settings()
    if args.lang:
        settings.language = args.lang
    if args.engine:
        settings.engine.name = args.engine
    if args.key:
        settings.bind.toggle = args.key
    if args.bind_mode:
        settings.bind.mode = args.bind_mode
    if args.paste:
        settings.paste.mode = args.paste
    if args.live_paste is not None:
        settings.paste.live = args.live_paste
    if args.linux_paste_key:
        settings.paste.linux_terminal_combo = args.linux_paste_key
    if args.source:
        settings.audio.source = args.source
    if args.linux_router:
        settings.audio.linux_router = args.linux_router
    if args.windows_speaker_input:
        settings.audio.windows_speaker_input = args.windows_speaker_input
    if args.chrome_channel:
        settings.chrome.channel = args.chrome_channel
    if args.chrome_profile_dir is not None:
        settings.chrome.profile_dir = args.chrome_profile_dir
    if args.chrome_hidden is not None:
        settings.chrome.hidden = args.chrome_hidden
    if args.chrome_setup_required is not None:
        settings.chrome.setup_required = args.chrome_setup_required
    return settings


def make_dictation(settings: AppSettings, args: argparse.Namespace) -> Dictation:
    return Dictation(
        language=settings.language,
        engine=settings.engine.name,
        paste_mode=settings.paste.mode,
        paste_live=settings.paste.live,
        paste_live_during_recording=settings.bind.mode != "dual-hold",
        paste_linux_combo=settings.paste.linux_terminal_combo,
        paste_windows_combo=settings.paste.windows_combo,
        audio_source=settings.audio.source,
        debug=args.debug,
        restore_default_after_start=settings.audio.restore_default_after_start,
        audio_linux_router=settings.audio.linux_router,
        audio_windows_speaker_input=settings.audio.windows_speaker_input,
        chrome_channel=settings.chrome.channel,
        chrome_hidden=settings.chrome.hidden,
        chrome_profile_dir=settings.chrome.profile_dir,
    )


async def main(args, overlay=None, tray=None) -> None:
    settings = effective_settings(args)
    ensure_kwin_rule()
    dictation = None

    try:
        dictation = make_dictation(settings, args)
        dictation.overlay = overlay
        dictation.tray = tray

        ui_status = "on" if (overlay or tray) else "off"
        print("╔═══════════════════════════════════════╗", flush=True)
        print("║  Google Speech Streaming Dictation    ║", flush=True)
        print("╠═══════════════════════════════════════╣", flush=True)
        print(f"║  Lang: {settings.language:<30}║", flush=True)
        print(f"║  Engine:{settings.engine.name:<29}║", flush=True)
        print(f"║  Bind: {settings.bind.mode:<30}║", flush=True)
        print(f"║  UI:   {ui_status:<30}║", flush=True)
        print(f"║  Audio:{settings.audio.source:<30}║", flush=True)
        print("╚═══════════════════════════════════════╝", flush=True)

        need_setup = args.setup or settings.chrome.setup_required or not is_browser_configured(settings.chrome.profile_dir)
        if need_setup:
            print("\n[SETUP] First run — opening Chrome to grant microphone permission.", flush=True)
            print("        Click 'Allow' when prompted, then close Chrome.\n", flush=True)
            await dictation.init(setup_mode=True)
            await dictation.start_recording("mic")
            print("[SETUP] Waiting for permission... (Ctrl+C when done)\n", flush=True)
            try:
                while not is_browser_configured(settings.chrome.profile_dir):
                    await asyncio.sleep(1)
            except (KeyboardInterrupt, asyncio.CancelledError):
                pass
            await dictation.close()
            dictation = None
            if not is_browser_configured(settings.chrome.profile_dir):
                print("\n[SETUP] Permission not detected. Run again to retry.", flush=True)
                return
            print("\n[SETUP] Permission granted! Restarting in normal mode...\n", flush=True)
            dictation = make_dictation(settings, args)
            dictation.overlay = overlay
            dictation.tray = tray

        await dictation.init()

        if tray:
            def on_tray_toggle():
                asyncio.ensure_future(dictation.toggle())

            tray.toggle_requested.connect(on_tray_toggle)

        if args.test:
            print("=== TEST: 5s ===", flush=True)
            await dictation.start_recording(settings.audio.source)
            await asyncio.sleep(5)
            await dictation.stop_recording()
            return

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, lambda: [task.cancel() for task in asyncio.all_tasks(loop)])
            except NotImplementedError:
                pass

        if settings.bind.mode == "enter":
            await run_stdin_toggle(dictation)
        elif settings.bind.mode == "dual-hold":
            use_evdev = settings.bind.linux_backend in ("de-shortcut+evdev", "evdev")
            if use_evdev and not await run_dual_hold_evdev(dictation):
                await run_stdin_toggle(dictation)
            elif not use_evdev:
                await run_stdin_toggle(dictation)
        else:
            if not await run_evdev(dictation, settings.bind.toggle):
                await run_stdin_toggle(dictation)
    except asyncio.CancelledError:
        pass
    finally:
        if dictation:
            await dictation.close()


async def daemon_main(args, overlay=None, tray=None) -> None:
    settings = effective_settings(args)
    ensure_kwin_rule()
    server = None
    dictation = None

    try:
        need_setup = settings.chrome.setup_required or not is_browser_configured(settings.chrome.profile_dir)
        if need_setup:
            print("[SETUP] Microphone permission missing; opening Chrome setup window.", flush=True)
            print("[SETUP] Click 'Allow' for microphone access. Daemon will continue after permission is saved.", flush=True)
            setup_dictation = make_dictation(settings, args)
            try:
                await setup_dictation.init(setup_mode=True)
                await setup_dictation.start_recording("mic")
                deadline = asyncio.get_running_loop().time() + 180
                while not is_browser_configured(settings.chrome.profile_dir):
                    if asyncio.get_running_loop().time() >= deadline:
                        raise RuntimeError("microphone permission not granted; run gdictate with --setup")
                    await asyncio.sleep(1)
            finally:
                await setup_dictation.close()
            print("[SETUP] Microphone permission granted.", flush=True)

        dictation = make_dictation(settings, args)
        dictation.overlay = overlay
        dictation.tray = tray
        server = ControlServer(dictation)
        dictation.on_event = server.on_event

        print("[DAEMON] starting", flush=True)
        await dictation.init(setup_mode=settings.chrome.setup_required)
        await server.start()

        if tray:
            def on_tray_toggle():
                asyncio.ensure_future(dictation.toggle())

            tray.toggle_requested.connect(on_tray_toggle)

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, server.request_shutdown)
            except NotImplementedError:
                pass

        await server.wait_closed()
    except asyncio.CancelledError:
        pass
    finally:
        if server:
            await server.close()
        if dictation:
            await dictation.close()


async def control_command(args) -> None:
    try:
        if args.status:
            result = await get_status()
        elif args.start:
            result = await post_control("/start", {"source": args.start})
        elif args.stop:
            result = await post_control("/stop")
        elif args.toggle:
            result = await post_control("/toggle", {"source": args.toggle})
        elif args.shutdown:
            result = await post_control("/shutdown")
        elif args.file_jobs:
            result = await get_control("/file-jobs")
        elif args.file_job:
            result = await get_control(f"/file-jobs/{args.file_job}")
        elif args.file_cancel:
            result = await post_control(f"/file-jobs/{args.file_cancel}/cancel")
        elif args.file_start:
            result = await post_control(
                "/file-jobs",
                {
                    "path": args.file_start,
                    "output_dir": args.output_dir,
                    "language": effective_settings(args).language,
                    "model_size": args.model_size,
                    "device": args.device,
                    "compute_type": args.compute_type,
                    "diarize": args.diarize,
                    "diarization_backend": args.diarization_backend,
                    "formats": args.export_format or ["json", "txt", "srt", "vtt"],
                },
            )
        else:
            return
    except (aiohttp.ClientError, RuntimeError, TimeoutError, ConnectionError) as exc:
        print(f"[ERR] daemon unavailable: {exc}", file=sys.stderr, flush=True)
        sys.exit(2)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


async def daemon_hotkeys_main(args) -> None:
    settings = effective_settings(args)
    deadline = asyncio.get_running_loop().time() + 60
    while True:
        try:
            await get_status()
            break
        except (aiohttp.ClientError, RuntimeError, TimeoutError, ConnectionError):
            if asyncio.get_running_loop().time() >= deadline:
                raise
            await asyncio.sleep(1)

    if args.parent_pid and os.name == "posix":
        async def stop_with_parent() -> None:
            parent = Path(f"/proc/{args.parent_pid}")
            while parent.exists():
                await asyncio.sleep(1)
            os._exit(0)

        asyncio.create_task(stop_with_parent())

    async def on_start(source: str) -> None:
        await post_control("/start", {"source": source})

    async def on_stop() -> None:
        await post_control("/stop")

    if settings.bind.mode != "dual-hold":
        print(f"[WARN] daemon hotkeys use dual-hold; current mode is {settings.bind.mode}", file=sys.stderr, flush=True)
    ok = await run_dual_hold_evdev_actions(on_start, on_stop)
    if not ok:
        sys.exit(2)


def run() -> None:
    parser = build_parser()
    args = parser.parse_args()
    settings = effective_settings(args)

    if args.capabilities:
        print(json.dumps(asdict(capability_report()), ensure_ascii=False, indent=2))
        return
    if args.diagnostics:
        print(json.dumps(asdict(diagnostics_report()), ensure_ascii=False, indent=2))
        return
    if args.preflight:
        print(json.dumps(asdict(preflight_report()), ensure_ascii=False, indent=2))
        return
    if args.live_report:
        print(json.dumps(asdict(live_report()), ensure_ascii=False, indent=2))
        return
    if args.apply_system_action:
        print(json.dumps(asdict(apply_system_action(args.apply_system_action)), ensure_ascii=False, indent=2))
        return
    if args.user_install_plan:
        print(json.dumps(asdict(user_install_plan()), ensure_ascii=False, indent=2))
        return
    if args.install_user_assets:
        result = install_user_assets()
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        sys.exit(0 if result.ok else 2)
    if args.print_settings:
        print(json.dumps(asdict(settings), ensure_ascii=False, indent=2))
        return
    if args.default_settings:
        print(json.dumps(asdict(default_settings()), ensure_ascii=False, indent=2))
        return
    if args.settings_schema:
        print(json.dumps([asdict(group) for group in settings_schema()], ensure_ascii=False, indent=2))
        return
    if args.settings_snapshot:
        print(json.dumps(settings_snapshot(), ensure_ascii=False, indent=2))
        return
    if args.reset_settings:
        print(json.dumps(asdict(reset_settings()), ensure_ascii=False, indent=2))
        return
    if args.save_settings:
        save_settings(settings)
        print("[OK] settings saved")
        return
    if args.shortcut_report:
        print(json.dumps(asdict(shortcut_report(settings)), ensure_ascii=False, indent=2))
        return
    if args.file_report is not None:
        print(json.dumps(asdict(pipeline_report(args.file_report or None)), ensure_ascii=False, indent=2))
        return
    if args.transcribe_file:
        result = transcribe_file(
            FileTranscriptionOptions(
                path=args.transcribe_file,
                output_dir=args.output_dir,
                language=settings.language,
                model_size=args.model_size,
                device=args.device,
                compute_type=args.compute_type,
                diarize=args.diarize,
                diarization_backend=args.diarization_backend,
                formats=args.export_format or ["json", "txt", "srt", "vtt"],
            )
        )
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        sys.exit(0 if result.ok else 2)
    if args.daemon_hotkeys:
        asyncio.run(daemon_hotkeys_main(args))
        return
    if args.status or args.start or args.stop or args.toggle or args.shutdown or args.file_jobs or args.file_job or args.file_cancel or args.file_start:
        asyncio.run(control_command(args))
        return

    check_dependencies(settings.paste.mode)

    if not args.no_ui:
        try:
            from overlay import DictationTray, OverlayPopup
            from PyQt6.QtWidgets import QApplication
            import qasync
        except ImportError:
            asyncio.run(daemon_main(args) if args.daemon else main(args))
            return

        app = QApplication(sys.argv)
        overlay = OverlayPopup()
        tray = DictationTray()
        loop = qasync.QEventLoop(app)
        asyncio.set_event_loop(loop)
        with loop:
            loop.run_until_complete(daemon_main(args, overlay, tray) if args.daemon else main(args, overlay, tray))
    else:
        asyncio.run(daemon_main(args) if args.daemon else main(args))
