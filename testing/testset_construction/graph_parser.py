"""Parse a Mermaid flowchart into nodes and an adjacency list."""

import re
from dataclasses import dataclass, field


@dataclass
class ParsedGraph:
    """Result of parsing a Mermaid flowchart."""

    nodes: dict[str, str]  # id -> label
    adjacency: dict[str, list[str]]  # source -> [targets]


def parse_mermaid(mermaid_text: str) -> ParsedGraph:
    """Parse a Mermaid flowchart string into a ParsedGraph.

    Supports:
        - Node definitions: ``A[label]``
        - Directed edges: ``A --> B``
        - Bidirectional edges: ``A <--> B`` (becomes two directed edges)
    """
    nodes: dict[str, str] = {}
    adjacency: dict[str, list[str]] = {}

    # Parse node definitions: A[label]
    for match in re.finditer(r"(\w+)\[([^\]]+)\]", mermaid_text):
        node_id, label = match.group(1), match.group(2)
        nodes[node_id] = label
        adjacency.setdefault(node_id, [])

    # Parse bidirectional edges: A <--> B
    for match in re.finditer(r"(\w+)\s*<-->\s*(\w+)", mermaid_text):
        src, dst = match.group(1), match.group(2)
        adjacency.setdefault(src, []).append(dst)
        adjacency.setdefault(dst, []).append(src)

    # Parse directed edges: A --> B  (exclude already-matched bidirectional)
    for match in re.finditer(r"(\w+)\s*(?<!<)-->\s*(\w+)", mermaid_text):
        src, dst = match.group(1), match.group(2)
        # Avoid duplicating edges already added by <-->
        if dst not in adjacency.get(src, []):
            adjacency.setdefault(src, []).append(dst)

    return ParsedGraph(nodes=nodes, adjacency=adjacency)
