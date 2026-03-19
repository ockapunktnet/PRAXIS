"""Camunda 7 Tools — MCP Tool Server for Camunda 7 Community Edition.

Provides tools to deploy BPMN processes, monitor execution status,
and export event logs as CSV.

Run standalone:
    python workshop/tools/camunda/server.py
"""

import asyncio
import csv
import io
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp import types

# Load .env from project root
from dotenv import load_dotenv

_project_root = Path(__file__).parent.parent.parent.parent
load_dotenv(_project_root / ".env")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Server("camunda-tools")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CAMUNDA_REST_URL = os.getenv("CAMUNDA_REST_URL", "http://localhost:8080/engine-rest")


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@app.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="deploy_bpmn",
            description=(
                "Deploy a BPMN process model (.bpmn file) to the Camunda 7 engine. "
                "Returns the deployment ID and process definition key. "
                "Requires file_path pointing to a .bpmn file. "
                "Optionally set history_ttl (days) — required by Camunda 7.20+ for history cleanup. "
                "Defaults to 180 days if not set in the BPMN model."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "bpmn_file": {
                        "type": "string",
                        "description": "BPMN XML content or path to a .bpmn file.",
                    },
                    "history_ttl": {
                        "type": ["integer", "null"],
                        "description": (
                            "History Time To Live in days. "
                            "How long Camunda keeps history data before cleanup. "
                            "Default: 180. Set to 0 for immediate cleanup."
                        ),
                    },
                },
                "required": ["bpmn_file"],
            },
        ),
        types.Tool(
            name="get_process_status",
            description=(
                "Get the status of process instances. "
                "Optionally filter by process definition key."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "process_definition_key": {
                        "type": ["string", "null"],
                        "description": "Optional process definition key to filter by.",
                    },
                },
                "required": [],
            },
        ),
        types.Tool(
            name="downgrade_bpmn_to_camunda7",
            description=(
                "Convert a Camunda 8 (Zeebe/Cloud) BPMN model to Camunda 7 format. "
                "Patches execution platform, replaces Zeebe extensions with Camunda 7 equivalents, "
                "adds missing messageRef declarations, condition expressions, and historyTimeToLive. "
                "Use this before deploy_bpmn if the model was created for Camunda 8."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "bpmn_file": {
                        "type": "string",
                        "description": "BPMN XML content or path to a .bpmn file.",
                    },
                    "history_ttl": {
                        "type": ["integer", "null"],
                        "description": "History Time To Live in days (default: 180).",
                    },
                },
                "required": ["bpmn_file"],
            },
        ),
        types.Tool(
            name="export_event_log",
            description=(
                "Export the execution history of a process as a CSV event log. "
                "The CSV contains columns: case_id, activity, timestamp, lifecycle. "
                "Compatible with pm4py analysis tools."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "process_definition_key": {
                        "type": "string",
                        "description": "The process definition key to export the event log for.",
                    },
                },
                "required": ["process_definition_key"],
            },
        ),
    ]


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _is_camunda8_model(bpmn_xml: str) -> bool:
    """Detect whether a BPMN XML is a Camunda 8 / Zeebe model."""
    return (
        "Camunda Cloud" in bpmn_xml
        or "zeebe:" in bpmn_xml
        or 'executionPlatformVersion="8.' in bpmn_xml
    )


def _downgrade_bpmn_to_camunda7(bpmn_xml: str, ttl: int = 180) -> dict:
    """Convert a Camunda 8 BPMN model to Camunda 7 compatible format."""
    xml = bpmn_xml
    changes: list[str] = []

    # 1. Execution platform
    if "Camunda Cloud" in xml:
        xml = xml.replace(
            'modeler:executionPlatform="Camunda Cloud"',
            'modeler:executionPlatform="Camunda Platform"',
        )
        xml = re.sub(
            r'modeler:executionPlatformVersion="8\.[^"]*"',
            'modeler:executionPlatformVersion="7.21.0"',
            xml,
        )
        changes.append("executionPlatform: Cloud -> Platform 7.21.0")

    # 2. isExecutable — Camunda 7 ignores processes with isExecutable="false"
    if 'isExecutable="false"' in xml:
        xml = xml.replace('isExecutable="false"', 'isExecutable="true"')
        changes.append("isExecutable: false -> true")

    # 3. historyTimeToLive
    xml = _ensure_history_ttl(xml, ttl)
    changes.append(f"historyTimeToLive: {ttl}")

    # 3. Add camunda namespace if missing (needed for historyTimeToLive, expressions)
    if "xmlns:camunda" not in xml:
        xml = xml.replace(
            'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
            'xmlns:camunda="http://camunda.org/schema/1.0/bpmn" '
            'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
        )
        changes.append("xmlns:camunda namespace added")

    # 3b. Add xsi namespace if missing (needed for conditionExpressions)
    if "xmlns:xsi" not in xml:
        xml = xml.replace(
            'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
            'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
        )

    # 4. Replace Zeebe service/send task extensions with camunda:expression
    def _patch_task_with_zeebe(match: re.Match) -> str:
        tag = match.group(0)
        if "camunda:expression" in tag or "camunda:class" in tag:
            return tag
        changes.append(f"task: added camunda:expression")
        return tag.replace(">", ' camunda:expression="${true}">', 1)

    xml = re.sub(r"<(?:bpmn:)?serviceTask\s[^>]*>", _patch_task_with_zeebe, xml)
    xml = re.sub(r"<(?:bpmn:)?sendTask\s[^>]*>", _patch_task_with_zeebe, xml)

    # Remove zeebe:taskDefinition extension elements
    xml = re.sub(
        r"<(?:bpmn:)?extensionElements>\s*<zeebe:taskDefinition[^/]*/>\s*</(?:bpmn:)?extensionElements>",
        "",
        xml,
    )

    # 5. Add messageRef to messageEventDefinitions that lack one
    msg_counter = [0]
    msg_ids: list[str] = []

    def _add_msg_ref(match: re.Match) -> str:
        tag = match.group(0)
        if "messageRef" in tag:
            return tag
        msg_counter[0] += 1
        msg_id = f"DowngradeMsg_{msg_counter[0]}"
        msg_ids.append(msg_id)
        return tag.replace("/>", f' messageRef="{msg_id}" />')

    xml = re.sub(r"<(?:bpmn:)?messageEventDefinition[^/>]*/>", _add_msg_ref, xml)

    if msg_ids:
        msg_elements = "".join(
            f'<bpmn:message id="{mid}" name="{mid}" />' for mid in msg_ids
        )
        for anchor in ("<bpmndi:BPMNDiagram", "<BPMNDiagram", "</bpmn:definitions>"):
            if anchor in xml:
                xml = xml.replace(anchor, f"{msg_elements}{anchor}", 1)
                break
        changes.append(f"messageRef: added {len(msg_ids)} message declarations")

    # 6. Add dummy conditions on exclusive gateway non-default flows
    #    Only for split gateways (>1 outgoing flow), not merge gateways
    try:
        root = ET.fromstring(xml)
        bpmn_ns = "http://www.omg.org/spec/BPMN/20100524/MODEL"
        default_flows: set[str] = set()
        split_gw_ids: set[str] = set()

        for gw in root.iter(f"{{{bpmn_ns}}}exclusiveGateway"):
            gw_id = gw.get("id", "")
            default_id = gw.get("default")
            if default_id:
                default_flows.add(default_id)
            # Count outgoing flows (child <outgoing> elements)
            outgoing = [c for c in gw if c.tag.split("}")[-1] == "outgoing"]
            if len(outgoing) > 1:
                split_gw_ids.add(gw_id)

        cond_count = 0
        for flow in root.iter(f"{{{bpmn_ns}}}sequenceFlow"):
            flow_id = flow.get("id", "")
            source = flow.get("sourceRef", "")
            if source not in split_gw_ids or flow_id in default_flows:
                continue
            has_cond = any(
                child.tag.split("}")[-1] == "conditionExpression" for child in flow
            )
            if has_cond:
                continue
            flow_name = flow.get("name", "condition")
            cond_expr = f"${{{flow_name}}}" if flow_name else "${true}"
            pattern = rf'(<(?:bpmn:)?sequenceFlow\s+id="{re.escape(flow_id)}"[^>]*)/>'
            replacement = (
                rf'\1>'
                rf'<bpmn:conditionExpression xsi:type="bpmn:tFormalExpression">{cond_expr}</bpmn:conditionExpression>'
                rf'</bpmn:sequenceFlow>'
            )
            new_xml = re.sub(pattern, replacement, xml, count=1)
            if new_xml != xml:
                xml = new_xml
                cond_count += 1
        if cond_count:
            changes.append(f"conditionExpression: added {cond_count} dummy conditions")
    except ET.ParseError:
        changes.append("conditionExpression: skipped (XML parse error)")

    return {
        "file_content": xml,
        "file_extension": ".bpmn",
        "changes": changes,
    }


def _ensure_camunda_ns(bpmn_xml: str) -> str:
    """Ensure xmlns:camunda is declared if any camunda: prefix is used."""
    if "xmlns:camunda" not in bpmn_xml and "camunda:" in bpmn_xml:
        bpmn_xml = bpmn_xml.replace(
            'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
            'xmlns:camunda="http://camunda.org/schema/1.0/bpmn" '
            'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"',
        )
    return bpmn_xml


def _ensure_history_ttl(bpmn_xml: str, ttl: int = 180) -> str:
    """Ensure camunda:historyTimeToLive is set on all <process> elements."""
    if "camunda:historyTimeToLive" in bpmn_xml:
        return _ensure_camunda_ns(bpmn_xml)
    bpmn_xml = re.sub(
        r"(<(?:bpmn:)?process\s+id=\"[^\"]*\")",
        rf'\1 camunda:historyTimeToLive="{ttl}"',
        bpmn_xml,
    )
    return _ensure_camunda_ns(bpmn_xml)


async def _deploy_bpmn(bpmn_content: str, history_ttl: int = 180) -> dict:
    """Deploy BPMN XML content to Camunda."""
    # Check for Camunda 8 model before attempting deploy
    if _is_camunda8_model(bpmn_content):
        return {
            "error": (
                "This BPMN model uses Camunda 8 (Zeebe/Cloud) format. "
                "Only Camunda 7 format is supported for deployment."
            )
        }

    bpmn_content = _ensure_history_ttl(bpmn_content, history_ttl)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            f"{CAMUNDA_REST_URL}/deployment/create",
            files={"upload": ("process.bpmn", bpmn_content.encode("utf-8"), "application/octet-stream")},
            data={"deployment-name": "mcp-deployment", "enable-duplicate-filtering": "false"},
        )
        response.raise_for_status()

    data = response.json()
    deployment_id = data.get("id")

    # Extract process definition info from deploy response
    deployed = data.get("deployedProcessDefinitions") or {}
    definitions = []
    for def_id, def_info in deployed.items():
        definitions.append({
            "id": def_id,
            "key": def_info.get("key"),
            "name": def_info.get("name"),
            "version": def_info.get("version"),
        })

    # Fallback: query process definitions by deployment ID if deploy response was empty
    if not definitions and deployment_id:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{CAMUNDA_REST_URL}/process-definition",
                params={"deploymentId": deployment_id},
            )
            resp.raise_for_status()
        for def_info in resp.json():
            definitions.append({
                "id": def_info.get("id"),
                "key": def_info.get("key"),
                "name": def_info.get("name"),
                "version": def_info.get("version"),
            })

    result = {
        "deployment_id": deployment_id,
        "process_definitions": definitions,
    }

    if definitions:
        result["process_definition_key"] = definitions[0]["key"]

    return result


async def _get_process_status(process_definition_key: str | None = None) -> dict:
    """Get status of process instances."""
    params = {}
    if process_definition_key:
        params["processDefinitionKey"] = process_definition_key

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{CAMUNDA_REST_URL}/history/process-instance",
            params=params,
        )
        response.raise_for_status()

    instances = response.json()

    # Aggregate by state
    state_counts: dict[str, int] = {}
    instance_list = []
    for inst in instances:
        state = inst.get("state", "UNKNOWN")
        state_counts[state] = state_counts.get(state, 0) + 1
        instance_list.append({
            "id": inst.get("id"),
            "process_definition_key": inst.get("processDefinitionKey"),
            "state": state,
            "start_time": inst.get("startTime"),
            "end_time": inst.get("endTime"),
        })

    return {
        "total_instances": len(instances),
        "state_counts": state_counts,
        "instances": instance_list,
    }


async def _export_event_log(process_definition_key: str) -> dict:
    """Export activity history as CSV event log."""
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{CAMUNDA_REST_URL}/history/activity-instance",
            params={
                "processDefinitionKey": process_definition_key,
                "sortBy": "startTime",
                "sortOrder": "asc",
                "maxResults": 10000,
            },
        )
        response.raise_for_status()

    activities = response.json()

    if not activities:
        return {"error": f"No activity history found for process '{process_definition_key}'."}

    # Build CSV
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["case_id", "activity", "timestamp", "lifecycle"])

    for act in activities:
        case_id = act.get("processInstanceId", "")
        activity_name = act.get("activityName") or act.get("activityId", "")
        start_time = act.get("startTime", "")
        end_time = act.get("endTime", "")

        if start_time:
            writer.writerow([case_id, activity_name, start_time, "start"])
        if end_time:
            writer.writerow([case_id, activity_name, end_time, "complete"])

    csv_content = output.getvalue()
    num_events = sum(1 for act in activities if act.get("startTime")) + sum(1 for act in activities if act.get("endTime"))
    num_cases = len(set(act.get("processInstanceId", "") for act in activities))

    return {
        "file_content": csv_content,
        "file_extension": ".csv",
        "process_definition_key": process_definition_key,
        "num_events": num_events,
        "num_cases": num_cases,
    }


# ---------------------------------------------------------------------------
# Call handler
# ---------------------------------------------------------------------------

@app.call_tool()
async def handle_call_tool(
    name: str, arguments: dict | None
) -> list[types.TextContent]:
    arguments = arguments or {}

    try:
        if name == "deploy_bpmn":
            result = await _deploy_bpmn(
                arguments["bpmn_file"],
                history_ttl=int(arguments.get("history_ttl") or 180),
            )
        elif name == "downgrade_bpmn_to_camunda7":
            result = _downgrade_bpmn_to_camunda7(
                arguments["bpmn_file"],
                ttl=int(arguments.get("history_ttl") or 180),
            )
        elif name == "get_process_status":
            result = await _get_process_status(
                arguments.get("process_definition_key") or None,
            )
        elif name == "export_event_log":
            result = await _export_event_log(arguments["process_definition_key"])
        else:
            result = {"error": f"Unknown tool: {name}"}

    except httpx.ConnectError:
        result = {
            "error": (
                "Camunda engine is not reachable. "
                "Start it with: docker compose up -d"
            )
        }
    except httpx.HTTPStatusError as e:
        body = e.response.text[:500] if e.response.text else ""
        result = {
            "error": f"Camunda API error (HTTP {e.response.status_code}): {body}"
        }
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
