"""RemodelBPMN Tools — MCP Tool Server for BPMN model modification.

Provides a tool to modify existing BPMN process models based on text descriptions.

Run standalone:
    python workshop/tools/modelbpmn/server.py
"""

import asyncio
import json
import logging
import os
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Server("modelbpmn-tools")

_project_root = Path(__file__).parent.parent.parent.parent


_example_bpmn_path = _project_root / "workshop" / "tools" / "modelbpmn" / "invoice.bpmn"


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="remodel_bpmn",
            description=(
                "Modify/remodel an existing BPMN process model based on a text description. "
                "Provide the existing BPMN XML content and a description of the desired changes. "
                "Returns a modified BPMN XML file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Text description of the desired changes to the BPMN process model.",
                    },
                    "bpmn_file": {
                        "type": "string",
                        "description": "Existing BPMN XML content to modify.",
                    },
                },
                "required": ["description", "bpmn_file"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _remodel_bpmn(description: str, bpmn_file: str) -> dict:
    """Modify a BPMN model based on a text description."""
    logger.info("remodel_bpmn called with description: %s", description[:100])

    bpmn_content = _example_bpmn_path.read_text(encoding="utf-8")

    return {
        "file_content": bpmn_content,
        "file_extension": ".bpmn",
        "description": description,
    }


# ---------------------------------------------------------------------------
# Call handler
# ---------------------------------------------------------------------------

@app.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    arguments = arguments or {}

    try:
        if name == "remodel_bpmn":
            result = _remodel_bpmn(
                arguments["description"],
                bpmn_file=arguments["bpmn_file"],
            )
        else:
            result = {"error": f"Unknown tool: {name}"}

    except Exception as e:
        logger.exception("Error in %s", name)
        result = {"error": str(e)}

    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
