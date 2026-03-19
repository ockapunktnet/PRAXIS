"""Tool registry: discovers and manages Tool MCP Server subprocesses."""

import asyncio
import logging
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

logger = logging.getLogger(__name__)


class ToolConnection:
    """Holds a live connection to a single Tool MCP Server."""

    def __init__(self, name: str, params: StdioServerParameters):
        self.name = name
        self.params = params
        self.session: ClientSession | None = None
        self._cm = None  # context manager for stdio_client
        self._read = None
        self._write = None
        self._tools: list[dict] = []

    async def connect(self) -> None:
        """Start the subprocess and initialise the MCP session."""
        self._cm = stdio_client(self.params)
        self._read, self._write = await self._cm.__aenter__()
        self.session = ClientSession(self._read, self._write)
        await self.session.__aenter__()
        await self.session.initialize()
        # Cache the tool list
        result = await self.session.list_tools()
        self._tools = result.tools
        logger.info(
            "Connected to tool %s — %d tools available",
            self.name,
            len(self._tools),
        )

    async def disconnect(self) -> None:
        """Tear down the session and subprocess."""
        if self.session:
            await self.session.__aexit__(None, None, None)
        if self._cm:
            await self._cm.__aexit__(None, None, None)

    @property
    def tools(self) -> list[dict]:
        return self._tools

    async def call_tool(self, tool_name: str, arguments: dict) -> Any:
        """Forward a tool call to the subprocess."""
        if not self.session:
            raise RuntimeError(f"Not connected to {self.name}")
        result = await self.session.call_tool(tool_name, arguments)
        return result


class ToolRegistry:
    """Manages all Tool MCP Server connections."""

    def __init__(self, tools_config: dict):
        """
        Args:
            tools_config: mapping of tool-name → config dict with keys
                ``command``, ``args``, ``transport``.
        """
        self._config = tools_config
        self._connections: dict[str, ToolConnection] = {}
        # tool_name → connection name (for routing calls)
        self._tool_index: dict[str, str] = {}

    async def connect_tools(self) -> None:
        """Start all configured tool subprocesses and connect."""
        for name, cfg in self._config.items():
            params = StdioServerParameters(
                command=cfg["command"],
                args=cfg.get("args", []),
                env=cfg.get("env"),
            )
            conn = ToolConnection(name, params)
            try:
                await conn.connect()
                self._connections[name] = conn
                # Index each tool so we can route calls later
                for tool in conn.tools:
                    tool_name = tool.name if hasattr(tool, "name") else tool.get("name", "")
                    self._tool_index[tool_name] = name
            except Exception:
                logger.exception("Failed to connect tool %s", name)

    async def list_tools(self) -> list[dict]:
        """Return a flat list of all tools from all connected servers."""
        all_tools = []
        for conn in self._connections.values():
            all_tools.extend(conn.tools)
        return all_tools

    async def call_tool(self, name: str, arguments: dict) -> Any:
        """Route a tool call to the correct subprocess."""
        conn_name = self._tool_index.get(name)
        if conn_name is None:
            raise ValueError(f"Unknown tool: {name}")
        conn = self._connections[conn_name]
        return await conn.call_tool(name, arguments)

    async def disconnect(self) -> None:
        """Shut down all tool subprocesses."""
        for conn in self._connections.values():
            try:
                await conn.disconnect()
            except Exception:
                logger.exception("Error disconnecting %s", conn.name)
        self._connections.clear()
        self._tool_index.clear()
