mod settings;
mod system;

use serde::Serialize;
use settings::{load_settings_file, save_settings_file, AppSettings};
use std::collections::HashSet;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};
use system::Capabilities;
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{
    App, AppHandle, Manager, PhysicalPosition, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
    WindowEvent,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};

#[derive(Default)]
struct NativeHotkeys {
    registered: Vec<String>,
    mode: String,
    warnings: Vec<String>,
}

#[derive(Default)]
struct EvdevHotkeys {
    child: Option<Child>,
}

#[derive(Clone)]
struct DaemonSupervisor {
    enabled: Arc<AtomicBool>,
}

impl Default for DaemonSupervisor {
    fn default() -> Self {
        Self {
            enabled: Arc::new(AtomicBool::new(true)),
        }
    }
}

impl Drop for EvdevHotkeys {
    fn drop(&mut self) {
        if let Some(child) = self.child.as_mut() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

#[derive(Debug, Clone, Serialize)]
struct NativeHotkeyReport {
    backend: String,
    mode: String,
    registered: Vec<String>,
    warnings: Vec<String>,
}

#[derive(Debug, Clone, Serialize)]
struct OverlayStatus {
    running: bool,
    visible: bool,
    position: String,
}

#[derive(Clone)]
enum NativeHotkeyAction {
    Hold(String),
    Toggle(String),
}

#[tauri::command]
fn load_settings() -> Result<AppSettings, String> {
    load_settings_file()
}

#[tauri::command]
fn save_settings(app: AppHandle, settings: AppSettings) -> Result<NativeHotkeyReport, String> {
    save_settings_file(&settings)?;
    Ok(sync_hotkey_backend(&app, &settings))
}

#[tauri::command]
fn default_settings() -> AppSettings {
    AppSettings::default()
}

#[tauri::command]
fn settings_schema() -> Result<String, String> {
    run_python(&["--settings-schema"])
}

#[tauri::command]
fn reset_settings(app: AppHandle) -> Result<AppSettings, String> {
    let settings = AppSettings::default();
    save_settings_file(&settings)?;
    let _ = sync_hotkey_backend(&app, &settings);
    Ok(settings)
}

#[tauri::command]
fn capabilities() -> Result<Capabilities, String> {
    let output = run_python(&["--capabilities"])?;
    serde_json::from_str(&output).map_err(|err| err.to_string())
}

#[tauri::command]
fn diagnostics() -> Result<String, String> {
    run_python(&["--diagnostics"])
}

#[tauri::command]
fn preflight() -> Result<String, String> {
    run_python(&["--preflight"])
}

#[tauri::command]
fn live_report() -> Result<String, String> {
    run_python(&["--live-report"])
}

#[tauri::command]
fn apply_system_action(action_id: String) -> Result<String, String> {
    run_python_json_result(&["--apply-system-action", &action_id])
}

#[tauri::command]
fn shortcut_report() -> Result<String, String> {
    run_python(&["--shortcut-report"])
}

#[tauri::command]
fn file_pipeline_report(path: Option<String>) -> Result<String, String> {
    match path {
        Some(path) if !path.trim().is_empty() => run_python(&["--file-report", &path]),
        _ => run_python(&["--file-report"]),
    }
}

#[tauri::command]
fn transcribe_file(
    path: String,
    output_dir: Option<String>,
    model_size: String,
    device: String,
    compute_type: String,
    diarize: bool,
    diarization_backend: String,
    formats: Vec<String>,
) -> Result<String, String> {
    let mut args = vec![
        "--transcribe-file".to_string(),
        path,
        "--model-size".to_string(),
        model_size,
        "--device".to_string(),
        device,
        "--compute-type".to_string(),
        compute_type,
    ];
    if let Some(output_dir) = output_dir {
        if !output_dir.trim().is_empty() {
            args.push("--output-dir".into());
            args.push(output_dir);
        }
    }
    if diarize {
        args.push("--diarize".into());
        args.push("--diarization-backend".into());
        args.push(diarization_backend);
    }
    for format in formats {
        args.push("--export-format".into());
        args.push(format);
    }
    let borrowed: Vec<&str> = args.iter().map(String::as_str).collect();
    run_python_json_result(&borrowed)
}

#[tauri::command]
fn file_job_start(
    path: String,
    output_dir: Option<String>,
    model_size: String,
    device: String,
    compute_type: String,
    diarize: bool,
    diarization_backend: String,
    formats: Vec<String>,
) -> Result<String, String> {
    ensure_daemon_ready()?;
    let mut args = vec![
        "--file-start".to_string(),
        path,
        "--model-size".to_string(),
        model_size,
        "--device".to_string(),
        device,
        "--compute-type".to_string(),
        compute_type,
    ];
    if let Some(output_dir) = output_dir {
        if !output_dir.trim().is_empty() {
            args.push("--output-dir".into());
            args.push(output_dir);
        }
    }
    if diarize {
        args.push("--diarize".into());
        args.push("--diarization-backend".into());
        args.push(diarization_backend);
    }
    for format in formats {
        args.push("--export-format".into());
        args.push(format);
    }
    let borrowed: Vec<&str> = args.iter().map(String::as_str).collect();
    run_python_json_result(&borrowed)
}

#[tauri::command]
fn file_jobs() -> Result<String, String> {
    run_python_json_result(&["--file-jobs"])
}

#[tauri::command]
fn file_job_status(job_id: String) -> Result<String, String> {
    run_python_json_result(&["--file-job", &job_id])
}

#[tauri::command]
fn file_job_cancel(job_id: String) -> Result<String, String> {
    run_python_json_result(&["--file-cancel", &job_id])
}

#[tauri::command]
fn daemon_command(args: Vec<String>) -> Result<String, String> {
    let borrowed: Vec<&str> = args.iter().map(String::as_str).collect();
    run_python(&borrowed)
}

#[tauri::command]
fn daemon_spawn(app: AppHandle) -> Result<String, String> {
    set_daemon_supervisor(&app, true);
    match run_python(&["--status"]) {
        Ok(_) => return Ok("daemon already running".into()),
        Err(_) => {}
    }

    spawn_daemon_process()?;
    Ok("daemon starting".into())
}

#[tauri::command]
fn daemon_status() -> Result<String, String> {
    run_python(&["--status"])
}

#[tauri::command]
fn daemon_start(app: AppHandle, source: String) -> Result<String, String> {
    set_daemon_supervisor(&app, true);
    ensure_daemon_ready()?;
    run_python(&["--start", &source])
}

#[tauri::command]
fn daemon_stop() -> Result<String, String> {
    run_python(&["--stop"])
}

#[tauri::command]
fn daemon_toggle(app: AppHandle, source: Option<String>) -> Result<String, String> {
    set_daemon_supervisor(&app, true);
    ensure_daemon_ready()?;
    match source {
        Some(source) => run_python(&["--toggle", &source]),
        None => run_python(&["--toggle"]),
    }
}

#[tauri::command]
fn daemon_shutdown(app: AppHandle) -> Result<String, String> {
    set_daemon_supervisor(&app, false);
    run_python(&["--shutdown"])
}

#[tauri::command]
fn evdev_hotkeys_spawn(app: AppHandle) -> Result<String, String> {
    spawn_evdev_hotkeys(&app)
}

fn spawn_evdev_hotkeys(app: &AppHandle) -> Result<String, String> {
    ensure_daemon_ready()?;
    let state = app.state::<Mutex<EvdevHotkeys>>();
    let mut state = state.lock().unwrap();
    if let Some(child) = state.child.as_mut() {
        if child.try_wait().map_err(|err| err.to_string())?.is_none() {
            return Ok("evdev bind listener already running".into());
        }
    }
    let python = python_exe();
    let script = python_script();
    let mut child = Command::new(&python)
        .arg(&script)
        .arg("--daemon-hotkeys")
        .arg("--parent-pid")
        .arg(std::process::id().to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|err| err.to_string())?;
    thread::sleep(Duration::from_millis(150));
    if let Some(status) = child.try_wait().map_err(|err| err.to_string())? {
        return Err(format!(
            "evdev listener exited immediately with {status}; python={}; script={}",
            python.display(),
            script.display()
        ));
    }
    state.child = Some(child);
    Ok("evdev bind listener started".into())
}

#[tauri::command]
fn evdev_hotkeys_stop(app: AppHandle) -> Result<String, String> {
    stop_evdev_hotkeys(&app)
}

fn stop_evdev_hotkeys(app: &AppHandle) -> Result<String, String> {
    let state = app.state::<Mutex<EvdevHotkeys>>();
    let mut state = state.lock().unwrap();
    if let Some(child) = state.child.as_mut() {
        let _ = child.kill();
        let _ = child.wait();
        state.child = None;
        return Ok("evdev bind listener stopped".into());
    }
    Ok("evdev bind listener not running".into())
}

#[tauri::command]
fn open_overlay(
    app: AppHandle,
    click_through: Option<bool>,
    position: Option<String>,
) -> Result<String, String> {
    let settings = load_settings_file().unwrap_or_default();
    let placement = position.unwrap_or(settings.overlay.position);
    show_overlay(
        &app,
        click_through.unwrap_or(settings.overlay.click_through),
        &placement,
    )
}

#[tauri::command]
fn close_overlay(app: AppHandle) -> Result<String, String> {
    hide_overlay(&app)
}

#[tauri::command]
fn overlay_status(app: AppHandle) -> Result<OverlayStatus, String> {
    let settings = load_settings_file().unwrap_or_default();
    if let Some(window) = app.get_webview_window("overlay") {
        return Ok(OverlayStatus {
            running: true,
            visible: window.is_visible().unwrap_or(false),
            position: settings.overlay.position,
        });
    }
    Ok(OverlayStatus {
        running: false,
        visible: false,
        position: settings.overlay.position,
    })
}

#[tauri::command]
fn native_hotkeys_reload(app: AppHandle) -> Result<NativeHotkeyReport, String> {
    let settings = load_settings_file()?;
    Ok(sync_hotkey_backend(&app, &settings))
}

#[tauri::command]
fn native_hotkeys_status(app: AppHandle) -> Result<NativeHotkeyReport, String> {
    Ok(native_hotkey_report(&app))
}

fn show_main_window(app: &AppHandle) -> Result<String, String> {
    if let Some(window) = app.get_webview_window("main") {
        window.show().map_err(|err| err.to_string())?;
        window.unminimize().ok();
        window.set_focus().ok();
        return Ok("main shown".into());
    }
    Ok("main window not found".into())
}

fn hide_main_window(app: &AppHandle) -> Result<String, String> {
    if let Some(window) = app.get_webview_window("main") {
        window.hide().map_err(|err| err.to_string())?;
        return Ok("main hidden".into());
    }
    Ok("main window not found".into())
}

fn show_overlay(app: &AppHandle, click_through: bool, placement: &str) -> Result<String, String> {
    if let Some(window) = app.get_webview_window("overlay") {
        window.show().map_err(|err| err.to_string())?;
        window
            .set_ignore_cursor_events(click_through)
            .map_err(|err| err.to_string())?;
        apply_overlay_position(&window, placement)?;
        return Ok("overlay shown".into());
    }

    let window = WebviewWindowBuilder::new(app, "overlay", WebviewUrl::App("index.html".into()))
        .title("gdictate live")
        .inner_size(430.0, 54.0)
        .min_inner_size(292.0, 54.0)
        .position(0.0, 0.0)
        .decorations(false)
        .transparent(true)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .build()
        .map_err(|err| err.to_string())?;

    window
        .set_ignore_cursor_events(click_through)
        .map_err(|err| err.to_string())?;
    apply_overlay_position(&window, placement)?;
    Ok("overlay opened".into())
}

fn apply_overlay_position(window: &WebviewWindow, placement: &str) -> Result<(), String> {
    let monitor = window
        .current_monitor()
        .map_err(|err| err.to_string())?
        .or(window.primary_monitor().map_err(|err| err.to_string())?);
    let Some(monitor) = monitor else {
        return Ok(());
    };
    let work_area = monitor.work_area();
    let size = window.outer_size().map_err(|err| err.to_string())?;
    let margin = 32_i32;
    let lower_margin = 150_i32;
    let work_x = work_area.position.x;
    let work_y = work_area.position.y;
    let work_w = work_area.size.width as i32;
    let work_h = work_area.size.height as i32;
    let width = size.width as i32;
    let height = size.height as i32;

    let x = match placement {
        "bottom-right" => work_x + work_w - width - margin,
        _ => work_x + ((work_w - width) / 2),
    };
    let y = match placement {
        "top-center" => work_y + 90,
        "bottom-right" => work_y + work_h - height - lower_margin,
        _ => work_y + work_h - height - lower_margin,
    };
    window
        .set_position(PhysicalPosition::new(x.max(work_x), y.max(work_y)))
        .map_err(|err| err.to_string())
}

fn hide_overlay(app: &AppHandle) -> Result<String, String> {
    if let Some(window) = app.get_webview_window("overlay") {
        window.hide().map_err(|err| err.to_string())?;
        return Ok("overlay hidden".into());
    }
    Ok("overlay not running".into())
}

fn setup_tray(app: &mut App) -> tauri::Result<()> {
    let show = MenuItem::with_id(app, "show", "Show settings", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, "hide", "Hide settings", true, None::<&str>)?;
    let overlay = MenuItem::with_id(app, "overlay", "Open live popup", true, None::<&str>)?;
    let hide_overlay_item =
        MenuItem::with_id(app, "hide_overlay", "Hide live popup", true, None::<&str>)?;
    let start_mic = MenuItem::with_id(app, "start_mic", "Start mic", true, None::<&str>)?;
    let start_speakers =
        MenuItem::with_id(app, "start_speakers", "Start speakers", true, None::<&str>)?;
    let stop = MenuItem::with_id(app, "stop", "Stop recording", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let sep1 = PredefinedMenuItem::separator(app)?;
    let sep2 = PredefinedMenuItem::separator(app)?;
    let sep3 = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(
        app,
        &[
            &show,
            &hide,
            &sep1,
            &overlay,
            &hide_overlay_item,
            &sep2,
            &start_mic,
            &start_speakers,
            &stop,
            &sep3,
            &quit,
        ],
    )?;

    let icon = Image::from_bytes(include_bytes!("../icons/icon.png"))?;

    TrayIconBuilder::with_id("main")
        .menu(&menu)
        .icon(icon)
        .tooltip("gdictate")
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => {
                let _ = show_main_window(app);
            }
            "hide" => {
                let _ = hide_main_window(app);
            }
            "overlay" => {
                let settings = load_settings_file().unwrap_or_default();
                let _ = show_overlay(app, true, &settings.overlay.position);
            }
            "hide_overlay" => {
                let _ = hide_overlay(app);
            }
            "start_mic" => {
                let _ = run_python(&["--start", "mic"]);
            }
            "start_speakers" => {
                let _ = run_python(&["--start", "speakers"]);
            }
            "stop" => {
                let _ = run_python(&["--stop"]);
            }
            "quit" => {
                let _ = run_python(&["--shutdown"]);
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                let _ = show_main_window(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

fn native_hotkey_report(app: &AppHandle) -> NativeHotkeyReport {
    let state = app.state::<Mutex<NativeHotkeys>>();
    let state = state.lock().unwrap();
    NativeHotkeyReport {
        backend: "tauri-plugin-global-shortcut".into(),
        mode: state.mode.clone(),
        registered: state.registered.clone(),
        warnings: state.warnings.clone(),
    }
}

fn is_linux_wayland() -> bool {
    cfg!(target_os = "linux")
        && std::env::var("XDG_SESSION_TYPE")
            .unwrap_or_default()
            .eq_ignore_ascii_case("wayland")
}

fn should_use_evdev_hotkeys(settings: &AppSettings) -> bool {
    is_linux_wayland()
        && settings.bind.mode == "dual-hold"
        && matches!(
            settings.bind.linux_backend.as_str(),
            "de-shortcut+evdev" | "evdev"
        )
}

fn sync_hotkey_backend(app: &AppHandle, settings: &AppSettings) -> NativeHotkeyReport {
    let mut report = register_native_hotkeys(app, settings);
    if should_use_evdev_hotkeys(settings) {
        match spawn_evdev_hotkeys(app) {
            Ok(message) => report.warnings.push(message),
            Err(err) => {
                eprintln!("[HOTKEY] evdev bind listener failed: {err}");
                report
                    .warnings
                    .push(format!("evdev bind listener failed: {err}"));
            }
        }
    } else {
        if let Err(err) = stop_evdev_hotkeys(app) {
            eprintln!("[HOTKEY] evdev bind listener stop failed: {err}");
        }
    }
    report
}

fn register_native_hotkeys(app: &AppHandle, settings: &AppSettings) -> NativeHotkeyReport {
    let global = app.global_shortcut();
    let hotkey_state = app.state::<Mutex<NativeHotkeys>>();
    let mut state = hotkey_state.lock().unwrap();

    for shortcut in state.registered.drain(..) {
        let _ = global.unregister(shortcut.as_str());
    }

    state.mode = settings.bind.mode.clone();
    state.warnings.clear();

    let mut desired: Vec<(String, NativeHotkeyAction, &'static str)> = Vec::new();
    match settings.bind.mode.as_str() {
        "dual-hold" => {
            desired.push((
                settings.bind.mic_hold.clone(),
                NativeHotkeyAction::Hold("mic".into()),
                "mic hold",
            ));
            desired.push((
                settings.bind.speakers_hold.clone(),
                NativeHotkeyAction::Hold("speakers".into()),
                "speakers hold",
            ));
        }
        "toggle" => {
            desired.push((
                settings.bind.toggle.clone(),
                NativeHotkeyAction::Toggle(settings.audio.source.clone()),
                "toggle",
            ));
        }
        "enter" => {
            state
                .warnings
                .push("native hotkeys disabled for enter mode".into());
        }
        other => {
            state.warnings.push(format!(
                "unknown bind mode: {other}; native hotkeys disabled"
            ));
        }
    }

    if is_linux_wayland() {
        state.warnings.push(
            "Native global shortcuts are X11-only on Linux here; evdev binds are used on Wayland."
                .into(),
        );
        desired.clear();
    }

    let mut seen = HashSet::new();
    for (shortcut, action, label) in desired {
        let shortcut = shortcut.trim().to_string();
        if shortcut.is_empty() {
            state.warnings.push(format!("{label}: empty shortcut"));
            continue;
        }
        if !seen.insert(shortcut.to_uppercase()) {
            state
                .warnings
                .push(format!("{label}: duplicate shortcut {shortcut}"));
            continue;
        }

        let action_for_handler = action.clone();
        match global.on_shortcut(shortcut.as_str(), move |_app, _shortcut, event| {
            handle_native_hotkey_event(action_for_handler.clone(), event.state);
        }) {
            Ok(()) => state.registered.push(shortcut),
            Err(err) => state
                .warnings
                .push(format!("{label}: failed to register {shortcut}: {err}")),
        }
    }

    NativeHotkeyReport {
        backend: "tauri-plugin-global-shortcut".into(),
        mode: state.mode.clone(),
        registered: state.registered.clone(),
        warnings: state.warnings.clone(),
    }
}

fn handle_native_hotkey_event(action: NativeHotkeyAction, key_state: ShortcutState) {
    match (action, key_state) {
        (NativeHotkeyAction::Hold(source), ShortcutState::Pressed) => {
            spawn_daemon_command(vec!["--start".into(), source], true);
        }
        (NativeHotkeyAction::Hold(_), ShortcutState::Released) => {
            spawn_daemon_command(vec!["--stop".into()], false);
        }
        (NativeHotkeyAction::Toggle(source), ShortcutState::Pressed) => {
            spawn_daemon_command(vec!["--toggle".into(), source], true);
        }
        _ => {}
    }
}

fn spawn_daemon_command(args: Vec<String>, ensure_daemon: bool) {
    thread::spawn(move || {
        if ensure_daemon {
            let _ = ensure_daemon_ready();
        }
        let borrowed: Vec<&str> = args.iter().map(String::as_str).collect();
        let _ = run_python(&borrowed);
    });
}

fn set_daemon_supervisor(app: &AppHandle, enabled: bool) {
    let supervisor = app.state::<DaemonSupervisor>();
    supervisor.enabled.store(enabled, Ordering::Relaxed);
}

fn spawn_daemon_supervisor(app: &AppHandle) {
    let supervisor = app.state::<DaemonSupervisor>().inner().clone();
    thread::spawn(move || {
        let mut first_check = true;
        while supervisor.enabled.load(Ordering::Relaxed) {
            if run_python(&["--status"]).is_err() {
                let _ = spawn_daemon_process();
                let wait_ms = if first_check { 2200 } else { 900 };
                thread::sleep(Duration::from_millis(wait_ms));
            }
            first_check = false;
            thread::sleep(Duration::from_secs(5));
        }
    });
}

fn spawn_daemon_process() -> Result<(), String> {
    let mut command = Command::new(python_exe());
    configure_python_command(&mut command);
    command
        .arg(python_script())
        .arg("--daemon")
        .arg("--no-ui")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|err| err.to_string())?;
    Ok(())
}

fn ensure_daemon_ready() -> Result<(), String> {
    if run_python(&["--status"]).is_ok() {
        return Ok(());
    }

    spawn_daemon_process()?;
    let deadline = Instant::now() + Duration::from_secs(15);
    while Instant::now() < deadline {
        if run_python(&["--status"]).is_ok() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(100));
    }
    Err("daemon did not become ready".into())
}

fn project_dir() -> std::path::PathBuf {
    if let Some(path) = std::env::var_os("GDICTATE_PROJECT_DIR") {
        let path = std::path::PathBuf::from(path);
        if path.join("gdictate.py").exists() {
            return path;
        }
    }

    let from_exe = std::env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(|p| p.to_path_buf()))
        .and_then(|path| path.parent().map(|p| p.to_path_buf()))
        .and_then(|path| path.parent().map(|p| p.to_path_buf()));

    if let Some(path) = from_exe {
        if path.join("gdictate.py").exists() {
            return path;
        }
        if path.ends_with("src-tauri") && path.join("../gdictate.py").exists() {
            return path.join("..");
        }
    }

    let cwd = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
    if cwd.join("gdictate.py").exists() {
        return cwd;
    }
    if cwd.join("../gdictate.py").exists() {
        return cwd.join("..");
    }
    cwd
}

fn configure_python_resources(app: &AppHandle) {
    if let Ok(cwd) = std::env::current_dir() {
        for ancestor in cwd.ancestors().take(8) {
            if ancestor.join("gdictate.py").exists() && ancestor.join("src-tauri").exists() {
                std::env::set_var("GDICTATE_PROJECT_DIR", ancestor);
                return;
            }
        }
    }

    let Ok(resource_dir) = app.path().resource_dir() else {
        return;
    };
    if resource_dir.join("python").join("gdictate.py").exists() {
        std::env::set_var("GDICTATE_PROJECT_DIR", resource_dir.join("python"));
    } else if resource_dir.join("gdictate.py").exists() {
        std::env::set_var("GDICTATE_PROJECT_DIR", resource_dir);
    } else if resource_dir.join("_up_").join("gdictate.py").exists() {
        std::env::set_var("GDICTATE_PROJECT_DIR", resource_dir.join("_up_"));
    }
}

fn python_exe() -> std::path::PathBuf {
    let project_dir = project_dir();

    let mut candidates = Vec::new();
    for ancestor in project_dir.ancestors().take(8) {
        candidates.push(ancestor.to_path_buf());
    }
    if let Ok(cwd) = std::env::current_dir() {
        for ancestor in cwd.ancestors().take(8) {
            candidates.push(ancestor.to_path_buf());
        }
    }

    for base in candidates {
        let venv = if cfg!(target_os = "windows") {
            base.join(".venv").join("Scripts").join("python.exe")
        } else {
            base.join(".venv").join("bin").join("python")
        };
        if venv.exists() {
            return venv;
        }
    }

    if cfg!(target_os = "windows") {
        std::path::PathBuf::from("python")
    } else {
        std::path::PathBuf::from("python3")
    }
}

fn python_script() -> std::path::PathBuf {
    let project_dir = project_dir();
    if project_dir.join("gdictate.py").exists() {
        project_dir.join("gdictate.py")
    } else {
        std::path::PathBuf::from("../gdictate.py")
    }
}

fn configure_python_command(command: &mut Command) {
    let vendor = project_dir().join("vendor");
    if !vendor.exists() {
        return;
    }
    let sep = if cfg!(target_os = "windows") {
        ";"
    } else {
        ":"
    };
    let current = std::env::var("PYTHONPATH").unwrap_or_default();
    let next = if current.is_empty() {
        vendor.to_string_lossy().to_string()
    } else {
        format!("{}{}{}", vendor.to_string_lossy(), sep, current)
    };
    command.env("PYTHONPATH", next);
}

fn run_python(args: &[&str]) -> Result<String, String> {
    let mut command = Command::new(python_exe());
    configure_python_command(&mut command);
    let output = command
        .arg(python_script())
        .args(args)
        .output()
        .map_err(|err| err.to_string())?;

    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();

    if output.status.success() {
        Ok(if stdout.is_empty() { stderr } else { stdout })
    } else {
        Err(if stderr.is_empty() { stdout } else { stderr })
    }
}

fn run_python_json_result(args: &[&str]) -> Result<String, String> {
    let mut command = Command::new(python_exe());
    configure_python_command(&mut command);
    let output = command
        .arg(python_script())
        .args(args)
        .output()
        .map_err(|err| err.to_string())?;

    let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();

    if !stdout.is_empty() {
        Ok(stdout)
    } else if output.status.success() {
        Ok(stderr)
    } else {
        Err(stderr)
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(Mutex::new(NativeHotkeys::default()))
        .manage(Mutex::new(EvdevHotkeys::default()))
        .manage(DaemonSupervisor::default())
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            configure_python_resources(app.handle());
            setup_tray(app)?;
            spawn_daemon_supervisor(app.handle());
            let handle = app.handle().clone();
            thread::spawn(move || {
                thread::sleep(Duration::from_millis(500));
                let settings = load_settings_file().unwrap_or_default();
                let _ = sync_hotkey_backend(&handle, &settings);
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() == "main" {
                if let WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .invoke_handler(tauri::generate_handler![
            load_settings,
            save_settings,
            default_settings,
            settings_schema,
            reset_settings,
            capabilities,
            diagnostics,
            preflight,
            live_report,
            apply_system_action,
            shortcut_report,
            file_pipeline_report,
            transcribe_file,
            file_job_start,
            file_jobs,
            file_job_status,
            file_job_cancel,
            daemon_command,
            daemon_spawn,
            daemon_status,
            daemon_start,
            daemon_stop,
            daemon_toggle,
            daemon_shutdown,
            evdev_hotkeys_spawn,
            evdev_hotkeys_stop,
            open_overlay,
            close_overlay,
            overlay_status,
            native_hotkeys_reload,
            native_hotkeys_status
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

fn main() {
    run();
}
