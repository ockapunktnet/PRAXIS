#!/usr/bin/env python3
"""Destructure test-set questions into natural BPM domain language.

Rephrases technical tool invocations into natural BPM domain language,
removing tool names, file names, and technical phrasing.

Usage:
    python -m testing.transform_difficulty \
        --input testing/test_cases/generated/baseline_testset.json \
        --output testing/test_cases/generated/baseline_testset_destructured.json \
        --overwrite
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# Load environment variables from project root .env file
load_dotenv(Path(__file__).parent.parent / ".env")


# ---------------------------------------------------------------------------
# Structured output schema
# ---------------------------------------------------------------------------

class TransformedQuestion(TypedDict):
    """LLM output schema for a transformed question."""
    transformed_question: str


# ---------------------------------------------------------------------------
# LLM setup
# ---------------------------------------------------------------------------

transform_llm = ChatOpenAI(
    temperature=0,
    model_name="gpt-5-mini",
    model_kwargs={"seed": 42},
).with_structured_output(TransformedQuestion, method="function_calling")


# ---------------------------------------------------------------------------
# System prompts
# ---------------------------------------------------------------------------

DESTRUCTURE_SYSTEM_PROMPT = """\
You are a translator for BPM test questions. Your task is to rephrase technical \
tool invocations into natural BPM domain language.

RULES:
- REMOVE: All tool names (e.g. export_event_log, render_bpmn, discover_dfg), \
all file names (e.g. 'export_event_log_result.csv', 'remodel_bpmn_result.bpmn'), \
technical invocation phrasing like "Run the X tool with...", \
ALL numbering (1., 2., 3., ...)
- KEEP: BPM domain terms (BPMN, Petri net, DFG, Directly-Follows Graph, \
event log, soundness, heuristic net, conformance), content parameters \
(process descriptions, activities), process definition key ('invoice'), \
references to predecessor results ("the created model", "the exported event log")
- LANGUAGE: English, BPM domain language
- For multi-step questions: Formulate as a natural flowing sentence without numbering. \
Use conjunctions like "and", "then", "afterwards". \
The logical sequence should be implicitly clear from context, NOT through \
explicit numbering or enumeration.

EXAMPLES:

Input: "Run the export_event_log tool with the process definition key 'invoice'."
Output: "Export the event log for the 'invoice' process."

Input: "Run the render_bpmn tool with the file 'remodel_bpmn_result.bpmn' as input."
Output: "Render the BPMN model as an image."

Input: "Run the discover_dfg tool with the file 'export_event_log_result.csv' as input."
Output: "Compute the Directly-Follows Graph from the event log."

Input: "Run the remodel_bpmn tool with the description 'A simple invoice approval process with the activities: receive invoice, approve invoice, and pay invoice.'"
Output: "Model a BPMN process for a simple invoice approval process with the activities: receive invoice, approve invoice, and pay invoice."

Input: "Run the convert_bpmn_to_petri_net tool with the file 'remodel_bpmn_result.bpmn' as input."
Output: "Convert the BPMN model into a Petri net."

Input: "Run the check_petri_net_property tool with the file 'convert_bpmn_to_petri_net_result.pnml' as input and the property 'soundness'."
Output: "Check the Petri net for soundness."

Input: "Run the lint_bpmn tool with the file 'remodel_bpmn_result.bpmn' as input."
Output: "Check the BPMN model for modeling errors."

Input: "Run the render_petri_net tool with the file 'convert_bpmn_to_petri_net_result.pnml' as input."
Output: "Render the Petri net as an image."

Input: "Run the render_dfg tool with the file 'discover_dfg_result.json' as input."
Output: "Render the Directly-Follows Graph as an image."

Input: "Run the discover_heuristic_net tool with the file 'export_event_log_result.csv' as input."
Output: "Compute the heuristic net from the event log."

Input: "Run the analyze_event_log tool with the file 'export_event_log_result.csv' as input."
Output: "Analyze the event log and create a statistical summary."

Input: "Run the downgrade_bpmn_to_camunda7 tool with the file 'remodel_bpmn_result.bpmn' as input."
Output: "Convert the BPMN model to the Camunda 7 format."

Input: "Run the deploy_bpmn tool with the file 'downgrade_bpmn_to_camunda7_result.bpmn' or a downgraded bpmn file as input."
Output: "Deploy the Camunda 7-compatible BPMN model to the process engine."

Input: "Run the get_process_status tool with the process definition key 'invoice'."
Output: "Retrieve the status of the 'invoice' process from the process engine."

Multi-Step Input: "1. Run the remodel_bpmn tool with the description 'A simple invoice approval process with the activities: receive invoice, approve invoice, and pay invoice.'\\n2. Run the render_bpmn tool with the file 'remodel_bpmn_result.bpmn' as input."
Multi-Step Output: "Model a BPMN process for a simple invoice approval process with the activities: receive invoice, approve invoice, and pay invoice, and then render the created BPMN model as an image."

Multi-Step Input: "1. Run the downgrade_bpmn_to_camunda7 tool with the file 'remodel_bpmn_result.bpmn' as input.\\n2. Run the deploy_bpmn tool with the file 'downgrade_bpmn_to_camunda7_result.bpmn' or a downgraded bpmn file as input."
Multi-Step Output: "Convert the BPMN model to the Camunda 7 format and then deploy it to the process engine."

Multi-Step Input: "1. Run the export_event_log tool with the process definition key 'invoice'.\\n2. Run the discover_dfg tool with the file 'export_event_log_result.csv' as input."
Multi-Step Output: "Export the event log for the 'invoice' process and compute the Directly-Follows Graph from it."

Multi-Step Input: "1. Run the convert_bpmn_to_petri_net tool with the file 'remodel_bpmn_result.bpmn' as input.\\n2. Run the check_petri_net_property tool with the file 'convert_bpmn_to_petri_net_result.pnml' as input and the property 'soundness'."
Multi-Step Output: "Convert the BPMN model into a Petri net and then check it for soundness."

Now transform the following question:"""


# ---------------------------------------------------------------------------
# Transform logic
# ---------------------------------------------------------------------------

def transform_question(question: str) -> str:
    """Destructure a single question into natural BPM domain language."""
    result: TransformedQuestion = transform_llm.invoke([
        {"role": "system", "content": DESTRUCTURE_SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ])
    return result["transformed_question"]


def transform_testset(testset: dict, *, dry_run: bool = False) -> dict:
    """Destructure all questions in a testset into natural BPM domain language.

    Returns a new testset dict with destructured questions and updated metadata.
    """
    input_cases = testset["test_cases"]
    transformed_cases = []

    for i, case in enumerate(input_cases, 1):
        original_q = case["question"]
        transformed_q = transform_question(original_q)

        if dry_run:
            print(f"\n--- [{i}/{len(input_cases)}] {case['name']} ---")
            print(f"  Original:      {original_q}")
            print(f"  Destructured:  {transformed_q}")

        new_case = dict(case)
        new_case["question"] = transformed_q
        transformed_cases.append(new_case)

    # Build output testset
    source_metadata = testset.get("metadata", {})
    new_metadata = dict(source_metadata)
    new_metadata["difficulty_level"] = "destructured"
    new_metadata["source_testset"] = source_metadata.get("source_baseline", "unknown")
    new_metadata["transformed_at"] = datetime.now(timezone.utc).isoformat()
    new_metadata["transform_model"] = "gpt-5-mini"

    return {
        "metadata": new_metadata,
        "fixtures_dir": testset.get("fixtures_dir", ""),
        "test_cases": transformed_cases,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Destructure test-set questions into natural BPM domain language",
    )
    parser.add_argument("--input", required=True, help="Path to input testset JSON")
    parser.add_argument("--output", default=None, help="Output path for transformed JSON")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output file (default: abort if file exists)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print transformations to stdout without writing a file",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}")
        sys.exit(1)

    # Load input testset
    testset = json.loads(input_path.read_text(encoding="utf-8"))
    n_cases = len(testset["test_cases"])
    print(f"Loaded {n_cases} test case(s) from {input_path}")

    # Transform
    print("Destructuring questions...")
    transformed = transform_testset(testset, dry_run=args.dry_run)

    # Write output
    if args.dry_run:
        print(f"\n[dry-run] {n_cases} question(s) transformed — no file written.")
        return

    if args.output is None:
        print("ERROR: --output is required when not using --dry-run")
        sys.exit(1)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists() and not args.overwrite:
        print(f"ERROR: {output_path} already exists. Use --overwrite to replace it.")
        sys.exit(1)

    output_path.write_text(
        json.dumps(transformed, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Generated {n_cases} destructured test case(s) -> {output_path}")


if __name__ == "__main__":
    main()
