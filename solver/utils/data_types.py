from pydantic import BaseModel, Field
from typing import Dict, List, Set, Iterator, Literal, Optional, Any


class OperationsNode(BaseModel):
    """Metadata for a single DAG node."""
    name: str
    description: str
    goal: str


class ReasoningNode(BaseModel):
    """Metadata for a reasoning node."""
    description: str


class Graph(BaseModel):
    """Simple graph class that reads node sets and edges from state."""
    
    nodes_of_operations: Dict[str, OperationsNode] = Field(default_factory=dict)
    nodes_of_reasoning: Dict[str, ReasoningNode] = Field(default_factory=dict)
    edges_of_operations: Dict[str, List[str]] = Field(default_factory=dict)
    edges_of_reasoning: Dict[str, List[str]] = Field(default_factory=dict)  
    connecting_edges: Dict[str, List[str]] = Field(default_factory=dict)  
    
    @classmethod
    def from_state(cls, state: Dict[str, Any]) -> "Graph":
        """Create Graph from state dictionary."""
        return cls(
            nodes_of_operations=state.get("nodes_of_operations", {}),
            nodes_of_reasoning=state.get("nodes_of_reasoning", {}),
            edges_of_operations=state.get("edges_of_operations", {}),
            edges_of_reasoning=state.get("edges_of_reasoning", {}),
            connecting_edges=state.get("connecting_edges", {})
        )
    
    
    def add_operation_node(self, node_id: str, node: OperationsNode) -> None:
        """Add operation node to graph."""
        self.nodes_of_operations[node_id] = node
        if node_id not in self.edges_of_operations:
            self.edges_of_operations[node_id] = []
    
    def add_reasoning_node(self, node_id: str, node: ReasoningNode) -> None:
        """Add reasoning node to graph."""
        self.nodes_of_reasoning[node_id] = node
        if node_id not in self.edges_of_operations:
            self.edges_of_operations[node_id] = []
    
    def add_edge(self, source: str, target: str) -> None:
        """Add edge from source to target."""
        if source not in self.edges_of_operations:
            self.edges_of_operations[source] = []
        self.edges_of_operations[source].append(target)
    
    def add_reasoning_edge(self, source: str, target: str) -> None:
        """Add edge from reasoning source to reasoning target."""
        if source not in self.edges_of_reasoning:
            self.edges_of_reasoning[source] = []
        self.edges_of_reasoning[source].append(target)
    
    def add_connecting_edge(self, source: str, target: str) -> None:
        """Add connecting edge from operations node to reasoning node or vice versa."""
        if source not in self.connecting_edges:
            self.connecting_edges[source] = []
        self.connecting_edges[source].append(target)
    
    
    def get_operations_nodes(self) -> Dict[str, OperationsNode]:
        """Get operations nodes."""
        return self.nodes_of_operations
    
    def get_operations_nodes_pp(self) -> str:
        """Get operations nodes in pretty print format."""
        if not self.nodes_of_operations:
            return "No operations nodes"
        
        result = []
        for node_id, node in self.nodes_of_operations.items():
            result.append(f"'{node_id}': '{node.name}', description='{node.description}', goal='{node.goal}'")
        
        return "\n".join(result)
    
    def get_reasoning_nodes(self) -> Dict[str, ReasoningNode]:
        """Get reasoning nodes."""
        return self.nodes_of_reasoning
    
    def get_reasoning_nodes_pp(self) -> str:
        """Get reasoning nodes in pretty print format."""
        if not self.nodes_of_reasoning:
            return "No reasoning nodes"
        
        result = []
        for node_id, node in self.nodes_of_reasoning.items():
            result.append(f"'{node_id}': '{node.description}'")
        
        return "\n".join(result)
    
    def get_edges(self) -> Dict[str, List[str]]:
        """Get edges/adjacency list."""
        return self.edges_of_operations
    
    def get_edges_of_reasoning(self) -> Dict[str, List[str]]:
        """Get reasoning edges/adjacency list."""
        return self.edges_of_reasoning
    
    def get_connecting_edges(self) -> Dict[str, List[str]]:
        """Get connecting edges/adjacency list."""
        return self.connecting_edges
    
    def get_edges_pp(self) -> str:
        """Get both operation edges and connecting edges in pretty print format."""
        result = []
        
        # Add operation edges
        if self.edges_of_operations:
            result.append("Operation Edges:")
            for source, targets in self.edges_of_operations.items():
                if targets:
                    targets_str = ", ".join([f"'{target}'" for target in targets])
                    result.append(f"  '{source}': [{targets_str}]")
                else:
                    result.append(f"  '{source}': []")
        else:
            result.append("Operation Edges: None")
        
        result.append("")  #
        
        # Add connecting edges
        if self.connecting_edges:
            result.append("Connecting Edges:")
            for source, targets in self.connecting_edges.items():
                if targets:
                    targets_str = ", ".join([f"'{target}'" for target in targets])
                    result.append(f"  '{source}': [{targets_str}]")
                else:
                    result.append(f"  '{source}': []")
        else:
            result.append("Connecting Edges: None")
        
        return "\n".join(result)
    
    def get_connecting_edges_pp(self) -> str:
        """Get connecting edges in pretty print format."""
        if not self.connecting_edges:
            return "No connecting edges"
        
        result = []
        for source, targets in self.connecting_edges.items():
            if targets:
                targets_str = ", ".join([f"'{target}'" for target in targets])
                result.append(f"'{source}': [{targets_str}]")
            else:
                result.append(f"'{source}': []")
        
        return "\n".join(result)
    
    def create_connecting_edges(self) -> None:
        """Create connecting edges between operations and reasoning nodes based on existing edges."""
        # Clear existing connecting edges
        self.connecting_edges.clear()
        
        # Connect operations nodes to reasoning nodes via regular edges
        for source, targets in self.edges_of_operations.items():
            for target in targets:
                # If source is operations node and target is reasoning node
                if source in self.nodes_of_operations and target in self.nodes_of_reasoning:
                    self.add_connecting_edge(source, target)
                # If source is reasoning node and target is operations node  
                elif source in self.nodes_of_reasoning and target in self.nodes_of_operations:
                    self.add_connecting_edge(source, target)
    
    def find_operation_node_id(self, next_task: List[str]) -> Optional[str]:
        """Find operation node ID from next_task list where first element is the task."""
        import json
        
        if not next_task or len(next_task) == 0:
            return None
            
        task_element = next_task[0]
        
        # Parse next_task if it's a JSON string
        try:
            if isinstance(task_element, str) and task_element.strip().startswith('{'):
                # Clean the JSON string to remove invalid control characters
                import re
                task_str = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]', '', task_element)
                
                next_task_data = json.loads(task_str)
                if isinstance(next_task_data, dict):
                    return list(next_task_data.keys())[0]
            else:
                # Try to find by name or description if it's a simple string
                for node_id, node in self.nodes_of_operations.items():
                    if node.name == task_element or node.description == task_element:
                        return node_id
        except (json.JSONDecodeError, IndexError):
            # Fallback: try to find by name or description
            for node_id, node in self.nodes_of_operations.items():
                if node.name == task_element or node.description == task_element:
                    return node_id
        
        return None
    
    def to_mermaid(self) -> str:
        """Convert graph to Mermaid format for visualization."""
        mermaid_lines = ["graph LR"]
        
        for node_id, node in self.nodes_of_operations.items():
            clean_description = node.description.replace("[", "").replace("]", "").replace('"', "").replace("'", "")
            node_label = f"{node_id}[{node.name}: {clean_description}]"
            mermaid_lines.append(f"    {node_label}")
        
        for node_id, node in self.nodes_of_reasoning.items():
            clean_description = node.description.replace("[", "").replace("]", "").replace('"', "").replace("'", "")
            node_label = f"{node_id}(({node_id}: {clean_description}))"
            mermaid_lines.append(f"    {node_label}")
        
        for source, targets in self.edges_of_operations.items():
            for target in targets:
                mermaid_lines.append(f"    {source} --> {target}")
        
        return "\n".join(mermaid_lines)
    
    def state_mermaid(self) -> str:
        """Convert graph to Mermaid format using edges_of_reasoning for reasoning connections."""
        mermaid_lines = ["graph LR"]
        
        for node_id, node in self.nodes_of_operations.items():
            clean_description = node.description.replace("[", "").replace("]", "").replace("(", "").replace(")", "").replace('"', "").replace("'", "")
            clean_name = node.name.replace("[", "").replace("]", "").replace("(", "").replace(")", "").replace('"', "").replace("'", "")
            node_label = f"{node_id}[{clean_name}: {clean_description}]"
            mermaid_lines.append(f"    {node_label}")
        
        for node_id, node in self.nodes_of_reasoning.items():
            clean_description = node.description.replace("[", "").replace("]", "").replace("(", "").replace(")", "").replace('"', "").replace("'", "")
            clean_node_id = node_id.replace("[", "").replace("]", "").replace("(", "").replace(")", "").replace('"', "").replace("'", "")
            node_label = f"{clean_node_id}(({clean_node_id}: {clean_description}))"
            mermaid_lines.append(f"    {node_label}")
        
        # Add operation edges (normal edges between operations and reasoning)
        for source, targets in self.edges_of_operations.items():
            for target in targets:
                mermaid_lines.append(f"    {source} ==> {target}")
        
        # Add connecting edges (connecting_edges - connections between operations and reasoning nodes)
        for source, targets in self.connecting_edges.items():
            for target in targets:
                mermaid_lines.append(f"    {source} ---> {target}")
        
        # Add reasoning edges (edges_of_reasoning - connections between reasoning nodes)
        for source, targets in self.edges_of_reasoning.items():
            for target in targets:
                mermaid_lines.append(f"    {source} -.-> {target}")
        
        return "\n".join(mermaid_lines)


class TaskAnalysis(BaseModel):
    """Structured output for task analysis"""
    task_description: str = Field(description="Detailed description of the user's task")
    inputs: List[str] = Field(description="List of inputs/parameters provided by the user")


class ExecutionGraph(BaseModel):
    """Structured output for execution graph planning"""
    graph_data: str = Field(
        description="JSON string containing the execution graph with 'nodes' and 'edges' structure"
    )

    model_config = {
        "json_schema_extra": {
            "title": "Execution Graph",
            "description": "A directed acyclic graph representing the execution plan"
        }
    }


class RouterDecision(BaseModel):
    """Structured output for router decisions"""
    decision: Literal["continue", "replan"] = Field(
        description="The router's decision: 'continue' if the plan is correct, 'replan' if the plan needs to be revised"
    )
    reasoning: str = Field(
        description="Brief explanation of why this decision was made"
    )
