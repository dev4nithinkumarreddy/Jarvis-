"""Configuration schema and loader for Jarvis."""

from __future__ import annotations

from pathlib import Path
from typing import Any
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class BrainConfig(BaseModel):
    """Configuration for the LLM brain provider and model."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(..., min_length=1, description="LLM provider name (e.g. anthropic, groq)")
    model: str = Field(..., min_length=1, description="LLM model name. No default hardcoded in code.")
    base_url: str = Field(
        default="https://api.groq.com",
        description="API base URL for Groq or OpenAI-compatible chat-completions endpoint",
    )
    max_tokens: int = Field(
        default=1024,
        gt=0,
        description="Maximum tokens for generation",
    )
    max_context_tokens: int = Field(
        default=6000,
        gt=0,
        description="Approximate maximum context tokens budget",
    )
    max_wait_seconds: float = Field(
        default=20.0,
        gt=0,
        description="Maximum wait time in seconds for HTTP 429 rate limit backoff",
    )
    tool_output_truncation_bytes: int = Field(
        default=4096,
        gt=0,
        description="Maximum byte length for tool output before truncation (default 4 KB for Groq)",
    )
    history_turns: int | None = Field(
        default=None,
        description="Optional override for conversation history turns (defaults to 4 for Groq, 10 for Anthropic)",
    )
    compact_tool_descriptions: bool = Field(
        default=True,
        description="Whether to compact tool descriptions to conserve token budget",
    )


class CommandConfig(BaseModel):
    """Configuration for an allowlisted shell command."""

    model_config = ConfigDict(extra="forbid")

    executable: str = Field(..., min_length=1, description="Command executable name or binary path")
    args_schema: list[str] = Field(
        default_factory=list,
        description="Regex patterns validating arguments in positional order",
    )
    timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        description="Execution timeout in seconds",
    )
    allowed_cwd: Path | None = Field(
        default=None,
        description="Optional directory constraint for command execution",
    )


class VoiceConfig(BaseModel):
    """Configuration for voice input, output, and wake word recognition."""

    model_config = ConfigDict(extra="forbid")

    input_device: int | str | None = Field(
        default=None,
        description="Audio input device index or substring name",
    )
    output_device: int | str | None = Field(
        default=None,
        description="Audio output device index or substring name (e.g. 'Speakers', 'Headphones')",
    )
    sample_rate: int = Field(default=16000, description="Audio capture sample rate in Hz (16 kHz)")
    stt_model: str = Field(default="base", description="Whisper model size (tiny, base, small, medium, large)")
    stt_device: str = Field(default="cpu", description="Compute device for faster-whisper (cpu, cuda)")
    language: str | None = Field(default="en", description="Spoken language code for Whisper STT (e.g. 'en')")
    tts_engine: str = Field(default="pyttsx3", description="Active TTS engine: 'piper' or 'pyttsx3'")
    piper_model_path: Path | None = Field(default=None, description="Path to local Piper ONNX model")
    piper_config_path: Path | None = Field(default=None, description="Path to local Piper JSON config")
    ptt_key: str = Field(default="space", description="Push-to-talk activation key")
    wake_word: str = Field(default="hey_jarvis_v0.1.tflite", description="Pre-trained openWakeWord model")
    wake_threshold: float = Field(default=0.5, ge=0.0, le=1.0, description="Wake word detection score threshold")
    silence_timeout_seconds: float = Field(default=1.5, gt=0, description="Silence timeout to terminate utterance")
    dual_confirm_key: str = Field(default="enter", description="Physical key required for high-risk voice confirmation")


class BrowserConfig(BaseModel):
    """Configuration for Playwright browser automation."""

    model_config = ConfigDict(extra="forbid")

    headless: bool = Field(default=True, description="Whether to run browser in headless mode")
    profile_dir: Path = Field(
        default=Path(".browser_profile"),
        description="Dedicated browser profile directory under project root",
    )
    allowed_domains: list[str] = Field(
        default_factory=list,
        description="Optional domain allowlist (empty allows any domain not denylisted)",
    )
    denied_domains: list[str] = Field(
        default_factory=list,
        description="Optional domain denylist",
    )
    screenshot_dir: Path = Field(
        default=Path("logs/screens"),
        description="Directory where screenshot_page stores screenshots",
    )
    timeout_ms: float = Field(
        default=30000.0,
        gt=0,
        description="Browser action timeout in milliseconds",
    )


class GUIConfig(BaseModel):
    """Configuration for OS desktop GUI automation."""

    model_config = ConfigDict(extra="forbid")

    approval_mode: str = Field(
        default="step",
        description="Approval mode: 'step' (confirm each action) or 'session' (bounded session)",
    )
    session_max_actions: int = Field(
        default=20,
        ge=1,
        description="Max actions allowed in a single confirmed GUI session",
    )
    session_timeout_seconds: float = Field(
        default=300.0,
        gt=0,
        description="Session validity period in seconds (default: 5 minutes)",
    )
    max_screen_width: int = Field(
        default=1280,
        gt=0,
        description="Max width for screenshot downscaling",
    )
    denied_window_titles: list[str] = Field(
        default_factory=lambda: [
            "1password", "bitwarden", "keepass", "lastpass", "dashlane",
            "bank", "paypal", "chase", "wells fargo", "citibank", "capital one",
        ],
        description="Foreground window title substrings where typing is strictly refused",
    )
    thumbnail_dir: Path = Field(
        default=Path("logs/screens"),
        description="Directory to save audit screenshot thumbnails",
    )


class MemoryConfig(BaseModel):
    """Configuration for local persistent SQLite memory store."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Enable or disable SQLite memory store",
    )
    db_path: Path = Field(
        default=Path("data/memory.db"),
        description="Path to SQLite memory database file",
    )
    max_context_chars: int = Field(
        default=2000,
        gt=0,
        description="Maximum characters of recalled facts to inject into prompt context",
    )
    history_turns: int = Field(
        default=10,
        gt=0,
        description="Number of recent conversation turns to keep in context window",
    )


class RoutinesConfig(BaseModel):
    """Configuration for scheduled routines (APScheduler)."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Enable or disable scheduled routines",
    )
    briefing_time: str = Field(
        default="09:00",
        description="Daily briefing execution time in HH:MM format",
    )
    max_files_scanned: int = Field(
        default=500,
        gt=0,
        description="Maximum number of files scanned when checking for modified files",
    )
    scan_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Timeout in seconds for scanning modified files in routines",
    )


class HUDConfig(BaseModel):
    """Configuration for local web HUD."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=True,
        description="Enable or disable the local web HUD",
    )
    host: str = Field(
        default="127.0.0.1",
        description="HUD bind host (strictly 127.0.0.1 for local security)",
    )
    port: int = Field(
        default=8000,
        gt=0,
        le=65535,
        description="HUD server port",
    )
    confirm_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="HUD confirmation timeout in seconds",
    )


class PersonaConfig(BaseModel):
    """Configuration for J.A.R.V.I.S. persona and addressing title."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_title: str = Field(
        default="Sir",
        description="Honorific or name used by J.A.R.V.I.S. to address the user (e.g. 'Sir', 'Mr. Stark')",
    )
    name: str = Field(
        default="J.A.R.V.I.S.",
        description="Name of the AI assistant",
    )


class SafetyConfig(BaseModel):
    """Configuration for safety boundaries, protected paths, and read-safe allowlists."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protected_paths: list[Path] = Field(
        default_factory=lambda: [
            Path("config.yaml").resolve(),
            Path("AGENT_RULES.md").resolve(),
            Path("SECURITY.md").resolve(),
            Path("src").resolve(),
            Path("logs").resolve(),
            Path("data").resolve(),
            Path(".browser_profile").resolve(),
            Path(".env").resolve(),
        ],
        description="Paths protected from modification and unauthorized read access.",
    )
    read_safe_files: list[Path] = Field(
        default_factory=lambda: [
            Path("README.md").resolve(),
        ],
        description="Files explicitly allowed for read-only access even if matching protected boundaries.",
    )

    @field_validator("protected_paths", "read_safe_files", mode="before")
    @classmethod
    def validate_paths(cls, v: Any) -> list[Path]:
        if not isinstance(v, list):
            raise ValueError(f"Must be a list of paths, got {type(v).__name__}")
        return [Path(item).resolve() for item in v]


class JarvisConfig(BaseModel):
    """Configuration schema for Jarvis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_roots: list[Path] = Field(
        default_factory=list,
        description="Allowed directory roots for path guard.",
    )
    brain: BrainConfig = Field(..., description="LLM brain configuration.")
    max_tool_calls_per_turn: int = Field(
        default=8,
        gt=0,
        description="Maximum tool calls allowed per single turn.",
    )
    audit_log_path: Path = Field(
        default=Path("logs/audit.log"),
        description="Path to tool execution audit log.",
    )
    confirm_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Confirmation prompt timeout in seconds.",
    )
    apps: dict[str, str] = Field(
        default_factory=dict,
        description="Allowlist mapping application names to launch commands/executables.",
    )
    commands: dict[str, CommandConfig] = Field(
        default_factory=dict,
        description="Allowlist of approved shell commands with fixed executables and argument validation rules.",
    )
    voice: VoiceConfig = Field(
        default_factory=VoiceConfig,
        description="Voice channel settings for audio capture, STT, TTS, and wake word.",
    )
    browser: BrowserConfig = Field(
        default_factory=BrowserConfig,
        description="Browser automation configuration.",
    )
    gui: GUIConfig = Field(
        default_factory=GUIConfig,
        description="Desktop GUI control configuration.",
    )
    memory: MemoryConfig = Field(
        default_factory=MemoryConfig,
        description="Local persistent memory configuration.",
    )
    routines: RoutinesConfig = Field(
        default_factory=RoutinesConfig,
        description="Scheduled routines configuration.",
    )
    hud: HUDConfig = Field(
        default_factory=HUDConfig,
        description="Local web HUD configuration.",
    )
    safety: SafetyConfig = Field(
        default_factory=SafetyConfig,
        description="Safety boundaries, protected paths, and read-safe allowlists.",
    )
    persona: PersonaConfig = Field(
        default_factory=PersonaConfig,
        description="J.A.R.V.I.S. persona and user addressing title configuration.",
    )

    @field_validator("allowed_roots", mode="before")
    @classmethod
    def validate_allowed_roots(cls, v: Any) -> Any:
        if not isinstance(v, list):
            raise ValueError(f"allowed_roots must be a list of paths, got {type(v).__name__}")
        return [Path(item) for item in v]

    @field_validator("audit_log_path", mode="before")
    @classmethod
    def validate_audit_log_path(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            raise ValueError("audit_log_path cannot be an empty path string")
        return Path(v)


# Convenient alias
Config = JarvisConfig


def load_dotenv_if_present(env_path: Path | str | None = None) -> None:
    """Load key-value pairs from a .env file into os.environ if not already set."""
    import os

    candidates: list[Path] = []
    if env_path is not None:
        candidates.append(Path(env_path))
    else:
        candidates.append(Path(".env"))
        candidates.append(Path(__file__).resolve().parent.parent.parent.parent / ".env")

    for cand in candidates:
        if cand.is_file():
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip().strip("'\"")
                        if k and k not in os.environ:
                            os.environ[k] = v
                break
            except Exception:
                pass


def load_config(config_path: Path | str = "config.yaml") -> JarvisConfig:
    """Load and validate the Jarvis configuration from a YAML file.

    Args:
        config_path: Path to the YAML configuration file. Defaults to 'config.yaml'.

    Returns:
        Validated JarvisConfig instance.

    Raises:
        FileNotFoundError: If the configuration file does not exist.
        ValueError: If YAML parsing fails or root is not a dictionary.
        pydantic.ValidationError: If configuration values fail schema validation.
    """
    path = Path(config_path)
    if not path.is_file():
        raise FileNotFoundError(f"Configuration file not found: {path.resolve()}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_data = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ValueError(f"Failed to parse YAML from {path}: {exc}") from exc

    if not isinstance(raw_data, dict):
        raise ValueError(
            f"Configuration in {path} must be a YAML mapping (key-value dictionary), "
            f"got {type(raw_data).__name__}"
        )

    return JarvisConfig.model_validate(raw_data)
