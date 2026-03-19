"""Workshop Test Harness — orchestrates per-run lifecycle with the new workbench.

Usage (standalone quick-test, no agent)::

    python -m testing.test_environment
"""

import json
import logging
import os
import re
import shutil
import sys
import uuid

from pathlib import Path
from typing import Dict, Any

from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode

# ---------------------------------------------------------------------------
# Ensure the project root is importable
# ---------------------------------------------------------------------------
_project_root = Path(__file__).parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from solver.utils.state import AgentState
from solver.utils.nodes.io_nodes import summerize_task, return_to_user
from solver.utils.nodes.planner_nodes import (
    create_strategy,
    router,
    router_guard,
    replan_strategy,
)
from solver.utils.nodes.execution_manager_nodes import (
    controller,
    controller_guard,
    parser,
)
from workshop.workbench.config import DATA_DIR
from workshop.workbench.file_manager import FileManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Graph builder — creates a fresh graph with custom (per-run) tools
# ---------------------------------------------------------------------------

def _make_prompter_tool_call(custom_tools):
    """Return a ``prompter_tool_call`` node that uses *custom_tools*."""

    def prompter_tool_call(state: Dict[str, Any], config_arg=None):
        next_task = state.get("next_task", [])
        task_element = next_task[0] if next_task else ""

        try:
            task_str = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', task_element)
            task_data = json.loads(task_str)

            main_task = None
            reasoning_info = []

            for key, value in task_data.items():
                if isinstance(value, dict):
                    if "name" in value and "description" in value and "goal" in value:
                        main_task = value
                    elif key.startswith("reasoning_"):
                        reasoning_info.append(f"{key}: {value.get('description', '')}")
                elif isinstance(value, str):
                    if "tool_execution" in value.lower() and main_task is None:
                        main_task = {"description": value, "name": "Task from string"}
                    elif key.startswith("reasoning_"):
                        reasoning_info.append(f"{key}: {value}")

            if main_task:
                task_description = main_task.get("description", "")
                task_name = main_task.get("name", "")
            else:
                task_description = task_element
                task_name = "Unknown task"

        except (json.JSONDecodeError, KeyError, IndexError):
            task_description = task_element
            task_name = "Unknown task"
            reasoning_info = []

        system_prompt = """Du bist ein Assistent für eine Business Process Technology Plattform.

    Deine Aufgabe ist es, das am besten passende Tool aus den verfügbaren Tools auszuwählen und auszuführen.

    Wichtige Regeln:
    - Wähle das Tool, das am besten zur Beschreibung passt
    - Falls ein Tool Eingaben benötigt, nutze die bereitgestellten Informationen
    - Antworte ausschließlich mit der Ausführung des entsprechenden Tools
    - Keine Erklärungen oder zusätzlichen Kommentare"""

        user_content = f"""AUFGABE: {task_name}

Beschreibung: {task_description}"""

        if reasoning_info:
            user_content += "\n\nZusätzliche Informationen:\n"
            for info in reasoning_info:
                user_content += f"- {info}\n"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        model = ChatOpenAI(temperature=0, model_name="gpt-5.2").bind_tools(custom_tools)
        response = model.invoke(messages)

        return {"messages": [response]}

    return prompter_tool_call


def build_agent_graph(custom_tools):
    """Build the agent graph with *custom_tools* (for user-isolation)."""
    prompter_tool_call = _make_prompter_tool_call(custom_tools)
    tool_node = ToolNode(custom_tools)

    workflow = StateGraph(AgentState)

    workflow.add_node("Summerize User task", summerize_task)
    workflow.add_node("Create Strategy", create_strategy)
    workflow.add_node("Controller - Decide next step", controller)
    workflow.add_node("Return to User", return_to_user)
    workflow.add_node("Prompter - Create prompt for tool call", prompter_tool_call)
    workflow.add_node("Parser - Parse answer", parser)
    workflow.add_node("Router", router)
    workflow.add_node("Replan Strategy", replan_strategy)
    workflow.add_node("Tool Node - Execute tool", tool_node)

    workflow.set_entry_point("Summerize User task")
    workflow.add_edge("Summerize User task", "Create Strategy")
    workflow.add_edge("Create Strategy", "Router")
    workflow.add_conditional_edges(
        "Router",
        router_guard,
        {"continue": "Controller - Decide next step", "replan": "Replan Strategy"},
    )
    workflow.add_edge("Replan Strategy", "Controller - Decide next step")
    workflow.add_conditional_edges(
        "Controller - Decide next step",
        controller_guard,
        {"finish": "Return to User", "tool_execution": "Prompter - Create prompt for tool call"},
    )
    workflow.add_edge("Prompter - Create prompt for tool call", "Tool Node - Execute tool")
    workflow.add_edge("Tool Node - Execute tool", "Parser - Parse answer")
    workflow.add_edge("Parser - Parse answer", "Router")
    workflow.add_edge("Return to User", END)

    return workflow.compile()


# ---------------------------------------------------------------------------
# Test harness class
# ---------------------------------------------------------------------------

class WorkshopTestHarness:
    """Manages one full test lifecycle: user → data → agent → cleanup."""

    def __init__(self):
        self.user_id: str | None = None
        self.file_manager: FileManager | None = None

    async def setup(self, user_id: str | None = None, test_data_dir: str | None = None) -> str:
        """Create a fresh user directory and optionally copy test data."""
        self.user_id = user_id or f"test-{uuid.uuid4().hex[:8]}"
        self.file_manager = FileManager(str(DATA_DIR), self.user_id)
        self.file_manager.ensure_user_dir()

        # Copy test data files into the user's directory
        if test_data_dir:
            src = Path(test_data_dir)
            if src.exists():
                for f in src.iterdir():
                    if f.is_file():
                        shutil.copy2(f, self.file_manager.user_dir / f.name)

        return self.user_id

    def get_mcp_server_config(self) -> dict:
        """MCP server config dict with the current user's USER_ID in env."""
        from solver.utils.tool_components.mcp_tools import WORKBENCH_MCP_PATH
        return {
            "workbench": {
                "command": "python",
                "args": [WORKBENCH_MCP_PATH],
                "transport": "stdio",
                "env": {**os.environ, "USER_ID": self.user_id},
            }
        }

    async def run_agent(self, question: str) -> dict:
        """Run the agent graph with workshop MCP tools.

        1. Start a ``MultiServerMCPClient`` with the harness MCP config.
        2. Get tools from the MCP client.
        3. Build a fresh agent graph bound to those tools.
        4. Invoke the graph and extract the response.
        """
        from langchain_mcp_adapters.client import MultiServerMCPClient

        mcp_client = MultiServerMCPClient(self.get_mcp_server_config())
        tools = await mcp_client.get_tools()
        graph = build_agent_graph(tools)

        result = await graph.ainvoke(
            {"messages": [{"role": "user", "content": question}]},
            {"recursion_limit": 120},
        )

        if not result or "messages" not in result or not result["messages"]:
            return {"response": "No response generated"}

        last_message = result["messages"][-1]
        if hasattr(last_message, "content"):
            response_content = last_message.content
        elif isinstance(last_message, dict):
            response_content = last_message.get("content", str(last_message))
        else:
            response_content = str(last_message)

        if response_content is None:
            response_content = "No response content"

        return {"response": response_content, "mermaid": result.get("mermaid", "")}

    async def extract_results(self) -> dict:
        """Return all files currently stored for this user."""
        files = self.file_manager.list_files()
        file_contents = {}
        for f in files:
            try:
                file_contents[f] = self.file_manager.read_file(f)
            except Exception:
                file_contents[f] = "<read error>"
        return {"user_id": self.user_id, "files": files, "contents": file_contents}

    def get_output_files(self) -> dict[str, Path]:
        """Return a mapping of filename → absolute Path for all user files.

        Unlike ``extract_results`` this does not read file contents, making
        it safe for binary files (PNG, etc.) and useful for file-comparison
        based evaluation.
        """
        result: dict[str, Path] = {}
        if self.file_manager:
            for rel_name in self.file_manager.list_files():
                result[rel_name] = (self.file_manager.user_dir / rel_name).resolve()
        return result

    async def cleanup(self) -> None:
        """Delete the user directory."""
        if self.file_manager:
            self.file_manager.delete_user_dir()


# ---------------------------------------------------------------------------
# Standalone quick-test (no agent, just lifecycle)
# ---------------------------------------------------------------------------

async def _quick_test():
    harness = WorkshopTestHarness()

    try:
        print("[1/4] Setting up user …")
        user_id = await harness.setup(
            test_data_dir=str(_project_root / "testing" / "test_data")
        )
        print(f"  → User: {user_id}")

        print("[2/4] Listing files …")
        results = await harness.extract_results()
        print(f"  → Files: {results['files']}")

        print("[3/4] MCP server config:")
        mcp_cfg = harness.get_mcp_server_config()
        print(f"  → command: {mcp_cfg['workbench']['command']}")
        print(f"  → args: {mcp_cfg['workbench']['args']}")
        print(f"  → USER_ID set: {mcp_cfg['workbench']['env'].get('USER_ID')}")

        print("[4/4] Cleaning up …")
    finally:
        await harness.cleanup()

    print("Done — lifecycle OK.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(_quick_test())
