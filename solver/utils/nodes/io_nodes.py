from pathlib import Path
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from solver.utils.data_types import TaskAnalysis, Graph

# Load environment variables from project root .env file
load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")


def summerize_task(state, config):
    """
    Analyzes user message and extracts structured task information.
    
    Args:
        state: Current agent state containing user messages
        config: Configuration object (unused)
        
    Returns:
        dict: Contains task_description, inputs, and available files
    """
    system_prompt = """Du bist ein Assistent einer Business Process Technology Plattform und hilfst Usern beim Lösen von Aufgaben. 
    
    Analysiere die Nachricht des Users und extrahiere:
    1. Eine detaillierte Beschreibung der Aufgabe (Wichtig ist das infos wie Was, wie und womit bei deiner Extration nicht verloren gehen. Behalte angegebene reinfolgen bei.)
    2. Alle gegebenen Inputs/Parameter/dateien die genutzt werden sollen (Keine schritte sondern Dateinamen oder ähnliches. Diese sollen auch in der task description enthalten sein)
    
    Gib eine strukturierte Zusammenfassung zurück.
    """
    
    messages = state["messages"]
    messages = [{"role": "system", "content": system_prompt}] + messages

    model = ChatOpenAI(temperature=0, model_name="gpt-5.2", request_timeout=180)
    structured_model = model.with_structured_output(TaskAnalysis)
    response = structured_model.invoke(messages)
    
    return {
        "task_description": str(response.task_description),
        "inputs": response.inputs,
    }


def return_to_user(state, config):
    """
    Formats final results for user presentation.
    
    Args:
        state: Current agent state containing task results
        config: Configuration object (unused)
        
    Returns:
        dict: Contains formatted message for user
    """
    graph = Graph.from_state(state)

    system_prompt = f"""Du bist ein Assistent einer Business Process Technology Plattform und hilfst Usern beim Lösen von Aufgaben.

        Es gab folgende Aufgabe:
        {state.get("task_description")}

        Es gab folgende Inputs:
        {state.get("inputs")}

        Es gab folgende Ergebnisse:
        {graph.to_mermaid()}

        Deine aufgabe ist das ergebniss für den user zusammen zu fassen.
        """

    messages = [{"role": "system", "content": system_prompt}]

    model = ChatOpenAI(temperature=0.5, model_name="gpt-5-mini", request_timeout=120)
    response = model.invoke(messages)

    return {
        "messages": [{"role": "assistant", "content": response.content}]
    }
