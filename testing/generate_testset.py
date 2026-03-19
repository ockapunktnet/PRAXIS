#!/usr/bin/env python3
"""Generate a test-set JSON from a Mermaid graph and a baseline definition.

Usage:
    python -m testing.generate_testset \
        --baseline testing/test_cases/baseline/baseline.json \
        --graph testing/test_cases/baseline/graph.md \
        --windows 1 2 3 \
        --output testing/test_cases/generated/baseline_testset.json
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from testing.testset_construction.graph_parser import parse_mermaid
from testing.testset_construction.path_extractor import extract_paths
from testing.testset_construction.builder import build_test_cases


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate test-set from graph + baseline")
    parser.add_argument("--baseline", required=True, help="Path to baseline.json")
    parser.add_argument("--graph", required=True, help="Path to graph.md (Mermaid)")
    parser.add_argument(
        "--windows",
        nargs="+",
        type=int,
        default=[1, 2, 3],
        help="Window sizes to generate (default: 1 2 3)",
    )
    parser.add_argument("--output", required=True, help="Output path for the generated JSON")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output file (default: abort if file exists)",
    )
    parser.add_argument(
        "--fixtures-dir",
        default=None,
        help="Path to fixtures directory (default: <baseline-dir>/fixtures)",
    )
    args = parser.parse_args()

    baseline_path = Path(args.baseline)
    graph_path = Path(args.graph)
    output_path = Path(args.output)
    fixtures_dir = Path(args.fixtures_dir) if args.fixtures_dir else baseline_path.parent / "fixtures"

    # Parse graph
    mermaid_text = graph_path.read_text(encoding="utf-8")
    graph = parse_mermaid(mermaid_text)
    print(f"Graph: {len(graph.nodes)} nodes, {sum(len(v) for v in graph.adjacency.values())} edges")
    for nid, label in graph.nodes.items():
        print(f"  {nid} -> {label}")

    # Load baseline
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    print(f"Baseline: {len(baseline)} activities")

    # Extract paths
    path_sets = extract_paths(graph.adjacency, args.windows)
    for w, paths in sorted(path_sets.items()):
        print(f"  Window {w}: {len(paths)} test case(s)")
        for p in sorted(paths, key=lambda s: sorted(s)):
            labels = [graph.nodes[nid] for nid in sorted(p)]
            print(f"    {{{', '.join(labels)}}}")

    # Build test cases
    test_cases = build_test_cases(
        path_sets=path_sets,
        baseline=baseline,
        adjacency=graph.adjacency,
        node_labels=graph.nodes,
    )

    # Assemble output
    cases_per_window = {w: len(paths) for w, paths in sorted(path_sets.items())}

    testset = {
        "metadata": {
            "source_graph": graph_path.name,
            "source_baseline": baseline_path.name,
            "window_sizes": args.windows,
            "cases_per_window": cases_per_window,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_cases": len(test_cases),
        },
        "fixtures_dir": str(fixtures_dir),
        "test_cases": test_cases,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not args.overwrite:
        print(f"\nERROR: {output_path} already exists. Use --overwrite to replace it.")
        return
    output_path.write_text(json.dumps(testset, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nGenerated {len(test_cases)} test case(s) -> {output_path}")


if __name__ == "__main__":
    main()
