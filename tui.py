from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button, Input, Label, ListItem, ListView, RichLog, Static, Header, Footer
)

from agent import Agent, _make_llm_client
from config import PROFILES
from tools import TOOLS

logger = logging.getLogger(__name__)

TOOL_ICONS: dict[str, str] = {
    "run_command": "⚡", "list_directory": "📁", "read_file": "📄",
    "write_file": "✏️", "mkdir": "📂", "delete": "🗑️", "search_files": "🔍",
    "get_system_info": "📊", "process_list": "📋", "process_kill": "💀",
    "health_diagnostic": "🩺", "get_battery": "🔋", "get_location": "📍",
    "get_wifi_info": "📶", "scan_wifi": "📡", "camera_photo": "📸",
    "take_screenshot": "🖼️", "send_notification": "🔔", "send_sms": "💬",
    "make_phone_call": "📞", "open_url": "🔗", "download_file": "⬇️",
    "get_weather": "🌤️", "speedtest": "🚀", "generate_qrcode": "■",
    "translate_text": "🌐", "git_operation": "🔀", "ssh_wizard": "🔐",
    "ping_server": "🏓",
}

THEMES = {
    "dark": {
        "primary": "blue",
        "surface": "#1a1a2e",
        "accent": "cyan",
        "success": "green",
        "error": "red",
        "warning": "yellow",
    },
    "light": {
        "primary": "blue",
        "surface": "#f0f0f0",
        "accent": "cyan",
        "success": "green",
        "error": "red",
        "warning": "orange",
    },
    "monokai": {
        "primary": "#a6e22e",
        "surface": "#272822",
        "accent": "#66d9ef",
        "success": "#a6e22e",
        "error": "#f92672",
        "warning": "#fd971f",
    },
    "nord": {
        "primary": "#81a1c1",
        "surface": "#2e3440",
        "accent": "#88c0d0",
        "success": "#a3be8c",
        "error": "#bf616a",
        "warning": "#ebcb8b",
    },
}


def get_tool_icon(name: str) -> str:
    return TOOL_ICONS.get(name, "🔧")


def fmt_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    return f"{n / 1000:.1f}k"


def fmt_duration(ms: int) -> str:
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


class StatusHeader(Static):
    prompt_tokens: reactive[int] = reactive(0)
    completion_tokens: reactive[int] = reactive(0)
    status_text: reactive[str] = reactive("Ready")
    session: reactive[str] = reactive("")
    mcp_status: reactive[str] = reactive("● Connected")
    profile: reactive[str] = reactive("default")
    tool_count: reactive[int] = reactive(0)

    def render(self) -> Text:
        p = fmt_tokens(self.prompt_tokens)
        c = fmt_tokens(self.completion_tokens)
        t = fmt_tokens(self.prompt_tokens + self.completion_tokens)
        mcp_dot = "●" if "Connected" in self.mcp_status else "○"
        status_style = "bold green" if self.status_text == "Ready" else "bold yellow"
        parts = [
            (f" {self.status_text} ", status_style),
        ]
        if self.session:
            parts.append((f"│ #{self.session} ", "cyan"))
        parts.append((f"│ MCP:{mcp_dot}", "green" if "Connected" in self.mcp_status else "red"))
        parts.append((f" │ {self.profile}", "blue"))
        parts.append((f" │ ↑{p} ↓{c} ∑{t}", "dim"))
        return Text.assemble(*parts)


class ConversationLog(RichLog):
    def write_user(self, text: str) -> None:
        self.write(Rule(style="green"))
        self.write(Text(f"  You", style="bold bright_green"))
        for line in text.strip().split("\n"):
            self.write(Text(f"    {line}", style="green", no_wrap=False))

    def write_tool(self, name: str, args: dict[str, Any], duration_ms: int = 0) -> None:
        icon = get_tool_icon(name)
        timing = f" ({fmt_duration(duration_ms)})" if duration_ms > 0 else ""
        if name == "run_command":
            cmd = args.get("cmd", "")
            label = f"$ {cmd}{timing}"
            self.write(Text(f"    {icon}  {label}", style="bold cyan", no_wrap=False))
        else:
            parts = " ".join(f"{k}={v!r}" for k, v in args.items()) if args else ""
            label = f"{name}{parts}{timing}"
            self.write(Text(f"    {icon}  {label}", style="bold blue", no_wrap=False))

    def write_agent(self, text: str) -> None:
        self.write(Rule(style="magenta"))
        self.write(Text(f"  Agent", style="bold bright_magenta"))
        try:
            self.write(Markdown(text.strip()))
        except Exception:
            self.write(Text(f"    {text.strip()}", style="white", no_wrap=False))

    def write_error(self, text: str) -> None:
        self.write(Text(f"  ✗ {text}", style="bold red", no_wrap=False))

    def write_info(self, text: str, style_str: str = "dim white") -> None:
        self.write(Text(f"  {text}", style=style_str, no_wrap=False))

    def write_search_result(self, role: str, snippet: str) -> None:
        role_tag = {"user": "You", "assistant": "Agent", "system": "System"}.get(role, role)
        self.write(Text(f"  [{role_tag}] {snippet[:200]}", style="cyan", no_wrap=False))


class CommandPalette(ModalScreen):
    """Command palette overlay (Ctrl+K)."""

    def __init__(self, commands: list[tuple[str, str, str]]) -> None:
        self._commands = commands
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Container(
            Static("  Command Palette  ", style="bold cyan"),
            Input(placeholder="Filter commands...", id="palette-input"),
            ListView(id="palette-list"),
            id="palette-box",
        )

    CSS = """
    #palette-box {
        width: 60%;
        height: 70%;
        margin: 3 20%;
        background: $surface;
        border: thick $primary;
    }
    #palette-input {
        margin: 1;
    }
    #palette-list {
        height: 1fr;
        margin: 0 1;
    }
    """

    def on_mount(self) -> None:
        self._populate()
        self.query_one("#palette-input", Input).focus()

    def _populate(self, filter_text: str = "") -> None:
        lv = self.query_one("#palette-list", ListView)
        lv.clear()
        flt = filter_text.lower()
        for key, desc, action in self._commands:
            if flt and flt not in key.lower() and flt not in desc.lower():
                continue
            lv.append(ListItem(Label(f"{key} — {desc}")))

    def on_input_changed(self, event: Input.Changed) -> None:
        self._populate(event.value)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item:
            label = event.item.children[0].renderable.plain
            key = label.split(" — ")[0].strip()
            self.dismiss(key)

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.prevent_default()


class SessionBrowser(ModalScreen):
    """Browse saved sessions (Ctrl+O)."""

    def __init__(self, sessions: list[dict[str, Any]]) -> None:
        self._sessions = sessions
        super().__init__()

    def compose(self) -> ComposeResult:
        yield Container(
            Static("  Saved Sessions  ", style="bold cyan"),
            ListView(id="session-list"),
            Horizontal(
                Label("  Ctrl+R resume  ·  Ctrl+D delete  ·  Esc close", style="dim"),
                id="session-help",
            ),
            id="session-box",
        )

    CSS = """
    #session-box {
        width: 60%;
        height: 70%;
        margin: 3 20%;
        background: $surface;
        border: thick $primary;
    }
    #session-list { height: 1fr; margin: 0 1; }
    #session-help { height: 1; margin: 0 1; }
    """

    def on_mount(self) -> None:
        self._populate()
        self.query_one("#session-list", ListView).focus()

    def _populate(self) -> None:
        lv = self.query_one("#session-list", ListView)
        lv.clear()
        if not self._sessions:
            lv.append(ListItem(Label("  No saved sessions  ", style="dim")))
            return
        for s in self._sessions:
            label = f"  #{s['code']}  {s['saved_at'][:19]}  {s['turns']}turns  {fmt_tokens(s['tokens'])}tok"
            if s.get("profile"):
                label += f"  [{s['profile']}]"
            lv.append(ListItem(Label(label)))

    def on_key(self, event) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.prevent_default()
        elif event.key == "ctrl+r":
            lv = self.query_one("#session-list", ListView)
            if lv.index is not None and self._sessions:
                code = self._sessions[lv.index]["code"]
                self.dismiss(("resume", code))
        elif event.key == "ctrl+d":
            lv = self.query_one("#session-list", ListView)
            if lv.index is not None and self._sessions:
                code = self._sessions[lv.index]["code"]
                self.dismiss(("delete", code))


class TermuxAgentApp(App):
    TITLE = "Termux-MCP AI Agent"
    CSS = """
    Screen { layout: vertical; }
    StatusHeader { height: 1; dock: top; padding: 0 1; background: $surface; }
    ConversationLog { height: 1fr; border: none; padding: 0 1; overflow-y: auto; }
    #stream-box { height: auto; max-height: 40%; dock: bottom; padding: 0 1; background: $surface; border-top: solid $primary; display: none; }
    #stream-box.-visible { display: block; }
    #stream-label { height: 1; }
    #stream-content { height: auto; overflow-y: auto; }
    #input-box { height: auto; dock: bottom; padding: 0 1; background: $surface; border-top: solid $primary; }
    #delta-bar { height: 1; dock: bottom; padding: 0 1; background: $surface; display: none; }
    #delta-bar.-visible { display: block; }
    #help-bar { height: 1; dock: bottom; padding: 0 1; background: $surface; }
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel"),
        Binding("ctrl+l", "clear", "Clear"),
        Binding("ctrl+r", "reset", "Reset"),
        Binding("ctrl+t", "tokens", "Tokens"),
        Binding("ctrl+p", "tools", "Tools"),
        Binding("ctrl+k", "command_palette", "Commands"),
        Binding("ctrl+o", "session_browser", "Sessions"),
        Binding("ctrl+f", "search", "Search"),
        Binding("up", "history_up", "History", show=False),
        Binding("down", "history_down", "History", show=False),
        Binding("escape", "focus_input", "Focus"),
        Binding("ctrl+s", "settings", "Settings"),
        Binding("ctrl+m", "model_switch", "Model"),
        Binding("ctrl+n", "new_session", "New Session"),
        Binding("enter", "submit_message", "Send", priority=True),
    ]

    def __init__(self, agent: Agent) -> None:
        self.agent = agent
        self._processing = False
        self._stream_text = ""
        self._tool_calls_shown: set[str] = set()
        self._session_code = agent.session_code or ""
        self._input_history: list[str] = []
        self._history_idx = -1
        self._turn_prompt_delta = 0
        self._turn_completion_delta = 0
        self._last_mcp_check = 0
        self._mcp_status = "Connected"
        super().__init__()

    def _get_theme(self) -> dict:
        return THEMES.get(self.agent.config.tui_theme, THEMES["dark"])

    def compose(self) -> ComposeResult:
        yield StatusHeader()
        yield ConversationLog(highlight=True, markup=True, wrap=True, max_lines=None)
        yield Container(
            Static(Text("▎Agent streaming...", style="bold magenta"), id="stream-label"),
            Static("", id="stream-content", markup=False),
            id="stream-box",
        )
        yield Static("", id="delta-bar")
        yield Static(Text(
            "  Ctrl+K palette · Ctrl+O sessions · Ctrl+F search · Ctrl+N new · ↑↓ history · Enter send",
            style="dim"), id="help-bar")
        yield Input(placeholder="Type a message... (Enter to send)", id="input-box")

    def on_mount(self) -> None:
        self.query_one("#input-box", Input).focus()
        log = self.query_one(ConversationLog)
        cfg = self.agent.config

        info = Table.grid(padding=(0, 2))
        info.add_column(style="bold cyan", no_wrap=True)
        info.add_column(style="white")
        info.add_row("Model", cfg.llm_model)
        info.add_row("Host", f"{cfg.mcp_host}:{cfg.mcp_port}")
        info.add_row("Max", fmt_tokens(cfg.llm_max_tokens))
        info.add_row("Profile", cfg.profile)
        info.add_row("Theme", cfg.tui_theme)
        if self._session_code:
            info.add_row("Session", f"#{self._session_code}")

        log.write(Panel(info, title="  Termux-MCP AI Agent  ", border_style="bright_blue"))
        log.write(Text(""))

        if self._session_code and len(self.agent.messages) > 1:
            log.write_info(f"Resumed #{self._session_code} ({len(self.agent.messages)} msgs)", "bold green")
            log.write(Text(""))

        self._update_header("Ready")
        self.set_interval(5.0, self._check_mcp_connection)

    def _update_header(self, status: str) -> None:
        h = self.query_one(StatusHeader)
        h.status_text = status
        h.session = self._session_code
        h.prompt_tokens = self.agent.llm_prompt_tokens
        h.completion_tokens = self.agent.llm_completion_tokens
        h.mcp_status = self._mcp_status
        h.profile = self.agent.config.profile

    def _check_mcp_connection(self) -> None:
        self.call_later(self._do_mcp_check)

    async def _do_mcp_check(self) -> None:
        try:
            ok = await self.agent.mcp.ping()
            self._mcp_status = "Connected" if ok else "Disconnected"
            self.query_one(StatusHeader).refresh()
        except Exception:
            self._mcp_status = "Error"
            self.query_one(StatusHeader).refresh()

    def _show_stream(self, text: str) -> None:
        box = self.query_one("#stream-box")
        box.classes = "-visible"
        self.query_one("#stream-content", Static).update(Text(text, style="white", no_wrap=False))
        self.query_one(ConversationLog).scroll_end(animate=True)

    def _hide_stream(self) -> None:
        self.query_one("#stream-box").classes = ""
        self.query_one("#stream-content", Static).update("")

    def _show_delta(self, prompt_delta: int, completion_delta: int) -> None:
        bar = self.query_one("#delta-bar")
        p = fmt_tokens(prompt_delta)
        c = fmt_tokens(completion_delta)
        t = fmt_tokens(prompt_delta + completion_delta)
        bar.classes = "-visible"
        bar.update(Text(f"  this turn: ↑{p} prompt  ↓{c} completion  ∑{t} total", style="bold yellow"))
        self.set_timer(5.0, self._hide_delta)

    def _hide_delta(self) -> None:
        self.query_one("#delta-bar").classes = ""

    def _save_to_history(self, text: str) -> None:
        if text and (not self._input_history or self._input_history[-1] != text):
            self._input_history.append(text)
        self._history_idx = len(self._input_history)

    def action_history_up(self) -> None:
        if not self._input_history:
            return
        inp = self.query_one("#input-box", Input)
        if self._history_idx > 0:
            self._history_idx -= 1
            inp.value = self._input_history[self._history_idx]
            inp.cursor_position = len(inp.value)

    def action_history_down(self) -> None:
        inp = self.query_one("#input-box", Input)
        if self._history_idx < len(self._input_history) - 1:
            self._history_idx += 1
            inp.value = self._input_history[self._history_idx]
            inp.cursor_position = len(inp.value)
        elif self._history_idx == len(self._input_history) - 1:
            self._history_idx = len(self._input_history)
            inp.value = ""
            inp.cursor_position = 0

    def action_cancel(self) -> None:
        if self._processing:
            self._processing = False
            self._hide_stream()
            self._update_header("Cancelled")
            self._finish_processing()

    def action_clear(self) -> None:
        self.query_one(ConversationLog).clear()
        self.query_one(ConversationLog).write(Text(""))
        self._update_header("Cleared")

    def action_reset(self) -> None:
        self.agent.reset_conversation()
        self.query_one(ConversationLog).clear()
        self._tool_calls_shown = set()
        self._session_code = ""
        h = self.query_one(StatusHeader)
        h.prompt_tokens = 0
        h.completion_tokens = 0
        self._update_header("Reset")

    def action_tokens(self) -> None:
        self._update_header("Ready")

    def action_tools(self) -> None:
        log = self.query_one(ConversationLog)
        log.write(Rule(style="cyan"))
        log.write(Text(f"  Tools ({len(TOOLS)})", style="bold cyan"))
        for t in TOOLS:
            fn = t["function"]
            log.write(Text(f"    {fn['name']}", style="dim") + Text(f" — {fn.get('description', '')[:80]}", style="white"))
        log.write(Text(""))

    def action_focus_input(self) -> None:
        self.query_one("#input-box", Input).focus()

    def action_settings(self) -> None:
        log = self.query_one(ConversationLog)
        log.write_info("Settings: Use /set <key> <value> to change config", "bold cyan")
        log.write_info("  /set temperature 0.8", "dim")
        log.write_info("  /set max-tokens 65536", "dim")
        log.write_info("  /set model deepseek-v4-flash-free", "dim")
        log.write_info("  /set profile fast|creative|precise|code", "dim")
        log.write_info("  /set theme dark|light|monokai|nord", "dim")
        log.write(Text(""))

    def action_model_switch(self) -> None:
        log = self.query_one(ConversationLog)
        log.write_info("Use: /set model <name> or /model <name>", "dim")
        log.write_info("  Available: deepseek-v4-flash-free (default)", "dim")
        log.write(Text(""))

    def action_command_palette(self) -> None:
        commands = [
            ("/exit", "Save & quit", "quit"),
            ("/reset", "Clear conversation", "reset"),
            ("/tokens", "Show token count", "tokens"),
            ("/tools", "List available tools", "tools"),
            ("/export", "Export conversation", "export"),
            ("/search", "Search conversation", "search"),
            ("/sessions", "List saved sessions", "sessions"),
            ("/set", "Change config", "set"),
            ("/model", "Switch model", "model"),
            ("/profile", "Switch profile", "profile"),
            ("Ctrl+N", "New session", "new"),
            ("Ctrl+O", "Browse sessions", "sessions"),
            ("Ctrl+F", "Search messages", "search"),
            ("Ctrl+L", "Clear screen", "clear"),
            ("Ctrl+R", "Reset", "reset"),
            ("Ctrl+P", "List tools", "tools"),
            ("Ctrl+S", "Show settings help", "settings"),
        ]

        async def on_selected(result) -> None:
            if result is None:
                return
            log = self.query_one(ConversationLog)
            if result == "quit":
                await self.action_exit()
            elif result == "reset":
                self.action_reset()
            elif result == "clear":
                self.action_clear()
            elif result == "tools":
                self.action_tools()
            elif result == "tokens":
                self.action_tokens()
            elif result == "settings":
                self.action_settings()
            elif result == "new":
                self.action_new_session()
            elif result.startswith("/"):
                inp = self.query_one("#input-box", Input)
                inp.value = result + " "
                inp.focus()
                inp.cursor_position = len(inp.value)
            else:
                inp = self.query_one("#input-box", Input)
                inp.value = result + " "
                inp.focus()

        self.push_screen(CommandPalette(commands), on_selected)

    def action_session_browser(self) -> None:
        sessions = self.agent.list_sessions()

        async def on_selected(result) -> None:
            if result is None:
                return
            action, code = result
            log = self.query_one(ConversationLog)
            if action == "resume":
                if self.agent.load_session(code):
                    self._session_code = code
                    log.clear()
                    log.write_info(f"Resumed #{code} ({len(self.agent.messages)} msgs)", "bold green")
                    log.write(Text(""))
                    self._update_header("Ready")
                else:
                    log.write_error(f"Failed to load {code}")
            elif action == "delete":
                if self.agent.delete_session(code):
                    log.write_info(f"Deleted session {code}", "yellow")

        self.push_screen(SessionBrowser(sessions), on_selected)

    def action_search(self) -> None:
        async def search_cb(input_text: str) -> None:
            log = self.query_one(ConversationLog)
            if not input_text:
                return
            results = self.agent.search_messages(input_text)
            if not results:
                log.write_info(f"No matches for '{input_text}'", "dim")
            else:
                log.write(Rule(style="cyan"))
                log.write(Text(f"  Results for '{input_text}' ({len(results)})", style="bold cyan"))
                for r in results[:20]:
                    log.write_search_result(r["role"], r["snippet"])
                if len(results) > 20:
                    log.write_info(f"  ... and {len(results) - 20} more", "dim")
                log.write(Text(""))

        def on_search_submit(value: str) -> None:
            self.call_later(search_cb, value)

        inp = self.query_one("#input-box", Input)
        old_value = inp.value
        inp.value = "/search "
        inp.cursor_position = len(inp.value)
        inp.focus()
        # When they press enter, the on_input_submitted handler will handle /search

    def action_new_session(self) -> None:
        code = self.agent.save_session()
        self._session_code = ""
        self.agent.reset_conversation()
        self.query_one(ConversationLog).clear()
        self._tool_calls_shown = set()
        log = self.query_one(ConversationLog)
        log.write_info(f"New session started. Previous saved as #{code}", "bold green")
        log.write(Text(""))
        h = self.query_one(StatusHeader)
        h.prompt_tokens = 0
        h.completion_tokens = 0
        self._update_header("Ready")

    async def action_exit(self) -> None:
        code = self.agent.save_session()
        self._session_code = code
        log = self.query_one(ConversationLog)
        log.write_info(f"Session #{code}. Resume: --resume {code}", "bold green")
        log.write(Text(""))
        self._update_header("Saved")
        self.exit()

    async def action_submit_message(self) -> None:
        """Called by app-level enter binding."""
        inp = self.query_one("#input-box", Input)
        text = inp.value.strip()
        if not text or self._processing:
            return
        inp.value = ""
        await self._process_input(text)

    def on_key(self, event) -> None:
        """Safety net: if Enter reaches the app unhandled, submit."""
        if event.key == "enter" and not event.is_shortcut:
            inp = self.query_one("#input-box", Input)
            if inp.has_focus and inp.value.strip() and not self._processing:
                event.prevent_default()
                self.call_later(self.action_submit_message)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text or self._processing:
            return
        inp = self.query_one("#input-box", Input)
        inp.value = ""
        await self._process_input(text)

    async def _process_input(self, text: str) -> None:
        self._save_to_history(text)

        if text.startswith("/"):
            await self._handle_command(text)
            return

        self._processing = True
        self._stream_text = ""
        self._tool_calls_shown = set()
        self._turn_prompt_delta = 0
        self._turn_completion_delta = 0
        inp = self.query_one("#input-box", Input)
        inp.disabled = True
        log = self.query_one(ConversationLog)
        log.write_user(text)
        self._update_header("Thinking...")
        self.call_after_refresh(self._run_chat, text)

    @work(thread=False, exit_on_error=False)
    async def _run_chat(self, text: str) -> None:
        log = self.query_one(ConversationLog)
        prev_prompt = self.agent.llm_prompt_tokens
        prev_completion = self.agent.llm_completion_tokens

        def on_chunk(chunk: str) -> None:
            self._stream_text += chunk
            self._show_stream(self._stream_text)

        def on_tool_call(tool_calls, content, has_reasoning, reasoning) -> None:
            if tool_calls:
                for tc in tool_calls:
                    key = tc["function"]["name"] + tc["function"]["arguments"]
                    if key in self._tool_calls_shown:
                        continue
                    self._tool_calls_shown.add(key)
                    name = tc["function"]["name"]
                    try:
                        args = json.loads(tc["function"]["arguments"])
                    except json.JSONDecodeError:
                        args = {}
                    self._hide_stream()
                    log.write_tool(name, args)

        def on_reasoning(chunk: str) -> None:
            pass

        try:
            await self.agent.chat(
                text,
                on_tool_call=on_tool_call,
                on_chunk=on_chunk,
                on_reasoning=on_reasoning,
            )
        except Exception as e:
            self._hide_stream()
            log.write_error(str(e))
            logger.exception("Chat error")
            self._finish_processing()
            return

        self._hide_stream()

        # Show tool calls with timing
        tool_msgs = self.agent.get_tool_calls_from_last()
        for tm in reversed(tool_msgs):
            for tc in tm.get("tool_calls", []):
                key = tc["function"]["name"] + tc["function"]["arguments"]
                if key in self._tool_calls_shown:
                    continue
                self._tool_calls_shown.add(key)
                name = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}
                log.write_tool(name, args)

        # Show tool results with timing
        for msg in reversed(self.agent.messages):
            if msg.get("role") == "tool":
                dur = msg.get("duration_ms", 0)
                name = msg.get("tool_name", "")
                if name and dur > 0:
                    content_preview = str(msg.get("content", ""))[:100]
                    log.write_info(f"  {name} ({fmt_duration(dur)}): {content_preview}", "dim")
                elif name:
                    content_preview = str(msg.get("content", ""))[:100]
                    log.write_info(f"  {name}: {content_preview}", "dim")
            elif msg.get("role") == "user":
                break

        # Show final response
        response = self.agent.get_last_response()
        if response:
            log.write_agent(response)

        # Show token delta
        delta_p = self.agent.llm_prompt_tokens - prev_prompt
        delta_c = self.agent.llm_completion_tokens - prev_completion
        if delta_p > 0 or delta_c > 0:
            self._show_delta(delta_p, delta_c)

        self._update_header("Ready")
        self._finish_processing()

    def _finish_processing(self) -> None:
        self._processing = False
        self._stream_text = ""
        inp = self.query_one("#input-box", Input)
        inp.disabled = False
        inp.focus()
        self.query_one(ConversationLog).scroll_end(animate=True)

    async def _handle_command(self, text: str) -> None:
        cmd = text.lower().split()
        log = self.query_one(ConversationLog)

        if cmd[0] in ("/exit", "/quit"):
            await self.action_exit()

        elif cmd[0] == "/help":
            t = Table.grid(padding=(0, 2))
            t.add_column(style="bold yellow")
            t.add_column()
            t.add_row("/exit", "Quit (auto-saves)")
            t.add_row("/reset", "Clear")
            t.add_row("/tokens", "Tokens")
            t.add_row("/tools", "Tools list")
            t.add_row("/export", "Export as markdown")
            t.add_row("/search", "Search messages")
            t.add_row("/sessions", "List sessions")
            t.add_row("/set", "Set config (e.g. /set temperature 0.7)")
            t.add_row("/model", "Switch model")
            t.add_row("/profile", "Switch profile")
            t.add_row("/theme", "Switch theme (dark|light|monokai|nord)")
            t.add_row("/help", "This help")
            t.add_row("Ctrl+K", "Command palette")
            t.add_row("Ctrl+O", "Browse sessions")
            t.add_row("Ctrl+F", "Search")
            t.add_row("Ctrl+N", "New session")
            t.add_row("↑↓", "Input history")
            log.write(Panel(t, title="  Commands  ", border_style="blue"))
            log.write(Text(""))

        elif cmd[0] == "/reset":
            self.action_reset()

        elif cmd[0] == "/tokens":
            self.action_tokens()

        elif cmd[0] == "/tools":
            self.action_tools()

        elif cmd[0] == "/export":
            fname = cmd[1] if len(cmd) > 1 else f"session-{self._session_code or 'chat'}.md"
            out = self.agent.export_session_markdown(fname)
            log.write_info(f"Exported to {out}", "bold green")
            log.write(Text(""))

        elif cmd[0] == "/search":
            if len(cmd) < 2:
                log.write_info("Usage: /search <term>", "dim")
            else:
                query = text[len("/search "):]
                results = self.agent.search_messages(query)
                if not results:
                    log.write_info(f"No matches for '{query}'", "dim")
                else:
                    log.write(Rule(style="cyan"))
                    log.write(Text(f"  Results for '{query}' ({len(results)})", style="bold cyan"))
                    for r in results[:20]:
                        log.write_search_result(r["role"], r["snippet"])
                    if len(results) > 20:
                        log.write_info(f"  ... and {len(results)-20} more", "dim")
                    log.write(Text(""))

        elif cmd[0] == "/sessions":
            sessions = self.agent.list_sessions()
            if not sessions:
                log.write_info("No saved sessions.", "dim")
            else:
                log.write(Rule(style="cyan"))
                log.write(Text("  Saved Sessions", style="bold cyan"))
                for s in sessions:
                    line = Text(f"    #{s['code']}", style="cyan")
                    line += Text(f"  {s['saved_at'][:19]}  {s['turns']}t  {fmt_tokens(s['tokens'])}tok", style="dim")
                    if s.get("profile"):
                        line += Text(f"  [{s['profile']}]", style="blue")
                    log.write(line)
                log.write(Text("  Use: --resume <code>", style="dim"))
                log.write(Text(""))

        elif cmd[0] == "/set":
            if len(cmd) < 3:
                log.write_info("Usage: /set <key> <value>", "dim")
                log.write_info("  Keys: temperature, max-tokens, model, profile, theme, top-p, freq-penalty, pres-penalty", "dim")
            else:
                key, value = cmd[1], cmd[2]
                old_val = None
                try:
                    if key == "temperature":
                        old_val = self.agent.config.llm_temperature
                        self.agent.config.llm_temperature = float(value)
                    elif key == "max-tokens":
                        old_val = self.agent.config.llm_max_tokens
                        self.agent.config.llm_max_tokens = int(value)
                    elif key == "model":
                        old_val = self.agent.config.llm_model
                        self.agent.config.llm_model = value
                        self.agent.llm = _make_llm_client(self.agent.config)
                    elif key == "profile":
                        if value in PROFILES:
                            old_val = self.agent.config.profile
                            self.agent.config.profile = value
                            self.agent.config.apply_profile(PROFILES[value])
                            self._update_header("Ready")
                        else:
                            log.write_error(f"Unknown profile: {value}. Options: {', '.join(PROFILES.keys())}")
                            return
                    elif key == "theme":
                        if value in THEMES:
                            old_val = self.agent.config.tui_theme
                            self.agent.config.tui_theme = value
                            self._update_header("Ready")
                        else:
                            log.write_error(f"Unknown theme: {value}. Options: {', '.join(THEMES.keys())}")
                            return
                    elif key in ("top-p", "top_p"):
                        old_val = self.agent.config.llm_top_p
                        self.agent.config.llm_top_p = float(value)
                    elif key in ("freq-penalty", "frequency-penalty"):
                        old_val = self.agent.config.llm_frequency_penalty
                        self.agent.config.llm_frequency_penalty = float(value)
                    elif key in ("pres-penalty", "presence-penalty"):
                        old_val = self.agent.config.llm_presence_penalty
                        self.agent.config.llm_presence_penalty = float(value)
                    else:
                        log.write_error(f"Unknown config key: {key}")
                        return
                    log.write_info(f"Set {key} = {value} (was {old_val})", "bold green")
                    log.write(Text(""))
                except ValueError as e:
                    log.write_error(f"Invalid value for {key}: {value}")

        elif cmd[0] == "/model":
            if len(cmd) < 2:
                log.write_info("Usage: /model <model-name>", "dim")
                log.write_info(f"  Current: {self.agent.config.llm_model}", "dim")
            else:
                model = cmd[1]
                old_model = self.agent.config.llm_model
                self.agent.config.llm_model = model
                self.agent.llm = _make_llm_client(self.agent.config)
                log.write_info(f"Switched model from {old_model} to {model}", "bold green")
                log.write(Text(""))

        elif cmd[0] == "/profile":
            if len(cmd) < 2:
                log.write_info(f"Current profile: {self.agent.config.profile}", "dim")
                log.write_info(f"Available: {', '.join(PROFILES.keys())}", "dim")
            else:
                profile = cmd[1]
                if profile in PROFILES:
                    old_profile = self.agent.config.profile
                    self.agent.config.profile = profile
                    self.agent.config.apply_profile(PROFILES[profile])
                    self._update_header("Ready")
                    log.write_info(f"Switched profile from {old_profile} to {profile}", "bold green")
                    log.write(Text(""))
                else:
                    log.write_error(f"Unknown profile: {profile}")
                    log.write_info(f"Available: {', '.join(PROFILES.keys())}", "dim")

        elif cmd[0] == "/theme":
            if len(cmd) < 2:
                log.write_info(f"Current theme: {self.agent.config.tui_theme}", "dim")
                log.write_info(f"Available: {', '.join(THEMES.keys())}", "dim")
            else:
                theme = cmd[1]
                if theme in THEMES:
                    old = self.agent.config.tui_theme
                    self.agent.config.tui_theme = theme
                    self._update_header("Ready")
                    log.write_info(f"Switched theme from {old} to {theme}", "bold green")
                    log.write(Text(""))
                else:
                    log.write_error(f"Unknown theme: {theme}")

        else:
            log.write_error(f"Unknown: {text}")
