from typing import Literal
from typing_extensions import TypedDict

from langgraph.graph import StateGraph, END
from solver.utils.nodes import (
    summerize_task, 
    return_to_user,
    create_strategy, 
    router, 
    router_guard, 
    replan_strategy,
    controller, 
    parser, 
    controller_guard, 
    prompter_tool_call, 
    tool_node
)
from solver.utils.state import AgentState



class GraphConfig(TypedDict):
    model_name: Literal["anthropic", "openai"]



workflow = StateGraph(AgentState, config_schema=GraphConfig)


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
workflow.add_conditional_edges("Router", router_guard, {
    "continue": "Controller - Decide next step",
    "replan": "Replan Strategy",
})
workflow.add_edge("Replan Strategy", "Controller - Decide next step")
workflow.add_conditional_edges("Controller - Decide next step", controller_guard, {
    "finish": "Return to User",
    "tool_execution": "Prompter - Create prompt for tool call",
})
workflow.add_edge("Prompter - Create prompt for tool call", "Tool Node - Execute tool")
workflow.add_edge("Tool Node - Execute tool", "Parser - Parse answer")
workflow.add_edge("Parser - Parse answer", "Router")

workflow.add_edge("Return to User", END)

graph = workflow.compile()
