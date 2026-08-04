from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from gdictate_core.audio import audio_router_label, configure_audio_source
from gdictate_core import app as app_module
from gdictate_core import paste as paste_module
from gdictate_core.app import Dictation
from gdictate_core.chrome import chrome_profile_dir, is_browser_configured
from gdictate_core.file_jobs import FileTranscriptionResult, FileTranscriptionSegment, export_transcription
from gdictate_core.cli import migrate_legacy_hotkey_service
from gdictate_core.install_assets import install_user_assets, user_install_plan
from gdictate_core.hotkeys import _binding_groups, _hold_keyboards, _is_pressed
from gdictate_core.ipc import ControlServer, _history_safe, control_token, get_status
from gdictate_core.models import State, TranscriptResult
from gdictate_core.paste import _linux_combo_keycodes
from gdictate_core.platforms import chrome_candidates
from gdictate_core.preflight import preflight_report
from gdictate_core.settings import AppSettings, load_settings, reset_settings, save_settings, settings_schema, settings_snapshot


TMP_ROOT = Path(__file__).resolve().parents[1] / "tmp"


def temporary_directory():
    TMP_ROOT.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=TMP_ROOT)


class InstallAssetsTests(unittest.TestCase):
    def test_user_install_assets_write_os_templates(self) -> None:
        with temporary_directory() as raw_home:
            home = Path(raw_home)
            plan = user_install_plan(home)
            result = install_user_assets(home)

            self.assertTrue(plan.installable)
            self.assertTrue(result.ok)
            self.assertEqual(len(result.installed), len(plan.assets))
            for installed in result.installed:
                path = Path(installed)
                self.assertTrue(path.exists())
                self.assertGreater(path.stat().st_size, 0)

            if sys.platform == "win32":
                startup = (
                    home
                    / "AppData"
                    / "Roaming"
                    / "Microsoft"
                    / "Windows"
                    / "Start Menu"
                    / "Programs"
                    / "Startup"
                    / "gdictate-daemon.cmd"
                )
                self.assertIn("--daemon --no-ui", startup.read_text(encoding="utf-8"))
            else:
                self.assertEqual(len(plan.assets), 2)
                service = home / ".config" / "systemd" / "user" / "gdictate-daemon.service"
                service_text = service.read_text(encoding="utf-8")
                self.assertIn("gdictate.py --daemon\n", service_text)
                self.assertNotIn("--no-ui", service_text)
                self.assertIn("NoNewPrivileges=true", service_text)
                if (home / ".config" / "gdictate" / "transcriber.env").exists():
                    self.assertIn("EnvironmentFile=-%h/.config/gdictate/transcriber.env", service_text)
                self.assertNotIn("gdictate-hotkeys.service", "\n".join(plan.actions))
                self.assertIn("systemctl --user restart gdictate-daemon.service", "\n".join(plan.actions))


class ExportTranscriptionTests(unittest.TestCase):
    def test_export_transcription_writes_all_formats(self) -> None:
        with temporary_directory() as raw_out:
            out_dir = Path(raw_out)
            result = FileTranscriptionResult(
                ok=True,
                path="/tmp/input.wav",
                text="hello\nworld",
                segments=[
                    FileTranscriptionSegment(0, 0.0, 1.2, "hello", speaker="SPEAKER_00"),
                    FileTranscriptionSegment(1, 1.3, 2.4, "world", speaker="SPEAKER_01"),
                ],
                diarization_backend="test",
                speaker_count=2,
            )

            files = export_transcription(result, out_dir, ["all"])

            self.assertEqual(set(files), {"json", "txt", "srt", "vtt"})
            payload = json.loads(Path(files["json"]).read_text(encoding="utf-8"))
            self.assertEqual(payload["speaker_count"], 2)
            self.assertEqual(payload["diarization_backend"], "test")
            self.assertIn("SPEAKER_01: world", Path(files["txt"]).read_text(encoding="utf-8"))
            self.assertIn("00:00:01,300", Path(files["srt"]).read_text(encoding="utf-8"))
            self.assertTrue(Path(files["vtt"]).read_text(encoding="utf-8").startswith("WEBVTT"))


class IpcAuthTests(unittest.IsolatedAsyncioTestCase):
    def test_retained_event_sanitizer_removes_nested_transcript_fields(self) -> None:
        event = {
            "type": "file.job",
            "text": "top-level secret",
            "job": {"result": {"text": "nested secret", "segments": [{"text": "segment secret"}]}},
        }
        self.assertEqual(_history_safe(event), {"type": "file.job", "job": {"result": {}}})

    async def test_control_http_and_websocket_require_token(self) -> None:
        import aiohttp

        class FakeDictation:
            def status(self) -> dict:
                return {"state": "idle"}

            async def close(self) -> None:
                return None

        previous = os.environ.get("GDICTATE_CONTROL_TOKEN")
        os.environ["GDICTATE_CONTROL_TOKEN"] = "test-control-token"
        server = ControlServer(FakeDictation(), port=0)  # type: ignore[arg-type]
        try:
            await server.start()
            sites = list(server._runner.sites) if server._runner else []  # type: ignore[union-attr]
            sock = next(iter(sites[0]._server.sockets))  # type: ignore[union-attr]
            port = sock.getsockname()[1]
            async with aiohttp.ClientSession() as session:
                response = await session.get(f"http://127.0.0.1:{port}/status")
                self.assertEqual(response.status, 401)
                response = await session.get(
                    f"http://127.0.0.1:{port}/status",
                    headers={"Authorization": "Bearer test-control-token"},
                )
                self.assertEqual(response.status, 200)
                status_payload = await response.json()
                self.assertEqual(status_payload["ipc_version"], 2)
                with self.assertRaises(aiohttp.WSServerHandshakeError):
                    await session.ws_connect(f"http://127.0.0.1:{port}/events")
                ws = await session.ws_connect(
                    f"http://127.0.0.1:{port}/events",
                    protocols=("gdictate", "test-control-token"),
                )
                await ws.close()
        finally:
            await server.close()
            if previous is None:
                os.environ.pop("GDICTATE_CONTROL_TOKEN", None)
            else:
                os.environ["GDICTATE_CONTROL_TOKEN"] = previous


class LegacyHotkeyMigrationTests(unittest.TestCase):
    def test_legacy_hotkey_unit_is_disabled_removed_and_reloaded(self) -> None:
        import gdictate_core.cli as cli_module

        with temporary_directory() as raw_home:
            home = Path(raw_home)
            unit = home / ".config" / "systemd" / "user" / "gdictate-hotkeys.service"
            unit.parent.mkdir(parents=True)
            unit.write_text("legacy", encoding="utf-8")
            calls: list[list[str]] = []

            class Result:
                stdout = "gdictate-hotkeys.service enabled\n"

            original_which = cli_module.shutil.which
            original_run = cli_module.subprocess.run
            cli_module.shutil.which = lambda name: "/usr/bin/systemctl" if name == "systemctl" else None

            def fake_run(args, **_kwargs):
                calls.append(list(args))
                return Result()

            cli_module.subprocess.run = fake_run
            try:
                migrate_legacy_hotkey_service(home)
            finally:
                cli_module.shutil.which = original_which
                cli_module.subprocess.run = original_run

            self.assertFalse(unit.exists())
            self.assertIn(["systemctl", "--user", "disable", "--now", "gdictate-hotkeys.service"], calls)
            self.assertIn(["systemctl", "--user", "daemon-reload"], calls)


class PreflightTests(unittest.TestCase):
    def test_preflight_contains_required_checks(self) -> None:
        report = preflight_report()

        self.assertTrue(
            {
                "chrome",
                "speaker_capture",
                "paste",
                "hotkeys",
                "live",
                "file_asr",
                "file_diarization",
                "user_install",
            }.issubset({check.id for check in report.checks})
        )


class SettingsTests(unittest.TestCase):
    def test_public_dictation_export_is_lazy_and_compatible(self) -> None:
        import gdictate_core

        self.assertIs(gdictate_core.Dictation, Dictation)

    def test_schema_defaults_and_reset_are_consistent(self) -> None:
        with temporary_directory() as raw_out:
            path = Path(raw_out) / "settings.json"
            custom = AppSettings()
            custom.language = "en-US"
            custom.audio.source = "speakers"
            save_settings(custom, path)

            schema = settings_schema()
            snapshot = settings_snapshot(path)
            reset = reset_settings(path)

            self.assertIn("language", {field.path for group in schema for field in group.fields})
            self.assertEqual(snapshot["current"]["language"], "en-US")
            self.assertEqual(reset.language, "ru-RU")
            self.assertEqual(load_settings(path).audio.source, "mic")

    def test_legacy_overlay_position_is_normalized(self) -> None:
        with temporary_directory() as raw_out:
            path = Path(raw_out) / "settings.json"
            path.write_text('{"overlay":{"position":"bottom-center"}}', encoding="utf-8")

            loaded = load_settings(path)
            save_settings(loaded, path)

            self.assertEqual(loaded.overlay.position, "lower-center")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["overlay"]["position"], "lower-center")

    def test_chrome_settings_contract(self) -> None:
        fields = {field.path: field for group in settings_schema() for field in group.fields}

        self.assertEqual(fields["engine.name"].options, ["chrome", "chatgpt", "openai"])
        self.assertEqual(AppSettings().bind.mic_hold, "F8")
        self.assertEqual(AppSettings().bind.speakers_hold, "F9")
        rust_settings = (Path(__file__).resolve().parents[1] / "src-tauri" / "src" / "settings.rs").read_text(encoding="utf-8")
        self.assertIn('mic_hold: "F8".into()', rust_settings)
        self.assertIn('speakers_hold: "F9".into()', rust_settings)
        self.assertEqual(fields["transcriber.endpoint"].default, "http://127.0.0.1:37182/v1/audio/transcriptions")
        self.assertIn("edge", fields["chrome.channel"].options)
        self.assertTrue(chrome_candidates("chromium"))
        self.assertTrue(chrome_candidates("edge"))
        self.assertEqual(chrome_profile_dir("/tmp/gdictate-test-profile"), Path("/tmp/gdictate-test-profile"))
        self.assertFalse(is_browser_configured("/tmp/gdictate-test-profile-missing"))

    def test_chrome_hidden_runtime_contract(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "gdictate_core" / "chrome.py").read_text(encoding="utf-8")

        self.assertIn("--window-size=1,1", source)
        self.assertIn("--window-position=32000,32000", source)
        self.assertIn("--renderer-process-limit=1", source)
        self.assertIn("--js-flags=--max-old-space-size=128", source)
        self.assertIn("async def _ensure_connected", source)

    def test_audio_router_settings_contract(self) -> None:
        with redirect_stderr(StringIO()):
            route = configure_audio_source("speakers", linux_router="manual", windows_speaker_input="vb-cable")

        expected_router = "windows:vb-cable" if sys.platform == "win32" else "manual"
        self.assertEqual(audio_router_label("manual", "vb-cable"), expected_router)
        self.assertEqual(route.mode, "speakers")
        self.assertEqual(route.router, expected_router)
        self.assertIsNone(route.active_source)

    def test_native_capture_does_not_require_default_source_switch(self) -> None:
        import gdictate_core.audio as audio_module

        original_default = audio_module.get_default_source
        original_best = audio_module.find_best_microphone
        original_set = audio_module.set_default_source
        audio_module.get_default_source = lambda: "current-mic"
        audio_module.find_best_microphone = lambda: {"name": "other-mic", "desc": "Other"}
        audio_module.set_default_source = lambda _name: self.fail("default source changed")
        try:
            route = configure_audio_source("mic", change_default=False)
        finally:
            audio_module.get_default_source = original_default
            audio_module.find_best_microphone = original_best
            audio_module.set_default_source = original_set
        self.assertEqual(route.active_source, "other-mic")
        self.assertIsNone(route.previous_default_source)

    def test_native_both_without_microphone_does_not_change_default(self) -> None:
        import gdictate_core.audio as audio_module

        originals = {
            "get_default_source": audio_module.get_default_source,
            "get_default_sink": audio_module.get_default_sink,
            "source_names": audio_module.source_names,
            "find_best_microphone": audio_module.find_best_microphone,
            "set_default_source": audio_module.set_default_source,
            "unload_stale_audio_modules": audio_module.unload_stale_audio_modules,
        }
        audio_module.get_default_source = lambda: "current-mic"
        audio_module.get_default_sink = lambda: "speaker"
        audio_module.source_names = lambda: {"speaker.monitor"}
        audio_module.find_best_microphone = lambda: None
        audio_module.set_default_source = lambda _name: self.fail("default source changed")
        audio_module.unload_stale_audio_modules = lambda: None
        try:
            route = configure_audio_source("both", change_default=False)
        finally:
            for name, value in originals.items():
                setattr(audio_module, name, value)
        self.assertEqual(route.active_source, "speaker.monitor")
        self.assertIsNone(route.previous_default_source)

    def test_tauri_settings_ui_matches_core_schema(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "App.tsx").read_text(encoding="utf-8")
        fields = [field for group in settings_schema() for field in group.fields]

        for field in fields:
            if field.path.startswith("transcriber."):
                continue
            self.assertIn(f"settings.{field.path}", source, field.path)
            if field.kind != "select":
                continue

            pattern = rf"<Select[^>]+value={{settings\.{re.escape(field.path)}}}[^>]+options={{\[(?P<options>[^\]]*)\]}}"
            match = re.search(pattern, source)
            self.assertIsNotNone(match, field.path)
            options = re.findall(r'"([^"]+)"', match.group("options"))
            self.assertEqual(options, field.options, field.path)

    def test_tauri_settings_apply_runtime_effects(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "App.tsx").read_text(encoding="utf-8")

        self.assertIn("function patchOverlay", source)
        self.assertRegex(source, r'Live popup" checked=\{settings\.overlay\.enabled\} onChange=\{\(enabled\) => patchOverlay')
        self.assertIn('await call<string>("close_overlay"', source)
        self.assertIn('await call<string>("daemon_shutdown"', source)
        self.assertIn('await call<string>("daemon_spawn"', source)
        self.assertNotIn('daemonCommand("evdev_hotkeys_spawn")', source)
        self.assertNotIn("setFinalText", source)
        self.assertNotIn("setEvents", source)
        tauri_source = (Path(__file__).resolve().parents[1] / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
        self.assertIn('"--user", "start", "gdictate-daemon.service"', tauri_source)
        self.assertNotIn('.arg("--daemon-hotkeys")', tauri_source)
        quit_block = tauri_source.split('"quit" => {', 1)[1].split("}", 1)[0]
        self.assertNotIn("--shutdown", quit_block)

    def test_linux_package_metadata_includes_core_runtime_deps(self) -> None:
        root = Path(__file__).resolve().parents[1]
        tauri_conf = json.loads((root / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))
        arch_script = (root / "scripts" / "package-arch.sh").read_text(encoding="utf-8")
        requirements = (root / "requirements.txt").read_text(encoding="utf-8")

        deb_deps = tauri_conf["bundle"]["linux"]["deb"]["depends"]
        rpm_deps = tauri_conf["bundle"]["linux"]["rpm"]["depends"]
        self.assertIn("python3", deb_deps)
        self.assertIn("python3-aiohttp", deb_deps)
        self.assertIn("python3-evdev", deb_deps)
        self.assertIn("python3", rpm_deps)
        self.assertIn("python3-aiohttp", rpm_deps)
        self.assertIn("python3-evdev", rpm_deps)
        self.assertIn("depend = python", arch_script)
        self.assertIn("depend = python-aiohttp", arch_script)
        self.assertIn("depend = python-evdev", arch_script)
        self.assertIn("aiohttp", requirements)
        self.assertIn("evdev", requirements)
        self.assertNotIn("dbus-python", requirements)
        self.assertNotIn("PyQt6", requirements)


class HotkeyParsingTests(unittest.TestCase):
    class Codes:
        EV_KEY = 1
        KEY_LEFTCTRL = 29
        KEY_RIGHTCTRL = 97
        KEY_LEFTALT = 56
        KEY_RIGHTALT = 100
        KEY_LEFTMETA = 125
        KEY_RIGHTMETA = 126
        KEY_LEFTSHIFT = 42
        KEY_RIGHTSHIFT = 54
        KEY_F8 = 66
        KEY_F9 = 67

    def test_hold_bindings_are_explicit_and_single_device_safe(self) -> None:
        mic = _binding_groups("F8", self.Codes)
        speaker = _binding_groups("ALT+F9", self.Codes)

        self.assertTrue(_is_pressed(mic, {self.Codes.KEY_F8}))
        self.assertFalse(_is_pressed(mic, {self.Codes.KEY_F9}))
        self.assertTrue(_is_pressed(speaker, {self.Codes.KEY_LEFTALT, self.Codes.KEY_F9}))
        self.assertFalse(_is_pressed(speaker, {self.Codes.KEY_LEFTALT}))

    def test_unknown_hold_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown evdev key"):
            _binding_groups("ALT+NOT_A_KEY", self.Codes)

    def test_virtual_keyboards_are_excluded(self) -> None:
        codes = self.Codes

        class Device:
            def __init__(self, name: str):
                self.name = name

            def capabilities(self):
                return {codes.EV_KEY: [codes.KEY_F8, codes.KEY_F9]}

        devices = [Device("ydotoold virtual device"), Device("AT Translated Set 2 keyboard")]
        keyboards = _hold_keyboards(devices, [_binding_groups("F8", codes)], codes)
        self.assertEqual([device.name for device in keyboards], ["AT Translated Set 2 keyboard"])


class PasteTests(unittest.TestCase):
    def test_linux_combo_keycodes(self) -> None:
        self.assertEqual(_linux_combo_keycodes("shift-insert"), ["42:1", "110:1", "110:0", "42:0"])
        self.assertEqual(_linux_combo_keycodes("ctrl-v"), ["29:1", "47:1", "47:0", "29:0"])
        self.assertEqual(
            _linux_combo_keycodes("ctrl-shift-v"),
            ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"],
        )


class PasteBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_type_mode_types_ascii_directly(self) -> None:
        calls: list[tuple[str, str]] = []

        async def fake_copy(text: str) -> bool:
            calls.append(("copy", text))
            return False

        async def fake_type(text: str) -> bool:
            calls.append(("type", text))
            return True

        original_copy = paste_module._copy_linux
        original_type = paste_module._ydotool_type
        paste_module._copy_linux = fake_copy
        paste_module._ydotool_type = fake_type
        try:
            ok = await paste_module._paste_linux("hello", "type", "ctrl-v")
            self.assertTrue(ok)
            self.assertEqual(calls, [("type", "hello")])
        finally:
            paste_module._copy_linux = original_copy
            paste_module._ydotool_type = original_type

    async def test_shift_insert_sets_primary_clipboard_best_effort(self) -> None:
        calls: list[tuple[str, bool]] = []

        async def fake_copy(_text: str, primary: bool = False) -> bool:
            calls.append(("copy", primary))
            return not primary

        async def fake_release() -> None:
            return None

        async def fake_sleep(_seconds: float) -> None:
            return None

        async def fake_paste(combo: str) -> bool:
            calls.append(("paste", combo == "shift-insert"))
            return True

        original_copy = paste_module._copy_linux
        original_release = paste_module._release_linux_virtual_modifiers
        original_paste = paste_module._ydotool_paste
        original_modifiers = paste_module._wait_linux_modifiers_released
        original_sleep = asyncio.sleep
        paste_module._copy_linux = fake_copy
        paste_module._release_linux_virtual_modifiers = fake_release
        paste_module._ydotool_paste = fake_paste
        paste_module._wait_linux_modifiers_released = fake_release
        asyncio.sleep = fake_sleep  # type: ignore[assignment]
        try:
            self.assertTrue(await paste_module._paste_linux("привет", "ydotool", "shift-insert"))
            self.assertEqual(calls, [("copy", False), ("copy", True), ("paste", True)])
        finally:
            paste_module._copy_linux = original_copy
            paste_module._release_linux_virtual_modifiers = original_release
            paste_module._ydotool_paste = original_paste
            paste_module._wait_linux_modifiers_released = original_modifiers
            asyncio.sleep = original_sleep  # type: ignore[assignment]

    async def test_clipboard_readback_failure_still_pastes(self) -> None:
        calls: list[str] = []

        async def fake_copy(_text: str) -> bool:
            return True

        async def fake_wait(_text: str) -> bool:
            return False

        async def fake_release() -> None:
            return None

        async def fake_paste(combo: str) -> bool:
            calls.append(combo)
            return True

        original_copy = paste_module._copy_linux
        original_wait = paste_module._wait_linux_clipboard_text
        original_release = paste_module._release_linux_virtual_modifiers
        original_paste = paste_module._ydotool_paste
        original_modifiers = paste_module._wait_linux_modifiers_released
        paste_module._copy_linux = fake_copy
        paste_module._wait_linux_clipboard_text = fake_wait
        paste_module._release_linux_virtual_modifiers = fake_release
        paste_module._ydotool_paste = fake_paste
        paste_module._wait_linux_modifiers_released = fake_release
        try:
            self.assertTrue(await paste_module._paste_linux("привет", "ydotool", "ctrl-shift-v"))
            self.assertEqual(calls, ["ctrl-shift-v"])
        finally:
            paste_module._copy_linux = original_copy
            paste_module._wait_linux_clipboard_text = original_wait
            paste_module._release_linux_virtual_modifiers = original_release
            paste_module._ydotool_paste = original_paste
            paste_module._wait_linux_modifiers_released = original_modifiers

    async def test_copy_mode_only_updates_clipboard(self) -> None:
        calls: list[tuple[str, str]] = []

        async def fake_copy(text: str) -> bool:
            calls.append(("copy", text))
            return True

        async def fake_type(text: str) -> bool:
            calls.append(("type", text))
            return True

        original_copy = paste_module._copy_linux
        original_type = paste_module._ydotool_type
        paste_module._copy_linux = fake_copy
        paste_module._ydotool_type = fake_type
        try:
            ok = await paste_module._paste_linux("hello", "copy", "ctrl-v")
            self.assertTrue(ok)
            self.assertEqual(calls, [("copy", "hello")])
        finally:
            paste_module._copy_linux = original_copy
            paste_module._ydotool_type = original_type

    async def test_type_mode_success_tracks_direct_type(self) -> None:
        async def fake_copy(_text: str) -> bool:
            return False

        async def fake_type(_text: str) -> bool:
            return True

        original_copy = paste_module._copy_linux
        original_type = paste_module._ydotool_type
        paste_module._copy_linux = fake_copy
        paste_module._ydotool_type = fake_type
        try:
            self.assertTrue(await paste_module._paste_linux("hello", "type", "ctrl-v"))
        finally:
            paste_module._copy_linux = original_copy
            paste_module._ydotool_type = original_type

    async def test_type_mode_uses_clipboard_paste_for_unicode(self) -> None:
        calls: list[tuple[str, str]] = []

        async def fake_copy(text: str) -> bool:
            calls.append(("copy", text))
            return True

        async def fake_type(text: str) -> bool:
            calls.append(("type", text))
            return True

        async def fake_paste(combo: str) -> bool:
            calls.append(("paste", combo))
            return True

        original_copy = paste_module._copy_linux
        original_type = paste_module._ydotool_type
        original_paste = paste_module._ydotool_paste
        original_which = paste_module.shutil.which
        paste_module._copy_linux = fake_copy
        paste_module._ydotool_type = fake_type
        paste_module._ydotool_paste = fake_paste
        paste_module.shutil.which = lambda name: "/usr/bin/ydotool" if name == "ydotool" else None
        try:
            ok = await paste_module._paste_linux("привет", "type", "ctrl-v")
            self.assertTrue(ok)
            self.assertEqual(calls, [("copy", "привет"), ("paste", "ctrl-v")])
        finally:
            paste_module._copy_linux = original_copy
            paste_module._ydotool_type = original_type
            paste_module._ydotool_paste = original_paste
            paste_module.shutil.which = original_which


class LivePasteTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_paste_queues_final_chunks_without_stop_duplicate(self) -> None:
        calls: list[str] = []

        async def fake_paste(text: str, *_args) -> bool:
            calls.append(text)
            return True

        original = app_module.paste_text
        app_module.paste_text = fake_paste
        try:
            dictation = Dictation(paste_live=True)
            dictation.state = State.RECORDING
            dictation.on_transcript(TranscriptResult("первый", True))
            dictation.on_transcript(TranscriptResult("второй", True))
            dictation.on_transcript(TranscriptResult("первый второй", False))

            await dictation.stop_recording()

            self.assertEqual(calls, ["первый", " второй"])
        finally:
            app_module.paste_text = original

    async def test_live_paste_appends_interim_delta(self) -> None:
        calls: list[str] = []

        async def fake_paste(text: str, *_args) -> bool:
            calls.append(text)
            return True

        original = app_module.paste_text
        app_module.paste_text = fake_paste
        try:
            dictation = Dictation(paste_live=True)
            dictation.state = State.RECORDING

            dictation.on_transcript(TranscriptResult("первый", False))
            dictation.on_transcript(TranscriptResult("первый второй", False))
            dictation.on_transcript(TranscriptResult("первый второй третий", False))
            dictation.on_transcript(TranscriptResult("первый второй третий", True))
            await dictation.stop_recording()

            self.assertEqual(calls, ["первый", " второй", " третий"])
        finally:
            app_module.paste_text = original

    async def test_dual_hold_defers_paste_until_stop(self) -> None:
        calls: list[str] = []

        async def fake_paste(text: str, *_args) -> bool:
            calls.append(text)
            return True

        original = app_module.paste_text
        app_module.paste_text = fake_paste
        try:
            dictation = Dictation(paste_live=True, paste_live_during_recording=False)
            dictation.state = State.RECORDING

            dictation.on_transcript(TranscriptResult("первый", False))
            dictation.on_transcript(TranscriptResult("первый второй", False))
            self.assertEqual(calls, [])

            await dictation.stop_recording()

            self.assertEqual(calls, ["первый второй"])
        finally:
            app_module.paste_text = original


if __name__ == "__main__":
    unittest.main()
