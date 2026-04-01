# TODO — gdictate cross-platform roadmap

Текущее состояние: полностью работает на Linux/Wayland.
Цель: поддержка macOS и Windows с единой кодовой базой.

---

## 1. Кросс-платформенный Chrome launcher

**Файл:** `gdictate.py` → `SpeechProxy._launch_chrome()`

Сейчас захардкожены Linux-пути и Linux-специфичные флаги.

### macOS
- [ ] Путь к Chrome: `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`
- [ ] Путь к Chromium: `/Applications/Chromium.app/Contents/MacOS/Chromium`
- [ ] Chrome profile → `~/Library/Caches/gdictate-chrome`
- [ ] Убрать `--class=gdictate` (Linux-only)
- [ ] Убрать `--no-sandbox` (не нужен на macOS)
- [ ] Скрытие окна: `--start-hidden` или AppleScript `set visible to false`

### Windows
- [ ] Путь к Chrome: `C:\Program Files\Google\Chrome\Application\chrome.exe`
- [ ] Поиск через реестр: `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe`
- [ ] Chrome profile → `%LOCALAPPDATA%\gdictate-chrome`
- [ ] Скрытие окна: `--window-position=-32000,-32000` (уже работает) или `SW_HIDE` через `ctypes`
- [ ] `asyncio.create_subprocess_exec` → нужен `creationflags=subprocess.CREATE_NO_WINDOW`

---

## 2. Clipboard + paste

**Файл:** `gdictate.py` → `Dictation._paste()`

Сейчас: `wl-copy` + `ydotool key` (Ctrl+Shift+V). Только Wayland.

### macOS
- [ ] Clipboard: `pbcopy` (`subprocess: echo text | pbcopy`)
- [ ] Paste: `osascript -e 'tell application "System Events" to keystroke "v" using command down'`
- [ ] Или через `pyperclip` + `pynput` (pip-пакеты, кросс-платформ)

### Windows
- [ ] Clipboard: `win32clipboard` (pywin32) или `subprocess: clip.exe`
- [ ] Paste: `ctypes.windll.user32.SendInput()` Ctrl+V
- [ ] Или `pynput.keyboard.Controller().press/release`

### Рекомендация
- [ ] Абстрагировать в класс `Paster` с методом `async paste(text)`
- [ ] `WaylandPaster` — wl-copy + ydotool (текущий)
- [ ] `X11Paster` — xclip + xdotool (для X11 Linux)
- [ ] `MacPaster` — pbcopy + osascript
- [ ] `WinPaster` — win32clipboard + SendInput
- [ ] Автодетект по `sys.platform` + `$XDG_SESSION_TYPE`

---

## 3. Глобальный хоткей

**Файл:** `gdictate.py` → `run_evdev()`

Сейчас: evdev (прямой доступ к /dev/input). Только Linux, требует root или группу `input`.

### macOS
- [ ] `Quartz.CGEventTapCreate()` — Core Graphics event tap
- [ ] Или `pynput.keyboard.GlobalHotKeys` (проще, pip install)
- [ ] Требует разрешение Accessibility в System Preferences

### Windows
- [ ] `ctypes.windll.user32.RegisterHotKey()` — нативный API
- [ ] Или `pynput.keyboard.GlobalHotKeys`
- [ ] Или `keyboard` pip-пакет (требует admin)

### Рекомендация
- [ ] Абстрагировать в класс `HotkeyListener` с callback `on_toggle`
- [ ] `EvdevHotkey` — текущий (Linux, raw evdev)
- [ ] `PynputHotkey` — кросс-платформ через pynput (macOS, Windows, Linux fallback)
- [ ] Автодетект: evdev если доступен, иначе pynput

---

## 4. OSD / уведомления

**Файл:** `overlay.py`

Сейчас: KDE D-Bus OSD + notify-send fallback. Только Linux.

### macOS
- [ ] `osascript -e 'display notification "text" with title "gdictate"'`
- [ ] Или `pync` / `terminal-notifier` (pip/brew)
- [ ] Для streaming interim: нет нативного аналога OSD
- [ ] Вариант: NSStatusBar item с dropdown (как tray, но с текстом)

### Windows
- [ ] `win10toast` или `plyer` (pip) — toast notifications
- [ ] `ctypes` + Shell_NotifyIcon для balloon tips
- [ ] Для streaming interim: overlay окно через `tkinter` с `topmost` + `overrideredirect`
- [ ] Или `win32gui` transparent layered window

### Рекомендация
- [ ] `OverlayPopup` — оставить текущий интерфейс (`show_interim`, `show_final`)
- [ ] Linux: KDE OSD → notify-send (уже сделано)
- [ ] macOS: AppleScript notification для final, skip interim
- [ ] Windows: toast для final, skip interim (или tkinter overlay)

---

## 5. System tray

**Файл:** `overlay.py` → `DictationTray`

Сейчас: PyQt6 QSystemTrayIcon. Работает на Linux (KDE/GNOME).

### macOS
- [ ] PyQt6 QSystemTrayIcon работает на macOS из коробки
- [ ] Или `rumps` (pip) — нативный macOS menu bar app
- [ ] Иконка в menu bar вместо tray

### Windows
- [ ] PyQt6 QSystemTrayIcon работает на Windows из коробки
- [ ] Или `pystray` (pip) — легковеснее чем PyQt6

### Рекомендация
- [ ] PyQt6 tray уже кросс-платформенный — оставить как есть
- [ ] Для --no-ui варианта без PyQt6: `pystray` как fallback

---

## 6. Chrome hiding (скрытие окна)

**Файл:** `gdictate.py` → `ensure_kwin_rule()`

Сейчас: KWin window rules (KDE only).

### GNOME
- [ ] `xdotool` (X11) или `gdbus` для Mutter
- [ ] Или просто `--window-position=32000,32000` + `--window-size=1,1` (уже есть)

### Sway / Hyprland
- [ ] Sway: `for_window [app_id="gdictate"] move scratchpad` в конфиге
- [ ] Hyprland: `windowrulev2 = float,class:^(gdictate)$` + `windowrulev2 = size 1 1`
- [ ] Документировать в README, не автоматизировать

### macOS
- [ ] AppleScript: `tell application "Google Chrome" to set visible of window 1 to false`
- [ ] Или `--start-hidden` + NSWindow API через pyobjc

### Windows
- [ ] `ctypes.windll.user32.ShowWindow(hwnd, SW_HIDE)` после запуска
- [ ] Найти окно по title/class через `FindWindow`
- [ ] Или `--window-position=-32000,-32000` (уже работает)

---

## 7. Микрофон auto-detect

**Файл:** `gdictate.py` → `ensure_microphone()`

Сейчас: `pactl` (PulseAudio/PipeWire). Только Linux.

### macOS
- [ ] `system_profiler SPAudioDataType` — список устройств
- [ ] Или `pyaudio` для проверки доступности
- [ ] Chrome использует Core Audio default — обычно работает из коробки

### Windows
- [ ] Chrome использует Windows Audio default — обычно работает из коробки
- [ ] `powershell Get-PnpDevice -Class AudioEndpoint` — проверка наличия
- [ ] Или `pyaudio` / `sounddevice` для проверки

### Рекомендация
- [ ] Linux: оставить pactl (текущий)
- [ ] macOS / Windows: проверить наличие хотя бы одного микрофона, без переключения default

---

## 8. SSL сертификаты

**Файл:** `gdictate.py` → `ensure_ssl()`

Сейчас: `openssl` CLI. Работает везде где есть openssl.

### Windows
- [ ] openssl может не быть в PATH
- [ ] Альтернатива: `cryptography` pip-пакет для генерации self-signed cert
- [ ] Или bundled openssl.exe

---

## 9. Install / packaging

**Файл:** `install.sh` (Linux only)

### macOS
- [ ] `install-mac.sh` — brew install chromium, pip install
- [ ] Или Homebrew formula
- [ ] Или `.app` bundle через py2app

### Windows
- [ ] `install.bat` или PowerShell скрипт
- [ ] Или `.exe` через PyInstaller
- [ ] Или `winget` / `scoop` манифест

### Рекомендация
- [ ] Phase 1: отдельные install скрипты per-OS
- [ ] Phase 2: `pyproject.toml` + `pip install gdictate`
- [ ] Phase 3: native packaging (Homebrew, winget, AUR)

---

## 10. Browser permission detection

**Файл:** `gdictate.py` → `is_browser_configured()`

Сейчас: парсит Chrome Preferences JSON. Путь захардкожен для Linux.

- [ ] macOS: `~/Library/Caches/gdictate-chrome/Default/Preferences`
- [ ] Windows: `%LOCALAPPDATA%\gdictate-chrome\Default\Preferences`
- [ ] Сам формат Preferences одинаковый — нужно только поменять путь

---

## Приоритет реализации

### Phase 1 — рефакторинг (Linux, без новых фич)
1. [ ] Абстрагировать `Paster` (clipboard + paste)
2. [ ] Абстрагировать `HotkeyListener` (evdev → интерфейс)
3. [ ] Платформо-зависимые пути в одно место (`CHROME_PATHS`, `CHROME_PROFILE`, etc.)

### Phase 2 — macOS
4. [ ] macOS Chrome paths + profile
5. [ ] `MacPaster` (pbcopy + osascript)
6. [ ] `PynputHotkey` (кросс-платформ)
7. [ ] macOS notifications (AppleScript)
8. [ ] install-mac.sh
9. [ ] Тест на macOS

### Phase 3 — Windows
10. [ ] Windows Chrome paths + profile
11. [ ] `WinPaster` (win32clipboard + SendInput)
12. [ ] Windows hotkey (pynput или RegisterHotKey)
13. [ ] Windows notifications (toast)
14. [ ] Chrome window hiding (SW_HIDE)
15. [ ] install.bat
16. [ ] Тест на Windows

### Phase 4 — X11 Linux
17. [ ] `X11Paster` (xclip + xdotool)
18. [ ] X11 hotkey (xdotool или pynput)
19. [ ] Тест на X11
