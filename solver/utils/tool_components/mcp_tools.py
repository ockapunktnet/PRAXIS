import os
import logging
import asyncio
import concurrent.futures
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    from langchain_mcp_adapters.client import MultiServerMCPClient
    MCP_AVAILABLE = True
except ImportError:
    logger.warning("langchain_mcp_adapters not available, MCP tools disabled")
    MCP_AVAILABLE = False
    MultiServerMCPClient = None

# Path to the new Workshop MCP Server
WORKBENCH_MCP_PATH = str(
    Path(__file__).parent.parent.parent.parent / "workshop" / "workbench" / "mcp_server.py"
)


def get_mcp_config(user_id: str = None) -> dict:
    """Get MCP server config, optionally with a specific user_id."""
    if user_id is None:
        user_id = os.getenv("USER_ID", "user")
    return {
        "workbench": {
            "command": "python",
            "args": [WORKBENCH_MCP_PATH],
            "transport": "stdio",
            "env": {**os.environ, "USER_ID": user_id},
        }
    }


# MCP Server Configuration (uses USER_ID from environment)
MCP_SERVER_CONFIG = get_mcp_config()

async def get_mcp_tools():
    """Get tools from MCP servers"""
    if not MCP_AVAILABLE:
        logger.warning("MCP adapters not available")
        return []
    
    try:
        client = MultiServerMCPClient(MCP_SERVER_CONFIG)
        tools = await client.get_tools()
        return tools
    except Exception as e:
        logger.error(f"Failed to get MCP tools: {e}")
        return []

def get_mcp_tools_sync():
    """Get MCP tools synchronously using thread executor"""
    if not MCP_AVAILABLE:
        logger.warning("MCP adapters not available")
        return []
    
    try:
        # Always use a new event loop in a thread to avoid conflicts
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, get_mcp_tools())
            mcp_tools = future.result()
        return mcp_tools
    except Exception as e:
        logger.error(f"Failed to load MCP tools, falling back to empty list: {e}")
        return []

def _format_tool_signature(tool) -> str:
    """Format a single MCP tool as a Python-style signature with description."""
    name = tool.name if hasattr(tool, 'name') else str(tool)
    desc = tool.description if hasattr(tool, 'description') else ""

    # Extract parameters from args_schema (dict or Pydantic model)
    schema = getattr(tool, 'args_schema', None)
    if schema is None:
        return f"@tool(description='{desc}')\ndef {name}():\n    pass"

    if isinstance(schema, dict):
        props = schema.get('properties', {})
        required = schema.get('required', [])
    else:
        s = schema.schema()
        props = s.get('properties', {})
        required = s.get('required', [])

    params = []
    for pname, pinfo in props.items():
        ptype = pinfo.get('type', 'any')
        pdesc = pinfo.get('description', '')
        if pname in required:
            params.append(f"{pname}: {ptype}")
        else:
            params.append(f"{pname}: {ptype} = None")

    sig = ', '.join(params)

    # Add parameter descriptions as inline comments
    param_docs = []
    for pname, pinfo in props.items():
        pdesc = pinfo.get('description', '')
        if pdesc:
            param_docs.append(f"    # {pname}: {pdesc}")
    param_block = '\n'.join(param_docs) if param_docs else '    pass'

    return f"@tool(description='{desc}')\ndef {name}({sig}):\n{param_block}"


def get_mcp_tools_string():
    """Generate formatted MCP tools string with full parameter signatures."""
    current_tools = get_mcp_tools_sync()
    if not current_tools:
        return "No MCP tools available"

    formatted_tools = [_format_tool_signature(t) for t in current_tools]
    return "\n\n".join(formatted_tools)
