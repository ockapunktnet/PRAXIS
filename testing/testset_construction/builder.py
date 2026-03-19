"""Build test-case dicts from frozen-set paths and baseline activity data."""

from __future__ import annotations

from collections import deque


def topological_order(
    nodes: frozenset[str],
    adjacency: dict[str, list[str]],
) -> list[str]:
    """Return nodes in topological order within the subgraph induced by *nodes*.

    Uses Kahn's algorithm.  Ties are broken alphabetically so the result is
    deterministic.  If the subgraph has no edges between some nodes they are
    ordered alphabetically relative to each other.
    """
    # Build in-degree map for the subgraph
    in_degree: dict[str, int] = {n: 0 for n in nodes}
    sub_adj: dict[str, list[str]] = {n: [] for n in nodes}

    for src in nodes:
        for dst in adjacency.get(src, []):
            if dst in nodes:
                sub_adj[src].append(dst)
                in_degree[dst] += 1

    queue: deque[str] = deque(sorted(n for n in nodes if in_degree[n] == 0))
    order: list[str] = []

    while queue:
        node = queue.popleft()
        order.append(node)
        for dst in sorted(sub_adj[node]):
            in_degree[dst] -= 1
            if in_degree[dst] == 0:
                queue.append(dst)

    # If there are remaining nodes (cycle within subgraph), append alphabetically
    remaining = sorted(nodes - set(order))
    order.extend(remaining)

    return order


def build_test_cases(
    path_sets: dict[int, set[frozenset[str]]],
    baseline: list[dict],
    adjacency: dict[str, list[str]],
    node_labels: dict[str, str],
    baseline_name: str = "baseline",
) -> list[dict]:
    """Construct test-case dicts ready for JSON serialisation.

    Parameters
    ----------
    path_sets:
        ``{window_size: {frozenset(node_ids), ...}}``
    baseline:
        Parsed ``baseline.json`` — list of activity dicts.
    adjacency:
        Full graph adjacency list (node IDs).
    node_labels:
        ``{node_id: activity_name}`` mapping.
    baseline_name:
        Prefix for generated test-case names.
    """
    # Index baseline by activity_name for fast lookup
    activity_map: dict[str, dict] = {}
    for entry in baseline:
        activity_map[entry["activity_name"]] = entry["content"]

    test_cases: list[dict] = []

    for window_size in sorted(path_sets):
        for fs in sorted(path_sets[window_size], key=lambda s: sorted(s)):
            ordered_ids = topological_order(fs, adjacency)
            ordered_labels = [node_labels[nid] for nid in ordered_ids]

            # Concatenate questions and grading hints
            questions: list[str] = []
            hints: list[str] = []
            expected_files: list[str] = []
            all_required: list[str] = []
            produced_so_far: set[str] = set()

            for i, label in enumerate(ordered_labels, 1):
                content = activity_map[label]
                prefix = f"{i}. " if len(ordered_labels) > 1 else ""
                questions.append(f"{prefix}{content['question']}")
                hints.append(f"{prefix}{content['grading_hints']}")

                for out_file in content.get("expected_output", []):
                    expected_files.append(out_file)

                # Fixtures = required_input files not produced by earlier activities
                for req in content.get("required_input", []):
                    if req not in produced_so_far:
                        all_required.append(req)

                produced_so_far.update(content.get("expected_output", []))

            # Deduplicate required fixtures
            required_fixtures = list(dict.fromkeys(all_required))

            name_suffix = "__".join(ordered_labels)
            test_cases.append({
                "name": f"{baseline_name}_w{window_size}_{name_suffix}",
                "window_size": window_size,
                "activities": ordered_labels,
                "question": "\n".join(questions),
                "grading_hints": "\n".join(hints),
                "required_fixtures": required_fixtures,
                "expected_files": expected_files,
            })

    return test_cases
