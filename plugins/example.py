"""
Example plugin for TermuxAgent.

Defines:
  - tools: list of extra tool definitions
  - tool_handlers: dict mapping tool name -> async callable
  - system_prompt_extras: str to append to system prompt
"""

import json
import logging

logger = logging.getLogger(__name__)

system_prompt_extras = """
### Example Plugin Tools
- `hello_world` — Returns a greeting.
- `echo` — Echoes back the input.
"""

tools = [
    {
        "name": "hello_world",
        "endpoint": "/custom/hello",
        "method": "POST",
        "description": "Say hello to someone.",
        "properties": {
            "name": {
                "type": "string",
                "description": "Name to greet",
                "default": "World",
            }
        },
        "required": [],
        "category": "other",
    },
    {
        "name": "echo",
        "endpoint": "/custom/echo",
        "method": "POST",
        "description": "Echo back the input text.",
        "properties": {
            "text": {
                "type": "string",
                "description": "Text to echo",
            }
        },
        "required": ["text"],
        "category": "other",
    },
]


async def _hello_handler(args: dict) -> str:
    name = args.get("name", "World")
    return json.dumps({"greeting": f"Hello, {name}! From plugin."})


async def _echo_handler(args: dict) -> str:
    text = args.get("text", "")
    return json.dumps({"echo": text, "length": len(text)})


tool_handlers = {
    "hello_world": _hello_handler,
    "echo": _echo_handler,
}
