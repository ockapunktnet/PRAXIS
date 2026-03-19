"""Workshop Test Evaluator — file-based evaluation with LLM-as-Judge.

Evaluates solver outputs by checking produced files against references
and using an LLM judge for semantic grading.
"""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

from langchain_openai import ChatOpenAI
from dotenv import load_dotenv

# Load environment variables from project root .env file
load_dotenv(Path(__file__).parent.parent / ".env")


# ---------------------------------------------------------------------------
# TestCase dataclass
# ---------------------------------------------------------------------------

@dataclass
class TestCase:
    name: str
    question: str
    input_dir: Path
    reference_dir: Path
    expected_files: list[str]
    grading_hints: str


def load_test_cases(test_cases_dir: Path) -> list[TestCase]:
    """Load all test cases from subdirectories containing ``case.json``."""
    cases = []
    for case_json in sorted(test_cases_dir.rglob("case.json")):
        data = json.loads(case_json.read_text(encoding="utf-8"))
        case_dir = case_json.parent
        cases.append(TestCase(
            name=data["name"],
            question=data["question"],
            input_dir=case_dir / "input",
            reference_dir=case_dir / "reference",
            expected_files=data["expected_files"],
            grading_hints=data.get("grading_hints", ""),
        ))
    return cases


# ---------------------------------------------------------------------------
# File comparison
# ---------------------------------------------------------------------------

def compare_results(
    actual_dir: Path,
    reference_dir: Path,
    expected_files: list[str],
) -> dict:
    """Compare actual output files against reference files.

    Returns a dict with ``files_found``, ``files_missing``, and ``diffs``.
    """
    files_found: list[str] = []
    files_missing: list[str] = []
    diffs: dict[str, dict] = {}

    for fname in expected_files:
        actual_path = actual_dir / fname
        ref_path = reference_dir / fname

        if not actual_path.exists():
            files_missing.append(fname)
            continue

        file_size = actual_path.stat().st_size
        if file_size == 0:
            files_missing.append(fname)
            diffs[fname] = {"error": "file is empty (0 bytes)"}
            continue

        files_found.append(fname)
        file_diff: dict = {"size_bytes": file_size}

        # JSON diff
        if fname.endswith(".json") and ref_path.exists():
            try:
                actual_data = json.loads(actual_path.read_text(encoding="utf-8"))
                ref_data = json.loads(ref_path.read_text(encoding="utf-8"))

                actual_keys = set(actual_data.keys()) if isinstance(actual_data, dict) else set()
                ref_keys = set(ref_data.keys()) if isinstance(ref_data, dict) else set()

                file_diff["keys_match"] = actual_keys == ref_keys
                file_diff["missing_keys"] = list(ref_keys - actual_keys)
                file_diff["extra_keys"] = list(actual_keys - ref_keys)

                # Compare edge counts for DFG results
                if "edges" in actual_data and "edges" in ref_data:
                    file_diff["actual_edge_count"] = len(actual_data["edges"])
                    file_diff["reference_edge_count"] = len(ref_data["edges"])
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                file_diff["json_error"] = str(exc)

        # Binary (PNG) size comparison
        elif fname.endswith(".png") and ref_path.exists():
            ref_size = ref_path.stat().st_size
            file_diff["reference_size_bytes"] = ref_size
            if ref_size > 0:
                ratio = file_size / ref_size
                file_diff["size_ratio"] = round(ratio, 2)
                file_diff["size_similar"] = 0.5 <= ratio <= 1.5

        diffs[fname] = file_diff

    return {
        "files_found": files_found,
        "files_missing": files_missing,
        "diffs": diffs,
    }


# ---------------------------------------------------------------------------
# LLM-as-Judge
# ---------------------------------------------------------------------------

GRADER_INSTRUCTIONS = """Du bist ein Bewerter fuer einen KI-Agenten, der Process-Mining-Aufgaben loest.

Du bekommst:
- FRAGE: Die urspruengliche Nutzerfrage
- SOLVER-ANTWORT: Die textuelle Antwort des Agenten
- EXECUTION GRAPH: Ein Mermaid-Diagramm das den Planungs- und Ausfuehrungsgraphen zeigt (==> Planungsschritte, ---> Verbindungen, -.-> Reasoning-Schritte)
- DATEI-ERGEBNISSE: Welche Dateien erzeugt wurden, Groessen, und ggf. JSON-Diffs
- BEWERTUNGSHINWEISE: Spezifische Kriterien fuer diesen Testfall

Bewertungskriterien:
(1) Wurden ALLE erwarteten Dateien erzeugt (nicht leer)?
(2) Haben die JSON-Dateien die erwartete Struktur (richtige Keys)?
(3) Sind die Ergebnisse inhaltlich plausibel (Edge-Anzahl, Bildgroesse)?
(4) Hat der Agent die richtigen Tools in der richtigen Reihenfolge aufgerufen?

Correctness:
True = Alle erwarteten Dateien existieren und die Ergebnisse sind plausibel.
False = Dateien fehlen, sind leer, oder die Ergebnisse sind offensichtlich falsch.
"""


class Grade(TypedDict):
    """LLM judge output schema."""
    reasoning: str
    is_correct: bool


grader_llm = ChatOpenAI(
    temperature=0,
    model_name="gpt-5-mini",
).with_structured_output(Grade, method="function_calling")


async def evaluate_with_judge(
    question: str,
    solver_response: str,
    comparison: dict,
    grading_hints: str,
    mermaid: str = "",
) -> Grade:
    """Use an LLM judge to evaluate the solver's output."""
    user_content = f"""FRAGE: {question}

SOLVER-ANTWORT: {solver_response}

EXECUTION GRAPH (Mermaid):
{mermaid if mermaid else "Not available"}

DATEI-ERGEBNISSE: {json.dumps(comparison, indent=2, ensure_ascii=False)}

BEWERTUNGSHINWEISE: {grading_hints}"""

    try:
        grade = await grader_llm.ainvoke([
            {"role": "system", "content": GRADER_INSTRUCTIONS},
            {"role": "user", "content": user_content},
        ])
        return grade
    except Exception as e:
        print(f"Judge evaluation error: {e}")
        return Grade(reasoning=f"Judge error: {e}", is_correct=False)


# ---------------------------------------------------------------------------
# Run a single test case
# ---------------------------------------------------------------------------

async def run_test_case(test_case: TestCase) -> dict:
    """Run a single test case end-to-end using the WorkshopTestHarness.

    1. Setup — copy input files into user directory
    2. Run agent — solver processes the question with real MCP tools
    3. Compare — check output files against references
    4. Judge — LLM evaluates the result
    5. Cleanup
    """
    from testing.test_environment import WorkshopTestHarness

    harness = WorkshopTestHarness()

    try:
        # 1. Setup
        user_id = await harness.setup(
            test_data_dir=str(test_case.input_dir),
        )

        # 2. Run agent
        result = await harness.run_agent(test_case.question)
        solver_response = result.get("response", "")

        # 3. Get output files and compare
        output_files = harness.get_output_files()
        actual_dir = harness.file_manager.user_dir
        comparison = compare_results(
            actual_dir, test_case.reference_dir, test_case.expected_files,
        )

        # 4. LLM judge
        grade = await evaluate_with_judge(
            test_case.question,
            solver_response,
            comparison,
            test_case.grading_hints,
        )

        return {
            "test_case": test_case.name,
            "user_id": user_id,
            "response": solver_response,
            "output_files": output_files,
            "comparison": comparison,
            "grade": grade,
        }

    finally:
        # 5. Cleanup
        await harness.cleanup()


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def get_git_info() -> dict:
    """Get current git branch and commit hash."""
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        return {"branch": branch, "commit": commit}
    except subprocess.CalledProcessError:
        return {"branch": "unknown", "commit": "unknown"}
