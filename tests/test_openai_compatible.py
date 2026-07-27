from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from gdictate_core.app import Dictation
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

        class FinishedProcess:
            returncode = 0

        engine._recording = FinishedProcess()  # type: ignore[assignment]

        async def fake_transcribe(_path: Path) -> str:
            return "  Привет, мир  "

        engine._transcribe = fake_transcribe  # type: ignore[method-assign]
        await engine.stop_recognition()

        self.assertEqual([(event.text, event.is_final) for event in events], [("Привет, мир", True)])
        self.assertFalse(path.exists())

    async def test_stop_failure_returns_to_idle_without_paste(self) -> None:
        class FailingEngine:
            name = "chatgpt"

            async def stop_recognition(self) -> None:
                raise RuntimeError("bridge expired")

        dictation = Dictation(paste_mode="none")
        dictation.state = State.RECORDING
        dictation.engine = FailingEngine()  # type: ignore[assignment]
        await dictation.stop_recording()
        self.assertEqual(dictation.state, State.IDLE)

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
