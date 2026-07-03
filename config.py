from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
import shutil
from datetime import datetime


VALID_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


@dataclass
class Profile:
    name: str
    description: str = ""
    llm_model: str | None = None
    llm_temperature: float | None = None
    llm_max_tokens: int | None = None
    llm_top_p: float | None = None
    llm_frequency_penalty: float | None = None
    llm_presence_penalty: float | None = None
    mcp_host: str | None = None
    mcp_port: int | None = None
    tags: list[str] = field(default_factory=list)


PROFILES: dict[str, Profile] = {
    "default": Profile(
        name="default",
        description="Default balanced settings",
    ),
    "fast": Profile(
        name="fast",
        description="Faster responses, lower temperature",
        llm_temperature=0.3,
        llm_max_tokens=65536,
    ),
    "creative": Profile(
        name="creative",
        description="More creative responses",
        llm_temperature=0.9,
        llm_max_tokens=131072,
    ),
    "precise": Profile(
        name="precise",
        description="Precise, factual responses",
        llm_temperature=0.1,
        llm_max_tokens=65536,
    ),
    "code": Profile(
        name="code",
        description="Optimized for code generation",
        llm_temperature=0.2,
        llm_max_tokens=131072,
    ),
}


@dataclass
class Config:
    mcp_host: str = "127.0.0.1"
    mcp_port: int = 8080
    mcp_auth: str = ""

    llm_api_key: str = ""
    llm_base_url: str = "https://opencode.ai/zen/v1"
    llm_model: str = "deepseek-v4-flash-free"
    llm_max_tokens: int = 131072
    llm_temperature: float = 0.7
    llm_top_p: float = 1.0
    llm_frequency_penalty: float = 0.0
    llm_presence_penalty: float = 0.0
    llm_stop: list[str] = field(default_factory=list)

    log_level: str = "WARNING"
    profile: str = "default"

    tui_theme: str = "dark"
    tui_show_timestamps: bool = False
    tui_max_visible_tools: int = 50

    tool_timeout_default: float = 180.0
    tool_timeout_short: float = 10.0
    tool_timeout_long: float = 300.0
    tool_max_retries: int = 1
    tool_concurrency_limit: int = 5
    tool_result_max_length: int = 12000

    circuit_breaker_threshold: int = 5
    circuit_breaker_reset_seconds: float = 30.0

    auto_continue_limit: int = 30
    max_total_tool_calls: int = 99
    context_preserve_turns: int = 5
    context_max_tokens: int = 65536

    session_dir: str = str(Path.home() / ".termux-agent" / "sessions")
    plugin_dir: str = str(Path.home() / ".termux-agent" / "plugins")
    config_dir: str = str(Path.home() / ".config" / "termux-agent")

    @classmethod
    def config_paths(cls) -> list[Path]:
        return [
            Path.cwd() / "termux-agent.json",
            Path.cwd() / ".termux-agent.json",
            Path.cwd() / "termux-agent.yaml",
            Path.cwd() / ".termux-agent.yaml",
            Path.home() / ".config" / "termux-agent" / "config.json",
            Path.home() / ".config" / "termux-agent" / "config.yaml",
            Path.home() / ".termux-agent.json",
        ]

    @classmethod
    def _load_file(cls, path: Path) -> dict | None:
        try:
            if path.exists():
                raw = path.read_text()
                if path.suffix in (".yaml", ".yml"):
                    try:
                        import yaml
                        return yaml.safe_load(raw)
                    except ImportError:
                        pass
                return json.loads(raw)
        except (json.JSONDecodeError, OSError, ImportError):
            pass
        return None

    @classmethod
    def from_env(cls) -> Config:
        return cls(
            mcp_host=os.getenv("MCP_HOST", "127.0.0.1"),
            mcp_port=int(os.getenv("MCP_PORT", "8080")),
            mcp_auth=os.getenv("MCP_AUTH", ""),
            llm_api_key=os.getenv("LLM_API_KEY", ""),
            llm_base_url=os.getenv("LLM_BASE_URL", "https://opencode.ai/zen/v1"),
            llm_model=os.getenv("LLM_MODEL", "deepseek-v4-flash-free"),
            llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "131072")),
            llm_temperature=float(os.getenv("LLM_TEMPERATURE", "0.7")),
            llm_top_p=float(os.getenv("LLM_TOP_P", "1.0")),
            llm_frequency_penalty=float(os.getenv("LLM_FREQUENCY_PENALTY", "0.0")),
            llm_presence_penalty=float(os.getenv("LLM_PRESENCE_PENALTY", "0.0")),
            log_level=os.getenv("LOG_LEVEL", "WARNING"),
            profile=os.getenv("PROFILE", "default"),
            tui_theme=os.getenv("TUI_THEME", "dark"),
        )

    @classmethod
    def from_env_and_files(cls) -> Config:
        cfg = cls.from_env()
        for path in cls.config_paths():
            data = cls._load_file(path)
            if data:
                for key, val in data.items():
                    if hasattr(cfg, key) and val is not None:
                        setattr(cfg, key, val)
                break
        return cfg

    @classmethod
    def from_args(cls, args) -> Config:
        base = cls.from_env_and_files()
        if args.mcp_host:
            base.mcp_host = args.mcp_host
        if args.mcp_port:
            base.mcp_port = args.mcp_port
        if args.mcp_auth:
            base.mcp_auth = args.mcp_auth
        if args.llm_api_key:
            base.llm_api_key = args.llm_api_key
        if args.llm_base_url:
            base.llm_base_url = args.llm_base_url
        if args.llm_model:
            base.llm_model = args.llm_model
        if args.llm_max_tokens:
            base.llm_max_tokens = args.llm_max_tokens
        if args.llm_temperature:
            base.llm_temperature = args.llm_temperature
        if args.log_level:
            base.log_level = args.log_level
        if args.profile:
            base.profile = args.profile
        if args.theme:
            base.tui_theme = args.theme
        # Apply profile if set
        if base.profile and base.profile in PROFILES:
            base.apply_profile(PROFILES[base.profile])
        return base

    def apply_profile(self, profile: Profile) -> None:
        for key in ("llm_model", "llm_temperature", "llm_max_tokens",
                     "llm_top_p", "llm_frequency_penalty", "llm_presence_penalty",
                     "mcp_host", "mcp_port"):
            val = getattr(profile, key, None)
            if val is not None:
                setattr(self, key, val)

    def to_dict(self) -> dict:
        return {
            "mcp_host": self.mcp_host,
            "mcp_port": self.mcp_port,
            "llm_base_url": self.llm_base_url,
            "llm_model": self.llm_model,
            "llm_max_tokens": self.llm_max_tokens,
            "llm_temperature": self.llm_temperature,
            "llm_top_p": self.llm_top_p,
            "llm_frequency_penalty": self.llm_frequency_penalty,
            "llm_presence_penalty": self.llm_presence_penalty,
            "log_level": self.log_level,
            "profile": self.profile,
            "tui_theme": self.tui_theme,
        }

    def validate(self) -> list[str]:
        errors = []
        if not 0 < self.mcp_port < 65536:
            errors.append(f"Invalid MCP port: {self.mcp_port}")
        if self.llm_temperature is not None and not (0 <= self.llm_temperature <= 2):
            errors.append(f"Temperature must be 0-2, got {self.llm_temperature}")
        if self.llm_max_tokens is not None and self.llm_max_tokens < 1:
            errors.append(f"max_tokens must be > 0, got {self.llm_max_tokens}")
        if self.log_level not in VALID_LOG_LEVELS:
            errors.append(f"Invalid log_level: {self.log_level}")
        if self.profile and self.profile not in PROFILES:
            errors.append(f"Unknown profile: {self.profile}")
        if self.tool_concurrency_limit < 1:
            errors.append(f"tool_concurrency_limit must be >= 1")
        if self.context_preserve_turns < 1:
            errors.append(f"context_preserve_turns must be >= 1")
        return errors

    def save(self, path: Path | None = None) -> Path:
        if path is None:
            path = Path(self.config_dir) / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2))
        return path

    @classmethod
    def backup(cls) -> Path:
        backup_dir = Path.home() / ".termux-agent" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = backup_dir / f"config_{stamp}.json"
        for p in cls.config_paths():
            if p.exists():
                shutil.copy2(p, dest)
                break
        return dest

    @classmethod
    def list_backups(cls) -> list[Path]:
        backup_dir = Path.home() / ".termux-agent" / "backups"
        if not backup_dir.exists():
            return []
        return sorted(backup_dir.glob("config_*.json"), reverse=True)

    def apply_partial(self, updates: dict[str, Any]) -> None:
        for key, val in updates.items():
            if hasattr(self, key) and val is not None:
                setattr(self, key, val)
