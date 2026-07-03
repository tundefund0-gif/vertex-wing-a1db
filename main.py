from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from rich.console import Console

from agent import Agent
from config import Config
from tools import load_plugins

console = Console()
err_console = Console(stderr=True)
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="termux-agent",
        description="AI agent with shell access to an Android device via Termux-MCP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Configuration (priority: CLI flags > config file > environment variables):
  Config file locations (first found wins):
    ./termux-agent.json, ./.termux-agent.json
    ./termux-agent.yaml, ./.termux-agent.yaml
    ~/.config/termux-agent/config.json, ~/.config/termux-agent/config.yaml
    ~/.termux-agent.json

Environment variables:
  MCP_HOST, MCP_PORT, MCP_AUTH    Termux-MCP connection settings
  LLM_API_KEY, LLM_BASE_URL       LLM API settings (default: OpenCode Free API)
  LLM_MODEL, LLM_MAX_TOKENS       Model selection
  LLM_TEMPERATURE, LLM_TOP_P      Sampling params
  LOG_LEVEL, PROFILE, TUI_THEME   App settings

Modes:
  default           Launch interactive TUI
  --eval <cmd>      Run one command and print response (headless)
  --serve           Start HTTP API server
  --list-sessions   List saved sessions
  --plugins         List loaded plugins
  --validate        Validate configuration

Examples:
  termux-agent
  termux-agent --eval "list files in home"
  termux-agent --serve --port 9876
  termux-agent --list-sessions
  termux-agent --resume abc123
  termux-agent --profile code --mcp-port 8080
        """,
    )
    p.add_argument("--mcp-host", help="Termux-MCP host")
    p.add_argument("--mcp-port", type=int, help="Termux-MCP port")
    p.add_argument("--mcp-auth", help="Termux-MCP auth token")
    p.add_argument("--llm-api-key", help="LLM API key")
    p.add_argument("--llm-base-url", help="LLM API base URL")
    p.add_argument("--llm-model", help="LLM model name")
    p.add_argument("--llm-max-tokens", type=int, help="Max tokens per response")
    p.add_argument("--llm-temperature", type=float, help="Sampling temperature")
    from config import PROFILES as _PROFILES
    p.add_argument("--profile", choices=list(_PROFILES.keys()),
                   help="Config profile")
    p.add_argument("--theme", choices=["dark", "light", "monokai", "nord"], help="TUI theme")
    p.add_argument("--save-config", action="store_true", help="Save current flags to ~/.termux-agent.json")
    p.add_argument("--resume", type=str, metavar="CODE", help="Resume a previous session by code")
    p.add_argument("--list-sessions", action="store_true", help="List saved sessions")
    p.add_argument("--eval", type=str, metavar="QUERY", help="Run one query in headless mode")
    p.add_argument("--serve", action="store_true", help="Start HTTP API server")
    p.add_argument("--api-port", type=int, default=9876, help="API server port (default: 9876)")
    p.add_argument("--plugins", action="store_true", help="List loaded plugins")
    p.add_argument("--validate", action="store_true", help="Validate configuration")
    p.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR"], default="WARNING",
                   help="Logging level")
    return p


async def run_headless(agent: Agent, query: str) -> None:
    """Run a single query in headless mode and print the response."""
    print(f"Query: {query}")
    print("─" * 60)
    response_text = ""

    def on_chunk(chunk: str) -> None:
        nonlocal response_text
        response_text += chunk
        print(chunk, end="", flush=True)

    try:
        await agent.chat(query, on_chunk=on_chunk)
        print()
        print("─" * 60)
        print(f"Tokens: ↑{agent.llm_prompt_tokens} ↓{agent.llm_completion_tokens} ∑{agent.total_tokens}")
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


async def run_api_server(agent: Agent, port: int) -> None:
    """Start a simple HTTP API server for programmatic access."""
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
    except ImportError:
        err_console.print("[red]http.server not available[/]")
        return

    class AgentHandler(BaseHTTPRequestHandler):
        _agent = agent

        def do_POST(self) -> None:
            if self.path != "/chat":
                self.send_response(404)
                self.end_headers()
                return

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            try:
                data = json.loads(body)
                query = data.get("message", "")
            except (json.JSONDecodeError, KeyError):
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b'{"error": "invalid JSON"}')
                return

            import threading
            result = {"response": "", "tokens": {}}

            async def _run() -> None:
                nonlocal result
                try:
                    await self._agent.chat(query)
                    result["response"] = self._agent.get_last_response()
                    result["tokens"] = {
                        "prompt": self._agent.llm_prompt_tokens,
                        "completion": self._agent.llm_completion_tokens,
                        "total": self._agent.total_tokens,
                    }
                except Exception as e:
                    result["error"] = str(e)

            loop = asyncio.new_event_loop()
            loop.run_until_complete(_run())
            loop.close()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

        def do_GET(self) -> None:
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                ok = asyncio.run(self._agent.mcp.ping())
                self.wfile.write(json.dumps({
                    "status": "ok" if ok else "degraded",
                    "tokens": {
                        "prompt": self._agent.llm_prompt_tokens,
                        "completion": self._agent.llm_completion_tokens,
                        "total": self._agent.total_tokens,
                    },
                }).encode())
            else:
                self.send_response(404)
                self.end_headers()

    server = HTTPServer(("0.0.0.0", port), AgentHandler)
    console.print(f"[green]API server running on http://0.0.0.0:{port}[/]")
    console.print("[dim]POST /chat  —  {\"message\": \"...\"}[/dim]")
    console.print("[dim]GET  /health[/dim]")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]Server stopped[/]")


async def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    logging.getLogger().setLevel(args.log_level)

    if args.save_config:
        cfg = Config.from_args(args)
        config_path = Config.config_paths()[-1]
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w") as f:
            json.dump(cfg.to_dict(), f, indent=2)
        console.print(f"[green]Config saved to {config_path}[/green]")
        return

    config = Config.from_args(args)

    # Validate config
    errors = config.validate()
    if errors:
        for e in errors:
            err_console.print(f"[red]Config error: {e}[/]")
        if args.validate:
            sys.exit(1)

    if args.validate:
        console.print("[green]Configuration valid[/]")
        console.print(json.dumps(config.to_dict(), indent=2))
        return

    # Load plugins
    plugin_count = load_plugins(config.plugin_dir)
    if plugin_count > 0:
        console.print(f"[dim]Loaded {plugin_count} plugin(s)[/dim]")

    if args.plugins:
        console.print(f"[bold]Plugins loaded:[/] {plugin_count}")
        return

    agent = Agent(config)

    if args.list_sessions:
        sessions = agent.list_sessions()
        if not sessions:
            console.print("[yellow]No saved sessions.[/]")
        else:
            console.print("[bold]Saved sessions:[/]")
            for s in sessions:
                line = f"  [cyan]{s['code']}[/] — {s['saved_at'][:19]}  ({s['turns']} turns, {s['tokens']} tokens)"
                if s.get("profile"):
                    line += f"  [{s['profile']}]"
                console.print(line)
        await agent.close()
        return

    if args.resume:
        if not agent.load_session(args.resume):
            err_console.print(f"[red]Session '{args.resume}' not found.[/]")
            await agent.close()
            sys.exit(1)
        console.print(f"[green]Resumed session {args.resume} ({len(agent.messages)} messages).[/]")

    # Headless eval mode
    if args.eval:
        with console.status("[bold yellow]Connecting to Termux-MCP..."):
            ok = await agent.mcp.ping()
        if not ok:
            err_console.print(f"[red]Cannot reach Termux-MCP at {config.mcp_host}:{config.mcp_port}[/]")
            await agent.close()
            sys.exit(1)
        await run_headless(agent, args.eval)
        await agent.close()
        return

    # API server mode
    if args.serve:
        with console.status("[bold yellow]Connecting to Termux-MCP..."):
            ok = await agent.mcp.ping()
        if not ok:
            err_console.print(f"[red]Cannot reach Termux-MCP at {config.mcp_host}:{config.mcp_port}[/]")
            await agent.close()
            sys.exit(1)
        await run_api_server(agent, args.api_port)
        await agent.close()
        return

    # TUI mode
    try:
        with console.status("[bold yellow]Connecting to Termux-MCP..."):
            ok = await agent.mcp.ping()
        if not ok:
            err_console.print(
                f"[bold red]✗[/] Cannot reach Termux-MCP at [yellow]{config.mcp_host}:{config.mcp_port}[/]\n"
                f"  Ensure termux-mcp is running and the host/port are correct."
            )
            await agent.close()
            sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted.[/]")
        await agent.close()
        sys.exit(0)

    console.print("[dim]Starting TUI...[/dim]")

    try:
        from tui import TermuxAgentApp
        app = TermuxAgentApp(agent)
        await app.run_async()
    except KeyboardInterrupt:
        pass
    finally:
        await agent.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Goodbye![/]")
