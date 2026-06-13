use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Capabilities {
    pub os: String,
    pub desktop: String,
    pub chrome: bool,
    pub microphone_routing: String,
    pub speaker_routing: String,
    pub global_hotkeys: String,
    pub paste: String,
    pub overlay: String,
    pub warnings: Vec<String>,
}
