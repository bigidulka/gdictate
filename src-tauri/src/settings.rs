use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EngineSettings {
    pub name: String,
}

impl Default for EngineSettings {
    fn default() -> Self {
        Self {
            name: "chrome".into(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct BindingSettings {
    pub mode: String,
    pub toggle: String,
    pub mic_hold: String,
    pub speakers_hold: String,
    pub linux_backend: String,
}

impl Default for BindingSettings {
    fn default() -> Self {
        Self {
            mode: "dual-hold".into(),
            toggle: "CTRL+ALT".into(),
            mic_hold: "ALT+LEFT".into(),
            speakers_hold: "ALT+RIGHT".into(),
            linux_backend: "de-shortcut+evdev".into(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct AudioSettings {
    pub source: String,
    pub restore_default_after_start: bool,
    pub linux_router: String,
    pub windows_speaker_input: String,
}

impl Default for AudioSettings {
    fn default() -> Self {
        Self {
            source: "mic".into(),
            restore_default_after_start: true,
            linux_router: "pipewire-pulse".into(),
            windows_speaker_input: "auto".into(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct PasteSettings {
    pub mode: String,
    pub live: bool,
    pub linux_terminal_combo: String,
    pub windows_combo: String,
}

impl Default for PasteSettings {
    fn default() -> Self {
        Self {
            mode: "auto".into(),
            live: true,
            linux_terminal_combo: "ctrl-v".into(),
            windows_combo: "ctrl-v".into(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ChromeSettings {
    pub channel: String,
    pub hidden: bool,
    pub setup_required: bool,
    pub profile_dir: String,
}

impl Default for ChromeSettings {
    fn default() -> Self {
        Self {
            channel: "auto".into(),
            hidden: true,
            setup_required: false,
            profile_dir: "".into(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct OverlaySettings {
    pub enabled: bool,
    pub click_through: bool,
    pub show_interim: bool,
    pub position: String,
}

impl Default for OverlaySettings {
    fn default() -> Self {
        Self {
            enabled: true,
            click_through: true,
            show_interim: true,
            position: "lower-center".into(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct AppSettings {
    pub language: String,
    pub engine: EngineSettings,
    pub bind: BindingSettings,
    pub audio: AudioSettings,
    pub paste: PasteSettings,
    pub chrome: ChromeSettings,
    pub overlay: OverlaySettings,
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            language: "ru-RU".into(),
            engine: EngineSettings::default(),
            bind: BindingSettings::default(),
            audio: AudioSettings::default(),
            paste: PasteSettings::default(),
            chrome: ChromeSettings::default(),
            overlay: OverlaySettings::default(),
        }
    }
}

pub fn settings_path() -> Result<PathBuf, String> {
    let base = dirs::config_dir().ok_or_else(|| "config dir not found".to_string())?;
    Ok(base.join("gdictate").join("settings.json"))
}

pub fn load_settings_file() -> Result<AppSettings, String> {
    let path = settings_path()?;
    if !path.exists() {
        return Ok(AppSettings::default());
    }
    let text = fs::read_to_string(path).map_err(|err| err.to_string())?;
    let mut settings: AppSettings = serde_json::from_str(&text).map_err(|err| err.to_string())?;
    normalize_settings(&mut settings);
    Ok(settings)
}

pub fn save_settings_file(settings: &AppSettings) -> Result<(), String> {
    let path = settings_path()?;
    if let Some(parent) = path.parent() {
        fs::create_dir_all(parent).map_err(|err| err.to_string())?;
    }
    let mut settings = settings.clone();
    normalize_settings(&mut settings);
    let text = serde_json::to_string_pretty(&settings).map_err(|err| err.to_string())?;
    fs::write(path, format!("{text}\n")).map_err(|err| err.to_string())
}

fn normalize_settings(settings: &mut AppSettings) {
    if settings.overlay.position == "bottom-center" {
        settings.overlay.position = "lower-center".into();
    }
}
