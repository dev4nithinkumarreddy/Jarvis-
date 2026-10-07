# Jarvis

Jarvis is a local AI desktop agent built in strict phases.

## Safety Model

Jarvis enforces defense-in-depth safety checks in code before any tool can be executed by the model.

### 1. The Three Risk Tiers
Risk tiers are statically hardcoded in Python on each `ToolSpec` and can never be altered or overridden by the LLM:
- **`AUTO`**: Low-risk, read-only, or innocuous operations that can execute automatically without prompting the user.
- **`CONFIRM`**: Sensitive, state-altering, or external operations that require explicit user confirmation through a `Confirmer` (e.g. `CLIConfirmer` or `VoiceChannel`). In the CLI, prompts display `[y/N]` with a default of `No`. If confirmation times out, it automatically fails closed and evaluates to `No`.
- **`BLOCKED`**: Dangerous, policy-violating, or permanently disabled operations. Blocked tools will never execute under any circumstance.

### 2. The Taint Rule
- When untrusted external data (such as web search responses, untrusted file contents, or third-party web pages) is read during an agent turn, the session's safety context is marked as tainted (`session_state.tainted = True`).
- When a session is tainted, any tool requiring confirmation (`CONFIRM`) attaches an unmissable, visible warning to the confirmation prompt alerting the user that untrusted content was consumed this turn. This mitigates indirect prompt injection attacks where untrusted content instructs the agent to perform sensitive actions.

### 3. Path Guard
- All file system operations must pass through `resolve_allowed(path, allowed_roots)`.
- Environment variables (`$VAR`, `%VAR%`) and user tilde (`~`) are expanded.
- Symlinks, NTFS junctions, and parent directory traversal (`..`) are fully resolved to canonical target paths.
- Paths are strictly verified to reside within one of the `allowed_roots` specified in `config.yaml`, comparing fully resolved path hierarchies rather than raw string prefixes.

### 4. Cryptographic Audit Logging (SHA-256 Hash Chained)
- Every tool event (`call_requested`, `decision`, `executed`, `failed`) is appended to a JSONL file with cryptographic SHA-256 hash chaining.
- Each entry records `prev_hash` (pointing to the prior record's hash, or genesis 64 zeroes) and `record_hash` (SHA-256 digest of canonical JSON payload), ensuring cryptographic tamper-evidence.
- The entire chain can be verified at any time with `jarvis audit verify`.
- Sensitive parameters matching credential keywords (`key`, `token`, `password`, `secret`, `auth`, `credential`, `private`) are recursively redacted to `[REDACTED]`.
- Tool result summaries are truncated to a maximum of 500 characters to prevent log overflow.
- Every write operation is immediately flushed and synced to disk via `os.fsync`.

### 5. Emergency Stop (Kill Switch)
- Process-wide `KillSwitch` flag checked by the orchestrator prior to every tool execution.
- Can be triggered programmatically or via a background global hotkey listener powered by `pynput` (default `Ctrl+Alt+Shift+K`, configurable).
- Closes the active browser session, terminates pending tool dispatches, and prevents further execution.
- **Operating System Permissions & Limitations**:
  - **Windows**:
    - Uses Win32 low-level hooks (`SetWindowsHookExW`). Standard user applications require no extra permissions; however, Windows UIPI (User Interface Privilege Isolation) restricts hook interception and input injection when an elevated (Administrator) window has focus unless Jarvis is also run as Administrator.
    - *Locked/Headless Workstations*: The Windows Display Driver disables `BitBlt` screen capture when the workstation is locked or running in a headless/non-interactive background service. Active desktop access is required for screen reading.
  - **macOS**: Requires explicit Accessibility permission (`System Settings -> Privacy & Security -> Accessibility`) to intercept Quartz event taps and inject mouse/keyboard events, as well as Screen Recording permission.
  - **Linux**: Requires an active X11 session. Native Wayland compositors restrict global key interception and screen capture by design.

---

## Tools Registry

### Read-Only Tools (Phase 2, AUTO Tier)
1. **`list_directory(path=".")`**: Lists file and directory entries, file sizes, and types within allowed roots.
2. **`read_text_file(path, max_bytes=100000)`**: Reads text contents up to `max_bytes`. Marked with `untrusted_output = True` to flag session taint.
3. **`search_files(root=".", name_pattern="*", max_results=50)`**: Recursive file search constrained by path guards.
4. **`system_info()`**: Queries CPU usage %, available and total memory, disk free space, and battery status via `psutil`.
5. **`current_time()`**: Returns local and UTC timestamps.

### State-Altering Tools (Phase 3, CONFIRM Tier)
1. **`create_text_file(path, content)`**: Creates or overwrites a text file. Confirmation text explicitly warns if the target file already exists ("Overwrites: yes").
2. **`move_path(src, dst)`**: Moves/renames a file or directory within allowed roots.
3. **`copy_path(src, dst)`**: Copies a file or directory within allowed roots.
4. **`move_to_trash(path)`**: Safely sends files or directories to the OS Recycle Bin / Trash via `send2trash`. No permanent delete tool exists.
5. **`open_app(name)`**: Launches an application configured in `config.yaml` (`apps:` section). Disallowed applications are rejected.
6. **`open_path(path)`**: Opens a document or folder using Windows default handler via `os.startfile`.
7. **`run_command(command_id, args, cwd)`**: Executes fixed commands allowlisted in `config.yaml` (`commands:` section) with regex argument validation, timeout limits, and 10 KB output truncation. Non-allowlisted commands are BLOCKED.

### Browser Tools (Phase 5, Playwright Python)
All web navigation uses a dedicated, isolated browser profile directory (`.browser_profile/` by default) so the agent never touches personal browser cookies, history, or saved passwords.
1. **`open_url(url)`** (`CONFIRM`): Navigates the browser to an HTTP/HTTPS URL. Rejects non-HTTP schemes (`file://`, `javascript:`, `data:`, etc.) and enforces configurable domain allowlists/denylists.
2. **`get_page_text()`** (`AUTO`, `untrusted_output=True`): Extracts visible textual content, truncated to a safe 20 KB limit. Marks session context as tainted.
3. **`list_links()`** (`AUTO`, `untrusted_output=True`): Extracts clickable links (anchor text and target URL). Marks session context as tainted.
4. **`click_element(selector)`** (`CONFIRM`): Clicks an element matching a CSS selector or description. Automatically detects and BLOCKS purchase/payment buttons and file downloads.
5. **`type_into(selector, text)`** (`CONFIRM`): Fills text into an input field. Strictly refuses and BLOCKS typing into password fields (`type="password"`, `autocomplete="password"`, etc.) or credit card/payment fields.
6. **`screenshot_page(name)`** (`AUTO`): Captures page screenshot and saves under `logs/screens/`.

#### Browser Security Policies
- **Strict Scheme Validation**: Only `http://` and `https://` URLs are accepted. `file://`, `javascript:`, `data:` and internal URI schemes are rejected.
- **Isolated Profile**: Uses a separate directory under the project root (`.browser_profile`), keeping personal user accounts and logins completely untouched.
- **Download Prevention**: `accept_downloads=False` and active download listeners automatically cancel all file download attempts.
- **Payment & Purchase Block**: Automated purchases, checkouts, and credit card form entries are detected and BLOCKED with a clear safety message.
- **Password Protection**: Input fields detected as password inputs (`type="password"`, `autocomplete="current-password"`, etc.) are completely refused.
- **Tainted Data Safeguard**: All web page content is treated as untrusted data (`untrusted_output=True`). Directives inside pages (e.g. prompt injection attempts) are disregarded per the system prompt, and any subsequent CONFIRM tools require explicit user confirmation displaying the taint warning.
- **Clean Shutdown**: Shuts down Playwright and persistent browser context cleanly on application exit and on emergency stop (`Ctrl+Alt+Shift+K`).

### GUI Control Tools (Phase 6, Mouse & Keyboard Automation)
Phase 6 enables desktop interaction via native screen capture and input emulation under strict safety constraints:
1. **`take_screenshot(monitor=1)`** (`AUTO`, `untrusted_output=True`): Captures the specified monitor using `mss`, downscales to `max_screen_width` (default 1280px) via Pillow (`LANCZOS`), saves a thumbnail under `logs/screens/`, and returns PNG bytes with scaling metadata. Sessions are marked tainted.
2. **`gui_click(x, y, button="left")`** (`CONFIRM`): Maps model coordinates back to physical monitor pixels using the stored scale factor and monitor offset, executes a mouse click via `pyautogui`, and captures a visual audit trail thumbnail.
3. **`gui_type(text)`** (`CONFIRM`): Inspects the active foreground window title against the denylist (`denied_window_titles`). If safe, types text via keyboard and logs an audit thumbnail. Refuses execution if foreground window matches sensitive applications.
4. **`gui_key(key)`** (`CONFIRM`): Presses a single keyboard key (e.g. `enter`, `tab`, `esc`, `down`) and captures an audit thumbnail.
5. **`gui_scroll(amount)`** (`CONFIRM`): Scrolls mouse wheel (positive for up, negative for down) and logs an audit thumbnail.

#### Coordinate Transformation Math
A screenshot of physical monitor dimensions ($W_{\text{orig}}, H_{\text{orig}}$) at screen offset ($L, T$) is downscaled by factor:
$$\text{scale\_factor} = \min\left(1.0, \frac{\text{max\_screen\_width}}{W_{\text{orig}}}\right)$$
When the model predicts coordinates ($x_{\text{model}}, y_{\text{model}}$) on the downscaled image:
$$x_{\text{rel}} = \text{round}\left(\frac{x_{\text{model}}}{\text{scale\_factor}}\right), \quad y_{\text{rel}} = \text{round}\left(\frac{y_{\text{model}}}{\text{scale\_factor}}\right)$$
$$x_{\text{screen}} = L + x_{\text{rel}}, \quad y_{\text{screen}} = T + y_{\text{rel}}$$
Coordinates are strictly clamped to physical monitor bounds:
$$L \le x_{\text{screen}} \le L + W_{\text{orig}} - 1, \quad T \le y_{\text{screen}} \le T + H_{\text{orig}} - 1$$

#### Safety & Approval Modes
- **Step Mode (`step`, default)**: Every individual GUI action prompts the user for explicit confirmation (`[y/N]`).
- **Session Mode (`session`)**: Confirms once for a bounded session: maximum 20 actions or 5 minutes (whichever comes first), after which approval expires. Displays a persistent terminal banner on each action showing remaining actions and time.
- **Fail-Safe Mechanism**: `pyautogui.FAILSAFE = True` is strictly enforced. Slamming the mouse cursor into any screen corner immediately aborts execution.
- **Sensitive Window Denylist**: Refuses typing if the active window title contains blacklisted keywords (e.g., `1password`, `bitwarden`, `bank`, `vault`, `keepass`).
- **Visual Audit Trail**: Every GUI action saves a downscaled thumbnail to `logs/screens/` and records `thumbnail_path` in `logs/audit.log`.

#### Evaluation of Provider Dedicated Computer-Use Tool
Anthropic's beta computer use tool (`computer_20241022`) requires specific beta headers and bundles all actions into a single unconstrained tool schema without granular risk tiers. Jarvis instead provides modular, typed tool specifications (`take_screenshot`, `gui_click`, `gui_type`, etc.) with code-level risk tiers, audit logging, window title denylisting, fail-safe corners, and bounded session approvals.

### Memory & Routine Tools (Phase 7)
Phase 7 adds small, transparent local memory and background scheduled routines:

1. **`remember(text)`** (`CONFIRM`): Stores a user-provided fact into SQLite. Explicit user invocation only; strictly rejects sensitive patterns (passwords, API keys, payment card numbers).
2. **`recall(query)`** (`AUTO`): Searches saved facts matching a query string.
3. **`list_memories()`** (`AUTO`): Lists all active remembered facts.
4. **`forget(id)`** (`CONFIRM`): Deletes a remembered fact by ID.

#### Memory Architecture & Safeguards
- **Transparent SQLite Storage**: Stored locally in `data/memory.db` across two tables:
  - `facts`: `id` (INTEGER PRIMARY KEY), `text` (TEXT), `created_at` (TEXT UTC ISO), `tainted` (INTEGER DEFAULT 0).
  - `conversations`: `id` (INTEGER PRIMARY KEY), `ts` (TEXT UTC ISO), `role` (TEXT), `content` (TEXT).
- **Safe Schema Migration**: Databases created with prior schemas automatically migrate via `PRAGMA table_info` and `ALTER TABLE facts ADD COLUMN tainted INTEGER DEFAULT 0` with zero data loss.
- **Taint Marker on Facts**: Facts saved during a tainted session turn have `tainted = 1`.
- **Tainted Context Labelling**: Injected memory context explicitly labels such facts as `[saved during a turn that read untrusted content]` inside `<user_provided_memory_context>`.
- **Zero-Storage Invariant**: When `memory.enabled = False` in `config.yaml`, no database file or directory is created on disk.
- **Secret-Refusal Pattern Safeguard**: Automatically inspects text on store attempts and refuses to record sensitive credentials:
  - Credit/Debit cards (13–19 digits matching major card patterns).
  - API keys and tokens (`sk-ant-`, `sk-`, `ghp_`, `glpat-`, `xox-`, JWT prefixes).
  - Password and credential assignments (`password=`, `pwd:`, `secret:`, etc.).
- **Conversation & Tool-Result Redaction**: Every message and tool result logged to `conversations` is scanned with `detect_sensitive_pattern` and has sensitive spans replaced with `[REDACTED]`. Raw secrets never enter `memory.db` or `jarvis memory export`.
- **Clear Context Injection**: Recalled facts are injected into Brain context labelled as `<user_provided_memory_context>`, capped strictly to `max_context_chars` (default 2000 chars) to prevent context exhaustion.
- **Conversation Windowing**: Retains the last $N$ turns (`history_turns` in config) in prompt context, with summarization turned OFF.

#### Scheduled Routines (APScheduler)
- **`RoutinesManager`**: Runs in a background thread using `APScheduler.schedulers.background.BackgroundScheduler`.
- **Daily Briefing**: Configurable at `routines.briefing_time` (e.g., `"08:30"`).
- **Bounded Briefing Scan**: File scanning is strictly bounded by `routines.max_files_scanned` (default 500) and `routines.scan_timeout_seconds` (default 5.0s). If a limit is reached, a notice is explicitly included in the briefing text.
- **Symlink Protection**: Directory and file symlinks leaving `allowed_roots` are verified via `resolve_allowed` and never followed.
- **Strict AUTO Tier Invariant**: Routines are strictly prohibited from executing `CONFIRM` or `BLOCKED` tools. Only `AUTO` read-only tools (`current_time`, `system_info`, disk space, and files modified today) are permitted. Any non-AUTO tool invocation raises `PermissionError`.
- **Channel Delivery**: Delivers briefings through the active `OutputChannel` (`say` or `send`).

---

## Voice Channel (Phase 4)

Jarvis features a local, privacy-first voice communication channel implementing `InputChannel`, `OutputChannel`, and `Confirmer`.

### 1. Push-to-Talk (PTT) and Wake Word Modes
- **Push-to-Talk (`--ptt`)**: Hold down a configurable key (default `space`) while speaking; release to transcribe and execute.
- **Wake Word (`--wake`)**: Continuously monitors audio stream using `openWakeWord` with `onnxruntime`. Upon wake word detection, records speech and uses silence Voice Activity Detection (VAD) to detect the end of the utterance.
- **Echo / Self-Hearing Prevention**: While TTS is speaking aloud, microphone audio capture is automatically muted (`AudioCapture.set_muted(True)`), preventing the agent from hearing or looping its own speech.

### 2. Speech-to-Text (STT) Tradeoffs: Faster-Whisper
STT uses `faster-whisper` running locally (int8 quantized on CPU or CUDA).

| Model Size | Relative Speed | Accuracy (WER) | VRAM / RAM | Recommended Usage |
|------------|----------------|----------------|------------|-------------------|
| `tiny`     | ~4x faster     | Good (~85%)    | ~200 MB    | Ultra-fast local voice queries, low-power machines |
| `base`     | ~2.5x faster   | Great (~92%)   | ~350 MB    | **Default recommendation**: best speed vs. accuracy balance |
| `small`    | Baseline (1x)  | High (~95%)    | ~800 MB    | Accurate technical / code dictation |
| `medium`   | ~0.4x speed    | Very High      | ~2.2 GB    | Multilingual or high-noise environments |
| `large-v3` | ~0.2x speed    | State-of-the-art | ~4.5 GB  | High-end GPU workstations |

### 3. Text-to-Speech (TTS): Piper & pyttsx3
Two local, zero-network TTS engines are supported:
- **`pyttsx3`**: System speech synthesis (Windows SAPI5). Zero download required; starts immediately.
- **`piper`**: Fast local neural TTS using Piper ONNX.
  - To download a high-quality voice model (e.g. `en_US-lessac-medium`):
    ```powershell
    Invoke-WebRequest -Uri "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx" -OutFile "models/en_US-lessac-medium.onnx"
    Invoke-WebRequest -Uri "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json" -OutFile "models/en_US-lessac-medium.onnx.json"
    ```
  - Set `tts_engine: "piper"` and paths in `config.yaml`.

### 4. Verified Pre-Trained Wake Words
`openWakeWord` bundles pre-trained models. The following official models are verified and supported:
- `hey_jarvis_v0.1.onnx` (Default: "Hey Jarvis")
- `alexa_v0.1.onnx`
- `hey_mycroft_v0.1.onnx`
- `hey_rhasspy_v0.1.onnx`
- `timer_v0.1.onnx`
- `weather_v0.1.onnx`

### 5. Strict Voice Confirmation & Dual-Factor Authentication
To prevent accidental or hijacked execution of sensitive actions via voice:
1. The agent speaks the exact confirmation prompt aloud.
2. Only the exact spoken words **`"yes confirm"`** or **`"no"`** are accepted. Any ambiguous response (`"yes"`, `"ok"`, `"sure"`, noise, or silence past `confirm_timeout_seconds`) evaluates to **NO**.
3. **Dual-Factor Requirement**: For any action whose description contains **`"overwrite"`**, **`"move"`**, or an untrusted **taint warning**, the user must **ALSO** press a physical key (default `enter`, configurable as `dual_confirm_key`). Speaking `"yes confirm"` without the physical key press strictly fails closed and denies the action.

---

## Web HUD Channel (Phase 8)

Jarvis features a local, zero-build web HUD interface operating as an `InputChannel`, `OutputChannel`, and `Confirmer` simultaneously alongside the CLI and Voice channels.

### 1. Architecture & Local Security
- **Loopback Only**: The FastAPI / Starlette server binds strictly to `127.0.0.1` (`localhost`).
- **Host & Origin Header Validation**: The server validates both the `Host` header (strictly accepting only `127.0.0.1:<port>` and `localhost:<port>`) and `Origin` header. External hosts or origins are rejected with HTTP `400 Bad Request` or `403 Forbidden` / WebSocket closure code `1008`.
- **URL Fragment Token Authentication**: The session token is transmitted exclusively in the URL fragment (`#token=<token>`). It never appears in HTTP request lines, query strings, or server access logs. The HUD JavaScript reads the token from `location.hash` and immediately clears it from the address bar with `history.replaceState`.
- **Query String Prohibitions**: Any token passed in a query string (`?token=...`) on HTTP or WebSocket is strictly rejected with HTTP `400 Bad Request` or closure code `1008`.
- **Content-Security-Policy (CSP)**: Enforces `default-src 'self'` with zero inline scripts (`script-src 'self'`) and zero wildcard CORS.
- **Zero-Token Logging**: Uvicorn access logging is disabled, and the server logger strictly logs host/port without token strings.
- **Pre-Broadcast Secret Redaction**: All messages and live audit events pass through `redact_sensitive_text` prior to WebSocket transmission, guaranteeing credentials, card numbers, and tokens never leak to the browser.
- **Zero-Build Static Assets**: Plain HTML5, CSS3, and ES6 JavaScript located in `src/jarvis/ui/static/`. No Node.js build step or remote CDN dependencies.

### 2. Live Interface Components
- **Telemetry Bar**: Live CPU %, RAM %, and Free Disk GB stream over WebSocket every 2 seconds.
- **Agent State Indicator**: Visual badge reflecting live status (`idle`, `listening`, `thinking`, `acting`, `halted`).
- **Transcript Feed**: Live chat transcript displaying user inputs and agent responses in real time.
- **Audit Feed**: Streaming real-time JSONL audit event log showing every tool call, decision, and result summary.
- **Modal Confirmation Dialog**: Prompts for user approval (`Approve` / `Deny`) for `CONFIRM` tier actions. Displays the exact proposed action and a high-visibility amber banner if the session is tainted by untrusted external data.
- **Emergency Kill Switch**: Prominent red button in the HUD header directly trips the orchestrator's `KillSwitch`.
- **Ambient Canvas Animation**: Lightweight particle/waveform animation capped to 30 FPS and automatically paused when the browser tab is hidden using the Page Visibility API (`document.hidden`).

### 3. Multi-Channel Confirmation Racing
When running multiple channels together (e.g. `jarvis run` with HUD, CLI, and Voice enabled), confirmations are handled by `CompositeConfirmer`:
- All active confirmers present the prompt simultaneously.
- The **first channel to respond wins** (whether Approve or Deny).
- The remaining confirmers are immediately dismissed (`dismiss()`), closing open dialogs and resetting prompts.

---

## Threat Model

Jarvis operates directly on the user's host operating system. The security architecture assumes that the underlying LLM can be manipulated by prompt injection (direct or indirect) and enforces hard boundaries in deterministic Python code.

### 1. System Capabilities
- Read, search, and list files within configured directory roots.
- Create, move, copy, and recycle files within configured directory roots.
- Launch explicitly allowlisted desktop applications.
- Execute explicitly allowlisted terminal commands with regex-validated arguments.
- Navigate web pages and interact with forms in an isolated browser profile.
- Capture screenshots, move the mouse cursor, and simulate keystrokes on interactive desktop sessions.
- Persist transparent user-provided facts and redacted conversation logs in local SQLite.
- Run scheduled daily briefings restricted strictly to read-only `AUTO` tools.
- Serve a local Web HUD on loopback with real-time telemetry and audit feeds.

### 2. Hard Boundaries & Invariants
- **Write Actions Always Require Confirmation**: State-altering tools are statically bound to the `CONFIRM` tier in Python. No prompt, tool output, or agent instruction can bypass user approval.
- **Path Guard Invariant**: All filesystem access must resolve to a subpath within `allowed_roots`. Symlink traversals, NTFS junctions, and path navigation (`..`) outside allowed roots are blocked.
- **No Password or Payment Field Interaction**: Browser automation strictly refuses typing into fields detected as passwords, credit cards, CVVs, or checkouts. Automated downloads are blocked.
- **No Privilege Escalation**: Jarvis runs with the exact OS permissions of the launching user account and cannot elevate beyond them.
- **Zero Raw Secrets in Logs or Broadcasts**: All audit records, conversation histories, and WebSocket broadcasts redact API keys, card numbers, and passwords.
- **No Unsafe Execution**: Jarvis never executes arbitrary shell strings (`shell=False` strictly enforced) and never runs `eval()` or `exec()`.

### 3. Disabling Tool Families in `config.yaml`
Every tool family can be completely disabled in `config.yaml`:
- **Web Navigation**: Remove browser tools or set `browser.headless: true`.
- **GUI Control**: Set `gui.approval_mode: "step"` or clear tool registrations.
- **Shell Commands**: Set `commands: {}` (empty allowlist blocks all commands).
- **Application Launching**: Set `apps: {}` (empty allowlist blocks all launches).
- **Scheduled Routines**: Set `routines.enabled: false`.
- **Memory**: Set `memory.enabled: false` (zero-storage invariant: no database created).
- **Web HUD**: Set `hud.enabled: false`.
- **Voice Channel**: Set `voice.enabled: false`.

---

## Packaging & Distribution

Jarvis supports two deployment strategies on Windows:

### Path (a): `pipx` / `venv` Installation (Recommended)
This is the recommended, lightweight deployment model:
1. Clone the repository and enter the directory:
   ```powershell
   git clone <repo-url>
   cd Jarvis
   ```
2. Create and activate a virtual environment:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```
3. Install the package in editable mode:
   ```powershell
   pip install -e .
   ```
   *Alternatively, install globally in an isolated environment via pipx:*
   ```powershell
   pipx install .
   ```
4. Verify the CLI installation:
   ```powershell
   jarvis --help
   ```

### Path (b): Standalone Executable with PyInstaller (Known Caveats)
A single-binary distribution can be produced using PyInstaller; however, note the following important technical caveats:
- **Hidden Dynamic Imports**: Jarvis loads platform adapters and tool modules dynamically. The PyInstaller spec must include explicit `--hidden-import` entries for `fastapi`, `uvicorn`, `playwright`, `sounddevice`, `faster_whisper`, `openwakeword`, and `apscheduler`.
- **Binary & Model Bloat**: Bundling Playwright browser binaries, Piper TTS models, and ONNX wake word models inflates the single-file executable to 500+ MB.
- **Cold Startup Latency**: PyInstaller single-file mode (`--onefile`) extracts runtime binaries to a temporary directory (`%TEMP%\_MEIxxxxxx`) on every launch, resulting in a noticeable 3–6 second startup delay.

### Windows Autostart Configuration
By default, Jarvis does **not** install background startup entries. To configure optional autostart on Windows:

#### Method 1: Windows Task Scheduler (Recommended)
1. Open PowerShell and create a scheduled task to launch `jarvis run` at user logon:
   ```powershell
   $Action = New-ScheduledTaskAction -Execute "d:\My Projects\Jarvis\.venv\Scripts\jarvis.exe" -Argument "run"
   $Trigger = New-ScheduledTaskTrigger -AtLogOn
   Register-ScheduledTask -TaskName "JarvisDesktopAgent" -Action $Action -Trigger $Trigger -Description "Jarvis AI Desktop Assistant"
   ```
2. To disable autostart:
   ```powershell
   Unregister-ScheduledTask -TaskName "JarvisDesktopAgent" -Confirm:$false
   ```

#### Method 2: Windows Startup Folder
1. Create a shortcut to `jarvis.exe run` (or a `.bat` wrapper) in your personal Startup directory:
   ```powershell
   explorer.exe shell:startup
   ```
2. To disable, delete the shortcut from that folder.

> [!WARNING]
> Running Jarvis as an unattended background service is not recommended. Screen capture (`mss`), keyboard injection (`pyautogui`), and hotkey monitoring require an active interactive Windows desktop session.

---

## CLI Usage

### Unified Multi-Channel Agent
Run Jarvis with all configured channels enabled (CLI, Web HUD, and optional Voice):
```powershell
# Standard run (starts CLI + Web HUD)
jarvis run

# Run with Voice channel enabled concurrently
jarvis run --voice

# Run CLI only without the Web HUD
jarvis run --no-hud

# Dry-run mode (simulates state-altering actions without writing to disk)
jarvis run --dry-run
```

When `jarvis run` starts, it outputs the authenticated Web HUD URL:
```text
[HUD] Web HUD running at: http://127.0.0.1:8000/#token=<generated_token>
```

### Interactive Text Chat (CLI Only)
```powershell
jarvis chat
```

### Voice Channel Modes
Run in Push-to-Talk mode (default, hold Space to speak):
```powershell
jarvis voice --ptt
```

Run in Wake Word mode ("Hey Jarvis"):
```powershell
jarvis voice --wake
```

Run Voice in Dry-Run mode:
```powershell
jarvis voice --dry-run
```

### Audit Log Cryptographic Verification
Verify the SHA-256 cryptographic hash chain of the audit log:
```powershell
# Verify audit log integrity
jarvis audit verify

# Verify a specific audit log file
jarvis audit verify --log logs/audit.log
```

> [!NOTE]
> On clean shutdown, Jarvis logs a `session_shutdown` event and prints the final `record_hash` in the console. The audit log is **tamper-evident against accidental or partial modification; an attacker who can rewrite the whole file can recompute the chain**. To protect against full file rewriting, we recommend noting the final hash outside the machine.

### Memory Inspection & Management
Inspect, delete, or export remembered facts directly from the command line:
```powershell
# List all remembered facts (shows ID, tainted flag, text, creation timestamp)
jarvis memory list

# Delete a specific memory fact by ID
jarvis memory delete --id 1

# Wipe all stored memories (prompts for confirmation)
jarvis memory delete --all

# Export memory facts and conversation history to JSON
jarvis memory export --output data/memory_export.json
```

Configure your API keys in your environment:
```powershell
# For Anthropic Claude
$env:ANTHROPIC_API_KEY = "sk-ant-..."

# For Groq
$env:GROQ_API_KEY = "gsk_..."
```

### Brain Inspection & Model Capabilities
Inspect available Groq models, context windows, and tool/vision capabilities directly from the CLI:
```powershell
jarvis brain models
```

---

## Testing

Run all unit, safety, voice, browser, GUI, memory, routines, HUD, and packaging integration tests:
```powershell
pytest -v
```

---

## Phase Scaffold History
- **Phase 0**: Project scaffold, pinned dependencies, `src` layout, core contracts, and platform adapter stubs.
- **Phase 1**: Safety layer with path guard, permission engine, taint tracking, audit logger, kill switch, and CLI confirmer.
- **Phase 2**: Text-only agent loop with read-only tools, Anthropic Claude Brain SDK integration, FakeBrain for deterministic testing, and `jarvis chat` CLI.
- **Phase 3**: State-altering tools (files write/move/copy/trash via `send2trash`), app and shell command allowlists, Windows OS adapter, exact human-readable confirmation descriptions, and `--dry-run` simulation flag.
- **Phase 4**: Voice as a Channel (`VoiceChannel`, `InputChannel`, `OutputChannel`, `Confirmer`), local STT (`faster-whisper`), local TTS (`pyttsx3` and `piper-tts`), push-to-talk, wake word detection (`openWakeWord`), strict spoken confirmation parser, and dual-factor physical key protection for destructive/tainted actions.
- **Phase 5**: Browser tools using Playwright (`open_url`, `get_page_text`, `list_links`, `click_element`, `type_into`, `screenshot_page`), isolated browser profile, scheme and domain validation, password/payment/download safety blocks, 20 KB page text truncation, tainted content handling, and clean shutdown on kill switch and exit.
- **Phase 6**: GUI control tools using `mss` and `pyautogui` (`take_screenshot`, `gui_click`, `gui_type`, `gui_key`, `gui_scroll`), HiDPI and multi-monitor coordinate math, Pillow screenshot downscaling, thumbnail visual audit trails, `step` vs bounded `session` approval modes, foreground window denylist blocking, and immediate killswitch aborts.
- **Phase 7**: Small, transparent local memory (`sqlite3` facts & conversations, secret-refusal safeguards, `remember`/`recall`/`list_memories`/`forget` tools, `jarvis memory` CLI subcommands, context injection capped to character budget, last $N$ turns history windowing) and scheduled background routines (`APScheduler` daily briefing restricted strictly to `AUTO` tier read-only tools).
- **Phase 7.1**: Hardening patch for memory and routines (conversation turn and tool-result secret redaction preventing leaks in DB and `jarvis memory export`, safe SQLite schema migration for `tainted` column on `facts`, tainted fact labelling in prompt context injection and CLI table, and bounded briefing file scans with symlink escape prevention and in-report limit notices).
- **Phase 8**: Local Web HUD as a Channel & Packaging (`ui/server.py` FastAPI app + Starlette WebSocket bound strictly to `127.0.0.1`, cryptographic session token authentication, strict local `Origin` header validation, zero-build dark HUD UI with live telemetry, transcript, streaming audit feed, confirmation modal, 30 FPS ambient canvas, `CompositeConfirmer` multi-channel racing, visible emergency kill switch button, `jarvis run` command, pre-broadcast secret redaction, and `pip-audit` vulnerability auditing).
- **Phase 8.1**: Hardening patch for HUD and test hygiene (URL fragment `#token=<token>` authentication, immediate address bar cleanup via `history.replaceState`, query string token prohibition, strict `Host` header validation, Content-Security-Policy with zero inline scripts and no wildcard CORS, zero-token server logging, cryptographic SHA-256 audit log chaining with `jarvis audit verify`, real Windows NTFS junction escape testing without mocks, and Python 3.14 dependency import verification).
- **Phase 8.2**: Hardening patch for safety boundaries (Protected paths enforcement locking `config.yaml`, `AGENT_RULES.md`, `SECURITY.md`, `src/`, `logs/`, `data/`, `.browser_profile/`, and `.env` against WRITE/MOVE/COPY-INTO/DELETE/TRASH and unapproved reads; read-safe allowlist for `README.md`; junction and `..` resolution; immutable config schema via `JarvisConfig(frozen=True)` and `MappingProxyType`; removal of synthetic `testserver` Host from production paths; unauthenticated WebSocket isolation and timeout drop; removal of `'unsafe-inline'` from CSP `style-src`; audit clean shutdown hash logging and tamper-evidence documentation).
- **Phase 9**: Groq Brain integration (`brain/groq_brain.py` utilizing official `groq` SDK 1.7.0, OpenAI-compatible chat completions, startup model existence validation, `jarvis brain models` Rich table CLI command, sequential multi-tool execution through `PermissionEngine`, token budgeting with `max_context_tokens` pruning, 4 KB tool output truncation, compact tool schemas, HTTP 429 Retry-After parsing and backoff with `max_wait_seconds` user announcement via active `OutputChannel`, zero-retry 401/403 rejection, `supports_images` multimodal gating with disabled screen capture fallback, and strict zero-leakage API key protection across logs, memory DB, exports, audit records, and exceptions).


