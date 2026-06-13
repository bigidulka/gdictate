import { invoke } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import {
  Activity,
  Clipboard,
  Eye,
  EyeOff,
  FileAudio,
  Keyboard,
  Mic,
  MonitorSpeaker,
  Play,
  RotateCcw,
  Save,
  Settings,
  Square,
  TestTube2,
  Wand2
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

type AppSettings = {
  language: string;
  engine: {
    name: string;
  };
  bind: {
    mode: string;
    toggle: string;
    mic_hold: string;
    speakers_hold: string;
    linux_backend: string;
  };
  audio: {
    source: string;
    restore_default_after_start: boolean;
    linux_router: string;
    windows_speaker_input: string;
  };
  paste: {
    mode: string;
    live: boolean;
    linux_terminal_combo: string;
    windows_combo: string;
  };
  chrome: {
    channel: string;
    hidden: boolean;
    setup_required: boolean;
    profile_dir: string;
  };
  overlay: {
    enabled: boolean;
    click_through: boolean;
    show_interim: boolean;
    position: string;
  };
};

type Capabilities = {
  os: string;
  desktop: string;
  chrome: boolean;
  microphone_routing: string;
  speaker_routing: string;
  global_hotkeys: string;
  paste: string;
  overlay: string;
  warnings: string[];
};

type LiveBackendReport = {
  os: string;
  desktop: string;
  backend: string;
  supports_click_through: boolean;
  supports_interim: boolean;
  positions: string[];
  warnings: string[];
  actions: string[];
};

type OverlayStatus = {
  running: boolean;
  visible: boolean;
  position: string;
};

type DaemonStatus = {
  ok: boolean;
  version?: string;
  state: string;
  language?: string;
  engine?: string;
  audio_source?: string;
  audio_router?: string;
  windows_speaker_input?: string;
  active_source?: string;
  paste_mode?: string;
  text?: string;
};

type DaemonEvent = {
  type: string;
  state?: string;
  active_source?: string;
  channel?: string;
  text?: string;
  confidence?: number;
  job?: FileJobStatus;
};

type ShortcutCommand = {
  id: string;
  label: string;
  suggested_key: string;
  mode: string;
  command: string;
  description: string;
};

type ShortcutReport = {
  desktop: string;
  recommended_backend: string;
  hold_available: boolean;
  notes: string[];
  commands: ShortcutCommand[];
};

type AudioDevice = {
  name: string;
  kind: string;
  state: string;
  default: boolean;
  virtual: boolean;
};

type DiagnosticsReport = {
  os: string;
  desktop: string;
  chrome_path: string | null;
  paste_backend: string;
  hotkey_backend: string;
  microphone_devices: AudioDevice[];
  speaker_devices: AudioDevice[];
  speaker_capture_ready: boolean;
  warnings: string[];
  actions: string[];
  system_actions: SystemAction[];
};

type PreflightCheck = {
  id: string;
  label: string;
  status: string;
  detail: string;
  action: string;
};

type PreflightReport = {
  os: string;
  desktop: string;
  checks: PreflightCheck[];
  warnings: string[];
  actions: string[];
};

type SystemAction = {
  id: string;
  label: string;
  status: string;
  command: string;
  description: string;
  requires_admin: boolean;
  manual: boolean;
};

type SystemActionResult = {
  ok: boolean;
  action_id: string;
  status: string;
  message: string;
  command: string;
};

type NativeHotkeyReport = {
  backend: string;
  mode: string;
  registered: string[];
  warnings: string[];
};

type FilePipelineDependency = {
  name: string;
  available: boolean;
  role: string;
  install_hint: string;
  path?: string | null;
};

type MediaStream = {
  index: number;
  kind: string;
  codec: string;
  channels?: number | null;
  sample_rate?: number | null;
  language: string;
  duration?: number | null;
};

type MediaProbe = {
  path: string;
  exists: boolean;
  duration?: number | null;
  format_name: string;
  streams: MediaStream[];
  error: string;
};

type FilePipelineStage = {
  id: string;
  label: string;
  status: string;
  detail: string;
};

type FilePipelineReport = {
  dependencies: FilePipelineDependency[];
  media?: MediaProbe | null;
  stages: FilePipelineStage[];
  warnings: string[];
  actions: string[];
};

type FileTranscriptionResult = {
  ok: boolean;
  path: string;
  output_dir: string;
  text: string;
  segments: Array<{ id: number; start: number; end: number; text: string; speaker: string; confidence: number }>;
  files: Record<string, string>;
  warnings: string[];
  diarization_backend: string;
  speaker_count: number;
  error: string;
};

type FileJobStatus = {
  id: string;
  status: string;
  path: string;
  progress: number;
  message: string;
  created_at: number;
  updated_at: number;
  result?: FileTranscriptionResult | null;
  error: string;
};

const defaults: AppSettings = {
  language: "ru-RU",
  engine: {
    name: "chrome"
  },
  bind: {
    mode: "dual-hold",
    toggle: "CTRL+ALT",
    mic_hold: "ALT+LEFT",
    speakers_hold: "ALT+RIGHT",
    linux_backend: "de-shortcut+evdev"
  },
  audio: {
    source: "mic",
    restore_default_after_start: true,
    linux_router: "pipewire-pulse",
    windows_speaker_input: "auto"
  },
  paste: {
    mode: "auto",
    live: true,
    linux_terminal_combo: "ctrl-v",
    windows_combo: "ctrl-v"
  },
  chrome: {
    channel: "auto",
    hidden: true,
    setup_required: false,
    profile_dir: ""
  },
  overlay: {
    enabled: true,
    click_through: true,
    show_interim: true,
    position: "lower-center"
  }
};

const fallbackCapabilities: Capabilities = {
  os: navigator.platform || "browser",
  desktop: "web preview",
  chrome: true,
  microphone_routing: "Tauri runtime required",
  speaker_routing: "Tauri runtime required",
  global_hotkeys: "Tauri runtime required",
  paste: "Tauri runtime required",
  overlay: "Tauri runtime required",
  warnings: ["running outside Tauri"]
};

const fallbackDaemonStatus: DaemonStatus = {
  ok: false,
  state: "offline"
};

const fallbackLiveReport: LiveBackendReport = {
  os: navigator.platform || "browser",
  desktop: "web preview",
  backend: "Tauri runtime required",
  supports_click_through: false,
  supports_interim: true,
  positions: ["lower-center", "top-center", "bottom-right"],
  warnings: ["Live backend report is loaded from Python core inside Tauri."],
  actions: []
};

const fallbackOverlayStatus: OverlayStatus = {
  running: false,
  visible: false,
  position: "lower-center"
};

const fallbackShortcutReport: ShortcutReport = {
  desktop: "web preview",
  recommended_backend: "tauri-runtime-required",
  hold_available: false,
  notes: ["Shortcut commands are loaded from the Python core inside Tauri."],
  commands: [
    {
      id: "daemon",
      label: "Start daemon",
      suggested_key: "autostart",
      mode: "background",
      command: "python gdictate.py --daemon --no-ui",
      description: "Run once at login before shortcut commands."
    },
    {
      id: "toggle_mic",
      label: "Toggle mic",
      suggested_key: "ALT+LEFT",
      mode: "toggle",
      command: "python gdictate.py --toggle mic",
      description: "Press once to start/stop microphone channel."
    },
    {
      id: "toggle_speakers",
      label: "Toggle speakers",
      suggested_key: "ALT+RIGHT",
      mode: "toggle",
      command: "python gdictate.py --toggle speakers",
      description: "Press once to start/stop speaker channel."
    }
  ]
};

const fallbackDiagnostics: DiagnosticsReport = {
  os: navigator.platform || "browser",
  desktop: "web preview",
  chrome_path: null,
  paste_backend: "Tauri runtime required",
  hotkey_backend: "Tauri runtime required",
  microphone_devices: [],
  speaker_devices: [],
  speaker_capture_ready: false,
  warnings: ["Diagnostics are loaded from Python core inside Tauri."],
  actions: [],
  system_actions: [
    {
      id: "tauri_runtime",
      label: "Tauri runtime required",
      status: "runtime",
      command: "",
      description: "Open the native app to load OS diagnostics.",
      requires_admin: false,
      manual: true
    }
  ]
};

const fallbackPreflight: PreflightReport = {
  os: navigator.platform || "browser",
  desktop: "web preview",
  checks: [
    {
      id: "tauri_runtime",
      label: "Native runtime",
      status: "runtime",
      detail: "Open the native app to load preflight checks.",
      action: ""
    }
  ],
  warnings: ["Preflight is loaded from Python core inside Tauri."],
  actions: ["Open the native app for full OS checks."]
};

const fallbackNativeHotkeys: NativeHotkeyReport = {
  backend: "Tauri runtime required",
  mode: "offline",
  registered: [],
  warnings: ["Native hotkeys are registered only inside the Tauri app."]
};

const fallbackFilePipeline: FilePipelineReport = {
  dependencies: [],
  media: null,
  stages: [
    { id: "probe", label: "Media probe", status: "runtime", detail: "Tauri runtime required" },
    { id: "extract", label: "Audio extraction", status: "runtime", detail: "Tauri runtime required" },
    { id: "asr", label: "Speech recognition", status: "runtime", detail: "Tauri runtime required" },
    { id: "diarization", label: "Speaker separation", status: "runtime", detail: "Tauri runtime required" },
    { id: "export", label: "Export", status: "planned", detail: "TXT/SRT/VTT/JSON" }
  ],
  warnings: ["File pipeline report is loaded from Python core inside Tauri."],
  actions: []
};

const fallbackTranscription: FileTranscriptionResult = {
  ok: false,
  path: "",
  output_dir: "",
  text: "",
  segments: [],
  files: {},
  warnings: [],
  diarization_backend: "off",
  speaker_count: 0,
  error: ""
};

const fallbackFileJob: FileJobStatus = {
  id: "",
  status: "idle",
  path: "",
  progress: 0,
  message: "",
  created_at: 0,
  updated_at: 0,
  result: null,
  error: ""
};

async function call<T>(command: string, args?: Record<string, unknown>, fallback?: T): Promise<T> {
  const internals = (window as Window & { __TAURI_INTERNALS__?: { invoke?: unknown } }).__TAURI_INTERNALS__;
  if (typeof internals?.invoke !== "function") {
    if (fallback !== undefined) {
      return fallback;
    }
    throw new Error(`Tauri runtime unavailable: ${command}`);
  }
  try {
    return await invoke<T>(command, args);
  } catch {
    if (fallback !== undefined) {
      return fallback;
    }
    throw new Error(`Tauri command unavailable: ${command}`);
  }
}

const tabs = [
  ["app", Activity, "App"],
  ["settings", Settings, "Настройки"]
] as const;

function withSettingsDefaults(value: Partial<AppSettings>): AppSettings {
  return {
    ...defaults,
    ...value,
    engine: { ...defaults.engine, ...(value.engine || {}) },
    bind: { ...defaults.bind, ...(value.bind || {}) },
    audio: { ...defaults.audio, ...(value.audio || {}) },
    paste: { ...defaults.paste, ...(value.paste || {}) },
    chrome: { ...defaults.chrome, ...(value.chrome || {}) },
    overlay: {
      ...defaults.overlay,
      ...(value.overlay || {}),
      position: value.overlay?.position === "bottom-center" ? "lower-center" : value.overlay?.position || defaults.overlay.position
    }
  };
}

function withDiagnosticsDefaults(value: Partial<DiagnosticsReport>): DiagnosticsReport {
  return {
    ...fallbackDiagnostics,
    ...value,
    microphone_devices: value.microphone_devices || [],
    speaker_devices: value.speaker_devices || [],
    warnings: value.warnings || [],
    actions: value.actions || [],
    system_actions: value.system_actions || []
  };
}

export function App() {
  const [settings, setSettings] = useState<AppSettings>(defaults);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [daemon, setDaemon] = useState<DaemonStatus>(fallbackDaemonStatus);
  const [eventState, setEventState] = useState("offline");
  const [liveText, setLiveText] = useState("");
  const [finalText, setFinalText] = useState<string[]>([]);
  const [events, setEvents] = useState<DaemonEvent[]>([]);
  const [liveReport, setLiveReport] = useState<LiveBackendReport>(fallbackLiveReport);
  const [overlayStatus, setOverlayStatus] = useState<OverlayStatus>(fallbackOverlayStatus);
  const [shortcutReport, setShortcutReport] = useState<ShortcutReport | null>(null);
  const [nativeHotkeys, setNativeHotkeys] = useState<NativeHotkeyReport>(fallbackNativeHotkeys);
  const [diagnostics, setDiagnostics] = useState<DiagnosticsReport>(fallbackDiagnostics);
  const [preflight, setPreflight] = useState<PreflightReport>(fallbackPreflight);
  const [filePath, setFilePath] = useState("");
  const [fileOutputDir, setFileOutputDir] = useState("");
  const [fileModelSize, setFileModelSize] = useState("small");
  const [fileDevice, setFileDevice] = useState("auto");
  const [fileComputeType, setFileComputeType] = useState("default");
  const [fileDiarize, setFileDiarize] = useState(false);
  const [fileDiarizationBackend, setFileDiarizationBackend] = useState("auto");
  const [fileResult, setFileResult] = useState<FileTranscriptionResult>(fallbackTranscription);
  const [fileJobs, setFileJobs] = useState<FileJobStatus[]>([]);
  const [activeFileJob, setActiveFileJob] = useState<FileJobStatus>(fallbackFileJob);
  const [filePipeline, setFilePipeline] = useState<FilePipelineReport>(fallbackFilePipeline);
  const [tab, setTab] = useState<(typeof tabs)[number][0]>("app");
  const [status, setStatus] = useState("");
  const [isOverlayWindow, setIsOverlayWindow] = useState(new URLSearchParams(window.location.search).get("view") === "overlay");
  const settingsRef = useRef(settings);
  const savedSettingsRef = useRef(settings);

  useEffect(() => {
    settingsRef.current = settings;
  }, [settings]);

  useEffect(() => {
    try {
      setIsOverlayWindow(getCurrentWindow().label === "overlay");
    } catch {
      setIsOverlayWindow(new URLSearchParams(window.location.search).get("view") === "overlay");
    }
    void load();
    const timer = window.setInterval(() => void refreshDaemon(false), 2000);
    let closed = false;
    let socket: WebSocket | null = null;
    let retry: number | null = null;

    const connect = () => {
      if (closed) {
        return;
      }
      try {
        socket = new WebSocket("ws://127.0.0.1:9877/events");
      } catch {
        setEventState("offline");
        retry = window.setTimeout(connect, 2000);
        return;
      }
      socket.onopen = () => setEventState("live");
      socket.onmessage = (message) => {
        try {
          applyDaemonEvent(JSON.parse(message.data) as DaemonEvent);
        } catch {
          setEventState("bad-event");
        }
      };
      socket.onclose = () => {
        setEventState("offline");
        if (!closed) {
          retry = window.setTimeout(connect, 2000);
        }
      };
      socket.onerror = () => {
        setEventState("offline");
      };
    };

    connect();

    return () => {
      closed = true;
      window.clearInterval(timer);
      if (retry !== null) {
        window.clearTimeout(retry);
      }
      socket?.close();
    };
  }, []);

  async function load() {
    const stored = localStorage.getItem("gdictate.settings");
    const fallbackSettings = stored ? withSettingsDefaults(JSON.parse(stored) as Partial<AppSettings>) : defaults;
    const loaded = withSettingsDefaults(await call<AppSettings>("load_settings", undefined, fallbackSettings));
    const caps = await call<Capabilities>("capabilities", undefined, fallbackCapabilities);
    const live = await loadLiveReport();
    const overlay = await loadOverlayStatus();
    const preflightReport = await loadPreflight();
    const shortcuts = await loadShortcutReport();
    const diagnosticsReport = await loadDiagnostics();
    const native = await loadNativeHotkeys();
    const files = await loadFilePipeline("");
    const jobs = await loadFileJobs();
    setSettings(loaded);
    savedSettingsRef.current = loaded;
    setCapabilities(caps);
    setLiveReport(live);
    setOverlayStatus(overlay);
    setPreflight(preflightReport);
    setShortcutReport(shortcuts);
    setDiagnostics(diagnosticsReport);
    setNativeHotkeys(native);
    setFilePipeline(files);
    setFileJobs(jobs);
    await refreshDaemon(false);
  }

  async function loadShortcutReport() {
    try {
      const result = await call<string>("shortcut_report", undefined, JSON.stringify(fallbackShortcutReport));
      return result ? (JSON.parse(result) as ShortcutReport) : fallbackShortcutReport;
    } catch {
      return fallbackShortcutReport;
    }
  }

  async function loadPreflight() {
    try {
      const result = await call<string>("preflight", undefined, JSON.stringify(fallbackPreflight));
      return result ? (JSON.parse(result) as PreflightReport) : fallbackPreflight;
    } catch {
      return fallbackPreflight;
    }
  }

  async function loadDiagnostics() {
    try {
      const result = await call<string>("diagnostics", undefined, JSON.stringify(fallbackDiagnostics));
      return result ? withDiagnosticsDefaults(JSON.parse(result) as Partial<DiagnosticsReport>) : fallbackDiagnostics;
    } catch {
      return fallbackDiagnostics;
    }
  }

  async function loadLiveReport() {
    try {
      const result = await call<string>("live_report", undefined, JSON.stringify(fallbackLiveReport));
      return result ? (JSON.parse(result) as LiveBackendReport) : fallbackLiveReport;
    } catch {
      return fallbackLiveReport;
    }
  }

  async function loadOverlayStatus() {
    try {
      return await call<OverlayStatus>("overlay_status", undefined, fallbackOverlayStatus);
    } catch {
      return fallbackOverlayStatus;
    }
  }

  async function openLiveOverlay() {
    const result = await call<string>(
      "open_overlay",
      { clickThrough: settings.overlay.click_through, position: settings.overlay.position },
      "Tauri runtime required: open_overlay"
    );
    setStatus(result);
    setOverlayStatus(await loadOverlayStatus());
  }

  async function closeLiveOverlay() {
    const result = await call<string>("close_overlay", undefined, "Tauri runtime required: close_overlay");
    setStatus(result);
    setOverlayStatus(await loadOverlayStatus());
  }

  async function setOverlayVisible(visible: boolean) {
    const overlay = settingsRef.current.overlay;
    if (!overlay.enabled) {
      return;
    }
    if (visible) {
      await call<string>(
        "open_overlay",
        { clickThrough: overlay.click_through, position: overlay.position },
        ""
      );
    } else {
      await call<string>("close_overlay", undefined, "");
    }
    setOverlayStatus(await loadOverlayStatus());
  }

  async function copySystemAction(action: SystemAction) {
    const text = action.command || action.description;
    if (!text) {
      setStatus("no command");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
      setStatus(`copied: ${action.label}`);
    } catch {
      setStatus(text);
    }
  }

  async function applySystemAction(action: SystemAction) {
    const fallback = JSON.stringify({
      ok: false,
      action_id: action.id,
      status: "runtime",
      message: action.description || "Tauri runtime required",
      command: action.command
    });
    const result = await call<string>("apply_system_action", { actionId: action.id }, fallback);
    const parsed = JSON.parse(result) as SystemActionResult;
    setStatus(parsed.message);
    setDiagnostics(await loadDiagnostics());
  }

  async function persistSettings(next: AppSettings) {
    localStorage.setItem("gdictate.settings", JSON.stringify(next));
    const hotkeys = await call<NativeHotkeyReport | null>("save_settings", { settings: next }, null);
    if (hotkeys) {
      setNativeHotkeys(hotkeys);
    }
    savedSettingsRef.current = next;
  }

  async function applyOverlaySettings(next: AppSettings) {
    const current = await loadOverlayStatus();
    if (!next.overlay.enabled) {
      await call<string>("close_overlay", undefined, "");
    } else if (current.visible) {
      await call<string>(
        "open_overlay",
        { clickThrough: next.overlay.click_through, position: next.overlay.position },
        ""
      );
    }
    setOverlayStatus(await loadOverlayStatus());
  }

  function daemonSettingsChanged(previous: AppSettings, next: AppSettings) {
    return JSON.stringify({
      language: previous.language,
      engine: previous.engine,
      audio: previous.audio,
      paste: previous.paste,
      chrome: previous.chrome
    }) !== JSON.stringify({
      language: next.language,
      engine: next.engine,
      audio: next.audio,
      paste: next.paste,
      chrome: next.chrome
    });
  }

  async function applySettingsRuntime(previous: AppSettings, next: AppSettings) {
    await applyOverlaySettings(next);
    if (!daemonSettingsChanged(previous, next)) {
      return;
    }
    if (daemon.state === "recording" || daemon.state === "finalizing") {
      setStatus("settings saved; daemon reload after stop");
      return;
    }
    await call<string>("daemon_shutdown", undefined, "");
    await call<string>("daemon_spawn", undefined, "");
    window.setTimeout(() => void refreshDaemon(false), 800);
    setStatus("settings saved; daemon restarted");
  }

  async function save() {
    const previous = savedSettingsRef.current;
    const next = withSettingsDefaults(settings);
    const reloadsDaemon = daemonSettingsChanged(previous, next);
    await persistSettings(next);
    await applySettingsRuntime(previous, next);
    if (!reloadsDaemon) {
      setStatus("settings saved");
    }
  }

  async function resetSettings() {
    const previous = savedSettingsRef.current;
    const next = withSettingsDefaults(await call<AppSettings>("reset_settings", undefined, defaults));
    localStorage.setItem("gdictate.settings", JSON.stringify(next));
    setSettings(next);
    savedSettingsRef.current = next;
    setNativeHotkeys(await loadNativeHotkeys());
    await applySettingsRuntime(previous, next);
    setStatus("settings reset");
  }

  async function loadNativeHotkeys() {
    try {
      return await call<NativeHotkeyReport>("native_hotkeys_status", undefined, fallbackNativeHotkeys);
    } catch {
      return fallbackNativeHotkeys;
    }
  }

  async function reloadNativeHotkeys() {
    const hotkeys = await call<NativeHotkeyReport>("native_hotkeys_reload", undefined, fallbackNativeHotkeys);
    setNativeHotkeys(hotkeys);
    setStatus(`native hotkeys: ${hotkeys.registered.length}`);
  }

  async function startHotkeys() {
    const hotkeys = await call<NativeHotkeyReport>("native_hotkeys_reload", undefined, fallbackNativeHotkeys);
    setNativeHotkeys(hotkeys);
    const needsEvdev = isLinux && hotkeys.registered.length === 0 && hotkeys.warnings.some((item) => item.toLowerCase().includes("wayland"));
    if (needsEvdev) {
      await daemonCommand("evdev_hotkeys_spawn");
      return;
    }
    setStatus(`native hotkeys: ${hotkeys.registered.length}`);
  }

  async function loadFilePipeline(path: string) {
    try {
      const result = await call<string>("file_pipeline_report", { path }, JSON.stringify(fallbackFilePipeline));
      return result ? (JSON.parse(result) as FilePipelineReport) : fallbackFilePipeline;
    } catch {
      return fallbackFilePipeline;
    }
  }

  async function refreshFilePipeline() {
    const report = await loadFilePipeline(filePath);
    setFilePipeline(report);
    setStatus(`file pipeline: ${report.stages.filter((stage) => stage.status === "ready").length}/${report.stages.length}`);
  }

  async function loadFileJobs() {
    try {
      const result = await call<string>("file_jobs", undefined, JSON.stringify({ ok: false, jobs: [] }));
      const parsed = JSON.parse(result) as { jobs: FileJobStatus[] };
      return parsed.jobs || [];
    } catch {
      return [];
    }
  }

  async function refreshFileJobs() {
    const jobs = await loadFileJobs();
    setFileJobs(jobs);
    setStatus(`file jobs: ${jobs.length}`);
  }

  async function runFileTranscription() {
    const result = await call<string>(
      "file_job_start",
      {
        path: filePath,
        outputDir: fileOutputDir,
        modelSize: fileModelSize,
        device: fileDevice,
        computeType: fileComputeType,
        diarize: fileDiarize,
        diarizationBackend: fileDiarizationBackend,
        formats: ["json", "txt", "srt", "vtt"]
      },
      JSON.stringify({ ok: false, job: { ...fallbackFileJob, status: "failed", error: "Tauri runtime required", message: "Tauri runtime required" } })
    );
    const parsed = JSON.parse(result) as { ok: boolean; job: FileJobStatus; error?: string };
    if (parsed.job) {
      mergeFileJob(parsed.job);
      setActiveFileJob(parsed.job);
      setStatus(parsed.ok ? `file job started: ${parsed.job.id}` : `file job failed: ${parsed.job.error || parsed.error}`);
    }
  }

  async function cancelFileJob(jobId: string) {
    const result = await call<string>("file_job_cancel", { jobId }, JSON.stringify({ ok: false, job: { ...fallbackFileJob, id: jobId, status: "failed", error: "Tauri runtime required" } }));
    const parsed = JSON.parse(result) as { job: FileJobStatus };
    if (parsed.job) {
      mergeFileJob(parsed.job);
      setActiveFileJob(parsed.job);
    }
  }

  function mergeFileJob(job: FileJobStatus) {
    setFileJobs((current) => [job, ...current.filter((item) => item.id !== job.id)]);
    if (job.result) {
      setFileResult(job.result);
    }
  }

  async function refreshDaemon(showStatus = true) {
    try {
      const result = await call<string>("daemon_status", undefined, "");
      const parsed = result ? (JSON.parse(result) as DaemonStatus) : fallbackDaemonStatus;
      setDaemon(parsed);
      if (showStatus) {
        setStatus(`daemon ${parsed.state}`);
      }
    } catch {
      setDaemon(fallbackDaemonStatus);
      if (showStatus) {
        setStatus("daemon offline");
      }
    }
  }

  async function daemonCommand(command: string, args?: Record<string, unknown>) {
    const fallback = `Tauri runtime required: ${command}`;
    try {
      const result = await call<string>(command, args, fallback);
      setStatus(result);
    } finally {
      window.setTimeout(() => void refreshDaemon(false), 600);
    }
  }

  function applyDaemonEvent(event: DaemonEvent) {
    setEvents((current) => [event, ...current].slice(0, 20));
    if (event.type === "file.job" && event.job) {
      mergeFileJob(event.job);
      setActiveFileJob(event.job);
    }
    if (event.state) {
      setDaemon((current) => ({
        ...current,
        ok: true,
        state: event.state || current.state,
        active_source: event.active_source || event.channel || current.active_source
      }));
    }
    if (event.type === "recording.started") {
      setLiveText("");
      void setOverlayVisible(true);
    }
    if (event.type === "transcript.interim") {
      setLiveText(event.text || "");
    }
    if (event.type === "transcript.final" && event.text) {
      setLiveText("");
      setFinalText((current) => [event.text || "", ...current].slice(0, 8));
    }
    if (event.type === "recording.stopped") {
      if (event.text) {
        setFinalText((current) => [event.text || "", ...current].slice(0, 8));
      }
      setLiveText("");
      void setOverlayVisible(false);
    }
  }

  const activeTab = useMemo(() => tabs.find(([id]) => id === tab), [tab]);
  const osName = `${capabilities?.os || diagnostics.os || navigator.platform || ""}`.toLowerCase();
  const isWindows = osName.includes("win");
  const isLinux = !isWindows;

  if (isOverlayWindow) {
    return (
      <OverlayView
        channel={daemon.active_source || settings.audio.source}
        state={daemon.state}
        eventState={eventState}
        text={settings.overlay.show_interim ? liveText : ""}
      />
    );
  }

  return (
    <main className="shell">
      <aside className="sidebar">
        <div className="brand">gdictate</div>
        <div className="tabs">
          {tabs.map(([id, Icon, label]) => (
            <button key={id} className={id === tab ? "tab active" : "tab"} onClick={() => setTab(id)} title={label}>
              <Icon size={18} />
              <span>{label}</span>
            </button>
          ))}
        </div>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <h1>{activeTab?.[2]}</h1>
            <p>{capabilities ? `${capabilities.os} / ${capabilities.desktop}` : "loading"}</p>
          </div>
          <div className="actions">
            <button className="iconButton" onClick={save} title="Сохранить">
              <Save size={18} />
            </button>
            <button className="iconButton" onClick={resetSettings} title="Сбросить настройки">
              <RotateCcw size={18} />
            </button>
            <button className="iconButton" onClick={() => daemonCommand("daemon_command", { args: ["--setup", "--no-ui"] })} title="Chrome setup">
              <Wand2 size={18} />
            </button>
          </div>
        </header>

        {tab === "app" && (
          <Panel>
            <Field label="Режим по умолчанию">
              <Select value={settings.audio.source} options={["mic", "speakers", "both"]} onChange={(source) => patch({ audio: { ...settings.audio, source } })} />
            </Field>
            <div className="commandRow">
              <button onClick={() => daemonCommand("daemon_spawn")}><Play size={16} />Daemon</button>
              <button onClick={() => daemonCommand("daemon_start", { source: settings.audio.source })}><Mic size={16} />Start</button>
              <button onClick={() => daemonCommand("daemon_stop")}><Square size={16} />Stop</button>
              <button onClick={() => daemonCommand("daemon_command", { args: ["--test", "--source", settings.audio.source, "--no-ui"] })}><TestTube2 size={16} />Test 5s</button>
            </div>
            <div className="daemonStrip">
              <span>{daemon.state}</span>
              <strong>{daemon.active_source || settings.audio.source}</strong>
              <em>{daemon.audio_router || eventState}</em>
              <button onClick={() => refreshDaemon(true)}>Status</button>
              <button onClick={() => daemonCommand("daemon_shutdown")}>Shutdown</button>
            </div>
            <section className="livePanel" aria-live="polite">
              <div className="liveHeader">
                <span>Live</span>
                <strong>{daemon.active_source || settings.audio.source}</strong>
              </div>
              <p className={liveText ? "liveText active" : "liveText"}>{liveText || finalText[0] || "..."}</p>
              <div className="finalList">
                {finalText.slice(0, 3).map((text, index) => (
                  <span key={`${text}-${index}`}>{text}</span>
                ))}
              </div>
            </section>
            <section className="settingsSection">
              <h2>Файл</h2>
              <Field label="Audio/video path">
                <input value={filePath} onChange={(event) => setFilePath(event.target.value)} placeholder="/path/to/audio-or-video" />
              </Field>
              <Field label="Output dir">
                <input value={fileOutputDir} onChange={(event) => setFileOutputDir(event.target.value)} placeholder="default: source.gdictate" />
              </Field>
              <div className="settingsGrid">
                <Field label="Whisper model">
                  <Select value={fileModelSize} options={["tiny", "base", "small", "medium", "large-v3"]} onChange={setFileModelSize} />
                </Field>
                <Field label="Device">
                  <Select value={fileDevice} options={["auto", "cpu", "cuda"]} onChange={setFileDevice} />
                </Field>
                <Field label="Compute">
                  <Select value={fileComputeType} options={["default", "int8", "int8_float16", "float16", "float32"]} onChange={setFileComputeType} />
                </Field>
                <Toggle label="Diarization" checked={fileDiarize} onChange={setFileDiarize} />
                <Field label="Diarization backend">
                  <Select value={fileDiarizationBackend} options={["auto", "whisperx", "pyannote", "off"]} onChange={setFileDiarizationBackend} />
                </Field>
              </div>
              <div className="commandRow">
                <button onClick={refreshFilePipeline}><FileAudio size={16} />Probe</button>
                <button onClick={runFileTranscription}><FileAudio size={16} />Start job</button>
                <button onClick={refreshFileJobs}><Activity size={16} />Jobs</button>
                {activeFileJob.id && activeFileJob.status === "running" && (
                  <button onClick={() => cancelFileJob(activeFileJob.id)}><Square size={16} />Cancel</button>
                )}
              </div>
            </section>
            {activeFileJob.id && (
              <section className="pipelinePanel">
                <div className="shortcutMeta">
                  <span>{activeFileJob.status}</span>
                  <strong>{activeFileJob.path}</strong>
                  <em>{Math.round(activeFileJob.progress * 100)}%</em>
                </div>
                <div className="jobProgress">
                  <span style={{ width: `${Math.round(activeFileJob.progress * 100)}%` }} />
                </div>
                <p>{activeFileJob.message || activeFileJob.error || activeFileJob.id}</p>
              </section>
            )}
            <section className="pipelinePanel">
              <div className="pipelineStages">
                {filePipeline.stages.map((stage) => (
                  <div className="pipelineStage" key={stage.id}>
                    <span>{stage.label}</span>
                    <strong>{stage.status}</strong>
                    <em>{stage.detail}</em>
                  </div>
                ))}
              </div>
              {filePipeline.media && (
                <div className="mediaProbe">
                  <div className="shortcutMeta">
                    <span>{filePipeline.media.exists ? "media" : "missing"}</span>
                    <strong>{filePipeline.media.format_name || filePipeline.media.path}</strong>
                    <em>{filePipeline.media.duration ? `${filePipeline.media.duration.toFixed(1)}s` : filePipeline.media.error || "unknown"}</em>
                  </div>
                  <div className="deviceList">
                    {filePipeline.media.streams.map((stream) => (
                      <div className="deviceRow" key={`${stream.index}-${stream.kind}`}>
                        <span>{stream.kind}</span>
                        <strong>{stream.codec || `stream ${stream.index}`}</strong>
                        <em>{[stream.channels ? `${stream.channels}ch` : "", stream.sample_rate ? `${stream.sample_rate}Hz` : "", stream.language].filter(Boolean).join(" ") || "stream"}</em>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="dependencyList">
                {filePipeline.dependencies.map((dep) => (
                  <div className="dependencyRow" key={dep.name}>
                    <span>{dep.available ? "ready" : "missing"}</span>
                    <strong>{dep.name}</strong>
                    <em>{dep.role}</em>
                  </div>
                ))}
              </div>
              <NoticeList title="Warnings" items={filePipeline.warnings} />
              <NoticeList title="Actions" items={filePipeline.actions} />
            </section>
            {(fileResult.error || fileResult.ok) && (
              <section className="pipelinePanel">
                <div className="shortcutMeta">
                  <span>{fileResult.ok ? "done" : "failed"}</span>
                  <strong>{fileResult.output_dir || fileResult.path || "file transcription"}</strong>
                  <em>
                    {fileResult.ok
                      ? `${fileResult.segments.length} segments / ${fileResult.speaker_count} speakers / ${fileResult.diarization_backend}`
                      : fileResult.error}
                  </em>
                </div>
                <div className="dependencyList">
                  {Object.entries(fileResult.files).map(([format, path]) => (
                    <div className="dependencyRow" key={format}>
                      <span>{format}</span>
                      <strong>{path}</strong>
                      <em>export</em>
                    </div>
                  ))}
                </div>
                <NoticeList title="Warnings" items={fileResult.warnings} />
              </section>
            )}
            {fileJobs.length > 0 && (
              <section className="pipelinePanel">
                <div className="dependencyList">
                  {fileJobs.slice(0, 8).map((job) => (
                    <div className="dependencyRow" key={job.id}>
                      <span>{job.status}</span>
                      <strong>{job.path}</strong>
                      <em>{job.message || job.error || job.id}</em>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </Panel>
        )}

        {tab === "settings" && (
          <Panel>
            <section className="settingsSection">
              <h2>Основное</h2>
              <Field label="Язык">
                <input value={settings.language} onChange={(event) => patch({ language: event.target.value })} />
              </Field>
              <Field label="Engine">
                <Select value={settings.engine.name} options={["chrome"]} onChange={(name) => patch({ engine: { ...settings.engine, name } })} />
              </Field>
              <Field label="Режим по умолчанию">
                <Select value={settings.audio.source} options={["mic", "speakers", "both"]} onChange={(source) => patch({ audio: { ...settings.audio, source } })} />
              </Field>
            </section>

            <section className="settingsSection">
              <h2>Chrome</h2>
              <div className="settingsGrid">
                <Field label="Channel">
                  <Select value={settings.chrome.channel} options={["auto", "stable", "beta", "dev", "chromium", "edge"]} onChange={(channel) => patch({ chrome: { ...settings.chrome, channel } })} />
                </Field>
                <Toggle label="Hidden window" checked={settings.chrome.hidden} onChange={(hidden) => patch({ chrome: { ...settings.chrome, hidden } })} />
                <Toggle label="Force setup" checked={settings.chrome.setup_required} onChange={(setup_required) => patch({ chrome: { ...settings.chrome, setup_required } })} />
              </div>
              <Field label="Profile directory">
                <input value={settings.chrome.profile_dir} onChange={(event) => patch({ chrome: { ...settings.chrome, profile_dir: event.target.value } })} placeholder="default app cache profile" />
              </Field>
            </section>

            <section className="settingsSection">
              <h2>Аудио</h2>
              {isLinux && (
                <Field label="Linux router">
                  <Select value={settings.audio.linux_router} options={["pipewire-pulse", "pulse", "manual"]} onChange={(linux_router) => patch({ audio: { ...settings.audio, linux_router } })} />
                </Field>
              )}
              {isWindows && (
                <Field label="Speakers input">
                  <Select value={settings.audio.windows_speaker_input} options={["auto", "stereo-mix", "vb-cable", "manual"]} onChange={(windows_speaker_input) => patch({ audio: { ...settings.audio, windows_speaker_input } })} />
                </Field>
              )}
              <Toggle label="Restore input after start" checked={settings.audio.restore_default_after_start} onChange={(restore_default_after_start) => patch({ audio: { ...settings.audio, restore_default_after_start } })} />
            </section>

            <section className="settingsSection">
              <h2>Бинды и вставка</h2>
              <div className="settingsGrid">
                <Field label="Bind mode">
                  <Select value={settings.bind.mode} options={["dual-hold", "toggle", "enter"]} onChange={(mode) => patch({ bind: { ...settings.bind, mode } })} />
                </Field>
                <Field label="Toggle">
                  <input value={settings.bind.toggle} onChange={(event) => patch({ bind: { ...settings.bind, toggle: event.target.value } })} />
                </Field>
                <Field label="Mic hold">
                  <input value={settings.bind.mic_hold} onChange={(event) => patch({ bind: { ...settings.bind, mic_hold: event.target.value } })} />
                </Field>
                <Field label="Speakers hold">
                  <input value={settings.bind.speakers_hold} onChange={(event) => patch({ bind: { ...settings.bind, speakers_hold: event.target.value } })} />
                </Field>
                {isLinux && (
                  <Field label="Linux backend">
                    <Select value={settings.bind.linux_backend} options={["de-shortcut+evdev", "de-shortcut", "evdev", "terminal"]} onChange={(linux_backend) => patch({ bind: { ...settings.bind, linux_backend } })} />
                  </Field>
                )}
                <Field label="Paste backend">
                  <Select value={settings.paste.mode} options={["auto", "ydotool", "wtype", "none"]} onChange={(mode) => patch({ paste: { ...settings.paste, mode } })} />
                </Field>
                <Toggle label="Live paste" checked={settings.paste.live} onChange={(live) => patch({ paste: { ...settings.paste, live } })} />
                {isLinux && (
                  <Field label="Linux paste key">
                    <Select value={settings.paste.linux_terminal_combo} options={["ctrl-shift-v", "ctrl-v"]} onChange={(linux_terminal_combo) => patch({ paste: { ...settings.paste, linux_terminal_combo } })} />
                  </Field>
                )}
                {isWindows && (
                  <Field label="Paste combo">
                    <Select value={settings.paste.windows_combo} options={["ctrl-v"]} onChange={(windows_combo) => patch({ paste: { ...settings.paste, windows_combo } })} />
                  </Field>
                )}
              </div>
            </section>

            <section className="settingsSection">
              <h2>Live</h2>
              <div className="settingsGrid">
                <Toggle label="Live popup" checked={settings.overlay.enabled} onChange={(enabled) => patchOverlay({ ...settings.overlay, enabled })} />
                <Toggle label="Click-through" checked={settings.overlay.click_through} onChange={(click_through) => patchOverlay({ ...settings.overlay, click_through })} />
                <Toggle label="Interim text" checked={settings.overlay.show_interim} onChange={(show_interim) => patchOverlay({ ...settings.overlay, show_interim })} />
                <Field label="Position">
                  <Select value={settings.overlay.position} options={["lower-center", "top-center", "bottom-right"]} onChange={(position) => patchOverlay({ ...settings.overlay, position })} />
                </Field>
              </div>
              <div className="commandRow">
                <button onClick={openLiveOverlay}><Eye size={16} />Open popup</button>
                <button onClick={closeLiveOverlay}><EyeOff size={16} />Hide popup</button>
              </div>
            </section>

            <section className="shortcutPanel">
              <div className="shortcutMeta">
                <span>native hotkeys</span>
                <strong>{nativeHotkeys.backend}</strong>
                <em>{nativeHotkeys.mode}</em>
              </div>
              <div className="commandRow compact">
                <button onClick={startHotkeys}><Keyboard size={16} />Start binds</button>
                <button onClick={reloadNativeHotkeys}><RotateCcw size={16} />Reload native</button>
                {isLinux && (
                  <>
                    <button onClick={() => daemonCommand("evdev_hotkeys_spawn")}><Keyboard size={16} />Start evdev binds</button>
                    <button onClick={() => daemonCommand("evdev_hotkeys_stop")}><Square size={16} />Stop evdev binds</button>
                  </>
                )}
              </div>
              <NoticeList title="Warnings" items={nativeHotkeys.warnings} />
              {shortcutReport && (
                <div className="shortcutList">
                  {shortcutReport.commands.map((item) => (
                    <div className="shortcutRow" key={item.id}>
                      <span>{item.label}</span>
                      <strong>{item.suggested_key}</strong>
                      <em>{item.mode}</em>
                      <code>{item.command}</code>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="diagnosticsPanel">
              <div className="diagnosticsGrid">
                <div>
                  <span>Chrome</span>
                  <strong>{diagnostics.chrome_path || "missing"}</strong>
                </div>
                <div>
                  <span>Paste</span>
                  <strong>{diagnostics.paste_backend}</strong>
                </div>
                <div>
                  <span>Hotkeys</span>
                  <strong>{diagnostics.hotkey_backend}</strong>
                </div>
                <div>
                  <span>Speaker capture</span>
                  <strong>{diagnostics.speaker_capture_ready ? "ready" : "not ready"}</strong>
                </div>
              </div>
              <DeviceList title="Inputs" devices={diagnostics.microphone_devices} />
              <DeviceList title="Outputs" devices={diagnostics.speaker_devices} />
              <NoticeList title="Warnings" items={diagnostics.warnings} />
              <NoticeList title="Actions" items={diagnostics.actions} />
              <div className="dependencyList">
                {(diagnostics.system_actions || []).map((action) => (
                  <div className="dependencyRow actionRow" key={action.id}>
                    <span>{action.status}</span>
                    <strong>{action.label}</strong>
                    <em>{action.command || action.description || (action.manual ? "manual" : "check")}</em>
                    <div className="rowActions">
                      {(action.command || action.description) && (
                        <button onClick={() => copySystemAction(action)} title="Copy command">
                          <Clipboard size={14} />Copy
                        </button>
                      )}
                      <button onClick={() => applySystemAction(action)} title={action.manual || action.requires_admin ? "Show required manual action" : "Apply action"}>
                        <Play size={14} />Apply
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </section>
            <div className="commandRow">
              <button onClick={load}><Activity size={16} />Refresh</button>
              <button onClick={async () => setPreflight(await loadPreflight())}><Activity size={16} />Preflight</button>
              <button onClick={() => daemonCommand("daemon_command", { args: ["--capabilities"] })}><MonitorSpeaker size={16} />CLI report</button>
              <button onClick={() => daemonCommand("daemon_command", { args: ["--diagnostics"] })}><MonitorSpeaker size={16} />Diagnostics</button>
            </div>
          </Panel>
        )}

        <footer className="status">{status}</footer>
      </section>
    </main>
  );

  function patch(next: Partial<AppSettings>) {
    setSettings((current) => ({ ...current, ...next }));
  }

  function patchOverlay(overlay: AppSettings["overlay"]) {
    const next = withSettingsDefaults({ ...settingsRef.current, overlay });
    setSettings(next);
    void persistSettings(next)
      .then(() => applyOverlaySettings(next))
      .then(() => setStatus(next.overlay.enabled ? "live popup settings applied" : "live popup disabled"))
      .catch((error) => setStatus(String(error)));
  }
}

function Panel({ children }: { children: React.ReactNode }) {
  return <div className="panel">{children}</div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
    </label>
  );
}

function OverlayView({ channel, state, eventState, text }: { channel: string; state: string; eventState: string; text: string }) {
  return (
    <main className="overlayShell" aria-label={`${state} ${channel} ${eventState}`}>
      <span className="overlayDot" />
      <p className={text ? "overlayText active" : "overlayText"}>{text || "..."}</p>
    </main>
  );
}

function Select({ value, options, onChange }: { value: string; options: string[]; onChange: (value: string) => void }) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  );
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (value: boolean) => void }) {
  return (
    <label className="toggle">
      <span>{label}</span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
    </label>
  );
}

function DeviceList({ title, devices }: { title: string; devices: AudioDevice[] }) {
  return (
    <div className="deviceList">
      <h2>{title}</h2>
      {devices.length === 0 ? (
        <span className="emptyText">none</span>
      ) : (
        devices.map((device) => (
          <div key={`${device.kind}-${device.name}`} className="deviceRow">
            <span>{device.default ? "default" : device.kind}</span>
            <strong>{device.name}</strong>
            <em>{device.virtual ? "virtual" : device.state || "device"}</em>
          </div>
        ))
      )}
    </div>
  );
}

function NoticeList({ title, items }: { title: string; items: string[] }) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div className="noticeList">
      <h2>{title}</h2>
      {items.map((item, index) => (
        <span key={`${item}-${index}`}>{item}</span>
      ))}
    </div>
  );
}
