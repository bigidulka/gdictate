from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Optional


class PipeWireCapture:
    """One native PipeWire capture stream shared by recording and level UI."""

    def __init__(
        self,
        output_path: Path,
        source_name: Optional[str],
        *,
        rate: int = 48000,
        channels: int = 1,
        debug: bool = False,
    ) -> None:
        self.output_path = output_path
        self.source_name = source_name or "@DEFAULT_AUDIO_SOURCE@"
        self.rate = rate
        self.channels = channels
        self.debug = debug
        self.process: Optional[asyncio.subprocess.Process] = None
        self._stderr_task: Optional[asyncio.Task[bytes]] = None

    @staticmethod
    def available() -> bool:
        return shutil.which("pw-record") is not None

    async def start(self) -> None:
        if not self.available():
            raise RuntimeError("pw-record is required for native PipeWire capture")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_path.unlink(missing_ok=True)
        fd = os.open(self.output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        try:
            stderr = asyncio.subprocess.PIPE if self.debug else asyncio.subprocess.DEVNULL
            self.process = await asyncio.create_subprocess_exec(
                "pw-record",
                "--target",
                self.source_name,
                "--rate",
                str(self.rate),
                "--channels",
                str(self.channels),
                "--format",
                "s16",
                "--container",
                "wav",
                str(self.output_path),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=stderr,
            )
        except Exception:
            self.output_path.unlink(missing_ok=True)
            raise
        if self.debug and self.process.stderr:
            self._stderr_task = asyncio.create_task(self.process.stderr.read())
        await asyncio.sleep(0.08)
        if self.process.returncode is not None:
            detail = await self._stderr_detail()
            self.process = None
            self.output_path.unlink(missing_ok=True)
            raise RuntimeError(detail or "pw-record exited before recording started")
        try:
            if self.output_path.stat().st_mode & 0o077:
                raise RuntimeError("recorded audio permissions are broader than 0600")
        except OSError as exc:
            await self.stop()
            self.output_path.unlink(missing_ok=True)
            raise RuntimeError(f"cannot secure recorded audio: {exc}") from exc

    async def stop(self) -> None:
        proc, self.process = self.process, None
        if not proc:
            return
        if proc.returncode is None:
            proc.send_signal(2)  # SIGINT lets pw-record finalize the WAV header.
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
        await self._stderr_detail()
        if self.output_path.exists():
            try:
                self.output_path.chmod(0o600)
            except OSError:
                pass

    async def close(self) -> None:
        await self.stop()

    async def _stderr_detail(self) -> str:
        task, self._stderr_task = self._stderr_task, None
        if not task:
            return ""
        try:
            data = await task
        except Exception:
            return ""
        lines = [line.strip() for line in data.decode("utf-8", errors="replace").splitlines() if line.strip()]
        return lines[-1] if lines else ""
