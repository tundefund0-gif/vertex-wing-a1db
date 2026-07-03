#!/usr/bin/env python3
"""
End-to-end test suite for Termux-MCP AI Agent.
Tests MCP connectivity, LLM API, agent tool calling, parallelism, and error recovery.
"""

import asyncio
import os
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PASS = 0
FAIL = 0
TIMEOUT_MS = 180_000


def report(name: str, ok: bool, detail: str = ""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}")
        if detail:
            for line in detail.strip().split("\n"):
                print(f"     {line}")


class MCPServerProcess:
    def __init__(self, port: int = 8083):
        self.port = port
        self.proc: subprocess.Popen | None = None

    def start(self):
        env = os.environ.copy()
        env["HOME"] = "/tmp"
        env["TERMUX_MCP_PORT"] = str(self.port)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "termux_mcp"],
            cwd="/termux-mcp",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN),
        )
        import httpx
        for _ in range(30):
            try:
                r = httpx.get(f"http://127.0.0.1:{self.port}/ping", timeout=2)
                if r.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.3)
        raise RuntimeError("MCP server failed to start")

    def stop(self):
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


async def test_mcp_connectivity(mcp):
    print("\n── MCP Connectivity ──")
    ok = await mcp.ping()
    report("Ping", ok)


async def test_mcp_endpoints(mcp):
    print("\n── MCP Endpoints ──")

    r = await mcp.call_endpoint("run_command", "/run", {"cmd": "echo ok123"})
    report("Run command", "ok123" in str(r).strip(), str(r)[:100])

    r = await mcp.call_endpoint("list_directory", "/ls", {"path": "."})
    report("List directory", len(str(r)) > 10, str(r)[:80])

    r = await mcp.call_endpoint("write_file", "/write", {"path": "/tmp/mcp_test.txt", "content": "hello"})
    report("Write file", "Written" in str(r))

    r = await mcp.call_endpoint("read_file", "/read", {"path": "/tmp/mcp_test.txt"})
    report("Read file", "hello" in str(r), str(r)[:80])

    r = await mcp.call_endpoint("mkdir", "/mkdir", {"path": "/tmp/mcp_test_dir"})
    report("Create dir", "Created" in str(r))

    r = await mcp.call_endpoint("delete", "/delete", {"path": "/tmp/mcp_test_dir", "recursive": True, "confirmed": True})
    report("Delete dir", "Deleted" in str(r))

    r = await mcp.call_endpoint("search_files", "/search", {"path": "/tmp", "pattern": "mcp_test.txt"})
    report("Search files", "mcp_test.txt" in str(r))

    r = await mcp.call_endpoint("get_system_info", "/system-info")
    report("System info", len(str(r)) > 20)

    r = await mcp.call_endpoint("health_diagnostic", "/health")
    report("Health", len(str(r)) > 10)

    r = await mcp.call_endpoint("diff", "/diff", {"file": "/tmp/mcp_test.txt"})
    report("Diff/file info", len(str(r)) > 5)

    # Cleanup
    await mcp.call_endpoint("delete", "/delete", {"path": "/tmp/mcp_test.txt", "confirmed": True})


async def test_mcp_device_and_utility_endpoints(mcp):
    print("\n── MCP Device & Utility Endpoints ──")

    for tool_name, ep, params, display in [
        ("get_battery", "/battery", {}, "Battery"),
        ("get_public_ip", "/public-ip", {}, "Public IP"),
        ("get_weather", "/weather", {"city": "London"}, "Weather"),
        ("get_environment", "/env", {}, "Environment"),
        ("explain_command", "/explain", {"cmd": "ls -la"}, "Explain cmd"),
        ("ping_server", "/ping", {}, "Ping"),
        ("translate_text", "/translate", {"text": "hello", "target_lang": "es"}, "Translate"),
    ]:
        try:
            r = await mcp.call_endpoint(tool_name, ep, params)
            ok = len(str(r)) > 2
            report(f"{display} ({ep})", ok, str(r)[:120])
        except Exception as e:
            report(f"{display} ({ep})", False, str(e)[:120])


async def test_parallel_execution(mcp):
    """Test that multiple tools can be called concurrently."""
    print("\n── Parallel Execution ──")

    async def run_one(i: int):
        return await mcp.call_endpoint(
            "run_command", "/run",
            {"cmd": f"echo 'parallel_test_{i}' && sleep 0.1"}
        )

    start = time.monotonic()
    results = await asyncio.gather(*[run_one(i) for i in range(5)])
    elapsed = time.monotonic() - start

    ok = all(f"parallel_test_{i}" in str(r) for i, r in enumerate(results))
    report(f"5 parallel commands ({elapsed:.2f}s)", ok, f"Serial would be ~{elapsed * 5:.2f}s, actual {elapsed:.2f}s")


async def test_error_handling(mcp):
    print("\n── Error Handling ──")

    try:
        await mcp.call_endpoint("test", "/nonexistent")
        report("Nonexistent endpoint", False, "Should have raised")
    except Exception as e:
        report("Nonexistent endpoint error", "404" in str(e), str(e)[:100])

    try:
        await mcp.call_endpoint("read_file", "/read", {})
        report("Missing param", False, "Should have raised")
    except Exception as e:
        report("Missing param error", True, str(e)[:100])

    ok = await mcp.ping()
    report("Health check after errors", ok)


async def test_llm_connectivity():
    print("\n── LLM API Connectivity ──")

    from agent import _make_llm_client
    from config import Config

    cfg = Config(
        llm_base_url="https://opencode.ai/zen/v1",
        llm_model="deepseek-v4-flash-free",
    )
    client = _make_llm_client(cfg)

    try:
        response = await client.chat.completions.create(
            model="deepseek-v4-flash-free",
            messages=[{"role": "user", "content": "Say hello in one word"}],
            max_tokens=2000,
            temperature=0.1,
        )
        text = response.choices[0].message.content or ""
        report("Basic completion", len(text) > 0, repr(text[:80]))
    except Exception as e:
        report("Basic completion", False, str(e)[:200])
        return None, None

    try:
        stream = await client.chat.completions.create(
            model="deepseek-v4-flash-free",
            messages=[{"role": "user", "content": "Say hello"}],
            stream=True,
            max_tokens=2000,
            temperature=0.1,
        )
        total_chunks = 0
        content_chunks = 0
        async for chunk in stream:
            total_chunks += 1
            if chunk.choices and chunk.choices[0].delta:
                d = chunk.choices[0].delta
                if d.content:
                    content_chunks += 1
        report(f"Streaming ({content_chunks} content / {total_chunks} total)", content_chunks > 0,
               f"{content_chunks} content out of {total_chunks} chunks")
    except Exception as e:
        report("Streaming", False, str(e)[:200])

    return client, cfg


async def test_agent_tool_calling(mcp):
    print("\n── Agent Tool Calling ──")

    from agent import Agent
    from config import Config

    config = Config(
        mcp_host="127.0.0.1",
        mcp_port=8083,
        llm_model="deepseek-v4-flash-free",
        llm_base_url="https://opencode.ai/zen/v1",
        llm_max_tokens=8192,
        llm_temperature=0.3,
    )
    agent = Agent(config)
    agent.mcp = mcp  # use same test client

    # Test 1: Single tool call
    try:
        messages = await agent.chat("Run 'echo HELLO_AGENT_TEST' and tell me the output")
        resp = agent.get_last_response()
        has_tool = len(agent.get_tool_calls_from_last()) > 0
        report("Single tool call", has_tool, f"Called {has_tool} tools")
        report("Agent response", len(resp) > 0, resp[:150])
    except Exception as e:
        report("Single tool call flow", False, str(e)[:300])
        await agent.close()
        return

    # Test 2: Conversation continuity
    try:
        messages = await agent.chat("What was the output of the last command?")
        resp = agent.get_last_response()
        report("Follow-up question", "HELLO_AGENT_TEST" in resp or len(resp) > 10, resp[:150])
    except Exception as e:
        report("Follow-up question", False, str(e)[:200])

    # Test 3: Check server info (uses a tool naturally)
    try:
        messages = await agent.chat("What's my current working directory?")
        resp = agent.get_last_response()
        has_tool = len(agent.get_tool_calls_from_last()) > 0
        report("Context-aware question", has_tool and len(resp) > 5, resp[:150])
    except Exception as e:
        report("Context-aware question", False, str(e)[:200])

    # Test 4: Token tracking
    report("Token tracking active", agent.total_tokens > 0, f"Total: {agent.total_tokens}")

    # Test 5: Reset conversation
    agent.reset_conversation()
    report("Conversation reset", len(agent.messages) == 1, f"{len(agent.messages)} messages after reset")
    report("Token count reset", agent.total_tokens == 0, f"Tokens: {agent.total_tokens}")

    await agent.close()


async def run_all_tests():
    global PASS, FAIL
    print("=" * 60)
    print("  Termux-MCP AI Agent — End-to-End Test Suite v2")
    print("=" * 60)

    print("\n── Setup ──")
    server = MCPServerProcess(port=8083)
    try:
        server.start()
        print("  ✅ MCP server on port 8083")
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return

    from mcp_client import MCPClient
    mcp = MCPClient("127.0.0.1", 8083)

    try:
        await test_mcp_connectivity(mcp)
        await test_mcp_endpoints(mcp)
        await test_mcp_device_and_utility_endpoints(mcp)
        await test_parallel_execution(mcp)
        await test_error_handling(mcp)
        await test_llm_connectivity()
        await test_agent_tool_calling(mcp)
    finally:
        await mcp.close()
        server.stop()

    total = PASS + FAIL
    print(f"\n{'=' * 60}")
    color = "✅" if FAIL == 0 else "⚠️"
    print(f"  {color}  {PASS}/{total} passed, {FAIL} failed")
    print(f"{'=' * 60}")
    return FAIL == 0


if __name__ == "__main__":
    success = asyncio.run(run_all_tests())
    sys.exit(0 if success else 1)
