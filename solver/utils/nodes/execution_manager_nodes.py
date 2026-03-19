import json
import re
import logging
from typing import Dict, Any
from langchain_openai import ChatOpenAI
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode
from solver.utils.tools import tools
from solver.utils.data_types import ReasoningNode, Graph

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def get_tool_calls_from_state(state: Dict[str, Any]) -> str:
    """
    Returns tool calls and their outputs from the last AI message as a formatted string.
    
    Args:
        state: The agent state containing messages
        
    Returns:
        str: Formatted string with tool calls and outputs
    """
    messages = state.get("messages", [])
    if not messages:
        return "No messages found in state"
    
    tool_executions = []
    
    # Find the last AI message with tool calls
    last_ai_message = None
    last_ai_index = -1
    
    for i in reversed(range(len(messages))):
        message = messages[i]
        if isinstance(message, AIMessage) and hasattr(message, 'tool_calls') and message.tool_calls:
            last_ai_message = message
            last_ai_index = i
            break
    
    if not last_ai_message:
        return "No AI message with tool calls found in state"
    
    # Process tool calls from the last AI message only
    for tool_call in last_ai_message.tool_calls:
        tool_name = tool_call.get('name', 'unknown')
        tool_args = tool_call.get('args', {})
        tool_id = tool_call.get('id', 'unknown')
        
        # Find corresponding tool response
        tool_result = None
        for j in range(last_ai_index + 1, len(messages)):
            next_msg = messages[j]
            if (isinstance(next_msg, ToolMessage) and 
                hasattr(next_msg, 'tool_call_id') and 
                next_msg.tool_call_id == tool_id):
                tool_result = next_msg.content
                break
        
        tool_executions.append({
            'tool_name': tool_name,
            'input_parameters': tool_args,
            'output': tool_result or "No output found"
        })
    
    # Build the results as one block
    if tool_executions:
        log_block = ["=== Tool Execution Summary ==="]
        for i, execution in enumerate(tool_executions, 1):
            log_block.append(f"Tool {i}: {execution['tool_name']}")
            log_block.append(f"  Input: {execution['input_parameters']}")
            log_block.append(f"  Output: {execution['output']}")
            log_block.append("---")
        
        return "\n".join(log_block)
    else:
        return "No tool executions found in state"


def controller(state, config):
    """
    Controls execution flow by determining the next step to execute.
    
    Args:
        state: Current agent state containing execution progress
        config: Configuration object (unused)
        
    Returns:
        dict: Contains next task to execute with context
    """
    graph = Graph.from_state(state)
    
    system_prompt = """
    Du bist der Dirigent eines Systems, das aus verschiedenen LLMs besteht.
    Dein Ziel ist es, die Aufgabe des Nutzers zu lösen.

    Dir stehen folgende Datenstrukturen zur Verfügung:
    - Ablaufplan: mit auszuführenden Schritten
    - Ergebnisse: Das Ergebnis eines Ausgeführten Schrittes
    - Beziehung der Schritte und Ergebnisse: Verknüpfungen zwischen den Nodes

    Deine Aufgabe:
    1. Versuche nachzuvollzoehen was bisher passiert ist. Was wurde ausgeführt? Welche ergebnisse passen und welche schritte sind fehlgeschlagen?
    2. Welchen schritt sollte als nächster ausgeführt werden?
    3. Braucht der nächste Schritt informationen aus einem Ergebnis?
    4. Gib den nächsten Schritt zurück und die dafür benötigten Ergebnisse zurück (Niemals mehr als einen Schritt).

    Wichtige Hinweise:
    - Gib alle Ergebnisse zurück, die als Input/Kontext notwendig sind. Der spätere Agent hat AUSSCHLIESSLICH Zugriff auf deine Rückgabe.
    - Keine Erklärungen, keine Metadaten, nur das strukturierte Dictionary.
    - Wenn du denkst der Aublaufplan ist fertig abgeschlossen, gib ein leeres Dictionary zurück.
    - Sei bei dateien so präzise wir möglich und nenne auch den gesamten namen mit endung.
    Format:
    Gib ausschließlich das Dictionary der relevanten Nodes im JSON-Format zurück (keine Erklärungen, keine Zusatztexte).

    Hier noch ein paar Beispiele:

    Beispiel 1 (kein Ergebnis ist wichtig):
    {
        "A": {
            "name": "Kundendaten laden",
            "description": "Verwende das Tool 'fetch_customer_data' mit Parameter customer_id=123",
            "goal": "Herausfinden der Kundendaten für den Customer Schneider"
        }
    }

    Beispiel 2 (Wissen aus einem Ergebnis ist wichtig):
    {
        "C": {
            "name": "Kundendaten laden",
            "description": "Verwende das Tool 'fetch_customer_data_by_id' mit Parameter für den Customer Schneider",
            "goal": "Herausfinden der Kundendaten für den Customer Schneider"
        },
        "reasoning_0": {
            "description": "Mueller: id 455, Schneider: id 584, Schulze: id 767"
        }
    }
    """
    
    user_task = {
        "description": state.get("task_description", ""),
        "inputs": state.get("inputs", {})
    }
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"""
            Deine Aufgabe: {user_task['description']}
            Inputs: {user_task['inputs']}
            
            Der stand der Dinge:
            Ablaufplan: 
                {graph.get_operations_nodes_pp()}
            Ergebnisse: 
                {graph.get_reasoning_nodes_pp()}
            Beziehungen: 
                {graph.get_edges_pp()}
            
        """}
    ]
    
    model = ChatOpenAI(model_name="gpt-5.2", request_timeout=180)
    response = model.invoke(messages)

    response_content = response.content
    next_task = ""
    
    if "```json" in response_content:
        start_idx = response_content.find("```json") + 7
        end_idx = response_content.find("```", start_idx)
        if end_idx != -1:
            next_task = response_content[start_idx:end_idx].strip()
    elif "```" in response_content:
        start_idx = response_content.find("```") + 3
        end_idx = response_content.find("```", start_idx)
        if end_idx != -1:
            next_task = response_content[start_idx:end_idx].strip()
    else:
        next_task = response_content.strip()
    
    # Return as list with task as first element
    return {
        "next_task": [next_task]
    }


def controller_guard(state):
    """
    Guard function to determine if execution should continue or finish.
    
    Args:
        state: Current agent state containing next task
        
    Returns:
        str: Routing decision (tool_execution/finish)
    """
    next_task = state.get("next_task", [])
    
    # Check if next_task is empty or contains only "{}" 
    if next_task and next_task != ["{}"] and next_task[0] != "{}":
        return "tool_execution"
    else:
        return "finish"


def prompter_tool_call(state, config):
    """
    Formats task for tool execution and calls appropriate tool.
    
    Args:
        state: Current agent state containing next task
        config: Configuration object (unused)
        
    Returns:
        dict: Contains tool execution response
    """
    # Extract description from next_task JSON
    next_task = state.get("next_task", [])
    task_element = next_task[0] if next_task else ""
    
    try:
        # Parse JSON to extract task and reasoning information
        # Clean the JSON string to remove invalid control characters
        task_str = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', task_element)
        
        task_data = json.loads(task_str)
        
        # Find the main task (operation node)
        main_task = None
        reasoning_info = []
        
        for key, value in task_data.items():
            if isinstance(value, dict):
                # Handle dictionary values - operations nodes have name, description, goal
                if "name" in value and "description" in value and "goal" in value:
                    main_task = value
                elif key.startswith("reasoning_"):
                    reasoning_info.append(f"{key}: {value.get('description', '')}")
            elif isinstance(value, str):
                # Handle string values - check for tool_execution in string content
                if "tool_execution" in value.lower() and main_task is None:
                    # Create a simple dict structure from string
                    main_task = {"description": value, "name": "Task from string"}
                elif key.startswith("reasoning_"):
                    reasoning_info.append(f"{key}: {value}")
        
        if main_task:
            task_description = main_task.get("description", "")
            task_name = main_task.get("name", "")
        else:
            # Fallback if no tool_execution found
            task_description = task_element
            task_name = "Unknown task"
            
    except (json.JSONDecodeError, KeyError, IndexError):
        # Fallback to original task_element if parsing fails
        task_description = task_element
        task_name = "Unknown task"
        reasoning_info = []
    
    # Create formatted system prompt
    system_prompt = """Du bist ein Assistent für eine Business Process Technology Plattform. 
    
    Deine Aufgabe ist es, das am besten passende Tool aus den verfügbaren Tools auszuwählen und auszuführen.
    
    Wichtige Regeln:
    - Wähle das Tool, das am besten zur Beschreibung passt
    - Falls ein Tool Eingaben benötigt, nutze die bereitgestellten Informationen
    - Antworte ausschließlich mit der Ausführung des entsprechenden Tools
    - Keine Erklärungen oder zusätzlichen Kommentare"""
    
    # Create formatted user message
    user_content = f"""AUFGABE: {task_name}

Beschreibung: {task_description}"""
    
    # Add reasoning information if available
    if reasoning_info:
        user_content += "\n\nZusätzliche Informationen:\n"
        for info in reasoning_info:
            user_content += f"- {info}\n"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content}
    ]
    
    model = ChatOpenAI(temperature=0, model_name="gpt-5.2", request_timeout=180).bind_tools(tools)
    response = model.invoke(messages)

    return {
        "messages": [response]
    }


def parser(state, config):
    """
    Parses tool execution results and creates reasoning nodes.
    
    Args:
        state: Current agent state containing tool execution results
        config: Configuration object (unused)
        
    Returns:
        dict: Contains updated reasoning nodes and graph structure
    """
    messages = state.get("messages", [])
    last_message = messages[-1] if messages else None
    next_task = state.get("next_task", [])
    
    graph = Graph.from_state(state)
    
    # Get task goal for summarization
    task_goal = ""
    used_reasoning_nodes = []
    try:
        if next_task:
            # Clean the JSON string to remove invalid control characters
            task_str = next_task[0]
            
            # Remove invalid control characters except newlines and tabs
            task_str = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', task_str)
            
            task_data = json.loads(task_str)
            for key, value in task_data.items():
                if isinstance(value, dict):
                    # Handle dictionary values - operations nodes have name, description, goal
                    if "name" in value and "description" in value and "goal" in value:
                        task_goal = value.get("goal", "")
                        task_description = value.get("description", "")
                    elif key.startswith("reasoning_"):
                        # Track reasoning nodes that were used as context
                        used_reasoning_nodes.append(key)
                elif isinstance(value, str):
                    # Handle string values - check for tool_execution in string content
                    if "tool_execution" in value.lower():
                        task_description = value
                        task_goal = ""  # No goal available in string format
                    elif key.startswith("reasoning_"):
                        # Track reasoning nodes that were used as context
                        used_reasoning_nodes.append(key)
    except (json.JSONDecodeError, KeyError, IndexError):
        task_goal = ""
        task_description = ""
    
    # Summarize tool response based on task goal
    raw_output = last_message.content if last_message else 'No output'
    raw_toolname = last_message.name if last_message else 'No tool name'
    
    if task_goal and raw_output != 'No output':
        system_prompt = """Du bist ein Assistent, der Tool-Ausgaben zusammenfasst.
        
        Deine Aufgabe ist es, die Tool-Ausgabe basierend auf dem Ziel der Aufgabe zu filtern und zusammenzufassen.
        
        Regeln:
        - Behalte nur Informationen, die für das Ziel relevant sind
        - Entferne unnötige Details, Fehlermeldungen oder irrelevante Informationen
        - Gib eine präzise, zielgerichtete Zusammenfassung zurück
        - Falls die Ausgabe einen Fehler enthält, gib den Fehler klar an
        - Wenn es um IDs darteinamen oder ähnliches geht. Gebe diese immer 1:1 zurück ohne zeichen zu entfernen, groß oder kleinschreibung zu verändern oder sonstiges zu tun. Nenne den gesamten namen mit Endung."""
        
        user_content = f"""Aufgabe war es: {task_description}

        mit dem Ziel: {task_goal}
        
        Folgendes Tool wurde ausgeführt: 
        {get_tool_calls_from_state(state)}
        
        Bitte fasse die Ausgabe basierend auf dem Ziel zusammen."""
        
        summarization_messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]
        
        model = ChatOpenAI(temperature=0, model_name="gpt-5-mini", request_timeout=120)
        summary_response = model.invoke(summarization_messages)
        summarized_output = summary_response.content
    else:
        summarized_output = raw_output
    
    reasoning_node = ReasoningNode(
        description=summarized_output
    )
    
    reasoning_node_id = f"reasoning_{len(graph.get_reasoning_nodes())}"
    graph.add_reasoning_node(reasoning_node_id, reasoning_node)
    
    # Add connecting edge from operation node to reasoning node
    operation_node_id = graph.find_operation_node_id(next_task)
    if operation_node_id:
        graph.add_connecting_edge(operation_node_id, reasoning_node_id)
    
    # Add edges from used reasoning nodes to the new reasoning node
    for used_reasoning_node_id in used_reasoning_nodes:
        if used_reasoning_node_id in graph.get_reasoning_nodes():
            graph.add_reasoning_edge(used_reasoning_node_id, reasoning_node_id)
    
    return {
        "nodes_of_reasoning": graph.get_reasoning_nodes(),
        "edges_of_operations": graph.get_edges(),
        "edges_of_reasoning": graph.get_edges_of_reasoning(),
        "connecting_edges": graph.get_connecting_edges(),
        "mermaid": graph.state_mermaid(),
        "next_task": None
    }

# Tool node for executing actual tool calls
tool_node = ToolNode(tools)
