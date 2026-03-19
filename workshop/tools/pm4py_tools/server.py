"""pm4py Tools — MCP Tool Server for process mining with pm4py.

Provides discovery, rendering, property checking, and conversion tools
for event logs (.csv/.xes), Petri nets (.ptn/.pnml), and BPMN models (.bpmn).

Run standalone:
    python workshop/tools/pm4py_tools/server.py
"""

import asyncio
import base64
import io
import json
import logging
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import pm4py
from pm4py.objects.petri_net.obj import PetriNet, Marking
from pm4py.objects.petri_net.utils import petri_utils

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Server("pm4py-tools")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_event_log(event_log: str) -> pd.DataFrame:
    """Parse an event log (CSV content, XES content, or file path) into a pm4py-ready DataFrame.

    Accepts:
    - A file path to a .xes or .csv file
    - Raw XES content (string starting with '<?xml')
    - Raw CSV content
    """
    stripped = event_log.strip()

    if not stripped:
        raise ValueError(
            "Event log is empty. Provide a .csv or .xes file via file_path."
        )

    # Detect if input looks like JSON (wrong tool input — probably a DFG result)
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if "edges" in data:
                raise ValueError(
                    "The input is a DFG JSON result, not an event log. "
                    "This tool expects a .csv or .xes event log file. "
                    "To visualize a DFG, use render_dfg instead."
                )
        except json.JSONDecodeError:
            pass

    # Case 1: File path (only check short strings to avoid OS errors on content)
    path = Path(stripped)
    if len(stripped) < 250 and path.exists() and path.is_file():
        suffix = path.suffix.lower()
        if suffix == ".xes":
            log = pm4py.read_xes(str(path))
            return pm4py.convert_to_dataframe(log)
        elif suffix == ".csv":
            df = pd.read_csv(path)
            return _normalize_csv_columns(df)
        else:
            raise ValueError(
                f"Unsupported file format: '{suffix}'. "
                f"This tool only accepts .csv or .xes event log files."
            )

    # Case 2: XES content as string
    if stripped.startswith("<?xml") or stripped.startswith("<log"):
        with tempfile.NamedTemporaryFile(suffix=".xes", mode="w", delete=False) as f:
            f.write(stripped)
            tmp_path = f.name
        try:
            log = pm4py.read_xes(tmp_path)
            return pm4py.convert_to_dataframe(log)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    # Case 3: CSV content
    try:
        df = pd.read_csv(io.StringIO(stripped))
    except pd.errors.ParserError as e:
        raise ValueError(
            f"Could not parse input as CSV: {e}. "
            f"This tool expects a .csv or .xes event log file."
        ) from e
    return _normalize_csv_columns(df)


def _normalize_csv_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise CSV column names to XES standard and format for pm4py."""
    col_map = {}
    for col in df.columns:
        lower = col.strip().lower().replace(" ", "_")
        if lower in ("case_id", "caseid", "case", "case:concept:name"):
            col_map[col] = "case:concept:name"
        elif lower in ("activity", "concept:name", "activity_name", "event"):
            col_map[col] = "concept:name"
        elif lower in (
            "timestamp",
            "time:timestamp",
            "start_time",
            "end_time",
            "complete_timestamp",
        ):
            col_map[col] = "time:timestamp"
    df.rename(columns=col_map, inplace=True)

    if "case:concept:name" not in df.columns or "concept:name" not in df.columns:
        raise ValueError(
            f"CSV is missing required columns. Need a case ID column "
            f"(e.g. 'case_id', 'Case ID') and an activity column "
            f"(e.g. 'activity', 'Activity'). "
            f"Found columns: {list(df.columns)}"
        )

    if "time:timestamp" in df.columns:
        df["time:timestamp"] = pd.to_datetime(df["time:timestamp"], errors="coerce")
        df.sort_values(["case:concept:name", "time:timestamp"], inplace=True)

    df = pm4py.format_dataframe(
        df,
        case_id="case:concept:name",
        activity_key="concept:name",
        timestamp_key="time:timestamp",
    )

    return df


def _parse_ptn_content(xml_str: str) -> tuple[PetriNet, Marking, Marking]:
    """Parse a .ptn XML string (bpt-lab.org custom format) into pm4py Petri net objects."""
    ns = {"ptn": "http://bpt-lab.org/schemas/ptn"}
    root = ET.fromstring(xml_str)
    model = root.find("ptn:model", ns)
    if model is None:
        raise ValueError("Invalid PTN file: no <ptn:model> element found.")

    net = PetriNet("ptn_net")
    im = Marking()
    fm = Marking()

    places: dict[str, PetriNet.Place] = {}
    transitions: dict[str, PetriNet.Transition] = {}

    for place_el in model.findall("ptn:place", ns):
        pid = place_el.get("id")
        name_el = place_el.find("ptn:name", ns)
        name = name_el.text if name_el is not None and name_el.text else pid
        p = PetriNet.Place(name)
        net.places.add(p)
        places[pid] = p

        marking_el = place_el.find("ptn:initialMarking", ns)
        if marking_el is not None and marking_el.text:
            tokens = int(marking_el.text)
            if tokens > 0:
                im[p] = tokens

    for trans_el in model.findall("ptn:transition", ns):
        tid = trans_el.get("id")
        name_el = trans_el.find("ptn:name", ns)
        name = name_el.text if name_el is not None and name_el.text else None
        t = PetriNet.Transition(tid, name)
        net.transitions.add(t)
        transitions[tid] = t

    for arc_el in model.findall("ptn:arc", ns):
        src_id = arc_el.get("source")
        tgt_id = arc_el.get("target")
        src = places.get(src_id) or transitions.get(src_id)
        tgt = places.get(tgt_id) or transitions.get(tgt_id)
        if src is None or tgt is None:
            raise ValueError(f"Arc references unknown element: {src_id} -> {tgt_id}")
        petri_utils.add_arc_from_to(src, tgt, net)

    return net, im, fm


def _load_petri_net(input_str: str) -> tuple[PetriNet, Marking, Marking]:
    """Load a Petri net from a file path (.pnml/.ptn) or XML content string."""
    stripped = input_str.strip()
    if not stripped:
        raise ValueError("Petri net input is empty. Provide a .pnml or .ptn file via file_path.")

    # Case 1: File path
    path = Path(stripped)
    if len(stripped) < 250 and path.exists() and path.is_file():
        suffix = path.suffix.lower()
        if suffix == ".pnml":
            net, im, fm = pm4py.read_pnml(str(path))
            return net, im, fm
        elif suffix == ".ptn":
            return _parse_ptn_content(path.read_text(encoding="utf-8"))
        else:
            raise ValueError(
                f"Unsupported Petri net format: '{suffix}'. "
                f"This tool accepts .pnml or .ptn files."
            )

    # Case 2: XML content — detect PTN vs PNML
    if stripped.startswith("<?xml") or stripped.startswith("<"):
        if "bpt-lab.org/schemas/ptn" in stripped:
            return _parse_ptn_content(stripped)
        # Assume PNML content
        with tempfile.NamedTemporaryFile(suffix=".pnml", mode="w", delete=False) as f:
            f.write(stripped)
            tmp_path = f.name
        try:
            net, im, fm = pm4py.read_pnml(tmp_path)
            return net, im, fm
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    raise ValueError(
        "Could not interpret input as a Petri net file. "
        "Provide a file path (.pnml/.ptn) or XML content."
    )


def _load_bpmn(input_str: str):
    """Load a BPMN model from a file path (.bpmn) or XML content string."""
    stripped = input_str.strip()
    if not stripped:
        raise ValueError("BPMN input is empty. Provide a .bpmn file via file_path.")

    # Case 1: File path
    path = Path(stripped)
    if len(stripped) < 250 and path.exists() and path.is_file():
        suffix = path.suffix.lower()
        if suffix == ".bpmn":
            return pm4py.read_bpmn(str(path))
        else:
            raise ValueError(
                f"Unsupported BPMN format: '{suffix}'. This tool accepts .bpmn files."
            )

    # Case 2: XML content
    if stripped.startswith("<?xml") or stripped.startswith("<"):
        with tempfile.NamedTemporaryFile(suffix=".bpmn", mode="w", delete=False) as f:
            f.write(stripped)
            tmp_path = f.name
        try:
            return pm4py.read_bpmn(tmp_path)
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    raise ValueError(
        "Could not interpret input as a BPMN file. "
        "Provide a file path (.bpmn) or XML content."
    )


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="analyze_event_log",
            description=(
                "Analyze an event log (.csv or .xes) and return statistics: "
                "number of cases, activities, events, top variants, "
                "and activity frequencies. "
                "Requires file_path pointing to a .csv or .xes event log file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "event_log": {
                        "type": "string",
                        "description": (
                            "Path to an event log file (.csv or .xes). "
                            "CSV files must contain columns for case ID, activity, and timestamp."
                        ),
                    },
                },
                "required": ["event_log"],
            },
        ),
        types.Tool(
            name="discover_heuristic_net",
            description=(
                "Discover a Heuristic Net from an event log (.csv or .xes) "
                "using the Heuristic Miner algorithm (pm4py). "
                "Returns activities, dependencies with frequencies, and start/end activities. "
                "Requires file_path pointing to a .csv or .xes event log file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "event_log": {
                        "type": "string",
                        "description": (
                            "Path to an event log file (.csv or .xes). "
                            "CSV files must contain columns for case ID, activity, and timestamp."
                        ),
                    },
                    "dependency_threshold": {
                        "type": ["number", "null"],
                        "description": (
                            "Dependency threshold for the Heuristic Miner "
                            "(0.0–1.0, default 0.5)."
                        ),
                    },
                },
                "required": ["event_log"],
            },
        ),
        types.Tool(
            name="discover_dfg",
            description=(
                "Discover a Directly-Follows Graph (DFG) from an event log (.csv or .xes). "
                "Returns edges with frequencies plus start/end activities as JSON. "
                "The result can be passed to render_dfg to render a PNG image. "
                "Requires file_path pointing to a .csv or .xes event log file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "event_log": {
                        "type": "string",
                        "description": (
                            "Path to an event log file (.csv or .xes). "
                            "CSV files must contain columns for case ID, activity, and timestamp."
                        ),
                    },
                },
                "required": ["event_log"],
            },
        ),
        types.Tool(
            name="render_dfg",
            description=(
                "Render a Directly-Follows Graph (DFG) as a PNG image. "
                "Requires the JSON result file from a previous discover_dfg call. "
                "Usage: first call discover_dfg, then pass its result file to render_dfg. "
                "Requires file_path pointing to a discover_dfg result file (.json)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dfg_data": {
                        "type": "string",
                        "description": (
                            "Path to a DFG result file (.json) as produced by discover_dfg. "
                            "Must contain keys: edges, start_activities, end_activities."
                        ),
                    },
                },
                "required": ["dfg_data"],
            },
        ),
        types.Tool(
            name="render_petri_net",
            description=(
                "Render an existing Petri net model file (.ptn or .pnml) as a PNG image. "
                "Returns a base64-encoded image plus place/transition counts. "
                "Requires file_path pointing to a .ptn or .pnml Petri net file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "petri_net_file": {
                        "type": "string",
                        "description": (
                            "Path to a Petri net file (.ptn or .pnml)."
                        ),
                    },
                },
                "required": ["petri_net_file"],
            },
        ),
        types.Tool(
            name="render_bpmn",
            description=(
                "Render an existing BPMN model file (.bpmn) as a PNG image. "
                "Returns a base64-encoded image. "
                "Requires file_path pointing to a .bpmn file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "bpmn_file": {
                        "type": "string",
                        "description": (
                            "Path to a BPMN model file (.bpmn)."
                        ),
                    },
                },
                "required": ["bpmn_file"],
            },
        ),
        types.Tool(
            name="check_petri_net_property",
            description=(
                "Check a structural or behavioral property of a Petri net model "
                "(.ptn or .pnml file). "
                "Supported properties: soundness, reachability, boundedness, liveness. "
                "Requires file_path pointing to a .ptn or .pnml Petri net file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "petri_net_file": {
                        "type": "string",
                        "description": (
                            "Path to a Petri net file (.ptn or .pnml)."
                        ),
                    },
                    "property": {
                        "type": "string",
                        "enum": ["soundness", "reachability", "boundedness", "liveness"],
                        "description": (
                            "The Petri net property to check: "
                            "soundness (workflow net + liveness check), "
                            "reachability (reachability graph states), "
                            "boundedness (max tokens per place), "
                            "liveness (all transitions fireable from every reachable marking)."
                        ),
                    },
                },
                "required": ["petri_net_file", "property"],
            },
        ),
        types.Tool(
            name="convert_bpmn_to_petri_net",
            description=(
                "Convert a BPMN model file (.bpmn) to a Petri net (PNML format). "
                "Returns the PNML XML content plus a summary with place/transition counts. "
                "Requires file_path pointing to a .bpmn file."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "bpmn_file": {
                        "type": "string",
                        "description": (
                            "Path to a BPMN model file (.bpmn)."
                        ),
                    },
                },
                "required": ["bpmn_file"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _analyze_event_log(event_log: str) -> dict:
    df = _parse_event_log(event_log)

    case_col = "case:concept:name"
    act_col = "concept:name"

    cases = df[case_col].nunique()
    activities = df[act_col].unique().tolist()
    total_events = len(df)

    activity_freq = df[act_col].value_counts().to_dict()

    variants = (
        df.groupby(case_col)[act_col]
        .apply(lambda acts: " -> ".join(acts))
        .value_counts()
        .head(10)
        .to_dict()
    )

    return {
        "num_cases": cases,
        "num_activities": len(activities),
        "num_events": total_events,
        "activities": activities,
        "activity_frequencies": {str(k): int(v) for k, v in activity_freq.items()},
        "top_variants": {str(k): int(v) for k, v in variants.items()},
    }


def _discover_heuristic_net(event_log: str, dependency_threshold: float = 0.5) -> dict:
    df = _parse_event_log(event_log)
    log = pm4py.convert_to_event_log(df)

    heu_net = pm4py.discover_heuristics_net(
        log,
        dependency_threshold=dependency_threshold,
    )

    # Extract structure from heuristic net
    activities = list(heu_net.activities)
    activities_occurrences = {
        act: int(count) for act, count in heu_net.activities_occurrences.items()
    }

    dependencies = []
    for src, targets in heu_net.dependency_matrix.items():
        for tgt, dep_value in targets.items():
            if dep_value >= dependency_threshold:
                freq = int(heu_net.dfg.get((src, tgt), 0))
                dependencies.append({
                    "source": src,
                    "target": tgt,
                    "dependency_value": round(float(dep_value), 4),
                    "frequency": freq,
                })

    # start/end_activities is a list of dicts — merge into single dict
    start_activities = {}
    for d in heu_net.start_activities:
        start_activities.update(d)
    end_activities = {}
    for d in heu_net.end_activities:
        end_activities.update(d)

    return {
        "activities": activities,
        "activities_occurrences": activities_occurrences,
        "dependencies": dependencies,
        "start_activities": start_activities,
        "end_activities": end_activities,
        "dependency_threshold": dependency_threshold,
    }


def _discover_dfg(event_log: str) -> dict:
    df = _parse_event_log(event_log)
    log = pm4py.convert_to_event_log(df)

    dfg, start_activities, end_activities = pm4py.discover_dfg(log)

    edges = [
        {"source": src, "target": tgt, "frequency": int(freq)}
        for (src, tgt), freq in sorted(dfg.items(), key=lambda x: -x[1])
    ]

    return {
        "edges": edges,
        "start_activities": {k: int(v) for k, v in start_activities.items()},
        "end_activities": {k: int(v) for k, v in end_activities.items()},
    }


def _render_dfg(dfg_data: str) -> dict:
    from pm4py.visualization.dfg import visualizer as dfg_visualizer

    stripped = dfg_data.strip()

    if not stripped:
        raise ValueError(
            "DFG data is empty. Pass the result file from discover_dfg via file_path."
        )

    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        # Detect common mistakes: event log content instead of DFG JSON
        if stripped.startswith("<?xml") or stripped.startswith("<log"):
            raise ValueError(
                "The input is an XES event log, not a DFG result. "
                "First call discover_dfg on the event log, then pass that result file to render_dfg."
            )
        raise ValueError(
            "The input is not valid JSON. render_dfg expects the JSON result "
            "from discover_dfg (with keys: edges, start_activities, end_activities). "
            "First call discover_dfg, then pass its result file to render_dfg."
        )

    if not isinstance(data, dict):
        raise ValueError(
            f"Expected a JSON object, got {type(data).__name__}. "
            f"Pass the result file from discover_dfg."
        )

    missing = [k for k in ("edges", "start_activities", "end_activities") if k not in data]
    if missing:
        raise ValueError(
            f"DFG JSON is missing required keys: {missing}. "
            f"Expected the JSON output from discover_dfg with keys: "
            f"edges, start_activities, end_activities."
        )

    # Reconstruct pm4py DFG dict from edges list
    dfg = {
        (edge["source"], edge["target"]): edge["frequency"]
        for edge in data["edges"]
    }
    start_activities = {k: int(v) for k, v in data["start_activities"].items()}
    end_activities = {k: int(v) for k, v in data["end_activities"].items()}

    gviz = dfg_visualizer.apply(
        dfg,
        parameters={
            dfg_visualizer.Variants.FREQUENCY.value.Parameters.START_ACTIVITIES: start_activities,
            dfg_visualizer.Variants.FREQUENCY.value.Parameters.END_ACTIVITIES: end_activities,
            dfg_visualizer.Variants.FREQUENCY.value.Parameters.FORMAT: "png",
        },
    )

    png_bytes = gviz.pipe(format="png")
    image_b64 = base64.b64encode(png_bytes).decode("ascii")

    activities = set()
    for src, tgt in dfg:
        activities.add(src)
        activities.add(tgt)

    return {
        "image_base64": image_b64,
        "format": "png",
        "num_edges": len(dfg),
        "num_activities": len(activities),
    }


def _render_petri_net(petri_net_file: str) -> dict:
    from pm4py.visualization.petri_net import visualizer as pn_visualizer

    net, im, fm = _load_petri_net(petri_net_file)

    gviz = pn_visualizer.apply(net, im, fm, parameters={"format": "png"})
    png_bytes = gviz.pipe(format="png")
    image_b64 = base64.b64encode(png_bytes).decode("ascii")

    return {
        "image_base64": image_b64,
        "format": "png",
        "num_places": len(net.places),
        "num_transitions": len(net.transitions),
    }


def _render_bpmn(bpmn_file: str) -> dict:
    from pm4py.visualization.bpmn import visualizer as bpmn_visualizer

    bpmn = _load_bpmn(bpmn_file)

    gviz = bpmn_visualizer.apply(bpmn, parameters={"format": "png"})
    png_bytes = gviz.pipe(format="png")
    image_b64 = base64.b64encode(png_bytes).decode("ascii")

    return {
        "image_base64": image_b64,
        "format": "png",
    }


def _convert_bpmn_to_petri_net(bpmn_file: str) -> dict:
    bpmn = _load_bpmn(bpmn_file)
    net, im, fm = pm4py.convert_to_petri_net(bpmn)

    # Export PNML to string via tempfile
    with tempfile.NamedTemporaryFile(suffix=".pnml", mode="w", delete=False) as f:
        tmp_path = f.name
    try:
        pm4py.write_pnml(net, im, fm, tmp_path)
        pnml_str = Path(tmp_path).read_text(encoding="utf-8")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    return {
        "file_content": pnml_str,
        "file_extension": ".pnml",
        "num_places": len(net.places),
        "num_transitions": len(net.transitions),
        "num_arcs": len(net.arcs),
    }


def _check_petri_net_property(petri_net_file: str, prop: str) -> dict:
    from pm4py.objects.petri_net.utils.reachability_graph import marking_flow_petri

    net, im, fm = _load_petri_net(petri_net_file)

    if prop == "soundness":
        is_sound, _ = pm4py.check_soundness(net, im, fm)
        return {"property": "soundness", "is_sound": is_sound}

    # All other properties need the marking flow (reachable markings + transitions)
    incoming, outgoing, _ = marking_flow_petri(net, im)
    reachable_markings = list(incoming.keys())

    if prop == "reachability":
        states = [
            {str(p): int(n) for p, n in m.items()}
            for m in reachable_markings
        ]
        return {
            "property": "reachability",
            "num_states": len(reachable_markings),
            "states": states,
        }

    elif prop == "boundedness":
        place_bounds = {str(p): 0 for p in net.places}
        for m in reachable_markings:
            for p, n in m.items():
                name = str(p)
                if name in place_bounds:
                    place_bounds[name] = max(place_bounds[name], int(n))
        is_bounded = all(b < float('inf') for b in place_bounds.values())
        return {
            "property": "boundedness",
            "is_bounded": is_bounded,
            "place_bounds": place_bounds,
        }

    elif prop == "liveness":
        fired = set()
        for trans_map in outgoing.values():
            for t in trans_map:
                fired.add(str(t))
        transition_details = {}
        for t in sorted(net.transitions, key=str):
            name = str(t)
            transition_details[name] = {
                "fires_in_reachability_graph": name in fired,
            }
        all_fire = all(
            d["fires_in_reachability_graph"] for d in transition_details.values()
        )
        return {
            "property": "liveness",
            "is_live": all_fire,
            "transition_details": transition_details,
        }

    else:
        raise ValueError(
            f"Unknown property '{prop}'. "
            f"Supported: soundness, reachability, boundedness, liveness."
        )


# ---------------------------------------------------------------------------
# Call handler
# ---------------------------------------------------------------------------

@app.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    arguments = arguments or {}

    try:
        if name == "analyze_event_log":
            result = _analyze_event_log(arguments["event_log"])
        elif name == "discover_heuristic_net":
            threshold = arguments.get("dependency_threshold") or 0.5
            result = _discover_heuristic_net(arguments["event_log"], threshold)
        elif name == "discover_dfg":
            result = _discover_dfg(arguments["event_log"])
        elif name == "render_dfg":
            result = _render_dfg(arguments["dfg_data"])
        elif name == "render_petri_net":
            result = _render_petri_net(arguments["petri_net_file"])
        elif name == "render_bpmn":
            result = _render_bpmn(arguments["bpmn_file"])
        elif name == "check_petri_net_property":
            result = _check_petri_net_property(
                arguments["petri_net_file"], arguments["property"]
            )
        elif name == "convert_bpmn_to_petri_net":
            result = _convert_bpmn_to_petri_net(arguments["bpmn_file"])
        else:
            result = {"error": f"Unknown tool: {name}"}
    except Exception as e:
        logger.exception("Error in %s", name)
        error_msg = str(e)
        if len(error_msg) > 500:
            error_msg = error_msg[:500] + f"... [truncated, {len(error_msg)} chars total]"
        result = {"error": error_msg}

    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
