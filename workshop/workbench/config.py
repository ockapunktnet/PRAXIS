"""Workshop configuration."""

from pathlib import Path
from dotenv import load_dotenv

# Project root
PROJECT_ROOT = Path(__file__).parent.parent.parent

# Load .env from project root
load_dotenv(PROJECT_ROOT / ".env")

# Workshop paths
WORKSHOP_DIR = PROJECT_ROOT / "workshop"
DATA_DIR = WORKSHOP_DIR / "data"
TOOLS_DIR = WORKSHOP_DIR / "tools"

# Tool MCP Server configurations
# Each entry describes how to start a tool's MCP server process
TOOLS_CONFIG = {
    "pm4py_tools": {
        "command": "python",
        "args": [str(TOOLS_DIR / "pm4py_tools" / "server.py")],
        "transport": "stdio",
    },
    "camunda": {
        "command": "python",
        "args": [str(TOOLS_DIR / "camunda" / "server.py")],
        "transport": "stdio",
    },
    "remodelbpmn": {
        "command": "python",
        "args": [str(TOOLS_DIR / "modelbpmn" / "server.py")],
        "transport": "stdio",
    },
    "bpmn_lint": {
        "command": "python",
        "args": [str(TOOLS_DIR / "bpmn_lint" / "server.py")],
        "transport": "stdio",
    },
}

# Parameter names that accept file content → solver sends file_path instead
CONTENT_PARAMS = {"event_log", "dfg_data", "petri_net_file", "bpmn_file"}
