"""Extract all simple paths of given window sizes from a directed graph and deduplicate via frozen sets."""

from __future__ import annotations


def extract_paths(
    adjacency: dict[str, list[str]],
    window_sizes: list[int],
) -> dict[int, set[frozenset[str]]]:
    """Return deduplicated frozen-set paths for each requested window size.

    A *window size* is the number of nodes in the path (not edges).
    Only simple paths (no revisiting nodes) are considered.

    Returns ``{window_size: {frozenset(...), ...}, ...}``.
    """
    all_nodes = list(adjacency.keys())
    results: dict[int, set[frozenset[str]]] = {}

    for w in window_sizes:
        path_sets: set[frozenset[str]] = set()

        for start in all_nodes:
            _dfs(adjacency, start, [start], w, path_sets)

        results[w] = path_sets

    return results


def _dfs(
    adjacency: dict[str, list[str]],
    current: str,
    path: list[str],
    target_length: int,
    collector: set[frozenset[str]],
) -> None:
    """Depth-first search collecting all simple paths of *target_length* nodes."""
    if len(path) == target_length:
        collector.add(frozenset(path))
        return

    for neighbor in adjacency.get(current, []):
        if neighbor not in path:  # simple path — no cycles
            path.append(neighbor)
            _dfs(adjacency, neighbor, path, target_length, collector)
            path.pop()
