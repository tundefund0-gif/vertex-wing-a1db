from __future__ import annotations

import asyncio
import inspect
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
from openai import AsyncOpenAI

from config import Config
from mcp_client import MCPClient, MCPError
from tools import SYSTEM_PROMPT, TOOLS, TOOL_NAME_TO_ENDPOINT, TOOL_NAME_TO_METHOD, get_plugin_handler

logger = logging.getLogger(__name__)


_RATE_LIMITER: dict[str, float] = {}

def _check_rate_limit(key: str, min_interval: float = 0.5) -> bool:
    now = time.monotonic()
    last = _RATE_LIMITER.get(key, 0.0)
    if now - last < min_interval:
        return False
    _RATE_LIMITER[key] = now
    return True


def _make_llm_client(config: Config) -> AsyncOpenAI:
    api_key = config.llm_api_key
    base_url = config.llm_base_url

    async def _strip_auth(request: httpx.Request) -> httpx.Request:
        if not api_key:
            request.headers.pop("Authorization", None)
        return request

    http_client = httpx.AsyncClient(
        limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        timeout=httpx.Timeout(180.0, connect=15.0),
        event_hooks={"request": [_strip_auth]},
        follow_redirects=True,
    )
    return AsyncOpenAI(
        api_key=api_key or "sk-noauth",
        base_url=base_url,
        http_client=http_client,
    )


def _estimate_msgs_tokens(messages: list[dict[str, Any]]) -> int:
    total = 0
    for m in messages:
        if isinstance(m.get("content"), str):
            total += len(m["content"]) // 2
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                total += len(tc["function"]["arguments"]) // 2
    return max(1, total)


def _compress_conversation_turn(messages: list[dict], idx: int) -> str | None:
    """Generate a brief summary of a conversation turn for context compression."""
    user_msg = None
    assistant_msgs = []
    i = idx
    while i < len(messages):
        m = messages[i]
        if m["role"] == "user":
            if user_msg is None:
                user_msg = str(m.get("content", ""))[:150]
        elif m["role"] == "assistant":
            content = m.get("content")
            if content:
                assistant_msgs.append(str(content)[:200])
        elif m["role"] == "tool":
            pass
        elif m["role"] == "system" and i > idx:
            break
        elif m["role"] == "user" and i > idx:
            break
        i += 1

    parts = []
    if user_msg:
        parts.append(f"User asked: {user_msg}")
    if assistant_msgs:
        summary = " ".join(a[:100] for a in assistant_msgs)
        parts.append(f"Assistant replied: {summary[:200]}")
    return " | ".join(parts) if parts else None


class Agent:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.mcp = MCPClient(
            config.mcp_host, config.mcp_port, config.mcp_auth,
            circuit_breaker_threshold=config.circuit_breaker_threshold,
            circuit_breaker_reset_seconds=config.circuit_breaker_reset_seconds,
        )
        self.llm = _make_llm_client(config)
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self.llm_prompt_tokens = 0
        self.llm_completion_tokens = 0
        self._tool_call_history: set[str] = set()
        self._session_code: str = ""
        self._total_tool_calls = 0
        self._concurrency_sem = asyncio.Semaphore(config.tool_concurrency_limit)

    @property
    def session_code(self) -> str:
        return self._session_code

    @session_code.setter
    def session_code(self, val: str) -> None:
        self._session_code = val

    def session_dir(self) -> Path:
        return Path(self.config.session_dir)

    def save_session(self) -> str:
        code = self._session_code or secrets.token_hex(3)
        self._session_code = code
        sdir = self.session_dir()
        sdir.mkdir(parents=True, exist_ok=True)
        path = sdir / f"{code}.json"
        data = {
            "code": code,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "llm_prompt_tokens": self.llm_prompt_tokens,
            "llm_completion_tokens": self.llm_completion_tokens,
            "messages": self.messages,
            "config_profile": self.config.profile,
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        return code

    def load_session(self, code: str) -> bool:
        path = self.session_dir() / f"{code}.json"
        if not path.exists():
            return False
        with open(path) as f:
            data = json.load(f)
        self.messages = data.get("messages", self.messages)
        self.llm_prompt_tokens = data.get("llm_prompt_tokens", 0)
        self.llm_completion_tokens = data.get("llm_completion_tokens", 0)
        self._session_code = code
        sys_count = sum(1 for m in self.messages if m["role"] == "system")
        if sys_count == 0:
            self.messages.insert(0, {"role": "system", "content": SYSTEM_PROMPT})
        return True

    def list_sessions(self) -> list[dict[str, Any]]:
        sdir = self.session_dir()
        if not sdir.exists():
            return []
        sessions = []
        for p in sorted(sdir.glob("*.json"), reverse=True):
            try:
                with open(p) as f:
                    data = json.load(f)
                sessions.append({
                    "code": data.get("code", p.stem),
                    "saved_at": data.get("saved_at", "unknown"),
                    "tokens": data.get("llm_prompt_tokens", 0) + data.get("llm_completion_tokens", 0),
                    "turns": sum(1 for m in data.get("messages", []) if m["role"] == "user"),
                    "profile": data.get("config_profile", ""),
                })
            except Exception:
                continue
        return sessions

    def delete_session(self, code: str) -> bool:
        path = self.session_dir() / f"{code}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    async def close(self) -> None:
        await self.mcp.close()

    @property
    def total_prompt_tokens(self) -> int:
        return self.llm_prompt_tokens

    @property
    def total_completion_tokens(self) -> int:
        return self.llm_completion_tokens

    @property
    def total_tokens(self) -> int:
        return self.llm_prompt_tokens + self.llm_completion_tokens

    def _count_system_messages(self) -> int:
        return sum(1 for m in self.messages if m["role"] == "system")

    def _trim_context_if_needed(self) -> None:
        max_tokens = self.config.context_max_tokens
        preserve_turns = self.config.context_preserve_turns
        while _estimate_msgs_tokens(self.messages) > max_tokens and len(self.messages) > self._count_system_messages() + 2:
            sys_count = self._count_system_messages()
            total_turns = self.messages[sys_count:]
            turn_boundaries = [i for i, m in enumerate(total_turns) if m["role"] == "user"]
            if len(turn_boundaries) <= preserve_turns:
                break

            first_user_idx = sys_count + turn_boundaries[0]
            second_user_idx = (
                sys_count + turn_boundaries[1] if len(turn_boundaries) > 1 else len(self.messages)
            )
            removed_msgs = self.messages[first_user_idx:second_user_idx]
            del self.messages[first_user_idx:second_user_idx]

            # Generate a meaningful summary instead of just dropping
            summary = _compress_conversation_turn(removed_msgs, 0)
            if summary:
                self.messages.insert(first_user_idx, {
                    "role": "system",
                    "content": f"[Summarized earlier turn: {summary}]",
                })

    async def _format_tool_result(self, text: str | dict[str, Any], max_len: int | None = None) -> str:
        if max_len is None:
            max_len = self.config.tool_result_max_length
        if isinstance(text, dict):
            text = json.dumps(text, indent=2, ensure_ascii=False)
        if len(text) > max_len:
            return text[:max_len] + f"\n... (truncated, {len(text)} total chars)"
        return text

    async def _web_server_start(self, args: dict[str, Any]) -> str:
        port = args.get("port", 8080)
        directory = args.get("directory", ".")
        cmd = f"cd {directory} && nohup python3 -m http.server {port} > /dev/null 2>&1 &"
        result = await self.mcp.call_endpoint("run_command", "/run", {"cmd": cmd})
        return f"Web server starting on port {port}...\n{result}"

    async def ensure_mcp_connection(self) -> bool:
        for attempt in range(3):
            ok = await self.mcp.ping()
            if ok:
                return True
            if attempt < 2:
                await asyncio.sleep(1 * (attempt + 1))
        return False

    async def _execute_plugin_tool(self, name: str, call_id: str, args: dict[str, Any]) -> dict[str, Any]:
        handler = get_plugin_handler(name)
        if handler is None:
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"Plugin handler for '{name}' not found",
                "tool_name": name,
                "duration_ms": 0,
            }
        try:
            t0 = asyncio.get_event_loop().time()
            if inspect.iscoroutinefunction(handler):
                result = await handler(args)
            else:
                result = handler(args)
            ms = int((asyncio.get_event_loop().time() - t0) * 1000)
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "content": await self._format_tool_result(result),
                "tool_name": name,
                "duration_ms": ms,
            }
        except Exception as e:
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"Plugin error in {name}: {e}",
                "tool_name": name,
                "duration_ms": 0,
            }

    async def _execute_single_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        name = tool_call["function"]["name"]
        call_id = tool_call["id"]
        raw_args = tool_call["function"]["arguments"]
        args: dict[str, Any] = {}
        t0 = 0.0
        if raw_args:
            try:
                args = json.loads(raw_args)
            except json.JSONDecodeError as e:
                return {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": f"Error parsing arguments for {name}: {e}",
                    "tool_name": name,
                    "duration_ms": 0,
                }

        self._total_tool_calls += 1
        if self._total_tool_calls > self.config.max_total_tool_calls:
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"Tool call limit ({self.config.max_total_tool_calls}) exceeded",
                "tool_name": name,
                "duration_ms": 0,
            }

        # Check for plugin handler first
        if get_plugin_handler(name) is not None:
            return await self._execute_plugin_tool(name, call_id, args)

        if name == "web_server" and args.get("action") == "start":
            try:
                t0 = asyncio.get_event_loop().time()
                result = await self._web_server_start(args)
                ms = int((asyncio.get_event_loop().time() - t0) * 1000)
                return {"role": "tool", "tool_call_id": call_id, "content": result, "tool_name": name, "duration_ms": ms}
            except Exception as e:
                return {"role": "tool", "tool_call_id": call_id, "content": f"Error: {e}", "tool_name": name, "duration_ms": 0}

        endpoint = TOOL_NAME_TO_ENDPOINT.get(name)
        if endpoint is None:
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "content": f"Error: unknown tool '{name}'",
                "tool_name": name,
                "duration_ms": 0,
            }

        last_error: Exception | None = None
        max_retries = self.config.tool_max_retries
        for attempt in range(max_retries + 1):
            try:
                t0 = asyncio.get_event_loop().time()
                result = await self.mcp.call_endpoint(name, endpoint, args)
                ms = int((asyncio.get_event_loop().time() - t0) * 1000)
                return {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": await self._format_tool_result(result),
                    "tool_name": name,
                    "duration_ms": ms,
                }
            except MCPError as e:
                last_error = e
                err_str = str(e)
                if "Timeout" in err_str and attempt < max_retries:
                    logger.warning("Retrying tool %s after timeout (attempt %d)", name, attempt + 1)
                    await asyncio.sleep(0.5 * (attempt + 1))
                    continue
                if "Cannot connect" in err_str and attempt < max_retries:
                    logger.warning("MCP disconnect for %s, reconnecting...", name)
                    await self.ensure_mcp_connection()
                    continue
                break
            except Exception as e:
                last_error = e
                break

        # MCP connection may have dropped — try to reconnect once more
        if last_error and "Cannot connect" in str(last_error):
            logger.warning("MCP connection lost, attempting final reconnect...")
            if await self.ensure_mcp_connection():
                try:
                    t0 = asyncio.get_event_loop().time()
                    result = await self.mcp.call_endpoint(name, endpoint, args)
                    ms = int((asyncio.get_event_loop().time() - t0) * 1000)
                    return {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": await self._format_tool_result(result),
                        "tool_name": name,
                        "duration_ms": ms,
                    }
                except Exception as e2:
                    last_error = e2

        return {
            "role": "tool",
            "tool_call_id": call_id,
            "content": f"Error executing {name}: {last_error}",
            "tool_name": name,
            "duration_ms": 0,
        }

    async def _execute_tools(self, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        async def _run_with_sem(tc: dict[str, Any]) -> dict[str, Any]:
            async with self._concurrency_sem:
                return await self._execute_single_tool(tc)

        tasks = [_run_with_sem(tc) for tc in tool_calls]
        results = await asyncio.gather(*tasks)
        return results

    def _validate_messages(self) -> None:
        i = 0
        while i < len(self.messages):
            msg = self.messages[i]
            if msg["role"] == "tool":
                tcid = msg.get("tool_call_id", "")
                found = False
                for j in range(i - 1, -1, -1):
                    prev = self.messages[j]
                    if prev["role"] == "assistant" and prev.get("tool_calls"):
                        for tc in prev["tool_calls"]:
                            if tc.get("id") == tcid:
                                found = True
                                break
                    if found:
                        break
                if not found:
                    self.messages.pop(i)
                    continue
            elif msg["role"] == "assistant" and msg.get("tool_calls"):
                for tc in msg["tool_calls"]:
                    if not tc.get("id"):
                        tc["id"] = f"call_{id(tc)}_{hash(tc['function']['name'])}"
            i += 1

    async def chat(
        self, user_message: str, on_tool_call=None, on_chunk=None, on_reasoning=None
    ) -> list[dict[str, Any]]:
        self.messages.append({"role": "user", "content": user_message})

        iteration_limit = self.config.auto_continue_limit
        max_total = self.config.max_total_tool_calls
        auto_continue_count = 0

        for total_iterations in range(max_total):
            for iteration in range(iteration_limit):
                self._trim_context_if_needed()
                self._validate_messages()

                # Rate-limit API calls
                if not _check_rate_limit("llm", 0.3):
                    await asyncio.sleep(0.1)

                try:
                    stream = await self.llm.chat.completions.create(
                        model=self.config.llm_model,
                        messages=self.messages,
                        tools=TOOLS,
                        stream=True,
                        max_tokens=self.config.llm_max_tokens,
                        temperature=self.config.llm_temperature,
                        top_p=self.config.llm_top_p,
                        frequency_penalty=self.config.llm_frequency_penalty,
                        presence_penalty=self.config.llm_presence_penalty,
                        stop=self.config.llm_stop or None,
                    )
                except Exception as e:
                    error_str = str(e)
                    if "tool" in error_str and "preceding" in error_str:
                        self._validate_messages()
                        stream = await self.llm.chat.completions.create(
                            model=self.config.llm_model,
                            messages=self.messages,
                            tools=TOOLS,
                            stream=True,
                            max_tokens=self.config.llm_max_tokens,
                            temperature=self.config.llm_temperature,
                        )
                    elif "rate" in error_str.lower() or "429" in error_str:
                        logger.warning("Rate limited, backing off 5s...")
                        await asyncio.sleep(5)
                        stream = await self.llm.chat.completions.create(
                            model=self.config.llm_model,
                            messages=self.messages,
                            tools=TOOLS,
                            stream=True,
                            max_tokens=self.config.llm_max_tokens,
                            temperature=self.config.llm_temperature,
                        )
                    else:
                        raise

                full_content = ""
                reasoning_acc = ""
                tool_calls_acc: dict[int, dict[str, Any]] = {}
                has_reasoning = False
                stream_empty = True

                async for chunk in stream:
                    stream_empty = False
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta is None:
                        continue

                    if delta.content:
                        full_content += delta.content
                        if on_chunk:
                            on_chunk(delta.content)

                    rc = getattr(delta, "reasoning_content", None)
                    if rc:
                        if not has_reasoning:
                            has_reasoning = True
                        reasoning_acc += rc
                        if on_reasoning:
                            on_reasoning(rc)

                    if delta.tool_calls:
                        for tc in delta.tool_calls:
                            idx = tc.index
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            if tc.id:
                                tool_calls_acc[idx]["id"] = tc.id
                            if tc.function:
                                if tc.function.name:
                                    tool_calls_acc[idx]["function"]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_acc[idx]["function"]["arguments"] += tc.function.arguments

                    if chunk.usage:
                        self.llm_prompt_tokens += chunk.usage.prompt_tokens or 0
                        self.llm_completion_tokens += chunk.usage.completion_tokens or 0

                if stream_empty:
                    logger.warning("Empty stream response, retrying...")
                    continue

                tool_calls = list(tool_calls_acc.values()) if tool_calls_acc else None

                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": full_content or None,
                    "reasoning": reasoning_acc or None,
                }
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls

                self.messages.append(assistant_msg)

                if not tool_calls:
                    if on_tool_call:
                        on_tool_call(None, full_content, has_reasoning, reasoning_acc or None)
                    return self.messages

                if on_tool_call:
                    on_tool_call(tool_calls, full_content, has_reasoning, reasoning_acc or None)

                tool_results = await self._execute_tools(tool_calls)
                self.messages.extend(tool_results)

            auto_continue_count += 1
            self.messages.append({
                "role": "system",
                "content": f"[Auto-continue #{auto_continue_count}: you hit the tool call limit. Continue working on the same task from where you left off. Do NOT repeat completed steps.]",
            })

        self.messages.append({
            "role": "assistant",
            "content": "I've exceeded the total tool call limit. Please ask a more specific question or break your request into smaller steps.",
        })
        return self.messages

    def get_last_response(self) -> str:
        for msg in reversed(self.messages):
            if msg["role"] == "assistant" and msg.get("content"):
                return msg["content"]
        return ""

    def get_tool_calls_from_last(self) -> list[dict[str, Any]]:
        results = []
        for i in range(len(self.messages) - 1, -1, -1):
            msg = self.messages[i]
            if msg["role"] == "assistant" and msg.get("tool_calls"):
                results.append(msg)
            elif msg["role"] == "user":
                break
        return results

    def export_session_markdown(self, path: str | Path) -> str:
        path = Path(path)
        lines: list[str] = []
        lines.append("# Termux-MCP Agent Session\n")
        if self._session_code:
            lines.append(f"**Session:** #{self._session_code}\n")
        lines.append(f"**Tokens:** {self.llm_prompt_tokens + self.llm_completion_tokens} total\n")
        lines.append(f"**Exported:** {datetime.now(timezone.utc).isoformat()[:19]}\n")
        lines.append(f"**Profile:** {self.config.profile}\n")
        lines.append("---\n")

        for msg in self.messages:
            role = msg.get("role", "")
            content = msg.get("content") or ""
            if role == "system" and not content:
                continue
            if role == "system":
                lines.append(f"> *System: {content}*\n")
            elif role == "user":
                lines.append(f"## You\n\n{content}\n")
            elif role == "assistant":
                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc["function"]
                        args_str = fn.get("arguments", "")
                        lines.append(f"- **Tool:** `{fn['name']}({args_str})`\n")
                if content:
                    lines.append(f"## Agent\n\n{content}\n")
            elif role == "tool":
                c = str(content)[:200]
                timing = ""
                dur = msg.get("duration_ms", 0)
                if dur > 0:
                    timing = f" ({dur}ms)"
                lines.append(f"  _Result{timing}: {c}_\n")

        path.write_text("\n".join(lines))
        return str(path)

    def search_messages(self, query: str) -> list[dict[str, Any]]:
        q = query.lower()
        results: list[dict[str, Any]] = []
        for msg in self.messages:
            content = (msg.get("content") or "").lower()
            if q in content:
                results.append({
                    "role": msg["role"],
                    "snippet": (msg.get("content") or "")[:300],
                })
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                for tc in tool_calls:
                    args = tc["function"].get("arguments", "").lower()
                    if q in args or q in tc["function"]["name"].lower():
                        results.append({
                            "role": f"{msg['role']}.tool",
                            "snippet": f"{tc['function']['name']}({tc['function']['arguments'][:200]})",
                        })
        return results

    def reset_conversation(self) -> None:
        self.messages = [
            {"role": "system", "content": SYSTEM_PROMPT}
        ]
        self.llm_prompt_tokens = 0
        self.llm_completion_tokens = 0
        self._total_tool_calls = 0
