#!/usr/bin/env python3
"""Run a generated test-set with LangSmith evaluation.

Usage:
    python -m testing.run_testset \
        --testset testing/test_cases/generated/baseline_testset.json \
        --project my-eval-project
"""

import argparse
import asyncio
import json
import os
import re
import shutil
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

from langsmith import Client

from testing.evaluator import (
    compare_results,
    evaluate_with_judge,
    get_git_info,
)


def load_testset(testset_path: Path) -> dict:
    """Load a generated test-set JSON file."""
    return json.loads(testset_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# File-name matching (handles _N suffixes from FileManager)
# ---------------------------------------------------------------------------

def resolve_expected_files(actual_dir: Path, expected_files: list[str]) -> list[str]:
    """Map expected file names to actual files in *actual_dir*.

    The workshop FileManager appends ``_1``, ``_2``, … before the extension
    when a file already exists.  This function finds the best match for each
    expected name, preferring the exact name and falling back to numbered
    variants.

    Returns a list of actual file names (same length as *expected_files*).
    If no match is found, the original expected name is kept so that
    ``compare_results`` will report it as missing.
    """
    actual_files = {f.name for f in actual_dir.iterdir() if f.is_file()} if actual_dir.exists() else set()
    resolved: list[str] = []

    for expected in expected_files:
        if expected in actual_files:
            resolved.append(expected)
            continue

        # Build pattern: stem_N.ext
        p = Path(expected)
        pattern = re.compile(
            re.escape(p.stem) + r"_\d+" + re.escape(p.suffix) + "$"
        )
        matches = sorted(f for f in actual_files if pattern.match(f))
        resolved.append(matches[0] if matches else expected)

    return resolved


# ---------------------------------------------------------------------------
# LangSmith integration
# ---------------------------------------------------------------------------

def create_or_get_dataset(testset: dict, dataset_name: str) -> str:
    """Create or fetch a LangSmith dataset from the testset."""
    client = Client()

    examples = []
    for tc in testset["test_cases"]:
        examples.append({
            "inputs": {
                "question": tc["question"],
                "test_case_name": tc["name"],
            },
            "outputs": {
                "expected_files": tc["expected_files"],
                "grading_hints": tc["grading_hints"],
            },
        })

    if client.has_dataset(dataset_name=dataset_name):
        print(f"Dataset already exists: {dataset_name}")
        return dataset_name

    dataset = client.create_dataset(dataset_name=dataset_name)
    client.create_examples(dataset_id=dataset.id, examples=examples)
    print(f"Created dataset: {dataset_name} ({len(examples)} examples)")

    return dataset_name


def _make_run_fn(testset: dict):
    """Create a target function for ``client.aevaluate``."""
    fixtures_dir = Path(testset["fixtures_dir"])
    tc_lookup = {tc["name"]: tc for tc in testset["test_cases"]}

    async def run_test_case(inputs: dict) -> dict:
        from testing.test_environment import WorkshopTestHarness

        tc = tc_lookup.get(inputs["test_case_name"])
        if tc is None:
            return {"response": f"Test case not found: {inputs['test_case_name']}", "comparison": {}}

        harness = WorkshopTestHarness()
        try:
            await harness.setup()

            # Copy required fixtures into user directory
            for fixture_name in tc.get("required_fixtures", []):
                src = fixtures_dir / fixture_name
                if src.exists():
                    dst = harness.file_manager.user_dir / fixture_name
                    shutil.copy2(src, dst)
                else:
                    print(f"  WARNING: fixture not found: {src}")

            result = await harness.run_agent(tc["question"])
            solver_response = result.get("response", "")

            actual_dir = harness.file_manager.user_dir

            # Resolve expected file names (handles _N suffixes)
            resolved_files = resolve_expected_files(actual_dir, tc["expected_files"])

            # Empty reference dir (no ref files for generated testsets)
            ref_dir = actual_dir / "__ref__"
            ref_dir.mkdir(exist_ok=True)

            comparison = compare_results(actual_dir, ref_dir, resolved_files)

            return {
                "response": solver_response,
                "mermaid": result.get("mermaid", ""),
                "comparison": comparison,
                "grading_hints": tc["grading_hints"],
            }
        finally:
            await harness.cleanup()

    return run_test_case


async def _evaluator(inputs: dict, outputs: dict, reference_outputs: dict) -> bool:
    """LangSmith evaluator using LLM-as-Judge."""
    question = inputs.get("question", "")
    solver_response = outputs.get("response", "")
    mermaid = outputs.get("mermaid", "")
    comparison = outputs.get("comparison", {})
    grading_hints = outputs.get("grading_hints", reference_outputs.get("grading_hints", ""))

    grade = await evaluate_with_judge(question, solver_response, comparison, grading_hints, mermaid)
    return grade["is_correct"]


async def run_evaluation(testset_path: Path, project: str | None = None, repetitions: int = 1, max_concurrency: int = 4) -> None:
    """Run the full evaluation pipeline with LangSmith."""
    if project:
        os.environ["LANGSMITH_PROJECT"] = project

    client = Client()
    testset = load_testset(testset_path)
    metadata = testset["metadata"]

    print(f"Testset: {metadata.get('source_baseline', 'n/a')} | graph: {metadata.get('source_graph', 'n/a')}")
    print(f"Windows: {metadata['window_sizes']} | Total cases: {metadata['total_cases']}")
    if project:
        print(f"LangSmith project: {project}")

    windows_tag = "_".join(str(w) for w in metadata["window_sizes"])
    dataset_name = f"final-{testset_path.stem}-w{windows_tag}"
    create_or_get_dataset(testset, dataset_name)

    git_info = get_git_info()
    print(f"Git: {git_info['branch']} @ {git_info['commit'][:8]}")

    run_fn = _make_run_fn(testset)

    experiment_results = await client.aevaluate(
        run_fn,
        data=dataset_name,
        evaluators=[_evaluator],
        experiment_prefix=f"final-{testset_path.stem}",
        num_repetitions=repetitions,
        max_concurrency=max_concurrency,
        metadata={
            "git_branch": git_info["branch"],
            "git_commit": git_info["commit"],
            "source_graph": metadata.get("source_graph", "n/a"),
            "source_baseline": metadata.get("source_baseline", "n/a"),
            "window_sizes": str(metadata["window_sizes"]),
            "cases_per_window": str(metadata.get("cases_per_window", {})),
            "num_repetitions": repetitions,
        },
    )

    print("\nEvaluation complete! Check LangSmith dashboard for results.")
    return experiment_results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main():
    parser = argparse.ArgumentParser(description="Run a generated test-set")
    parser.add_argument("--testset", required=True, help="Path to generated testset JSON")
    parser.add_argument("--project", default=None, help="LangSmith project name for tracing")
    parser.add_argument("--repetitions", type=int, default=1, help="Number of repetitions per test case")
    parser.add_argument("--max-concurrency", type=int, default=4, help="Max parallel test cases (default: 4)")
    args = parser.parse_args()

    if not os.environ.get("LANGSMITH_API_KEY"):
        print("LANGSMITH_API_KEY not found. Check your .env file.")
        sys.exit(1)
    if not os.environ.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY not found. Check your .env file.")
        sys.exit(1)

    await run_evaluation(Path(args.testset), project=args.project, repetitions=args.repetitions, max_concurrency=args.max_concurrency)


if __name__ == "__main__":
    asyncio.run(main())
