#!/usr/bin/env bash
set -e

# Start script for termux-mcp-agent with Free AI Chat backend
# Uses g4f (PollinationsAI, Yqcloud) — no API key required

MCP_DIR="${MCP_DIR:-/termux-mcp}"
AGENT_DIR="${AGENT_DIR:-/termux-mcp-agent}"
VENV_DIR="${VENV_DIR:-${MCP_DIR}/venv}"
MCP_LOG="${MCP_LOG:-/tmp/mcp.log}"
AGENT_LOG="${AGENT_LOG:-/tmp/agent.log}"
FREE_CHAT_PORT="${FREE_CHAT_PORT:-9191}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'

echo -e "${GREEN}=== Termux-MCP + Free AI Chat ===${NC}"
echo ""

# 1. Kill any existing servers
echo -e "${YELLOW}[1/5]${NC} Stopping any running servers..."
pkill -f "free_chat.py" 2>/dev/null || true
pkill -f "termux_mcp" 2>/dev/null || true
sleep 1

# 2. Start Free AI Chat server
echo -e "${YELLOW}[2/5]${NC} Starting Free AI Chat server (port $FREE_CHAT_PORT)..."
setsid "$AGENT_DIR/.venv/bin/python" "$AGENT_DIR/free_chat.py" > /tmp/free_chat.log 2>&1 &
FREE_PID=$!
echo "  Free AI Chat PID: $FREE_PID (log: /tmp/free_chat.log)"
sleep 2

# Verify
if kill -0 $FREE_PID 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} Free AI Chat server running"
else
    echo -e "  ${RED}✗${NC} Free AI Chat failed to start"
    cat /tmp/free_chat.log
    exit 1
fi

# 3. Start MCP server
echo -e "${YELLOW}[3/5]${NC} Starting MCP server..."
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${RED}Error: Virtual env not found at $VENV_DIR${NC}"
    exit 1
fi
setsid "$VENV_DIR/bin/python" -m termux_mcp > "$MCP_LOG" 2>&1 &
MCP_PID=$!
echo "  MCP server PID: $MCP_PID (log: $MCP_LOG)"
sleep 2

if kill -0 $MCP_PID 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} MCP server is running"
else
    echo -e "  ${RED}✗${NC} MCP server failed to start"
    cat "$MCP_LOG"
    exit 1
fi

# 4. Wait for MCP server to be ready
echo -e "${YELLOW}[4/5]${NC} Waiting for MCP server..."
for i in $(seq 1 15); do
    if curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} MCP server ready on http://127.0.0.1:8080"
        break
    fi
    sleep 1
done

# 5. Launch agent with free AI chat backend
echo -e "${YELLOW}[5/5]${NC} Starting TUI agent..."
echo ""
echo -e "${GREEN}===============================${NC}"
echo -e "${GREEN}  Agent using Free AI Chat${NC}"
echo -e "${CYAN}  Models: GPT-4o Mini, GPT-4o, Gemini, DeepSeek, Llama${NC}"
echo -e "${CYAN}  Backend: PollinationsAI / Yqcloud (no API key)${NC}"
echo -e "${CYAN}  Ctrl+C to exit${NC}"
echo -e "${GREEN}===============================${NC}"
echo ""

cd "$AGENT_DIR"
LLM_BASE_URL="http://127.0.0.1:$FREE_CHAT_PORT/v1" \
LLM_MODEL="gpt-4o-mini" \
LLM_API_KEY="" \
exec "$VENV_DIR/bin/python" main.py "$@"
