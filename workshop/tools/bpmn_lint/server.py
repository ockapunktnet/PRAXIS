"""BPMN Lint Tools — MCP Tool Server for BPMN model validation.

Validates BPMN process models against best practices and correctness rules.

Run standalone:
    python workshop/tools/bpmn_lint/server.py
"""

import asyncio
import json
import logging
import subprocess
import tempfile
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

LINT_SCRIPT = Path(__file__).parent / "lint.mjs"

app = Server("bpmn-lint-tools")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="lint_bpmn",
            description=(
                "Validate a BPMN process model against correctness rules and best practices. "
                "Returns a list of issues found (errors, warnings, info) or confirms the model is valid."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "bpmn_file": {
                        "type": "string",
                        "description": "BPMN XML content to validate.",
                    },
                },
                "required": ["bpmn_file"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _lint_bpmn(bpmn_file: str) -> dict:
    """Validate a BPMN model using bpmnlint."""
    logger.info("lint_bpmn called (%d chars)", len(bpmn_file))

    # Write BPMN XML to temp file
    tmp = tempfile.NamedTemporaryFile(suffix=".bpmn", mode="w", delete=False)
    try:
        tmp.write(bpmn_file)
        tmp.close()

        proc = subprocess.run(
            ["node", str(LINT_SCRIPT), tmp.name],
            capture_output=True,
            text=True,
            timeout=30,
        )

        if proc.returncode not in (0, 1):
            stderr = proc.stderr.strip()
            return {
                "valid": False,
                "errors": [{"rule": "lint-runner", "id": None, "message": f"bpmnlint failed: {stderr}"}],
                "warnings": [],
                "summary": "Linting failed due to an internal error.",
            }

        raw = json.loads(proc.stdout)
        issues = raw.get("issues", [])

        errors = [i for i in issues if i.get("severity") == "error"]
        warnings = [i for i in issues if i.get("severity") == "warning"]
        valid = len(errors) == 0

        parts = []
        if errors:
            parts.append(f"{len(errors)} error{'s' if len(errors) != 1 else ''}")
        if warnings:
            parts.append(f"{len(warnings)} warning{'s' if len(warnings) != 1 else ''}")
        summary = f"Found {' and '.join(parts)}." if parts else "No issues found. The BPMN model is valid."

        return {
            "valid": valid,
            "errors": errors,
            "warnings": warnings,
            "summary": summary,
        }

    except FileNotFoundError:
        return {
            "valid": False,
            "errors": [{"rule": "lint-runner", "id": None, "message": "Node.js not found. Please install Node.js."}],
            "warnings": [],
            "summary": "Linting failed: Node.js not found.",
        }
    except subprocess.TimeoutExpired:
        return {
            "valid": False,
            "errors": [{"rule": "lint-runner", "id": None, "message": "bpmnlint timed out after 30 seconds."}],
            "warnings": [],
            "summary": "Linting failed: timeout.",
        }
    except json.JSONDecodeError as e:
        return {
            "valid": False,
            "errors": [{"rule": "lint-runner", "id": None, "message": f"Failed to parse linter output: {e}"}],
            "warnings": [],
            "summary": "Linting failed: invalid output from bpmnlint.",
        }
    finally:
        Path(tmp.name).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Call handler
# ---------------------------------------------------------------------------

@app.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    arguments = arguments or {}

    try:
        if name == "lint_bpmn":
            result = _lint_bpmn(arguments["bpmn_file"])
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
