"""Workbench MCP Server — exposes file management and tool-proxy tools to the Solver.

Run via stdio transport:
    USER_ID=test-user python workshop/workbench/mcp_server.py
"""

import asyncio
import base64
import copy
import json
import logging
import os
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# Ensure project root is on sys.path so imports work when run as a script
_project_root = Path(__file__).parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from workshop.workbench.file_manager import FileManager
from workshop.workbench.tool_registry import ToolRegistry
from workshop.workbench.config import DATA_DIR, TOOLS_CONFIG, CONTENT_PARAMS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------
USER_ID = os.environ.get("USER_ID", "user")

# ---------------------------------------------------------------------------
# Core objects
# ---------------------------------------------------------------------------
file_manager = FileManager(str(DATA_DIR), USER_ID)
file_manager.ensure_user_dir()

tool_registry = ToolRegistry(TOOLS_CONFIG)

app = Server("workbench-mcp-server")

# ---------------------------------------------------------------------------
# Built-in file management tools
# ---------------------------------------------------------------------------

FILE_TOOLS = [
    types.Tool(
        name="list_files",
        description="List all files available for the current user.",
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    types.Tool(
        name="delete_file",
        description="Delete a file.",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Relative path to the file to delete.",
                },
            },
            "required": ["file_path"],
        },
    ),
]


def _handle_file_tool(name: str, arguments: dict) -> str:
    """Execute a file-management tool and return a text result."""
    if name == "list_files":
        files = file_manager.list_files()
        if not files:
            return "No files found. The user directory is empty."
        result = {
            "total_files": len(files),
            "files": files,
            "message": f"Found {len(files)} file(s) in the user directory. This is the complete list.",
        }
        return json.dumps(result, indent=2)

    elif name == "delete_file":
        file_manager.delete_file(arguments["file_path"])
        return f"File deleted: {arguments['file_path']}"

    raise ValueError(f"Unknown file tool: {name}")


# ---------------------------------------------------------------------------
# Dynamic tool-proxy tools (generated from ToolRegistry)
# ---------------------------------------------------------------------------

_proxy_tools: list[types.Tool] = []
_proxy_tool_names: set[str] = set()
# Mapping: tool_name -> {solver_param: original_param}  e.g. {"file_path": "csv_content"}
_param_mapping: dict[str, dict[str, str]] = {}


async def _build_proxy_tools() -> None:
    """Connect to tool servers and build proxy tool definitions.

    For parameters in CONTENT_PARAMS, the schema is rewritten so the solver
    sees a ``file_path`` parameter instead of the raw-content parameter.
    """
    global _proxy_tools, _proxy_tool_names
    await tool_registry.connect_tools()
    remote_tools = await tool_registry.list_tools()

    for tool in remote_tools:
        tool_name = tool.name if hasattr(tool, "name") else tool.get("name", "")
        tool_desc = tool.description if hasattr(tool, "description") else tool.get("description", "")
        tool_schema = tool.inputSchema if hasattr(tool, "inputSchema") else tool.get("inputSchema", {})

        # Deep-copy schema so we don't mutate the original
        rewritten_schema = copy.deepcopy(tool_schema)
        mapping: dict[str, str] = {}

        props = rewritten_schema.get("properties", {})
        required = rewritten_schema.get("required", [])

        for param_name in list(props.keys()):
            if param_name in CONTENT_PARAMS:
                is_required = param_name in required
                # Replace content param with file_path
                props["file_path"] = {
                    "type": "string" if is_required else ["string", "null"],
                    "description": "Path to the file in the user directory.",
                }
                del props[param_name]
                # Update required list
                if is_required:
                    idx = required.index(param_name)
                    required[idx] = "file_path"
                mapping["file_path"] = param_name

        if mapping:
            _param_mapping[tool_name] = mapping
            logger.info("Rewrote schema for %s: %s", tool_name, mapping)

        _proxy_tools.append(
            types.Tool(
                name=tool_name,
                description=tool_desc,
                inputSchema=rewritten_schema,
            )
        )
        _proxy_tool_names.add(tool_name)

    logger.info("Registered %d proxy tools from tool servers", len(_proxy_tools))


async def _handle_proxy_tool(name: str, arguments: dict) -> str:
    """Forward a tool call to the appropriate tool server.

    1. Resolve file_path → read file content and map back to original param name.
    2. Call the remote tool with resolved arguments.
    3. Save full result to file, return only a compact summary to the solver.
    """
    # --- Input resolution: file_path → original content param ---
    mapping = _param_mapping.get(name, {})
    resolved_args = {}
    for key, value in arguments.items():
        if key in mapping:
            original_param = mapping[key]
            if value is None:
                # Optional file_path not provided — skip mapping
                continue
            content = file_manager.read_file(value)
            resolved_args[original_param] = content
            logger.info("Resolved %s=%s → %s (%d chars)", key, value, original_param, len(content))
        else:
            if value is not None:
                resolved_args[key] = value

    result = await tool_registry.call_tool(name, resolved_args)

    # --- Extract text content from MCP result ---
    if hasattr(result, "content"):
        texts = []
        for block in result.content:
            if hasattr(block, "text"):
                texts.append(block.text)
            else:
                texts.append(str(block))
        result_text = "\n".join(texts)
    else:
        result_text = str(result)

    # --- Check for image result (generic convention: image_base64 key) ---
    try:
        data = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        data = None

    if isinstance(data, dict) and "image_base64" in data:
        img_format = data.get("format", "png")
        image_bytes = base64.b64decode(data["image_base64"])
        result_filename = file_manager.write_bytes(
            f"{name}_result.{img_format}", image_bytes
        )
        logger.info("Saved image to %s", result_filename)

        # Summary from remaining keys (exclude image_base64)
        summary_parts = []
        for key, value in data.items():
            if key == "image_base64":
                continue
            summary_parts.append(f"{key}: {value}")
        summary = ", ".join(summary_parts)

        return f"Ergebnis gespeichert: {result_filename}\n{summary}"

    # --- Check for file_content result (e.g. CSV export) ---
    if isinstance(data, dict) and "file_content" in data:
        ext = data.get("file_extension", ".txt")
        result_filename = file_manager.write_file(f"{name}_result{ext}", data["file_content"])
        logger.info("Saved file content to %s", result_filename)
        summary_parts = [f"{k}: {v}" for k, v in data.items() if k not in ("file_content", "file_extension")]
        return f"Ergebnis gespeichert: {result_filename}\n{', '.join(summary_parts)}"

    # --- Save full result to user directory ---
    result_filename = file_manager.write_file(f"{name}_result.json", result_text)
    logger.info("Saved result to %s", result_filename)

    # --- Return compact summary instead of full result ---
    summary = _summarize_result(result_text)
    return f"Ergebnis gespeichert: {result_filename}\n{summary}"


def _summarize_result(result_text: str) -> str:
    """Create a compact, tool-agnostic summary of a tool result.

    - JSON objects: show scalar values directly, list/dict as type[length].
    - Non-JSON: just show character count.
    """
    try:
        data = json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return f"Result: {len(result_text)} characters"

    if not isinstance(data, dict):
        if isinstance(data, list):
            return f"Result: list[{len(data)}]"
        return f"Result: {len(result_text)} characters"

    parts = []
    for key, value in data.items():
        if isinstance(value, (int, float, bool)):
            parts.append(f"{key}: {value}")
        elif isinstance(value, str):
            if key == "error" or len(value) <= 80:
                parts.append(f"{key}: {value}")
            else:
                parts.append(f"{key}: str[{len(value)} chars]")
        elif isinstance(value, list):
            parts.append(f"{key}: list[{len(value)}]")
        elif isinstance(value, dict):
            parts.append(f"{key}: dict[{len(value)} keys]")
        elif value is None:
            parts.append(f"{key}: null")
        else:
            parts.append(f"{key}: {type(value).__name__}")

    return ", ".join(parts)


# ---------------------------------------------------------------------------
# MCP handlers
# ---------------------------------------------------------------------------

UTILITY_TOOLS = [
    types.Tool(
        name="get_available_tools",
        description=(
            "Return a list of all available tools with their names, "
            "descriptions, and input schemas. Use this to discover "
            "which tools are available and how to call them."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
]


@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    """Return all available tools (file + proxy + utility)."""
    return FILE_TOOLS + _proxy_tools + UTILITY_TOOLS


@app.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    """Route the call to the correct handler."""
    arguments = arguments or {}

    try:
        if name == "get_available_tools":
            all_tools = FILE_TOOLS + _proxy_tools + UTILITY_TOOLS
            tool_docs = []
            for t in all_tools:
                tool_docs.append({
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.inputSchema,
                })
            result = json.dumps(tool_docs, indent=2)
            return [types.TextContent(type="text", text=result)]

        # Check file tools first
        file_tool_names = {t.name for t in FILE_TOOLS}
        if name in file_tool_names:
            result = _handle_file_tool(name, arguments)
        elif name in _proxy_tool_names:
            result = await _handle_proxy_tool(name, arguments)
        else:
            result = f"Error: Unknown tool '{name}'"

    except Exception as e:
        logger.exception("Error calling tool %s", name)
        result = f"Error: {e}"

    return [types.TextContent(type="text", text=result)]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    """Start the MCP server on stdio."""
    # Build proxy tools before serving
    await _build_proxy_tools()

    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(read_stream, write_stream, app.create_initialization_options())
    finally:
        await tool_registry.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
