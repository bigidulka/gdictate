from __future__ import annotations

import importlib.util
import asyncio
import json
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional


@dataclass
class FilePipelineDependency:
    name: str
    available: bool
    role: str
    install_hint: str
    path: Optional[str] = None


@dataclass
class MediaStream:
    index: int
    kind: str
    codec: str = ""
    channels: Optional[int] = None
    sample_rate: Optional[int] = None
    language: str = ""
    duration: Optional[float] = None


@dataclass
class MediaProbe:
    path: str
    exists: bool
    duration: Optional[float] = None
    format_name: str = ""
    streams: list[MediaStream] = field(default_factory=list)
    error: str = ""


@dataclass
class FilePipelineStage:
    id: str
    label: str
    status: str
    detail: str


@dataclass
class FilePipelineReport:
    dependencies: list[FilePipelineDependency]
    media: Optional[MediaProbe]
    stages: list[FilePipelineStage]
    warnings: list[str]
    actions: list[str]


@dataclass
class FileTranscriptionSegment:
    id: int
    start: float
    end: float
    text: str
    speaker: str = "SPEAKER_00"
    confidence: float = 0.0


@dataclass
class FileTranscriptionOptions:
    path: str
    output_dir: Optional[str] = None
    language: Optional[str] = None
    model_size: str = "small"
    device: str = "auto"
    compute_type: str = "default"
    diarize: bool = False
    diarization_backend: str = "auto"
    formats: list[str] = field(default_factory=lambda: ["json", "txt", "srt", "vtt"])


@dataclass
class FileTranscriptionResult:
    ok: bool
    path: str
    output_dir: str = ""
    text: str = ""
    segments: list[FileTranscriptionSegment] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    diarization_backend: str = "off"
    speaker_count: int = 0
    error: str = ""


@dataclass
class FileJobStatus:
    id: str
    status: str
    path: str
    progress: float = 0.0
    message: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    result: Optional[FileTranscriptionResult] = None
    error: str = ""


EventEmitter = Callable[[dict], None]


class FileJobManager:
    def __init__(self, emit: Optional[EventEmitter] = None):
        self.emit = emit
        self.jobs: dict[str, FileJobStatus] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    async def start(self, options: FileTranscriptionOptions) -> FileJobStatus:
        now = time.time()
        job = FileJobStatus(
            id=uuid.uuid4().hex,
            status="queued",
            path=options.path,
            message="queued",
            created_at=now,
            updated_at=now,
        )
        self.jobs[job.id] = job
        self._emit(job)
        self._tasks[job.id] = asyncio.create_task(self._run(job.id, options))
        return job

    def list(self) -> list[FileJobStatus]:
        return sorted(self.jobs.values(), key=lambda job: job.created_at, reverse=True)

    def get(self, job_id: str) -> Optional[FileJobStatus]:
        return self.jobs.get(job_id)

    def cancel(self, job_id: str) -> Optional[FileJobStatus]:
        job = self.jobs.get(job_id)
        if not job:
            return None
        task = self._tasks.get(job_id)
        if job.status == "running":
            self._update(job, "cancel_requested", job.progress, "cancel requested; backend will finish current operation")
            return job
        if task and not task.done():
            task.cancel()
        self._update(job, "cancelled", 1.0, "cancelled")
        return job

    async def close(self) -> None:
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _run(self, job_id: str, options: FileTranscriptionOptions) -> None:
        job = self.jobs[job_id]
        try:
            self._update(job, "running", 0.05, "starting")
            result = await asyncio.to_thread(transcribe_file, options)
            if job.status == "cancel_requested":
                result.warnings.append("Cancel was requested while backend was running; backend finished before it could stop.")
            if result.ok:
                self._update(job, "done", 1.0, f"{len(result.segments)} segments", result=result)
            else:
                self._update(job, "failed", 1.0, result.error, result=result, error=result.error)
        except asyncio.CancelledError:
            self._update(job, "cancelled", 1.0, "cancelled")
        except Exception as exc:
            self._update(job, "failed", 1.0, str(exc), error=str(exc))

    def _update(
        self,
        job: FileJobStatus,
        status: str,
        progress: float,
        message: str,
        result: Optional[FileTranscriptionResult] = None,
        error: str = "",
    ) -> None:
        job.status = status
        job.progress = progress
        job.message = message
        job.updated_at = time.time()
        job.result = result
        job.error = error
        self._emit(job)

    def _emit(self, job: FileJobStatus) -> None:
        if self.emit:
            self.emit({"type": "file.job", "job": _dataclass_to_dict(job)})


def _module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except ModuleNotFoundError:
        return False


def dependency_report() -> list[FilePipelineDependency]:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    return [
        FilePipelineDependency(
            name="ffmpeg",
            available=ffmpeg is not None,
            path=ffmpeg,
            role="extract/convert audio from video/audio containers",
            install_hint="install ffmpeg via OS package manager",
        ),
        FilePipelineDependency(
            name="ffprobe",
            available=ffprobe is not None,
            path=ffprobe,
            role="inspect media duration, streams, codecs",
            install_hint="install ffmpeg via OS package manager",
        ),
        FilePipelineDependency(
            name="faster-whisper",
            available=_module_available("faster_whisper"),
            role="local Whisper ASR engine for batch transcription",
            install_hint="python gdictate.py --apply-system-action install_batch_extras",
        ),
        FilePipelineDependency(
            name="whisperx",
            available=_module_available("whisperx"),
            role="word alignment and optional diarization workflow",
            install_hint="python gdictate.py --apply-system-action install_batch_extras",
        ),
        FilePipelineDependency(
            name="pyannote.audio",
            available=_module_available("pyannote.audio"),
            role="speaker diarization backend",
            install_hint="python gdictate.py --apply-system-action install_batch_extras; Hugging Face token may be required by models",
        ),
    ]


def probe_media(path: str | Path | None) -> Optional[MediaProbe]:
    if not path:
        return None
    media_path = Path(path).expanduser()
    if not media_path.exists():
        return MediaProbe(path=str(media_path), exists=False, error="file not found")
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return MediaProbe(path=str(media_path), exists=True, error="ffprobe not found")

    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(media_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return MediaProbe(path=str(media_path), exists=True, error=result.stderr.strip() or "ffprobe failed")

    try:
        data: dict[str, Any] = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return MediaProbe(path=str(media_path), exists=True, error=f"ffprobe JSON parse failed: {exc}")

    fmt = data.get("format") or {}
    streams: list[MediaStream] = []
    for stream in data.get("streams") or []:
        tags = stream.get("tags") or {}
        duration = stream.get("duration")
        streams.append(
            MediaStream(
                index=int(stream.get("index", 0)),
                kind=str(stream.get("codec_type", "")),
                codec=str(stream.get("codec_name", "")),
                channels=_safe_int(stream.get("channels")),
                sample_rate=_safe_int(stream.get("sample_rate")),
                language=str(tags.get("language", "")),
                duration=_safe_float(duration),
            )
        )

    return MediaProbe(
        path=str(media_path),
        exists=True,
        duration=_safe_float(fmt.get("duration")),
        format_name=str(fmt.get("format_name", "")),
        streams=streams,
    )


def pipeline_report(path: str | Path | None = None) -> FilePipelineReport:
    deps = dependency_report()
    dep_map = {dep.name: dep for dep in deps}
    media = probe_media(path)
    warnings: list[str] = []
    actions: list[str] = []

    if not dep_map["ffmpeg"].available:
        warnings.append("ffmpeg not found; audio extraction from files is unavailable.")
        actions.append(dep_map["ffmpeg"].install_hint)
    if not dep_map["ffprobe"].available:
        warnings.append("ffprobe not found; media probing is unavailable.")
        actions.append(dep_map["ffprobe"].install_hint)
    if not dep_map["faster-whisper"].available:
        warnings.append("No local ASR backend installed for batch transcription.")
        actions.append(dep_map["faster-whisper"].install_hint)
    if not dep_map["whisperx"].available and not dep_map["pyannote.audio"].available:
        warnings.append("Speaker diarization backend is not installed.")
        actions.append("python gdictate.py --apply-system-action install_batch_extras")
    if media and media.error:
        warnings.append(media.error)

    stages = [
        FilePipelineStage(
            id="probe",
            label="Media probe",
            status="ready" if dep_map["ffprobe"].available and not (media and media.error) else "blocked",
            detail="ffprobe reads streams, duration, codec metadata",
        ),
        FilePipelineStage(
            id="extract",
            label="Audio extraction",
            status="ready" if dep_map["ffmpeg"].available else "blocked",
            detail="ffmpeg converts source to mono/stereo PCM for ASR",
        ),
        FilePipelineStage(
            id="asr",
            label="Speech recognition",
            status="ready" if dep_map["faster-whisper"].available else "missing",
            detail="faster-whisper local model produces segments and text",
        ),
        FilePipelineStage(
            id="diarization",
            label="Speaker separation",
            status="ready" if dep_map["whisperx"].available or dep_map["pyannote.audio"].available else "missing",
            detail="WhisperX/pyannote assigns speaker labels to segments",
        ),
        FilePipelineStage(
            id="export",
            label="Export",
            status="planned",
            detail="TXT/SRT/VTT/JSON export with speaker labels",
        ),
    ]

    return FilePipelineReport(dependencies=deps, media=media, stages=stages, warnings=warnings, actions=actions)


def transcribe_file(options: FileTranscriptionOptions) -> FileTranscriptionResult:
    source = Path(options.path).expanduser()
    result = FileTranscriptionResult(ok=False, path=str(source))
    if not source.exists():
        result.error = "file not found"
        return result

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        result.error = "ffmpeg not found"
        result.warnings.append("Install ffmpeg before batch transcription.")
        return result

    if not _module_available("faster_whisper"):
        result.error = "faster-whisper not installed"
        result.warnings.append("Run `python gdictate.py --apply-system-action install_batch_extras` to enable local batch transcription.")
        return result

    out_dir = Path(options.output_dir).expanduser() if options.output_dir else source.parent / f"{source.stem}.gdictate"
    work_dir = out_dir / ".work"
    out_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    result.output_dir = str(out_dir)

    audio_path = work_dir / "audio.wav"
    extract = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-f",
            "wav",
            str(audio_path),
        ],
        capture_output=True,
        text=True,
    )
    if extract.returncode != 0:
        result.error = extract.stderr.strip() or "ffmpeg extraction failed"
        return result

    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception as exc:
        result.error = f"failed to import faster-whisper: {exc}"
        return result

    try:
        model = WhisperModel(options.model_size, device=options.device, compute_type=options.compute_type)
        segments_iter, info = model.transcribe(
            str(audio_path),
            language=_asr_language(options.language),
            vad_filter=True,
        )
        segments: list[FileTranscriptionSegment] = []
        for index, segment in enumerate(segments_iter):
            text = str(getattr(segment, "text", "")).strip()
            if not text:
                continue
            segments.append(
                FileTranscriptionSegment(
                    id=index,
                    start=float(getattr(segment, "start", 0.0)),
                    end=float(getattr(segment, "end", 0.0)),
                    text=text,
                    confidence=float(getattr(segment, "avg_logprob", 0.0) or 0.0),
                )
            )
    except Exception as exc:
        result.error = f"ASR failed: {exc}"
        return result

    if options.diarize:
        _apply_diarization(segments, audio_path, options, result)
    else:
        result.speaker_count = _speaker_count(segments)

    result.segments = segments
    result.text = "\n".join(segment.text for segment in segments).strip()
    result.files = export_transcription(result, out_dir, options.formats)
    result.ok = True
    return result


def _apply_diarization(
    segments: list[FileTranscriptionSegment],
    audio_path: Path,
    options: FileTranscriptionOptions,
    result: FileTranscriptionResult,
) -> None:
    backend = _select_diarization_backend(options.diarization_backend)
    if backend == "off":
        result.warnings.append("Diarization requested but no backend is installed; exported as SPEAKER_00.")
        result.speaker_count = _speaker_count(segments)
        return

    attempted: list[str] = []
    for candidate in _candidate_diarization_backends(backend):
        attempted.append(candidate)
        try:
            if candidate == "whisperx":
                labels = _diarize_with_whisperx(audio_path, segments, options)
            elif candidate == "pyannote":
                labels = _diarize_with_pyannote(audio_path, segments, options)
            else:
                continue
        except Exception as exc:
            result.warnings.append(f"{candidate} diarization failed: {exc}")
            continue

        if labels:
            for segment, speaker in zip(segments, labels):
                segment.speaker = speaker
            result.diarization_backend = candidate
            result.speaker_count = _speaker_count(segments)
            return

        result.warnings.append(f"{candidate} diarization produced no speaker labels.")

    result.diarization_backend = "off"
    result.speaker_count = _speaker_count(segments)
    tried = ", ".join(attempted) if attempted else "no installed backend"
    result.warnings.append(f"Diarization unavailable after trying: {tried}; exported as SPEAKER_00.")


def _select_diarization_backend(requested: str) -> str:
    requested = (requested or "auto").lower()
    if requested in ("none", "off", "false"):
        return "off"
    if requested in ("whisperx", "pyannote"):
        return requested
    return "auto"


def _candidate_diarization_backends(selected: str) -> list[str]:
    if selected == "auto":
        return [backend for backend in ("whisperx", "pyannote") if _diarization_backend_available(backend)]
    if _diarization_backend_available(selected):
        return [selected]
    return []


def _diarization_backend_available(backend: str) -> bool:
    if backend == "whisperx":
        return _module_available("whisperx")
    if backend == "pyannote":
        return _module_available("pyannote.audio")
    return False


def _diarize_with_whisperx(
    audio_path: Path,
    segments: list[FileTranscriptionSegment],
    options: FileTranscriptionOptions,
) -> list[str]:
    import whisperx  # type: ignore

    token = _hf_token()
    device = _torch_device(options.device)
    audio = whisperx.load_audio(str(audio_path))
    diarize_model = whisperx.DiarizationPipeline(use_auth_token=token, device=device)
    diarized = diarize_model(audio)
    payload = {
        "segments": [
            {"start": segment.start, "end": segment.end, "text": segment.text, "speaker": segment.speaker}
            for segment in segments
        ]
    }
    assigned = whisperx.assign_word_speakers(diarized, payload)
    return [str(item.get("speaker") or "SPEAKER_00") for item in assigned.get("segments", [])]


def _diarize_with_pyannote(
    audio_path: Path,
    segments: list[FileTranscriptionSegment],
    options: FileTranscriptionOptions,
) -> list[str]:
    from pyannote.audio import Pipeline  # type: ignore

    token = _hf_token()
    model_name = os.environ.get("GDICTATE_PYANNOTE_MODEL", "pyannote/speaker-diarization-3.1")
    if token:
        pipeline = Pipeline.from_pretrained(model_name, use_auth_token=token)
    else:
        pipeline = Pipeline.from_pretrained(model_name)
    if hasattr(pipeline, "to"):
        try:
            import torch  # type: ignore

            pipeline.to(torch.device(_torch_device(options.device)))
        except Exception:
            pass
    annotation = pipeline(str(audio_path))
    turns: list[tuple[float, float, str]] = []
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        turns.append((float(turn.start), float(turn.end), str(speaker)))
    return _assign_speakers_by_overlap(segments, turns)


def _assign_speakers_by_overlap(
    segments: list[FileTranscriptionSegment],
    turns: list[tuple[float, float, str]],
) -> list[str]:
    labels: list[str] = []
    for segment in segments:
        scores: dict[str, float] = {}
        for start, end, speaker in turns:
            overlap = max(0.0, min(segment.end, end) - max(segment.start, start))
            if overlap > 0:
                scores[speaker] = scores.get(speaker, 0.0) + overlap
        labels.append(max(scores, key=scores.get) if scores else segment.speaker)
    return labels


def _hf_token() -> Optional[str]:
    return (
        os.environ.get("HUGGING_FACE_HUB_TOKEN")
        or os.environ.get("HF_TOKEN")
        or os.environ.get("PYANNOTE_AUTH_TOKEN")
        or None
    )


def _torch_device(requested: str) -> str:
    requested = (requested or "auto").lower()
    if requested in ("cpu", "cuda"):
        return requested
    try:
        import torch  # type: ignore

        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _speaker_count(segments: list[FileTranscriptionSegment]) -> int:
    return len({segment.speaker for segment in segments if segment.speaker})


def export_transcription(result: FileTranscriptionResult, out_dir: Path, formats: list[str]) -> dict[str, str]:
    selected = {fmt.lower() for fmt in formats}
    if "all" in selected:
        selected = {"json", "txt", "srt", "vtt"}
    files: dict[str, str] = {}

    if "json" in selected:
        path = out_dir / "transcript.json"
        payload = {
            "source": result.path,
            "text": result.text,
            "segments": [segment.__dict__ for segment in result.segments],
            "warnings": result.warnings,
            "diarization_backend": result.diarization_backend,
            "speaker_count": result.speaker_count,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        files["json"] = str(path)

    if "txt" in selected:
        path = out_dir / "transcript.txt"
        lines = [f"{segment.speaker}: {segment.text}" for segment in result.segments]
        path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
        files["txt"] = str(path)

    if "srt" in selected:
        path = out_dir / "transcript.srt"
        path.write_text(_srt(result.segments), encoding="utf-8")
        files["srt"] = str(path)

    if "vtt" in selected:
        path = out_dir / "transcript.vtt"
        path.write_text("WEBVTT\n\n" + _vtt(result.segments), encoding="utf-8")
        files["vtt"] = str(path)

    return files


def _asr_language(language: Optional[str]) -> Optional[str]:
    if not language:
        return None
    return language.split("-")[0].lower()


def _srt(segments: list[FileTranscriptionSegment]) -> str:
    blocks = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            f"{index}\n{_stamp(segment.start, comma=True)} --> {_stamp(segment.end, comma=True)}\n{segment.speaker}: {segment.text}\n"
        )
    return "\n".join(blocks)


def _vtt(segments: list[FileTranscriptionSegment]) -> str:
    blocks = []
    for segment in segments:
        blocks.append(f"{_stamp(segment.start)} --> {_stamp(segment.end)}\n{segment.speaker}: {segment.text}\n")
    return "\n".join(blocks)


def _stamp(seconds: float, comma: bool = False) -> str:
    millis = int(round(max(seconds, 0.0) * 1000))
    hours, rem = divmod(millis, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    sep = "," if comma else "."
    return f"{hours:02}:{minutes:02}:{secs:02}{sep}{ms:03}"


def _safe_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dataclass_to_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return {key: _dataclass_to_dict(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, list):
        return [_dataclass_to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: _dataclass_to_dict(item) for key, item in value.items()}
    return value
