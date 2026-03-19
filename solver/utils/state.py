from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage
from typing import Annotated, Optional, List, Dict
from typing_extensions import TypedDict, NotRequired
from solver.utils.data_types import OperationsNode, ReasoningNode

class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

    task_description: NotRequired[str]
    inputs: NotRequired[List[str]]
    next_task: NotRequired[Optional[List[str]]]  # First element is the task, rest can be reasoning nodes

    nodes_of_operations: NotRequired[Dict[str, OperationsNode]]  # Operation nodes with OperationsNode metadata
    nodes_of_reasoning: NotRequired[Dict[str, ReasoningNode]]  # Reasoning nodes with description
    edges_of_operations: NotRequired[Dict[str, List[str]]]  # Adjacency list for the graph
    edges_of_reasoning: NotRequired[Dict[str, List[str]]]
    connecting_edges: NotRequired[Dict[str, List[str]]]  # Edges connecting operations and reasoning nodes

    mermaid: NotRequired[str]
