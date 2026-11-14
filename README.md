# Needle Desktop

A local-first Windows desktop voice assistant built around **Needle 3** for local intent routing. Press `Ctrl+Space` or say a wake phrase, speak a command, and a dark glass popup slides up from the taskbar, shows the live transcript, routes the request locally, and performs the action.

No cloud AI APIs, no accounts, and no telemetry. The microphone, speech recognition, intent model, and automation all run on the local machine. An optional Telegram bot lets you send the same commands from a phone; it is off by default.

## What it does

- **Wake word** — offline Vosk recognition for `hey needle`, `hey jarvis`, and `hey computer` (editable in Settings).
- **Global hotkey** — `Ctrl+Space` by default (editable in Settings).
- **Gemini-style popup** — frameless, translucent, always-on-top, bottom-center, with a glowing audio wave and live transcript.
- **Local routing** — a conservative fast path handles common phrasings instantly; Needle 3 handles novel/ambiguous commands through the official Cactus engine and `needle3.cact` weights.
- **Safe Windows execution** — app resolution with allow-listed paths, `uiautomation` element clicks, `pynput`/`pyautogui` input, foreground-window restore, and a top-left corner abort failsafe.
- **Telegram bot (optional)** — pair your own Telegram chat and send commands such as “open Notepad” or “take a screenshot” from your phone. Off by default.
- **Tray app** — settings, wake toggle, typed command, open data folder, start with Windows, and quit.

## Tool catalog

The router is restricted to exactly six tools:

| Tool | Arguments | Purpose |
| --- | --- | --- |
| `open_application` | `app_name` | Launch Chrome, Notepad, Calculator, Explorer, etc. |
| `type_text` | `text` | Type text into the focused field |
| `click_element` | `target` | Click a UI element by name or `x,y` coordinates |
| `press_hotkey` | `keys` | Press a shortcut such as `ctrl+c` or `alt+tab` |
| `take_screenshot` | — | Save a screenshot to `Pictures\Needle` |
| `control_media` | `action` | Play, pause, next, previous, mute |

The model cannot call arbitrary tools. Tool names and arguments are validated again before execution.

## Requirements

- Windows 10 or 11, x86-64.
- A microphone for voice input (the hotkey and typed-command path work without one).
- No Python installation is required to run the built executable.

For building from source:

- Python 3.10+ (verified with Python 3.13).
- The bundled assets in this repository:
  - `assets/engine/libcactus.dll`
  - `assets/models/needle3/needle3.cact`
  - `assets/models/vosk-en-us/`
  - `assets/icons/`, `assets/sounds/`

## Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

Useful development commands:

```powershell
python run.py --selftest --verbose      # verify assets, Qt, Vosk, Needle, automation
python run.py --route "open notepad"    # route one command and print the JSON call
python run.py --render-preview          # render the overlay to a PNG
python run.py --settings                # open Settings on launch
python run.py --no-wake                 # disable wake listening for this run
python -m unittest discover -s tests -v # run the unit tests
```

On a slow CPU, the first Needle 3 initialization can take roughly 35–45 seconds because the engine prefills the tool schemas. The app starts immediately and the fast path works while Needle warms up in the background. Once initialized, novel-command routing takes roughly 10–20 seconds on the tested 2-core Celeron; the deterministic fast path is instant.

## Build the standalone exe

```powershell
python build_exe.py
```

This verifies the model checksum, runs the unit tests, builds a onefile executable, and runs a frozen selftest. The result is:

```text
dist\NeedleDesktop.exe
```

A folder build starts faster because it does not extract ~200 MB on every launch:

```powershell
python build_exe.py --onedir
```

Result:

```text
dist\NeedleDesktop\NeedleDesktop.exe
```

Build options:

| Option | Meaning |
| --- | --- |
| `--onedir` | Build a folder instead of a onefile exe |
| `--no-tests` | Skip unit tests |
| `--no-clean` | Reuse the PyInstaller work cache |
| `--no-verify` | Skip the frozen selftest after building |

The frozen selftest writes its report to:

```text
%APPDATA%\NeedleDesktop\selftest.json
```

## Launch and use

1. Double-click `NeedleDesktop.exe` (or the exe inside `dist\NeedleDesktop\`).
2. On first run, the Settings dialog opens. Choose the microphone and confirm the wake phrases/hotkey.
3. Leave the app running in the system tray.
4. Trigger a command in any of these ways:
   - Press `Ctrl+Space`.
   - Say **“Hey Needle”**, **“Hey Jarvis”**, or **“Hey Computer”**.
   - Right-click the tray icon → **Type a command…**
5. Speak or type, for example:
   - “Open Notepad”
   - “Type hello world into the search box”
   - “Click the save button”
   - “Press Ctrl+C”
   - “Take a screenshot”
   - “Pause the music”
6. The popup shows the parsed action badge and auto-hides about a second after execution. Click outside or press `Esc` to dismiss it.

## Telegram bot (optional)

The Telegram bridge is opt-in and off by default. When it is enabled, command text and results travel through Telegram's cloud, so the PC needs internet. The local voice path remains fully offline.

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. Open **Needle Desktop → Settings → Telegram bot**, paste the token, and enable the bot.
3. Save. The header badge changes from **100% OFFLINE** to **TELEGRAM ENABLED**.
4. Send `/start` (or `/help`) to your bot. It replies with your numeric chat ID.
5. Open Settings again, add that chat ID to **Allowed chat IDs**, and save.
6. Send a normal message such as `open Notepad`, `type hello world`, or `take a screenshot`.

The bot stays in **pairing mode** until at least one chat ID is added: it replies with the sender's chat ID but refuses to execute commands. Only listed chats can run commands. For a group, add the group's negative chat ID; Telegram's privacy mode may also require addressing the bot or disabling privacy in BotFather.

Bot commands:

| Command | Purpose |
| --- | --- |
| `/start`, `/help` | Show usage and the current chat ID |
| `/id` | Show this chat ID |
| `/status` | Show whether Needle, the router, wake word, and the bot are ready |
| `/screenshot` | Capture the screen and send the image to the chat |

Screenshots and command results are uploaded to Telegram; avoid using `/screenshot` on sensitive screens. The bot token is stored in `%APPDATA%\NeedleDesktop\config.json`; keep that file private and revoke the token with @BotFather if it is ever exposed.

Interactive commands (`type_text`, `click_element`, `press_hotkey`) act on the window that was focused when the Telegram message arrived. The desktop must be unlocked for those actions to reach a normal window.

### Safety

- Interactive actions (`type_text`, `click_element`, `press_hotkey`, `take_screenshot`) hide the popup first and restore the previously focused window.
- Move the mouse to the **top-left corner of the screen** to abort an automated click/drag (`pyautogui` failsafe).
- `Ctrl+Alt+Delete` is rejected by the hotkey validator.
- Application names are resolved against known executables, Start Menu shortcuts, and Windows `App Paths`; arbitrary command lines are not executed.

## Offline guarantees

- Intent routing uses `assets/engine/libcactus.dll` and `assets/models/needle3/needle3.cact` through a direct `ctypes` binding to the Cactus C ABI.
- Speech recognition uses the local Vosk `vosk-model-small-en-us-0.15` model.
- The voice pipeline does not call any network API. Vosk’s Python package includes helper functions that reference URLs, but this app only uses its local model/DLL classes.
- The app does not send telemetry.
- The default voice, hotkey, and typed-command paths make no network calls. The optional Telegram bot is the only network feature: when enabled, it long-polls Telegram's Bot API and sends command text/results to the paired chat. Leave it disabled for a fully offline setup.

## Troubleshooting

**“Needle 3 is unavailable”**
Check that `libcactus.dll` and `needle3.cact` are present in the bundled `assets` folder (or next to the exe in a source run). Rebuild with `python build_exe.py`.

**No wake word**
Open Settings and confirm the correct input device is selected. Windows may be blocking microphone access: Settings → Privacy & security → Microphone → allow desktop apps. The hotkey still works.

**The microphone works but commands are not recognized**
Use the wake phrase followed by a short command, or press `Ctrl+Space` and speak after the popup appears. The command recognizer stops after about 1.4 seconds of silence.

**Nothing happens on a click**
The target must be visible. `uiautomation` searches the foreground window first, then the desktop. If an element cannot be found, try a more exact name or use coordinates: “click 640, 480”.

**First Needle command is slow**
This is expected on slow CPUs during first initialization. Keep the app running in the tray, or enable **Start with Windows** in Settings so the model warms up at login.

**Windows SmartScreen warns about the exe**
The build is unsigned. Choose “More info” → “Run anyway”, or sign the executable with your own code-signing certificate.

## Project layout

```text
F:\NEEDLE
├─ run.py                       # source entry point
├─ build_exe.py                 # verified PyInstaller build
├─ NeedleDesktop.spec           # PyInstaller spec (onefile/onedir)
├─ requirements.txt
├─ assets/
│  ├─ engine/libcactus.dll      # official Cactus Windows engine
│  ├─ models/needle3/           # Needle 3 weights
│  ├─ models/vosk-en-us/        # offline speech model
│  ├─ icons/                    # app/overlay artwork
│  └─ sounds/                   # listen/done/error cues
├─ needle_assistant/
│  ├─ app.py                    # CLI, startup, selftest, preview
│  ├─ controller.py             # session/orchestration
│  ├─ needler.py                # ctypes Cactus ABI + routing
│  ├─ catalog.py                # six tools, fast path, validation
│  ├─ automation.py             # safe Windows execution layer
│  ├─ audio.py                  # Vosk wake word + command capture
│  ├─ telegram_bot.py           # optional long-polling Telegram bridge
│  ├─ overlay.py                # glass popup UI
│  ├─ settings_dialog.py
│  ├─ tray.py
│  ├─ hotkey.py
│  ├─ waveform.py
│  ├─ windows_api.py
│  └─ ...
└─ tests/
```

## Verified asset checksums

- `needle3.cact` SHA-256: `C9D915ECA282ED42D1A09B143B592ADB4CC6744FFE2D294ADF5CFC5548170C38`
- `libcactus.dll`: 1,870,336 bytes (official `windows-x86_64` release)
