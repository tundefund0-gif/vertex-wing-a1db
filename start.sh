#!/usr/bin/env bash
set -e

# Start script for termux-mcp-agent
# Launches both MCP server and the Textual TUI agent

AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${VENV_DIR:-${AGENT_DIR}/.venv}"
MCP_LOG="${MCP_LOG:-/tmp/mcp.log}"
AGENT_LOG="${AGENT_LOG:-/tmp/agent.log}"

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=== Termux-MCP Agent Starter ===${NC}"
echo ""

# 1. Kill any existing MCP server
echo -e "${YELLOW}[1/4]${NC} Stopping any running MCP server..."
pkill -f "termux_mcp" 2>/dev/null || true
sleep 1

# 2. Start MCP server with setsid to survive shell teardown
echo -e "${YELLOW}[2/4]${NC} Starting MCP server..."
if [ ! -d "$VENV_DIR" ]; then
    echo -e "${RED}Error: Virtual env not found at $VENV_DIR${NC}"
    echo "Run: cd $AGENT_DIR && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
    exit 1
fi
setsid "$VENV_DIR/bin/python" -m termux_mcp > "$MCP_LOG" 2>&1 &
MCP_PID=$!
echo "  MCP server PID: $MCP_PID (log: $MCP_LOG)"
sleep 2

# Verify MCP server started
if kill -0 $MCP_PID 2>/dev/null; then
    echo -e "  ${GREEN}✓${NC} MCP server is running"
else
    echo -e "  ${RED}✗${NC} MCP server failed to start - check $MCP_LOG"
    cat "$MCP_LOG"
    exit 1
fi

# 3. Wait for MCP server to be ready
echo -e "${YELLOW}[3/4]${NC} Waiting for MCP server to be ready..."
for i in $(seq 1 15); do
    if curl -sf http://127.0.0.1:8080/health > /dev/null 2>&1; then
        echo -e "  ${GREEN}✓${NC} MCP server ready on http://127.0.0.1:8080"
        break
    fi
    sleep 1
done

# 4. Launch agent
echo -e "${YELLOW}[4/4]${NC} Starting TUI agent..."
echo ""
echo -e "${GREEN}===============================${NC}"
echo -e "${GREEN}  Agent is starting up...${NC}"
echo -e "${GREEN}  Default LLM: OpenCode Free API${NC}"
echo -e "${GREEN}  Ctrl+C to exit${NC}"
echo -e "${GREEN}===============================${NC}"
echo ""

cd "$AGENT_DIR"
exec "$VENV_DIR/bin/python" main.py "$@"
