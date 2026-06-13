from __future__ import annotations

from dataclasses import dataclass

from .file_jobs import pipeline_report
from .install_assets import user_install_plan
from .platforms import capability_report, diagnostics_report, live_report


@dataclass
class PreflightCheck:
    id: str
    label: str
    status: str
    detail: str
    action: str = ""


@dataclass
class PreflightReport:
    os: str
    desktop: str
    checks: list[PreflightCheck]
    warnings: list[str]
    actions: list[str]


def preflight_report() -> PreflightReport:
    caps = capability_report()
    diagnostics = diagnostics_report()
    live = live_report()
    files = pipeline_report()
    install = user_install_plan()
    checks: list[PreflightCheck] = []

    checks.append(
        PreflightCheck(
            "chrome",
            "Chrome/WebSpeech engine",
            "ready" if caps.chrome else "missing",
            "Chrome/Chromium/Edge detected" if caps.chrome else "Chrome/Chromium/Edge not found",
            "Install Chrome/Chromium/Edge" if not caps.chrome else "",
        )
    )
    checks.append(
        PreflightCheck(
            "speaker_capture",
            "Speaker capture",
            "ready" if diagnostics.speaker_capture_ready else "warning",
            diagnostics.paste_backend if diagnostics.speaker_capture_ready else "Speaker output capture needs OS audio routing/virtual input",
            "Open OS tab diagnostics" if not diagnostics.speaker_capture_ready else "",
        )
    )
    paste_missing = "missing" in diagnostics.paste_backend.lower()
    checks.append(
        PreflightCheck(
            "paste",
            "Paste backend",
            "missing" if paste_missing else "ready",
            diagnostics.paste_backend,
            "Install wl-clipboard and ydotool/wtype" if paste_missing else "",
        )
    )
    checks.append(
        PreflightCheck(
            "hotkeys",
            "Global hotkeys",
            "ready" if diagnostics.hotkey_backend else "warning",
            diagnostics.hotkey_backend,
            "Use Tauri native hotkeys, DE shortcuts, or evdev input group",
        )
    )
    checks.append(
        PreflightCheck(
            "live",
            "Live popup",
            "ready" if live.backend else "warning",
            live.backend,
            "; ".join(live.actions),
        )
    )

    stage_map = {stage.id: stage for stage in files.stages}
    asr = stage_map.get("asr")
    diarization = stage_map.get("diarization")
    checks.append(
        PreflightCheck(
            "file_asr",
            "File transcription ASR",
            asr.status if asr else "missing",
            asr.detail if asr else "ASR stage not found",
            "python gdictate.py --apply-system-action install_batch_extras" if not asr or asr.status != "ready" else "",
        )
    )
    checks.append(
        PreflightCheck(
            "file_diarization",
            "File speaker separation",
            diarization.status if diarization else "missing",
            diarization.detail if diarization else "Diarization stage not found",
            "python gdictate.py --apply-system-action install_batch_extras" if not diarization or diarization.status != "ready" else "",
        )
    )

    missing_assets = [asset for asset in install.assets if not asset.exists]
    checks.append(
        PreflightCheck(
            "user_install",
            "User startup integration",
            "ready" if install.installable and not missing_assets else "available",
            f"{len(install.assets) - len(missing_assets)}/{len(install.assets)} files installed",
            "python gdictate.py --install-user-assets" if missing_assets else "",
        )
    )

    warnings = [*caps.warnings, *diagnostics.warnings, *live.warnings, *files.warnings, *install.warnings]
    actions = [check.action for check in checks if check.action]
    actions.extend(diagnostics.actions)
    actions.extend(live.actions)
    actions.extend(files.actions)
    actions.extend(install.actions)
    return PreflightReport(caps.os, caps.desktop, checks, _unique(warnings), _unique(actions))


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
