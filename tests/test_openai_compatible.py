from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path

from gdictate_core import app as app_module
from gdictate_core.app import Dictation
from gdictate_core.audio_capture import PipeWireCapture
from gdictate_core.models import State, TranscriptResult
from gdictate_core.openai_compatible import ChatGPTSpeechEngine, OpenAICompatibleSpeechEngine, endpoint_health_url


class EndpointTests(unittest.TestCase):
    def test_health_url_removes_transcription_path(self) -> None:
        self.assertEqual(
            endpoint_health_url("http://127.0.0.1:37182/v1/audio/transcriptions"),
            "http://127.0.0.1:37182/health",
        )
        self.assertEqual(endpoint_health_url("http://localhost:9000/api"), "http://localhost:9000/api/health")

    def test_chatgpt_defaults_to_loopback_bridge(self) -> None:
        engine = ChatGPTSpeechEngine(language="ru-RU")
        self.assertEqual(engine.name, "chatgpt")
        self.assertEqual(engine.endpoint, "http://127.0.0.1:37182/v1/audio/transcriptions")
        self.assertTrue(engine.health_check)


class OpenAICompatibleEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_transcription_emits_normalized_final_result(self) -> None:
        events: list[TranscriptResult] = []
        engine = OpenAICompatibleSpeechEngine("http://127.0.0.1:9/v1/audio/transcriptions", "ru-RU")
        engine._on_transcript = events.append
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as raw_audio:
            raw_audio.write(b"RIFF" + b"x" * 256)
            path = Path(raw_audio.name)
        engine._audio_path = path

        class FinishedCapture:
            async def stop(self) -> None:
                return None

        engine._capture = FinishedCapture()  # type: ignore[assignment]

        async def fake_transcribe(_path: Path) -> str:
            return "  Привет, мир  "

        engine._transcribe = fake_transcribe  # type: ignore[method-assign]
        await engine.stop_recognition()

        self.assertEqual([(event.text, event.is_final) for event in events], [("Привет, мир", True)])
        self.assertFalse(path.exists())

    async def test_pipewire_capture_builds_native_target_command(self) -> None:
        calls: list[tuple] = []

        class Process:
            returncode = None
            stderr = None

        async def fake_exec(*args, **kwargs):
            calls.append((args, kwargs))
            output = Path(args[-1])
            output.write_bytes(b"RIFF" + b"x" * 256)
            return Process()

        original = asyncio.create_subprocess_exec
        asyncio.create_subprocess_exec = fake_exec  # type: ignore[assignment]
        try:
            with tempfile.TemporaryDirectory() as raw_dir:
                output = Path(raw_dir) / "test.wav"
                capture = PipeWireCapture(output, "source.node", rate=48000, channels=1)
                await capture.start()
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        finally:
            asyncio.create_subprocess_exec = original  # type: ignore[assignment]

        args, _kwargs = calls[0]
        self.assertEqual(args[0], "pw-record")
        self.assertIn("source.node", args)
        self.assertIn("--container", args)
        self.assertIn("wav", args)

    async def test_recording_stopped_event_contains_length_not_text(self) -> None:
        events: list[tuple[str, dict]] = []

        class Engine:
            name = "chatgpt"

            async def stop_recognition(self) -> None:
                dictation._full_text = "private transcript"

        dictation = Dictation(paste_mode="none")
        dictation.state = State.RECORDING
        dictation.engine = Engine()  # type: ignore[assignment]
        dictation.on_event = lambda event: events.append((event.type, event.payload))
        await dictation.stop_recording()

        stopped = next(payload for event_type, payload in events if event_type == "recording.stopped")
        self.assertEqual(stopped["text_length"], len("private transcript"))
        self.assertNotIn("text", stopped)
        self.assertEqual(dictation.status()["text_length"], 0)
        self.assertEqual(dictation._final_segments, [])

    async def test_stop_hides_overlay_before_final_paste(self) -> None:
        calls: list[tuple[str, str]] = []

        class Engine:
            name = "chatgpt"

            async def stop_recognition(self) -> None:
                return None

        class Overlay:
            def show_transcribing(self) -> None:
                calls.append(("overlay", "transcribing"))

            def hide_popup(self) -> None:
                calls.append(("overlay", "hide"))

        async def fake_paste(text: str, *_args) -> bool:
            calls.append(("paste", text))
            return True

        async def fake_sleep(seconds: float) -> None:
            calls.append(("sleep", str(seconds)))

        dictation = Dictation(paste_mode="auto", paste_live=False)
        dictation.state = State.RECORDING
        dictation.engine = Engine()  # type: ignore[assignment]
        dictation.overlay = Overlay()
        dictation._full_text = "private transcript"
        original_paste = app_module.paste_text
        original_sleep = asyncio.sleep
        app_module.paste_text = fake_paste
        asyncio.sleep = fake_sleep  # type: ignore[assignment]
        try:
            await dictation.stop_recording()
        finally:
            app_module.paste_text = original_paste
            asyncio.sleep = original_sleep  # type: ignore[assignment]

        self.assertEqual(
            calls,
            [
                ("overlay", "transcribing"),
                ("overlay", "hide"),
                ("sleep", "0.12"),
                ("paste", "private transcript"),
            ],
        )

    async def test_stop_failure_returns_to_idle_without_paste(self) -> None:
        class FailingEngine:
            name = "chatgpt"

            async def stop_recognition(self) -> None:
                raise RuntimeError("bridge expired")

        class Overlay:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            def show_transcribing(self) -> None:
                self.calls.append(("transcribing", ""))

            def show_error(self, text: str) -> None:
                self.calls.append(("error", text))

            def hide_popup(self) -> None:
                self.calls.append(("hide", ""))

        overlay = Overlay()
        dictation = Dictation(paste_mode="none")
        dictation.state = State.RECORDING
        dictation.engine = FailingEngine()  # type: ignore[assignment]
        dictation.overlay = overlay
        original_sleep = asyncio.sleep

        async def no_sleep(_seconds: float) -> None:
            return None

        asyncio.sleep = no_sleep  # type: ignore[assignment]
        try:
            await dictation.stop_recording()
        finally:
            asyncio.sleep = original_sleep  # type: ignore[assignment]
        self.assertEqual(dictation.state, State.IDLE)
        self.assertEqual(overlay.calls, [("transcribing", ""), ("error", "bridge expired"), ("hide", "")])

    async def test_api_key_rejects_plaintext_remote_endpoint(self) -> None:
        import os

        engine = OpenAICompatibleSpeechEngine(
            "http://example.com/v1/audio/transcriptions",
            "ru-RU",
            api_key_env="TEST_GDICTATE_KEY",
        )
        with tempfile.NamedTemporaryFile(suffix=".wav") as raw_audio:
            raw_audio.write(b"RIFF" + b"x" * 256)
            raw_audio.flush()
            os.environ["TEST_GDICTATE_KEY"] = "secret"
            try:
                with self.assertRaisesRegex(RuntimeError, "non-loopback HTTP"):
                    await engine._transcribe(Path(raw_audio.name))
            finally:
                os.environ.pop("TEST_GDICTATE_KEY", None)

    async def test_http_payload_requires_text(self) -> None:
        engine = OpenAICompatibleSpeechEngine("http://127.0.0.1:9/v1/audio/transcriptions", "ru-RU")
        with tempfile.NamedTemporaryFile(suffix=".wav") as raw_audio:
            raw_audio.write(b"RIFF" + b"x" * 256)
            raw_audio.flush()

            class FakeResponse:
                status = 200

                async def read(self):
                    return json.dumps({"unexpected": "payload"}).encode()

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args):
                    return None

            class FakeSession:
                def post(self, *_args, **_kwargs):
                    return FakeResponse()

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *_args):
                    return None

            import gdictate_core.openai_compatible as module

            original = module.aiohttp.ClientSession
            module.aiohttp.ClientSession = lambda **_kwargs: FakeSession()  # type: ignore[assignment]
            try:
                with self.assertRaisesRegex(RuntimeError, "no text field"):
                    await engine._transcribe(Path(raw_audio.name))
            finally:
                module.aiohttp.ClientSession = original  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main()
