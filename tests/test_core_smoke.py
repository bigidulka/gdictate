from __future__ import annotations

import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from gdictate_core.audio import audio_router_label, configure_audio_source
from gdictate_core import app as app_module
from gdictate_core.app import Dictation
from gdictate_core.chrome import chrome_profile_dir, is_browser_configured
from gdictate_core.file_jobs import FileTranscriptionResult, FileTranscriptionSegment, export_transcription
from gdictate_core.install_assets import install_user_assets, user_install_plan
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
                self.assertEqual(len(plan.assets), 3)
                service = home / ".config" / "systemd" / "user" / "gdictate-daemon.service"
                self.assertIn("--daemon --no-ui", service.read_text(encoding="utf-8"))


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

    def test_tauri_settings_ui_matches_core_schema(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "src" / "App.tsx").read_text(encoding="utf-8")
        fields = [field for group in settings_schema() for field in group.fields]

        for field in fields:
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


class PasteTests(unittest.TestCase):
    def test_linux_combo_keycodes(self) -> None:
        self.assertEqual(_linux_combo_keycodes("ctrl-v"), ["29:1", "47:1", "47:0", "29:0"])
        self.assertEqual(
            _linux_combo_keycodes("ctrl-shift-v"),
            ["29:1", "42:1", "47:1", "47:0", "42:0", "29:0"],
        )


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
