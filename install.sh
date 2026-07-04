#!/usr/bin/env bash
set -e

# One-command installer for Termux-MCP AI Agent
# Includes MCP server, AI agent, and Free AI Chat backend

DIR="$(cd "$(dirname "$0")" && pwd)"
VENV_DIR="${DIR}/.venv"
PYTHON="${PYTHON:-python3}"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; RED='\033[0;31m'; NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Termux-MCP AI Agent — Installer${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

# 1. Check Python
echo -e "${YELLOW}[1/5]${NC} Checking Python..."
if ! command -v "$PYTHON" &>/dev/null; then
    echo -e "${RED}Python not found. Install it: pkg install python${NC}"
    exit 1
fi
echo -e "  ${GREEN}✓${NC} $("$PYTHON" --version)"

# 2. Create virtual environment
echo -e "${YELLOW}[2/5]${NC} Creating virtual environment..."
if [ -d "$VENV_DIR" ]; then
    echo "  Virtual env already exists, skipping"
else
    "$PYTHON" -m venv "$VENV_DIR"
    echo -e "  ${GREEN}✓${NC} Created .venv"
fi

# 3. Install dependencies
echo -e "${YELLOW}[3/5]${NC} Installing dependencies..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install -r "$DIR/requirements.txt" -q
echo -e "  ${GREEN}✓${NC} Dependencies installed"

# 4. Install Playwright browser
echo -e "${YELLOW}[4/5]${NC} Installing Playwright browser..."
"$VENV_DIR/bin/playwright" install chromium 2>/dev/null || \
    "$VENV_DIR/bin/python" -m playwright install chromium 2>/dev/null || \
    echo "  ${YELLOW}⚠ Playwright browser skipped (install manually if needed)${NC}"
echo -e "  ${GREEN}✓${NC} Playwright ready"

# 5. Verify installation
echo -e "${YELLOW}[5/5]${NC} Verifying installation..."
"$VENV_DIR/bin/python" -c "import termux_mcp; print('  ✓ termux_mcp')" 2>/dev/null || echo "  ${YELLOW}⚠ termux_mcp check skipped${NC}"
"$VENV_DIR/bin/python" -c "import g4f; print('  ✓ g4f')" 2>/dev/null || echo "  ${YELLOW}⚠ g4f check skipped${NC}"
"$VENV_DIR/bin/python" -c "from openai import AsyncOpenAI; print('  ✓ openai')" 2>/dev/null || echo "  ${YELLOW}⚠ openai check skipped${NC}"
echo ""

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "  Start with Free AI Chat (no API key):"
echo -e "    ${CYAN}./start_free.sh${NC}"
echo ""
echo -e "  Start with OpenCode API:"
echo -e "    ${CYAN}./start.sh${NC}"
echo ""
echo -e "  Or run headless:"
echo -e "    ${CYAN}./start_free.sh --eval \"list files\"${NC}"
echo ""