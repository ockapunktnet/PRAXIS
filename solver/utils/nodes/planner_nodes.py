import json
import re
import logging
from json_repair import repair_json
from langchain_openai import ChatOpenAI
from solver.utils.tools import tools_string
from solver.utils.data_types import OperationsNode, ExecutionGraph, Graph, RouterDecision

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)



def create_strategy(state, config):
    """
    Creates initial execution strategy as a directed graph.
    
    Args:
        state: Current agent state containing task description
        config: Configuration object (unused)
        
    Returns:
        dict: Contains operation nodes and edges for execution plan
    """
    system_prompt = """Du bist ein Assistent für eine Business Process Technology Plattform. Deine Aufgabe ist es, einen Ablaufplan zur Lösung der jeweiligen Aufgabe zu erstellen. Dafür kannst du alle dir zur Verfügung stehenden Tools oder ein allgemeines LLM einplanen.

    Hinweis zu deiner Umbgbung: Du bist nur ein Assistent und hast zugriff auf ein System was tools für dich bereitstellt. Das System kümmert sich darum das nach toolaufrufen die Rückgabe wenn sie etwas länger ist automatisch in einer datei gespeichert wird.



    Wichtig:
    - Du erstellst ausschließlich den Ablaufplan und beschreibst das geplante Vorgehen (Verwende bei namne für ausgabedeteien keine Dateiendungen).
    - Du führst keine Schritte selbst aus.
    - Gib den Ablaufplan als JSON-String zurück.
    - Inputs sind entweder in der Aufgabenstellung vom nutzer oder müssen über tools wie get_all_models() erhalten werden.
    - overthinke nicht. versuche simpele wege.

    Vorgehen:
    Erstelle einen Ablaufplan als gerichteten Graphen in folgender JSON-Struktur:

    {
        "nodes": {
            "A": {
                "name": "Kurzer name",
                "description": "Was genau soll getan werden?",
                "goal": "Was bringt dieser Schritt bezogen auf die gesamt Aufgabe?"
            }
        },
        "edges": {
            "A": []
        }
    }

    - Die "description" muss das zu nutzende Tool inklusive der Parameter beschreiben.
    - WICHTIG: Verwende KEINE doppelten Anführungszeichen innerhalb von JSON-String-Werten. Nutze stattdessen einfache Anführungszeichen (') oder beschreibe Parameter ohne Anführungszeichen.

    Zusatzhinweis:
    Du erstellst keinen Code zur Ausführung der Schritte, sondern NUR den Ablaufplan als JSON-String OHNE weitere Erklärungen.
    """
    
    messages = [
        {"role": "system", "content": system_prompt + tools_string},
        {"role": "user", "content": state.get("task_description")}
    ]
    
    model = ChatOpenAI(temperature=0, model_name="gpt-5.2", model_kwargs={"reasoning_effort": "high"}, request_timeout=180)
    structured_model = model.with_structured_output(ExecutionGraph)
    response = structured_model.invoke(messages)

    try:
        # Clean the JSON string to remove invalid control characters
        graph_data_str = response.graph_data

        # Remove all control characters except newlines, tabs, and carriage returns
        # This includes the problematic \x19 character
        graph_data_str = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]', '', graph_data_str)

        # Handle double-escaped JSON strings
        if graph_data_str.startswith('"') and graph_data_str.endswith('"'):
            graph_data_str = json.loads(graph_data_str)

        try:
            graph_data = json.loads(graph_data_str)
        except json.JSONDecodeError:
            logger.warning("JSON parse failed, attempting json_repair...")
            repaired = repair_json(graph_data_str)
            graph_data = json.loads(repaired)

        graph = Graph()

        for node_id, node_data in graph_data["nodes"].items():
            operation_node = OperationsNode(
                name=node_data["name"],
                description=node_data["description"],
                goal=node_data["goal"]
            )
            graph.add_operation_node(node_id, operation_node)

        for source, targets in graph_data["edges"].items():
            for target in targets:
                graph.add_edge(source, target)

    except Exception as e:
        raise RuntimeError(f"Failed to parse planner graph_data: {e}") from e

    return {
        "nodes_of_operations": graph.get_operations_nodes(),
        "edges_of_operations": graph.get_edges(),
    }


def router(state, config):
    """
    Decides whether to continue with current plan or replan strategy.
    
    Args:
        state: Current agent state containing execution progress
        config: Configuration object (unused)
        
    Returns:
        dict: Contains router decision (continue/replan)
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
    2. Wenn du denkst das der Ablaufplan aus logisch falsch ist und inkonsetenzen aufweist oder mehrmals der veruch einen Schritt auszuführen fehlgeschlagen ist gib "replan" zurück.
    3. Wenn du denkst das der Ablaufplan korrekt ist oder du dir zum jetzigen zeitpunkt noch unsicher bist gib "continue" zurück.

    Wichtig: Solange du keine Reasoning schritte siehtst ist alles noch okey! da die bearbeitung noch nicht angefangen hat. Falls es also zu einer aufgabe noch kein ergbniss gibt heißt es das diese Aufgabe noch nicht bekonnen wurde. Sollte ein fehler auftreten beim versuch der ausführung so würde dies auch als ergebniss auftreten
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
 
    model = ChatOpenAI(temperature=0.5, model_name="gpt-5-mini", request_timeout=120)
    structured_model = model.with_structured_output(RouterDecision)
    response = structured_model.invoke(messages)

    return {
        "next_task": [response]
    } 


def replan_strategy(state, config):
    """
    Replans execution strategy based on current results and feedback.
    
    Args:
        state: Current agent state containing execution progress and critique
        config: Configuration object (unused)
        
    Returns:
        dict: Contains updated operation nodes and edges
    """
    graph = Graph.from_state(state)
    
    system_prompt = """
    Du bist der Dirigent eines Systems, das aus verschiedenen LLMs besteht.
    Dein Ziel ist es, die Aufgabe des Nutzers zu lösen.

    Dir stehen folgende Datenstrukturen zur Verfügung:
    - Ablaufplan: mit auszuführenden Schritten
    - Ergebnisse: Das Ergebnis eines Ausgeführten Schrittes
    - Kritik: Eine begründung warum der Ablaufplan geändert werden soll

    Deine Aufgabe:
    1. Versuche nachzuvollzoehen was bisher passiert ist. Was wurde ausgeführt? Welche ergebnisse passen und welche schritte sind fehlgeschlagen?
    2. Änder den Ablaufplan so minimal wie möglich um die Kritik zu beheben.

    Wichtig: Solange du keine Reasoning schritte siehtst ist alles noch okey da die bearbeitung noch nicht angefangen hat.

    Hinweis zu deiner Umbgbung: Du bist nur ein Assistent und hast zugriff auf ein System was tools für dich bereitstellt. Das System kümmert sich darum das nach toolaufrufen die Rückgabe wenn sie etwas länger ist automatisch in einer datei gespeichert wird.


    Wichtig:
    - Du erstellst ausschließlich den Ablaufplan und beschreibst das geplante Vorgehen (Verwende bei namne für ausgabedeteien keine Dateiendungen).
    - Du führst keine Schritte selbst aus.
    - Gib den Ablaufplan als JSON-String zurück.
    - Inputs sind entweder in der Aufgabenstellung vom nutzer oder müssen über tools wie get_all_models() erhalten werden.
    - Reasoning schritte sind nicht teil des Ablaufplans sondern werden automatisch immer beim abarbeiten des ablaufplans gemacht.

    Vorgehen:
    Erstelle einen Ablaufplan als gerichteten Graphen welche zwingend die folgende JSON-Struktur hat:

    {
        "nodes": {
            "A": {
                "name": "Kurzer name",
                "description": "WAS soll WOMIT genau getan werden?",
                "goal": "Was bringt dieser Schritt bezogen auf die gesamt Aufgabe?"
            }
        },
        "edges": {
            "A": []
        }
    }

    - WICHTIG: Verwende KEINE doppelten Anführungszeichen innerhalb von JSON-String-Werten. Nutze stattdessen einfache Anführungszeichen (') oder beschreibe Parameter ohne Anführungszeichen.

    Dir stehen folgende Tools zur Verfügung:

    """
    
    user_task = {
        "description": state.get("task_description", ""),
        "inputs": state.get("inputs", {})
    }
    
    messages = [
        {"role": "system", "content": system_prompt + tools_string},
        {"role": "user", "content": f"""
            Deine Aufgabe: {user_task['description']}
            Inputs: {user_task['inputs']}
            
            Der stand der Dinge:
            Ablaufplan: 
                {graph.get_operations_nodes_pp()}
            Ergebnisse: 
                {graph.get_reasoning_nodes_pp()}
            Kritik: 
                {state.get("next_task", "")}
        """}
    ]
 
    model = ChatOpenAI(temperature=0, model_name="gpt-5.2", model_kwargs={"reasoning_effort": "high"}, request_timeout=180)
    structured_model = model.with_structured_output(ExecutionGraph)
    response = structured_model.invoke(messages)

    try:
        # Clean the JSON string to remove invalid control characters
        graph_data_str = response.graph_data

        # Remove all control characters except newlines, tabs, and carriage returns
        # This includes the problematic \x19 character
        graph_data_str = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]', '', graph_data_str)

        # Handle double-escaped JSON strings
        if graph_data_str.startswith('"') and graph_data_str.endswith('"'):
            graph_data_str = json.loads(graph_data_str)

        try:
            graph_data = json.loads(graph_data_str)
        except json.JSONDecodeError:
            logger.warning("JSON parse failed in replan, attempting json_repair...")
            repaired = repair_json(graph_data_str)
            graph_data = json.loads(repaired)

        graph = Graph()

        for node_id, node_data in graph_data["nodes"].items():
            operation_node = OperationsNode(
                name=node_data["name"],
                description=node_data["description"],
                goal=node_data["goal"]
            )
            graph.add_operation_node(node_id, operation_node)

        for source, targets in graph_data["edges"].items():
            for target in targets:
                graph.add_edge(source, target)

    except Exception as e:
        raise RuntimeError(f"Failed to parse replanner graph_data: {e}\nResponse: {response}") from e
    
    return {
        "nodes_of_operations": graph.get_operations_nodes(),
        "edges_of_operations": graph.get_edges(),
        "nodes_of_reasoning": {},
        "edges_of_reasoning": graph.get_edges_of_reasoning(),
        "connecting_edges": {},
        "mermaid": graph.state_mermaid(),
    }


def router_guard(state):
    """
    Guard function to determine routing based on router decision.
    
    Args:
        state: Current agent state containing router decision
        
    Returns:
        str: Routing decision (continue/replan/finish)
    """
    next_task = state.get("next_task", [])
    
    # Get the first element which is the router decision
    decision_obj = next_task[0] if next_task else None
    
    # Handle RouterDecision object or string
    if hasattr(decision_obj, 'decision'):
        decision = decision_obj.decision
    else:
        decision = str(decision_obj) if decision_obj else ""
    
    if decision == "continue":
        return "continue"
    elif decision == "replan":
        return "replan"
    else:
        return "finish"
