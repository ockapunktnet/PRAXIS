import logging
from pathlib import Path
from dotenv import load_dotenv
from .tool_components.mcp_tools import get_mcp_tools_sync, get_mcp_tools_string

# Load environment variables from project root .env file
load_dotenv(Path(__file__).parent.parent.parent / ".env")

logger = logging.getLogger(__name__)

# Initialize tools from MCP servers
tools = get_mcp_tools_sync()
tools_string = get_mcp_tools_string()