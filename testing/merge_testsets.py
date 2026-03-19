"""Merge multiple testset JSON files into one."""

import argparse
import json
from datetime import datetime, timezone


def merge_testsets(input_files: list[str], output_file: str) -> None:
    all_cases = []
    sources = []
    all_window_sizes = set()
    cases_per_window: dict[int, int] = {}
    fixtures_dir = None

    for path in input_files:
        with open(path) as f:
            data = json.load(f)

        meta = data.get("metadata", {})
        sources.append(meta.get("source_graph", path))
        all_window_sizes.update(meta.get("window_sizes", []))

        for ws, count in meta.get("cases_per_window", {}).items():
            cases_per_window[int(ws)] = cases_per_window.get(int(ws), 0) + count

        if fixtures_dir is None:
            fixtures_dir = data.get("fixtures_dir")

        all_cases.extend(data.get("test_cases", []))

    merged = {
        "metadata": {
            "source_files": sources,
            "window_sizes": sorted(all_window_sizes),
            "cases_per_window": {
                str(k): v for k, v in sorted(cases_per_window.items())
            },
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total_cases": len(all_cases),
        },
        "fixtures_dir": fixtures_dir,
        "test_cases": all_cases,
    }

    with open(output_file, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)

    print(f"Merged {len(input_files)} files → {len(all_cases)} test cases → {output_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge multiple testset JSONs into one.")
    parser.add_argument("inputs", nargs="+", help="Input testset JSON files")
    parser.add_argument("-o", "--output", required=True, help="Output file path")
    args = parser.parse_args()

    merge_testsets(args.inputs, args.output)
