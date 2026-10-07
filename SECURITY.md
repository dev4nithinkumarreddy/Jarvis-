# Security Policy & Access Revocation Guide

Jarvis operates on a defense-in-depth safety architecture with hardcoded permission tiers, path boundaries, and session taint tracking. This document describes security practices, emergency revocation procedures for all capabilities, and how to report vulnerabilities.

---

## 1. Access Revocation Procedures

If an anomalous state occurs, or if credentials, permissions, or local states need to be revoked immediately, follow the procedures below:

### A. Emergency Kill Switch (Immediate Halt)
- **Global Keyboard Shortcut**: Press `Ctrl+Alt+Shift+K` (default; configurable in `config.yaml` as `kill_switch.hotkey`).
- **Web HUD**: Click the visible red **"EMERGENCY KILL SWITCH"** button in the top navigation bar.
- **REST / WebSocket**: Send `POST /api/kill` with header `X-Session-Token: <token>` or emit `{"type": "kill"}` over the WebSocket.
- **Effect**:
  - Immediately trips `KillSwitch.trip()`.
  - Aborts pending and ongoing tool executions.
  - Automatically terminates any open Playwright browser sessions and GUI interactions.
  - Rejects any subsequent commands until the process is restarted.

### B. Anthropic API Key Revocation & Rotation
- **Environment Variable**: Jarvis reads your API key exclusively from the `ANTHROPIC_API_KEY` environment variable; it is never stored in config files.
- **To Revoke / Rotate**:
  1. Navigate to your [Anthropic Console](https://console.anthropic.com/settings/keys).
  2. Locate the active key used by Jarvis and click **Delete / Revoke**.
  3. Generate a new key and update your local environment:
     ```powershell
     # Windows PowerShell
     [System.Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-new...", "User")
     ```
  4. Restart your terminal or shell session.

### C. Invalidate Web HUD Sessions
- The Web HUD generates a cryptographically secure 256-bit URL-safe token at startup (`secrets.token_urlsafe(32)`).
- The token is held in-memory and is never persisted to disk or logged.
- The token is placed exclusively in the URL fragment (`#token=<token>`) so it is never transmitted in HTTP request lines, query strings, or server access logs. The browser page immediately strips it from the address bar via `history.replaceState`.
- **To Revoke Active HUD Sessions**:
  1. Restart Jarvis (`Ctrl+C` in the host terminal, then re-run `jarvis run` or `jarvis chat`).
  2. All prior browser tabs and WebSockets will immediately fail authentication with code `1008: Unauthorized` or HTTP `401 Unauthorized`.
  3. Access the newly generated URL printed in your terminal: `http://127.0.0.1:8000/#token=<new_token>`.

### D. Delete Memory Database
- Facts and conversation logs are stored in a local SQLite file (`data/memory.db`).
- **To Inspect and Wipe Memories**:
  ```powershell
  # Check active stored memories
  jarvis memory list

  # Wipe all stored memories via CLI (clears facts table)
  jarvis memory delete --all
  ```
- **To Completely Purge the Memory Database File**:
  ```powershell
  # Stop Jarvis, then remove the SQLite database entirely
  Remove-Item -Force "data/memory.db"
  ```
- **To Permanently Disable Memory**:
  In `config.yaml`, set:
  ```yaml
  memory:
    enabled: false
  ```
  When disabled, Jarvis enforces a zero-storage invariant and will never open, create, or query any SQLite database on disk.

### E. Wipe Isolated Browser Profile & Cache
- Web navigation uses an isolated browser user data directory located at `.browser_profile/` under the project root. Jarvis never interacts with your primary system browser profiles.
- **To Purge Cookies, Cache, and Browser History**:
  ```powershell
  # Ensure Jarvis is stopped
  Remove-Item -Recurse -Force ".browser_profile"
  ```
- Next time browser tools run, a fresh, clean profile will be created.

### F. Disable Tool Families in Configuration
Every capability can be individually and permanently disabled by editing `config.yaml`:
- **Web Navigation**:
  ```yaml
  browser:
    headless: true       # or remove browser tool specifications
  ```
- **GUI Desktop Automation**:
  Set `denied_window_titles` to block sensitive apps, or set approval mode to `step`:
  ```yaml
  gui:
    approval_mode: "step"   # Enforce manual confirmation for every single click/type
  ```
- **Shell Commands**:
  Jarvis ships with an empty shell allowlist by default. To disable shell execution completely:
  ```yaml
  commands: {}
  ```
- **Applications Launch**:
  To prevent launching desktop applications:
  ```yaml
  apps: {}
  ```
- **Scheduled Background Routines**:
  ```yaml
  routines:
    enabled: false
  ```
- **Web HUD**:
  ```yaml
  hud:
    enabled: false
  ```
- **Voice Channel**:
  ```yaml
  voice:
    enabled: false
  ```

---

## 2. Hard Security Boundaries

1. **Strict Permission Tiers**: State-altering tools are hardcoded to the `CONFIRM` tier in Python and cannot be downgraded to `AUTO` by prompt injection or model instructions.
2. **Loopback Only & Host Header Verification**: The HUD server strictly binds to `127.0.0.1` and enforces both same-origin verification and strict `Host` header checks (accepting only `127.0.0.1:<port>` and `localhost:<port>`). Non-local hosts/origins receive HTTP `400 Bad Request` or `403 Forbidden` / WebSocket code `1008`.
3. **URL Fragment Authentication & Prohibited Query Strings**: Tokens are delivered exclusively via fragment `#token=<token>` and transmitted via WebSocket auth payloads or `X-Session-Token` headers. Any query string containing a token is rejected with HTTP `400 Bad Request`.
4. **Content-Security-Policy**: Enforces `default-src 'self'` with zero inline scripts (`script-src 'self'`) and zero wildcard CORS.
5. **Cryptographic Audit Log Verification & Tamper-Evidence**: Every audit record is linked via a SHA-256 hash chain (`prev_hash`, `record_hash`). On clean shutdown, Jarvis logs a `session_shutdown` event and prints the final `record_hash`. The audit log is **tamper-evident against accidental or partial modification; an attacker who can rewrite the whole file can recompute the chain**. To protect against an attacker rewriting the entire file, we recommend noting the final hash outside the machine. The integrity of the audit log can be verified at any time with `jarvis audit verify`.
6. **Redaction Prior to Broadcast / Logging**: Sensitive patterns (API keys, card numbers, passwords) are scrubbed to `[REDACTED]` prior to writing to `logs/audit.log`, `memory.db`, or broadcasting over WebSocket feeds.
7. **No Code Execution**: Jarvis never uses `eval()`, `exec()`, or `shell=True`. All command executions pass through predefined allowlists with strict argument validation.
8. **Protected Paths & Safety Boundaries (Phase 8.2)**: `safety.protected_paths` explicitly locks critical project files and directories (`config.yaml`, `AGENT_RULES.md`, `SECURITY.md`, `src/`, `logs/`, `data/`, `.browser_profile/`, and any `.env` files). All WRITE, MOVE, COPY-INTO, DELETE, and TRASH operations targeting protected paths are strictly refused even when inside `allowed_roots`. Reading protected paths is refused except for explicitly allowlisted `safety.read_safe_files` (default: `README.md`). All symlinks and Windows NTFS directory junctions resolve canonical targets prior to checking.
9. **Config & Allowlist Immutability**: Configuration allowlists (`commands` and `apps`) are loaded only at startup and wrapped in read-only mapping proxies (`MappingProxyType`) with `JarvisConfig(frozen=True)`. No tool or runtime instruction can alter configuration or allowlists in memory; configuration changes strictly require restarting the process.

---

## 3. Reporting a Vulnerability

If you discover a security vulnerability or bypass in Jarvis's safety layer:
1. Do not open a public GitHub issue.
2. Please document the steps to reproduce the issue, including tool calls, configuration, and unexpected behavior.
3. Submit your report privately to the repository maintainers or contact the project security contact.
