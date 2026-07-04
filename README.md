# Termux-MCP AI Agent

AI agent with full shell access to your Android device via Termux-MCP. Features a Textual TUI, 115+ tools, session persistence, plugin system, **free AI chat** (no API key required), and config profiles.

## Quick Start

One command installs everything — MCP server, AI agent, and Free AI Chat:

```bash
git clone https://github.com/tundefund0-gif/vertex-wing-a1db
cd vertex-wing-a1db
bash install.sh
./start_free.sh
```

That's it. No API keys, no separate repos, no manual steps.

## Free AI Chat

No API keys required. Uses [g4f](https://github.com/xtekky/gpt4free) to access free AI providers:

| Provider | Models |
|----------|--------|
| PollinationsAI | GPT-4o Mini, GPT-4o, Gemini, DeepSeek, Llama |
| Yqcloud | GPT-4o Mini (fallback) |

**Start:** `./start_free.sh`

This launches:
1. **Free AI Chat server** on port 9191 (OpenAI-compatible API)
2. **MCP server** for Android device access
3. **TUI agent** connected to the free AI backend

## Configuration

Set via environment variables, config file, or CLI flags:

```bash
# Use a different LLM backend
LLM_BASE_URL="https://opencode.ai/zen/v1" LLM_MODEL="deepseek-v4-flash-free" ./start.sh

# Or with flags
./run.sh --llm-base-url http://127.0.0.1:9191/v1 --llm-model gpt-4o-mini
```

| Env Var | Default | Description |
|---------|---------|-------------|
| `LLM_BASE_URL` | `http://127.0.0.1:9191/v1` | OpenAI-compatible API endpoint |
| `LLM_MODEL` | `gpt-4o-mini` | Model name |
| `LLM_API_KEY` | (empty) | API key (not needed for free chat) |
| `MCP_HOST` | `127.0.0.1` | MCP server host |
| `MCP_PORT` | `8080` | MCP server port |
| `LOG_LEVEL` | `WARNING` | Logging level |
| `PROFILE` | `default` | Config profile |

### Config Profiles

| Profile | Temperature | Max Tokens | Use Case |
|---------|-------------|------------|----------|
| `default` | 0.7 | 131072 | Balanced |
| `fast` | 0.3 | 65536 | Quick responses |
| `creative` | 0.9 | 131072 | Creative writing |
| `precise` | 0.1 | 65536 | Factual answers |
| `code` | 0.2 | 131072 | Code generation |

```bash
./run.sh --profile code
```

## Usage

### TUI Mode (default)
```bash
./start_free.sh
```

### Headless Eval Mode
```bash
./run.sh --eval "list files in /sdcard"
```

### HTTP API Server
```bash
./run.sh --serve --api-port 9876
curl -X POST http://127.0.0.1:9876/chat -H "Content-Type: application/json" \
  -d '{"message": "what is my battery level?"}'
```

### Session Management
```bash
./run.sh --list-sessions                # List saved sessions
./run.sh --resume abc123                # Resume a session
```

## Tools

115+ tools for Android device management:

**File Operations:** read, write, search, delete, glob, diff
**System:** run commands, process info, battery, environment
**Network:** public IP, ping, translate
**Device:** camera, clipboard, notifications, SMS, WiFi, Bluetooth, sensors
**Media:** screenshot, record audio, take photo, OCR, QR codes
**Development:** web server, code execution

## Architecture

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Free Chat   │     │  MCP Server   │     │  TUI / API Client │
│  (port 9191) │────▶│  (port 8080)  │────▶│  (agent.py)       │
│  g4f backend │     │  termux_mcp  │     │  Textual / HTTP   │
└─────────────┘     └──────────────┘     └──────────────────┘
```

All three components are in this single repo. No separate installs needed.

## Testing

```bash
# All 31 tests
LLM_BASE_URL="http://127.0.0.1:9191/v1" LLM_MODEL="gpt-4o-mini" ./run.sh test_all.py

# Or via venv directly
.venv/bin/python test_all.py
```

## Plugin System

Create custom tools in `~/.termux-agent/plugins/`:

```python
# ~/.termux-agent/plugins/hello.py
def hello_handler(args: dict) -> str:
    name = args.get("name", "world")
    return f"Hello, {name}!"

TOOL_DEFINITIONS = [
    {
        "name": "hello",
        "description": "Say hello",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Name to greet"}
            }
        },
        "handler": hello_handler,
    }
]
```

## Related

- [Free AI Chat](https://github.com/tundefund0-gif/free-ai-chat) — Standalone version of the free chat server
