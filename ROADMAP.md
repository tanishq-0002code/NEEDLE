# Needle Desktop — Autonomous Agent Capability Roadmap

Status: **plan only, no code written yet**. Reviewed and approved by the owner before implementation starts.
Rule from the owner: implement **two capabilities at a time**, never everything at once.

---

## 1. Guiding principles (apply to every iteration)

1. **Local-first stays.** Voice, Vosk, Needle 3, automation and any new perception/planning run locally. Telegram remains an optional remote channel.
2. **Compact tool catalog.** Needle 3 pays for every tool-schema token and prefill is slow. Prefer **parameterized tools** (`action` enums) over dozens of tiny tools. Target ≤ 12 tools total.
3. **Safety gates.** Destructive/irreversible actions (shutdown, restart, close app, delete file, run shell) require an explicit confirmation step and are written to an audit log. The top-left-corner abort failsafe is preserved.
4. **No regression.** Standalone `dist\NeedleDesktop.exe`, orb UI states, right-click menu and enable/disable setting must keep working. Frozen selftest must pass after every iteration.
5. **Small, verifiable steps.** Each iteration ends with: unit tests → frozen selftest → rebuild exe → manual smoke test → commit.
6. **Version bump** at the end of each phase (`1.1.0` → `1.2.0` after Phase 1, etc.), matching `assets/version_info.txt`, `needle_assistant/__init__.py`, spec/README.

---

## 2. Capability inventory → iteration map

| # | Iteration (pair) | Capabilities added | Fixes your complaint |
|---|---|---|---|
| 1 | App lifecycle | `close_application`, `focus_window` | "not able to close apps", "switch to an app" |
| 2 | System power + confirmations | `system_control` (lock/shutdown/restart/sleep), `confirm_action` gate | "lock or shut down the pc" |
| 3 | Window targeting | `window_manage` (min/max/restore), `window` arg on `type_text`/`click_element` | "perform a task on a specific app" |
| 4 | Routing accuracy | tighter app resolution, confidence gating, clarification reply | "opens the wrong app" |
| 5 | Perception + verification | offline screen OCR, verify-after-action loop | "is it actually done?" |
| 6 | Clipboard + files | clipboard get/set, file create/read/edit/delete/move | general autonomy |
| 7 | Shell + output | sandboxed command runner, output capture | general autonomy |
| 8 | Browser control | open URL in chosen browser, tabs, page text | general autonomy |
| 9 | Planning | multi-step planner, plan progress UI | "book a table and put it in my calendar" |
| 10 | Robustness | wait/retry, error recovery, undo/rollback journal | reliability |
| 11 | Memory + dialogue | preference/history memory, follow-up clarification | "like last time" |
| 12 | Background + macros | timers/scheduled tasks, macro record/replay | "remind me in 20 minutes" |

Phase boundaries: **Phase 1 = iterations 1–4** (your stated problems). **Phase 2 = 5–8** (autonomy foundation). **Phase 3 = 9–12** (agent quality).

---

## 3. Per-iteration engineering checklist

Every iteration follows the same pipeline so nothing is forgotten:

1. **Schema** — extend `needle_assistant/catalog.py` (`TOOL_SCHEMAS`, `ALLOWED_TOOLS`).
2. **Validation** — extend `validate_call()` (argument normalization, allow-lists, rejection reasons).
3. **Fast path** — add deterministic triggers in `fast_route()` so common phrasings stay instant.
4. **Execution** — implement in `needle_assistant/automation.py` (`DesktopAutomation.execute` dispatch + helpers).
5. **Router glue** — `needler.py` unchanged unless new engine features are needed.
6. **UI/status** — orb state / badge text (`catalog.badge_text`, `describe_call`), settings rows if user-configurable.
7. **Telegram** — new tools automatically work through `_execute_telegram_command`; add confirm/callback handling only where required (iteration 2).
8. **Safety** — confirmation dialog + audit-log write for destructive actions.
9. **Tests** — unit tests in `tests/` (catalog, validation, routing, automation helpers).
10. **Build** — `python build_exe.py` (includes tests + frozen selftest), clean selftest artifacts.
11. **Commit** — local commit; push/release only when the owner asks.

---

## 4. Detailed iteration specs

### Iteration 1 — App lifecycle: `close_application` + `focus_window`

**Goal:** "close Chrome", "quit Spotify", "switch to Notepad".

**Tools**
```json
close_application { "app_name": "chrome" }        // graceful first, force fallback
focus_window     { "title": "Notepad" }           // substring match on window titles
```

**Behavior**
- Resolve app name with the existing `AppResolver` (allow-list + fuzzy).
- Enumerate top-level windows; find visible window whose process matches the resolved exe.
- Graceful: send `WM_CLOSE`; wait 2s; if still alive and the caller did not pass `force`, terminate via `taskkill /PID <pid>`.
- Never close critical system processes (svchost, explorer optional safeguard list).
- `focus_window`: match title case-insensitively, `ShowWindow(SW_RESTORE)` + `SetForegroundWindow`; return window title in the result message.
- Fast phrases: "close <app>", "quit <app>", "exit <app>", "switch to <title>", "focus <title>".

**Files:** `catalog.py` (schemas, validation, fast routes), `automation.py` (`_close_application`, `_focus_window`, window enumeration helper), `tests/test_catalog.py`.

**Safety:** confirmation when the app has unsaved-work indicators or more than N windows; audit-log entry `{time, action, target, success}`.

**DoD:** "close chrome" and "switch to notepad" work from voice, hotkey, typed and Telegram; wrong-process killing impossible; tests green; selftest passes.

---

### Iteration 2 — System power + confirmation gate: `system_control` + `confirm_action`

**Goal:** "lock my pc", "shut down the computer", "restart", "sleep".

**Tools**
```json
system_control { "action": "lock|shutdown|restart|sleep|hibernate" }
```
`lock` executes immediately (non-destructive). `shutdown|restart|sleep|hibernate` require confirmation.

**Behavior**
- `lock` → `ctypes.windll.user32.LockWorkStation()`.
- `shutdown|restart` → `ExitWindowsEx(EWX_SHUTDOWN|EWX_RESTART)`; require admin/SeShutdownPrivilege handling, graceful app close.
- `sleep|hibernate` → `SetSuspendState`.
- Confirmation flow: controller opens `QMessageBox`-style dialog (overlay stays responsive), 10s countdown default **Cancel**. Telegram: send an inline-keyboard message (`callback_query` update added to `allowed_updates`); only the paired chat can approve.
- Audit log for every power action.

**Files:** `catalog.py`, `automation.py` (`_system_control`, privilege helper), `controller.py` (confirm dispatcher), `settings_dialog.py` (toggle: allow power actions), `telegram_bot.py` (callback query handling), new `confirmation.py`, `tests/`.

**Safety:** default-off setting `allow_power_actions`; double confirmation for shutdown/restart; cancel always available; never auto-confirm.

**DoD:** all five actions work and are gated; cancelling prevents the action; Telegram approval works; logs record decision.

---

### Iteration 3 — Window targeting: `window_manage` + `window` argument

**Goal:** "minimize Notepad", "maximize Chrome", and typing/clicking *inside* a named window.

**Tools**
```json
window_manage   { "title": "notepad", "action": "minimize|maximize|restore" }
type_text       { "text": "...", "window": "notepad" }      // new optional field
click_element   { "target": "...", "window": "notepad" }    // new optional field
```

**Behavior**
- Resolve target window by title (same matcher as `focus_window`).
- `ShowWindow(hwnd, SW_MINIMIZE|SW_MAXIMIZE|SW_RESTORE)`.
- With `window` set, automation activates that window and performs the click/type against it via UIA rooted at that hwnd; without it, behavior is unchanged (foreground window).
- Ambiguity: if the title matches several windows, ask which one (list titles) instead of guessing.

**Files:** `catalog.py` (optional params in schemas + validation), `automation.py` (`_window_manage`, targeted `_type_text`/`_click_uia` with hwnd), `controller.py` (ambiguous-window prompt), `tests/`.

**DoD:** targeted typing/clicking lands in the correct window even when it is not foreground; minimize/maximize/restore verified manually; ambiguity handled without wrong actions.

---

### Iteration 4 — Routing accuracy: resolver + confidence gating

**Goal:** stop opening the wrong app; ask when unsure.

**Changes**
- `AppResolver.resolve()`: raise similarity threshold from 0.64 → 0.80; exact Start-menu name wins outright; return **candidate list** with scores.
- `catalog.validate_call()`: reject when the requested name is ambiguous and let the controller ask.
- Router confidence gate: if Needle 3 returns `confidence < 0.6` or `fast_route` misses and the app score is 0.64–0.80, reply with "Did you mean X, Y?" instead of executing.
- Clarification state machine: controller remembers the pending candidates; the next message ("the second one") resolves it.
- Show confidence in the popup badge (e.g., `OPEN APP  chrome · 0.71`) and in `/status`.

**Files:** `automation.py` (resolver API change), `catalog.py`, `controller.py`, `needler.py` (confidence threshold), `settings_dialog.py` (show confidence), `tests/`.

**DoD:** no wrong-app launches on a test set of 20 ambiguous/typo phrasings; ambiguous cases ask; exact names stay instant; tests green.

---

### Iteration 5 — Screen perception + verification loop

**Goal:** the agent can see and verify results (foundation for everything after).

**Approach (offline):** Windows Runtime OCR (`Windows.Media.Ocr` via `comtypes`/`winsdk`, built into Win10/11) + screen capture already present. Optional later: small local VLM.

**Behavior**
- New tool `read_screen` → returns recognized text + bounding boxes of the foreground window or full screen.
- After each action, controller can run a **verify** step: take a screenshot/OCR and check for expected markers (e.g., window title appeared, dialog closed).
- On mismatch: report failure and offer one retry.

**Files:** `catalog.py`, `automation.py` (`_read_screen`), new `vision.py` (OCR wrapper), `controller.py` (verify hook), `tests/`.

**DoD:** OCR extracts text from a known window reliably; a scripted "open notepad → verify title" succeeds; non-English locales degrade gracefully (clear error).

---

### Iteration 6 — Clipboard + file operations

**Goal:** "copy that link", "paste the address", "create a notes file", "rename this".

**Tools (parameterized)**
```json
clipboard      { "action": "get|set", "text": "..." }
file_operation { "op": "create|read|append|delete|rename|move|list",
                 "path": "...", "new_path": "...", "content": "..." }
```

**Behavior**
- Clipboard via `ctypes`/win32 (`OpenClipboard`, `CF_UNICODETEXT`); never read clipboard silently — returning it in the reply is fine.
- File ops restricted to user folders (`Desktop`, `Documents`, `Downloads`, `Pictures`, `%APPDATA%\NeedleDesktop`); absolute paths outside require explicit `allow_any_path` setting (default off).
- Delete uses the Recycle Bin (`SHFileOperation FO_DELETE`) when possible; rename/move validated for collisions.
- All destructive file ops confirmed + audited.

**Files:** `catalog.py`, new `filesystem.py`, `automation.py` dispatch, `settings_dialog.py` (allowed roots), `tests/`.

**DoD:** clipboard round-trip; create/read/append/list/rename/move/delete work inside allowed roots; blocked outside roots; confirmations appear; tests green.

---

### Iteration 7 — Sandboxed shell + output capture

**Goal:** "run ipconfig", "list my python packages", "how much disk space?".

**Tools**
```json
run_command { "command": "ipconfig /all", "timeout": 15 }
```

**Behavior**
- Allow-list of executables (`cmd.exe`, `powershell.exe`, `python.exe`, `winget.exe`, …) with argument filtering; deny download/execute chains, `rm -rf`-style destruction, registry edits.
- Run with `subprocess.run(..., timeout)`, capture stdout/stderr, truncate to ~2000 chars; never echo secrets.
- Result: success/failure + trimmed output, shown in popup and sent to Telegram.
- Confirmation required when the command is not in the safe list (setting `allow_shell` default off).

**Files:** `catalog.py`, new `shell.py`, `automation.py` dispatch, `settings_dialog.py`, `tests/`.

**DoD:** benign queries return output; dangerous commands are rejected with a clear reason; timeouts and huge outputs handled; Telegram receives output; tests green.

---

### Iteration 8 — Browser control

**Goal:** "open youtube.com in Chrome", "switch to the Gmail tab", "read this page".

**Tools**
```json
open_url    { "url": "https://...", "browser": "chrome|edge|firefox|default" }
tab_control { "action": "next|previous|close|new", "browser": "..." }
page_read   { "browser": "..." }
```

**Behavior**
- Resolve browser via `AppResolver`/registry; launch with the URL (graceful reuse of existing window).
- Tab control via browser-specific shortcuts (`ctrl+tab`, `ctrl+w`, `ctrl+t`) scoped to the browser window.
- `page_read` uses UIA `Document`/`Text` patterns in the browser window (works in Edge/Chrome with accessibility enabled), fallback to OCR.
- No credential access, no cookie/file reads; explicitly out of scope.

**Files:** `catalog.py`, new `browser.py`, `automation.py`, `tests/`.

**DoD:** URL opens in the requested browser; tab actions work in Chrome/Edge; page text readable for a simple page; unsupported browser reports clearly.

---

### Iteration 9 — Multi-step planner

**Goal:** "open Chrome, go to flights.com, search for tomorrow" — sequences, not single calls.

**Design (offline, honest about limits)**
- Needle 3 stays the per-step router. A deterministic **plan layer** recognizes sequence cues ("then", "after that", "and then", numbered steps) and splits the request into ordered steps; each step goes through the existing router/execution.
- `run_plan` meta-tool accepts an explicit JSON step list (for macros and advanced users) and validates every step before running any of them.
- `PlanRunner` in the controller executes steps serially, surfaces progress in the orb speech pill ("step 2 of 4: typing…"), stops on first hard failure, and supports per-step verification (iteration 5).
- Later option (phase 3+): a larger local model for decomposition; kept optional to preserve offline/no-install guarantees.

**Files:** `catalog.py`, new `planner.py` (splitter + `PlanRunner`), `controller.py`, `ui/needle_orb_window.py` (progress state), `tests/`.

**DoD:** three-step spoken command completes end-to-end; a failing step stops the plan with a clear message; plan preview before execution for destructive plans; tests green.

---

### Iteration 10 — Robustness: wait/retry, recovery, undo

**Goal:** reliable autonomous execution.

**Behavior**
- `wait` pseudo-step (delay until a condition: window appears, process exits, idle).
- Automatic retry once on transient failures (window not ready, UIA timeout) with backoff.
- Undo journal: reversible actions (open app, close app, type, click, file rename/move, clipboard) record inverse data; `/undo` or "undo that" replays the inverse, in reverse order.
- Error taxonomy: user-correctable vs system failure; surface actionable text.

**Files:** new `journal.py`, `planner.py` (wait/retry), `automation.py`, `catalog.py`, `telegram_bot.py` (`/undo`), `tests/`.

**DoD:** a flaky UIA click self-heals; "undo that" restores the previous state in 3/3 test scenarios; unknown errors produce actionable messages.

---

### Iteration 11 — Memory + dialogue

**Goal:** "use Chrome for PDFs like last time"; natural clarifications.

**Behavior**
- `memory.json` under `%APPDATA%\NeedleDesktop`: preferences, app aliases ("my editor" → Notepad++), frequent targets, corrections.
- Memory influences the **resolver and replies**, not the model prompt (re-initializing Cactus costs ~30s; avoid).
- Dialogue state: when the agent asks a question, the next message is treated as the answer (same state machine as iteration 4); works in overlay, typed command and Telegram.
- User can view/delete memory from Settings.

**Files:** new `memory.py`, `controller.py`, `automation.py` (alias resolution), `settings_dialog.py`, `telegram_bot.py`, `tests/`.

**DoD:** a learned alias resolves on the next request; clarification answer is consumed correctly; memory file is inspectable/deletable; tests green.

---

### Iteration 12 — Background tasks + macros

**Goal:** "remind me in 20 minutes", "when the render finishes, shut down".

**Behavior**
- Scheduler thread with persisted tasks (JSON): one-shot timers and recurring reminders; fires a Windows toast and/or runs an optional action (with confirmation for destructive actions).
- Condition triggers (optional stretch): window-title/process conditions polled at low frequency.
- Macros: record a sequence of tool calls and replay by voice/Telegram ("run my morning routine").

**Files:** new `scheduler.py`, `macros.py`, `catalog.py` (`run_macro`, `schedule_reminder`), `controller.py`, `tray.py`, `tests/`.

**DoD:** timer fires a toast; macro replays 3 actions; tasks survive restart; no runaway polling CPU usage; tests green.

---

## 5. Cross-cutting work items

- **Audit log:** `needle_assistant/audit.py` + `%APPDATA%\NeedleDesktop\logs\actions.jsonl`; every mutating action appends `{ts, tool, args(redacted), success}`. Needed from iteration 1.
- **Confirmation framework:** shared `confirmation.py` (Qt dialog + Telegram inline keyboard) used by iterations 2, 3, 6, 7.
- **Settings surface:** one new section "Capabilities" with toggles: power actions, shell, file roots, browser control, memory, scheduler. Off by default; enabling shows the safety note.
- **README + privacy:** document each capability, its data access, and its offline/network status.
- **Versioning:** bump `1.2.0` after Phase 1, `1.3.0` after Phase 2, `1.4.0` after Phase 3; update spec + version info + release notes.

---

## 6. Explicit non-goals (for now)

- Cloud LLM APIs as a requirement (optional add-on only).
- Email/calendar/chat integrations requiring accounts (would violate local-first unless user explicitly opts in).
- Controlling secure/desktops (UAC prompts, lock screen) — OS limitation, documented instead of hacked around.
- Pixel-perfect clicking in games/DirectX apps — UIA/OS automation boundary.

---

## 7. Approval checkpoint

Owner reviews this plan. On approval, implementation starts at **Iteration 1** and proceeds pair-by-pair, pausing after each pair for review.
