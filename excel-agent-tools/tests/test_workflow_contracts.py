from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / "n8n" / "workflows"
CORE = WORKFLOWS / "core"
SUPPORT = WORKFLOWS / "support"
RETIRED = WORKFLOWS / "retired"
TEMPLATES = ROOT / "n8n" / "templates"
RAG_SOURCE = ROOT / "n8n" / "rag" / "excel-agent-operating-guide.documents.json"
IMPORT_MANIFEST = ROOT / "n8n" / "import-manifest.json"
EXCEL_DELIVERY_WORKFLOWS = {
    "ai-components.workflow.json",
    "excel-extraction-agent.workflow.json",
    "excel-extraction-form-adapter.workflow.json",
}
MATH_DELIVERY_WORKFLOWS = {
    "calculation-specialist-agent.workflow.json",
}
MVP_ENTRY_WORKFLOWS = {
    "mvp-entry-form.workflow.json",
    "mas-human-gate-form.workflow.json",
    "mas-deployment-health-check.workflow.json",
    "mas-activity-hydrate.workflow.json",
}
DATA_TABLE_CSV = ROOT / "n8n" / "data-tables"
SCHEDULE_FOUNDATION_WORKFLOWS = {
    "mas-trace-event-writer.workflow.json",
    "tnavigator-schedule-builder.workflow.json",
    "tnavigator-schedule-hybrid-retrieval.workflow.json",
    "tnavigator-schedule-knowledge-ingestion.workflow.json",
}
UNIVERSAL_ENGINEERING_WORKFLOWS = {
    "cas-persist-task.workflow.json",
    "engineering-specialist-template.workflow.json",
    "universal-engineering-orchestrator.workflow.json",
} | SCHEDULE_FOUNDATION_WORKFLOWS
ERROR_AND_STUB_WORKFLOWS = {
    "mas-error-handler.workflow.json",
    "mas-error-traces.workflow.json",
    "mas-control-plane-proxy.workflow.json",
    "mas-orchestrator.workflow.json",
    "schedule-builder-agent.workflow.json",
    "excel-extractor-agent.workflow.json",
    "mas-runtime-config.workflow.json",
    "cluster-calc-specialist-adapter.workflow.json",
    "binary-results-specialist-adapter.workflow.json",
    "presentation-specialist-adapter.workflow.json",
}


def workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("**/*.workflow.json"))


def importable_workflow_files() -> list[Path]:
    return sorted(path for path in workflow_files() if "retired" not in path.parts)


def workflow_path(name: str) -> Path:
    for folder in (CORE, SUPPORT, RETIRED, WORKFLOWS):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return CORE / name

# Registry names and versions verified against the node packages bundled in
# the official n8nio/n8n:2.30.8 image. These are export JSON identifiers, not
# the shorter labels shown by the node picker UI.
N8N_2_30_8_PORTABLE_NODE_VERSIONS = {
    "@n8n/n8n-nodes-langchain.agent": {3.1},
    "@n8n/n8n-nodes-langchain.chainLlm": {1.9},
    "@n8n/n8n-nodes-langchain.documentDefaultDataLoader": {1.1},
    "@n8n/n8n-nodes-langchain.embeddingsOpenAi": {1.2},
    "@n8n/n8n-nodes-langchain.lmChatOpenAi": {1.3},
    "@n8n/n8n-nodes-langchain.memoryPostgresChat": {1.4},
    "@n8n/n8n-nodes-langchain.outputParserStructured": {1.3},
    "@n8n/n8n-nodes-langchain.textSplitterRecursiveCharacterTextSplitter": {1},
    "@n8n/n8n-nodes-langchain.toolHttpRequest": {1.1},
    "@n8n/n8n-nodes-langchain.vectorStorePGVector": {1.3},
    "n8n-nodes-base.code": {2},
    "n8n-nodes-base.dataTable": {1.1},
    "n8n-nodes-base.executeWorkflow": {1.3},
    "n8n-nodes-base.executeWorkflowTrigger": {1.2},
    "n8n-nodes-base.extractFromFile": {1.1},
    "n8n-nodes-base.errorTrigger": {1},
    "n8n-nodes-base.form": {2.5},
    "n8n-nodes-base.formTrigger": {2.6},
    "n8n-nodes-base.httpRequest": {4.4},
    "n8n-nodes-base.if": {2.2, 2.3},
    "n8n-nodes-base.manualTrigger": {1},
    "n8n-nodes-base.merge": {3.2},
    "n8n-nodes-base.postgres": {2.6},
    "n8n-nodes-base.set": {3.4},
    "n8n-nodes-base.splitInBatches": {3},
    "n8n-nodes-base.stickyNote": {1},
    "n8n-nodes-base.webhook": {2, 2.1},
    "n8n-nodes-base.switch": {3.4},
}


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def test_ui_import_manifest_is_complete_and_matches_static_bindings() -> None:
    assert IMPORT_MANIFEST.is_file(), f"Required import manifest is missing: {IMPORT_MANIFEST}"
    manifest = load_json(IMPORT_MANIFEST)
    assert manifest["contract"] == "n8n_ui_import_manifest"
    assert manifest["target_n8n_version"] == "2.30.8"
    imported = {Path(value).name for value in manifest["full_clean_import_set"]}
    assert imported == {path.name for path in importable_workflow_files()}
    assert len(imported) == 15
    assert {path.name for path in CORE.glob("*.workflow.json")} == {
        Path(value).name for value in manifest["runtime_import_order"]
    }
    assert {path.name for path in SUPPORT.glob("*.workflow.json")} == {
        Path(item["workflow"]).name for item in manifest["optional_or_non_runtime"]
    }
    retired = {Path(value).name for value in manifest["retired_not_imported"]}
    assert retired == {path.name for path in RETIRED.glob("*.workflow.json")}
    assert retired.isdisjoint(imported)
    assert not list(WORKFLOWS.glob("*.workflow.json"))

    workflows_by_name = {
        workflow["name"]: workflow
        for path in workflow_files()
        for workflow in [load_json(path)]
    }
    workflow_names = set(workflows_by_name)
    bindings = manifest["mandatory_execute_workflow_bindings"]
    retired_bindings = manifest["retired_execute_workflow_bindings"]
    future_bindings = manifest["future_enterprise_or_optional_bindings"]
    assert [binding["node"] for binding in bindings] == [
        "Runtime endpoints",
        "Runtime configuration",
        "Runtime configuration",
        "Call Excel Extractor",
        "Call Schedule Builder",
        "Call Knowledge Retrieval",
        "Call Knowledge Retrieval",
        "Call Knowledge Retrieval",
    ]
    assert [binding["owner"] for binding in bindings] == [
        "Orchestrator — MAS",
        "Agent — Excel Extractor",
        "Agent — Schedule Builder",
        "Orchestrator — MAS",
        "Orchestrator — MAS",
        "Orchestrator — MAS",
        "Agent — Excel Extractor",
        "Agent — Schedule Builder",
    ]
    assert future_bindings == []
    assert len(retired_bindings) == 24
    assert manifest["health_check"]["ui_name"] == "Form — MAS Deployment Health Check"
    assert (ROOT / "docs.md").is_file()
    header_auth = next(item for item in manifest["required_credentials"] if item["type"] == "httpHeaderAuth")
    assert "X-API-Key" in header_auth["use"]
    runtime_order = [Path(value).name for value in manifest["runtime_import_order"]]
    assert runtime_order.index("mas-runtime-config.workflow.json") < runtime_order.index(
        "excel-extractor-agent.workflow.json"
    )
    assert runtime_order.index("schedule-builder-agent.workflow.json") < runtime_order.index(
        "mas-orchestrator.workflow.json"
    )
    assert runtime_order.index("excel-extractor-agent.workflow.json") < runtime_order.index(
        "mas-orchestrator.workflow.json"
    )
    core_by_name = {load_json(path)["name"]: load_json(path) for path in CORE.glob("*.workflow.json")}
    support_by_name = {load_json(path)["name"]: load_json(path) for path in SUPPORT.glob("*.workflow.json")}
    live_by_name = {**support_by_name, **core_by_name}
    placeholder_targets: dict[str, tuple[str, str]] = {}
    for binding in bindings + future_bindings:
        identity = binding["target"]
        key = binding["placeholder"]
        if key in placeholder_targets:
            assert placeholder_targets[key] == identity, (
                f"Placeholder {key!r} is reused for a different owner/target"
            )
        else:
            placeholder_targets[key] = identity
    cas_nodes = [
        binding["node"]
        for binding in retired_bindings
        if binding["placeholder"] == "REPLACE_CAS_PERSIST_IN_UI"
    ]
    assert len(cas_nodes) == 10
    assert len(set(cas_nodes)) == 10
    for binding in bindings + future_bindings:
        assert binding["owner"] in live_by_name
        assert binding["target"] in live_by_name or binding["target"] in workflow_names
        owner = live_by_name[binding["owner"]]
        owner_nodes = {node["name"]: node for node in owner["nodes"]}
        assert binding["node"] in owner_nodes, (
            f"Mandatory binding node {binding['node']!r} is missing from workflow "
            f"{binding['owner']!r}; available nodes: {sorted(owner_nodes)}"
        )
        call = owner_nodes[binding["node"]]
        assert call["type"] == "n8n-nodes-base.executeWorkflow"
        assert call["parameters"]["workflowId"]["value"] == binding["placeholder"]
    retired_by_name = {
        load_json(path)["name"]: load_json(path) for path in RETIRED.glob("*.workflow.json")
    }
    for binding in retired_bindings:
        owner = retired_by_name.get(binding["owner"]) or workflows_by_name.get(binding["owner"])
        assert owner is not None, binding["owner"]
        owner_nodes = {node["name"]: node for node in owner["nodes"]}
        assert binding["node"] in owner_nodes, (
            f"Retired binding node {binding['node']!r} is missing from workflow "
            f"{binding['owner']!r}"
        )
    assert any("expert-authored" in blocker for blocker in manifest["mvp_external_blockers"])


def test_ai_tool_nodes_bind_session_from_prepared_workflow_context() -> None:
    workflow = load_json(workflow_path("excel-extraction-agent.workflow.json"))
    tool_nodes = [
        node
        for node in workflow["nodes"]
        if node["type"] == "@n8n/n8n-nodes-langchain.toolHttpRequest"
    ]
    assert {node["name"] for node in tool_nodes} == {
        "workbook_introspect",
        "sheet_preview",
        "detect_tables",
        "match_tables",
        "describe_table",
        "list_column_values",
        "query_table",
        "save_agent_plan",
    }
    required_model_fields = {
        "workbook_introspect": set(),
        "sheet_preview": {"sheet"},
        "detect_tables": set(),
        "match_tables": {"query"},
        "describe_table": {"table_id"},
        "list_column_values": {"table_id", "column"},
        "query_table": {"table_id"},
        "save_agent_plan": {"plan"},
    }
    for node in tool_nodes:
        assert node["typeVersion"] == 1.1, node["name"]
        assert node.get("disabled") is not True, node["name"]
        body_fields = node["parameters"].get("parametersBody", {}).get("values", [])
        session_fields = [field for field in body_fields if field.get("name") == "session_id"]
        assert len(session_fields) == 1, node["name"]
        expression = session_fields[0].get("value", "")
        assert "$('Prepare AI Agent input').first().json.session_id" in expression, node["name"]
        assert "$json.session_id" not in json.dumps(node, ensure_ascii=False), node["name"]
        fields = node["parameters"]["parametersBody"]["values"]
        actual_required = {
            field["name"] for field in fields if field.get("valueProvider") == "modelRequired"
        }
        assert actual_required == required_model_fields[node["name"]], node["name"]

        placeholders = {
            field["name"]: field
            for field in node["parameters"].get("placeholderDefinitions", {}).get("values", [])
        }
        for sequence_name in {"select", "filters", "selected_table_ids", "assumptions", "warnings"} & set(placeholders):
            assert "numeric keys" in placeholders[sequence_name]["description"], (node["name"], sequence_name)


def test_excel_http_tools_are_supply_only_agent_subnodes() -> None:
    workflow = load_json(workflow_path("excel-extraction-agent.workflow.json"))
    agent_name = "Excel Extractor AI Agent"
    tool_names = {
        node["name"]
        for node in workflow["nodes"]
        if node["type"] == "@n8n/n8n-nodes-langchain.toolHttpRequest"
    }
    connections = workflow["connections"]

    assert "ai_tool" not in connections.get(agent_name, {}), (
        "AI Agent must consume tools through its AI Tool input; an Agent -> Tool "
        "edge makes n8n execute a supplyData-only node as a regular node"
    )

    attached_tools: set[str] = set()
    for tool_name in tool_names:
        assert connections.get(tool_name) == {
            "ai_tool": [[{"node": agent_name, "type": "ai_tool", "index": 0}]]
        }, f"{tool_name!r} must have exactly one Tool -> AI Agent ai_tool edge"
        attached_tools.add(tool_name)

    assert attached_tools == tool_names

    for source_name, output_groups in connections.items():
        for edge_group in output_groups.get("main", []):
            for edge in edge_group:
                assert source_name not in tool_names, (
                    f"Supply-only tool {source_name!r} cannot be a main-connection source"
                )
                assert edge["node"] not in tool_names, (
                    f"Supply-only tool {edge['node']!r} cannot be a main-connection target"
                )


def test_workflows_use_current_n8n_2_30_8_ai_node_versions() -> None:
    for path in workflow_files():
        workflow = load_json(path)
        for node in workflow["nodes"]:
            if node["type"] == "@n8n/n8n-nodes-langchain.agent":
                assert node["typeVersion"] == 3.1, (path.name, node["name"])
                expected_parser = path.name in UNIVERSAL_ENGINEERING_WORKFLOWS
                assert node["parameters"].get("hasOutputParser") is expected_parser
            elif node["type"] == "@n8n/n8n-nodes-langchain.chainLlm":
                assert node["typeVersion"] == 1.9, (path.name, node["name"])
                if path.name == "mas-orchestrator.workflow.json":
                    assert node["parameters"].get("hasOutputParser") is True
            elif node["type"] == "@n8n/n8n-nodes-langchain.lmChatOpenAi":
                assert node["typeVersion"] == 1.3, (path.name, node["name"])
                options = node["parameters"].get("options") or {}
                assert options.get("timeout") == 300000, (path.name, node["name"], options)
                assert options.get("maxRetries") == 5, (path.name, node["name"], options)
            elif node["type"] == "@n8n/n8n-nodes-langchain.outputParserStructured":
                params = node["parameters"]
                assert params.get("autoFix") is True, (path.name, node["name"])
                assert params.get("customizeRetryPrompt") is True, (path.name, node["name"])
                assert "{error}" in str(params.get("prompt") or ""), (path.name, node["name"])
            elif node["type"] == "@n8n/n8n-nodes-langchain.memoryPostgresChat":
                assert node["typeVersion"] == 1.4, (path.name, node["name"])


def test_delivery_workflows_use_only_verified_n8n_2_30_8_registry_ids() -> None:
    """Prevent UI display labels or old unscoped package names entering exports."""
    for filename in EXCEL_DELIVERY_WORKFLOWS | MATH_DELIVERY_WORKFLOWS | UNIVERSAL_ENGINEERING_WORKFLOWS:
        workflow = load_json(workflow_path(filename))
        for node in workflow["nodes"]:
            assert node["type"] in N8N_2_30_8_PORTABLE_NODE_VERSIONS, (
                filename,
                node["name"],
                node["type"],
            )
            assert node["typeVersion"] in N8N_2_30_8_PORTABLE_NODE_VERSIONS[node["type"]], (
                filename,
                node["name"],
                node["type"],
                node["typeVersion"],
            )
            assert not node["type"].startswith("n8n-nodes-langchain."), (filename, node["name"])


def test_delivery_workflow_graph_references_are_importable() -> None:
    for filename in EXCEL_DELIVERY_WORKFLOWS | MATH_DELIVERY_WORKFLOWS | UNIVERSAL_ENGINEERING_WORKFLOWS:
        workflow = load_json(workflow_path(filename))
        nodes = workflow["nodes"]
        names = [node["name"] for node in nodes]
        ids = [node["id"] for node in nodes]
        assert len(names) == len(set(names)), filename
        assert len(ids) == len(set(ids)), filename
        known = set(names)
        assert set(workflow["connections"]) <= known, filename
        for groups in workflow["connections"].values():
            for branches in groups.values():
                for branch in branches:
                    for edge in branch:
                        assert edge["node"] in known, (filename, edge["node"])
        connected = set(workflow["connections"])
        for groups in workflow["connections"].values():
            for branches in groups.values():
                for branch in branches:
                    connected.update(edge["node"] for edge in branch)
        runtime_nodes = {
            node["name"]
            for node in nodes
            if node["type"] != "n8n-nodes-base.stickyNote"
        }
        assert runtime_nodes <= connected, (filename, sorted(runtime_nodes - connected))
        assert workflow["active"] is False


def test_core_has_no_disabled_legacy_or_orphan_nodes() -> None:
    workflow = load_json(workflow_path("excel-extraction-agent.workflow.json"))
    assert len(workflow["nodes"]) == 67
    assert not [node["name"] for node in workflow["nodes"] if node.get("disabled") is True]
    names = {node["name"] for node in workflow["nodes"]}
    assert not names.intersection(
        {
            "validate_result",
            "export_result",
            "get_session_state",
            "submit_clarification",
            "resolve_clarification",
            "finalize_extraction",
            "Normalize sub-workflow input",
            "Is sub-workflow input missing?",
            "Build sub-workflow validation error",
        }
    )
    # Runtime nodes are reachable from a normal main connection, or supply an
    # AI subnode connection to the Agent/vector-store parent.
    connected = set(workflow["connections"])
    for groups in workflow["connections"].values():
        for branches in groups.values():
            for branch in branches:
                connected.update(edge["node"] for edge in branch)
    runtime_names = {
        node["name"]
        for node in workflow["nodes"]
        if node["type"] != "n8n-nodes-base.stickyNote"
    }
    assert runtime_names <= connected


def test_excel_agent_repair_and_preflight_lock_column_ambiguity() -> None:
    workflow = load_json(workflow_path("excel-extraction-agent.workflow.json"))
    by_name = {node["name"]: node for node in workflow["nodes"]}
    assess = by_name["Assess deterministic clarification need"]["parameters"]["jsCode"]
    repair = by_name["Prepare deterministic query repair"]["parameters"]["jsCode"]
    terminal = by_name["Build deterministic terminal output"]["parameters"]["jsCode"]
    query = by_name["query_table"]
    retrieval = by_name["Call Excel protocol Hybrid Retrieval"]
    assert "column_candidates" in assess
    assert "ambiguous_columns" not in assess or "column_selection" in assess
    assert "column_selection" in assess
    assert "selected_table_authoritative" in assess
    assert "advisory" in assess
    assert "matchAmbiguous" in repair
    assert "missingQuery" in repair
    assert "missingRequired" in repair
    assert "missingSuggested" in repair
    assert "hasExtra" not in repair
    assert "suggested_tail" in repair
    assert "candidate_ids" in repair
    assert "stored_rows: storedRows" in terminal
    assert any(
        field.get("name") == "tail" and field.get("valueProvider") == "modelOptional"
        for field in query["parameters"]["parametersBody"]["values"]
    )
    assert retrieval["parameters"]["workflowId"]["value"] == "REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI"


def test_tool_instructions_use_structured_arguments_not_legacy_input_wrapper() -> None:
    paths = [
        workflow_path("excel-extraction-agent.workflow.json"),
        workflow_path("tnavigator-schedule-knowledge-ingestion.workflow.json"),
        RAG_SOURCE,
    ]
    stale = ('{"input":', "one input string", "exactly one input JSON string", "Use [] for arrays")
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for phrase in stale:
            assert phrase not in text, (path.name, phrase)


def test_form_adapter_uses_real_trigger_and_real_form_page() -> None:
    """In 2.30.8 ``form`` is a page node and cannot replace formTrigger."""
    workflow = load_json(workflow_path("excel-extraction-form-adapter.workflow.json"))
    by_type = {node["type"]: node for node in workflow["nodes"]}
    assert by_type["n8n-nodes-base.formTrigger"]["typeVersion"] == 2.6
    assert by_type["n8n-nodes-base.form"]["typeVersion"] == 2.5
    assert by_type["n8n-nodes-base.executeWorkflow"]["typeVersion"] == 1.3


def test_hitl_and_health_forms_are_hand_authored_not_generator_emitted() -> None:
    """Clean import uses committed JSON; generators intentionally do not emit forms."""
    import re

    hand_authored = MVP_ENTRY_WORKFLOWS
    for filename in sorted(hand_authored):
        assert (workflow_path(filename)).is_file(), filename

    schedule_source = (TEMPLATES / "generate_schedule_workflows.py").read_text(encoding="utf-8")
    schedule_emitted = set(re.findall(r"d\['([^']+\.workflow\.json)'\]", schedule_source))
    schedule_emitted |= set(re.findall(r'd\["([^"]+\.workflow\.json)"\]', schedule_source))
    assert hand_authored.isdisjoint(schedule_emitted)

    universal_source = (TEMPLATES / "generate_universal_engineering_workflows.py").read_text(encoding="utf-8")
    universal_emitted = set(re.findall(r'(?:WORKFLOWS|CORE|SUPPORT|RETIRED) / "([^"]+\.workflow\.json)"', universal_source))
    universal_emitted |= set(re.findall(r"(?:WORKFLOWS|CORE|SUPPORT|RETIRED) / '([^']+\.workflow\.json)'", universal_source))
    assert hand_authored.isdisjoint(universal_emitted)

    health = "workflows/core/mas-deployment-health-check.workflow.json"
    assert health in load_json(IMPORT_MANIFEST)["full_clean_import_set"]
    assert health in load_json(IMPORT_MANIFEST)["runtime_import_order"]
    for rel in (
        "workflows/retired/mvp-entry-form.workflow.json",
        "workflows/retired/mas-human-gate-form.workflow.json",
        "workflows/retired/mas-activity-hydrate.workflow.json",
    ):
        assert rel not in load_json(IMPORT_MANIFEST)["full_clean_import_set"]
        assert (ROOT / "n8n" / rel).is_file()


def test_mas_entry_and_human_gate_forms_are_native_hitl_ux() -> None:
    """Entry + Human Gate use only portable 2.30.8 Form nodes and auto-bind CAS."""
    entry = load_json(workflow_path("mvp-entry-form.workflow.json"))
    gate = load_json(workflow_path("mas-human-gate-form.workflow.json"))
    assert entry["meta"]["targetN8nVersion"] == "2.30.8"
    assert gate["meta"]["targetN8nVersion"] == "2.30.8"
    assert entry.get("active") is False
    assert gate.get("active") is False

    entry_by_name = {node["name"]: node for node in entry["nodes"]}
    assert entry_by_name["MAS task form"]["type"] == "n8n-nodes-base.formTrigger"
    assert entry_by_name["MAS task form"]["typeVersion"] == 2.6
    assert entry_by_name["Show MAS result"]["type"] == "n8n-nodes-base.form"
    assert entry_by_name["Show MAS result"]["typeVersion"] == 2.5
    assert entry_by_name["Show MAS result"]["parameters"]["operation"] == "completion"
    assert entry_by_name["Show MAS result"]["parameters"]["respondWith"] == "showText"
    assert "form_response_html" in entry_by_name["Build safe MAS form response"]["parameters"]["jsCode"]
    assert "Human input required" in entry_by_name["Build safe MAS form response"]["parameters"]["jsCode"]
    assert "Form — MAS Human Gate" in entry_by_name["Build safe MAS form response"]["parameters"]["jsCode"]
    entry_fields = {
        field["fieldName"] for field in entry_by_name["MAS task form"]["parameters"]["formFields"]["values"]
    }
    assert {"task_description", "file", "trajectory_files", "surface_file"} <= entry_fields
    assert ("schedule_file" in entry_fields) or ("schedule_files" in entry_fields)
    prepare_entry = entry_by_name["Prepare orchestrator request"]["parameters"]["jsCode"]
    assert "mappedBinary" in prepare_entry
    assert ("schedule_file" in prepare_entry) or ("schedule_files" in prepare_entry)
    assert "mappedBinary" in prepare_entry
    assert "binary" in prepare_entry

    gate_by_name = {node["name"]: node for node in gate["nodes"]}
    trigger = gate_by_name["Human gate form"]
    assert trigger["type"] == "n8n-nodes-base.formTrigger"
    assert trigger["typeVersion"] == 2.6
    assert trigger["parameters"].get("authentication") == "n8nUserAuth"
    field_names = {field["fieldName"] for field in trigger["parameters"]["formFields"]["values"]}
    assert {"task_id", "action", "human_response", "requested_by"} <= field_names
    assert "expected_version" not in field_names
    assert "gate_id" not in field_names
    decide = gate_by_name["Decide resume from status"]["parameters"]["jsCode"]
    assert "expected_version" in decide
    assert "gate_id" in decide
    assert "human_gate" in decide
    assert "should_resume" in decide
    assert "human_response is required for reply" in decide
    assert "reply/approve" not in decide
    assert gate_by_name["Call Orchestrator status"]["typeVersion"] == 1.3
    assert gate_by_name["Call Orchestrator resume"]["typeVersion"] == 1.3
    assert gate_by_name["Should resume gate?"]["type"] == "n8n-nodes-base.if"
    assert gate_by_name["Should resume gate?"]["typeVersion"] == 2.2
    assert gate_by_name["Show human gate result"]["typeVersion"] == 2.5
    assert gate_by_name["Show human gate result"]["parameters"]["operation"] == "completion"
    assert "CAS fields applied automatically" in gate_by_name["Build safe human gate response"]["parameters"]["jsCode"]


def test_mas_deployment_health_check_is_native_and_reports_where_to_fix() -> None:
    health = load_json(workflow_path("mas-deployment-health-check.workflow.json"))
    assert health["name"] == "Form — MAS Deployment Health Check"
    assert health["meta"]["targetN8nVersion"] == "2.30.8"
    assert health.get("active") is False
    by_name = {node["name"]: node for node in health["nodes"]}
    assert by_name["Health check form"]["type"] == "n8n-nodes-base.formTrigger"
    assert by_name["Health check form"]["typeVersion"] == 2.6
    assert by_name["Show health report"]["typeVersion"] == 2.5
    assert by_name["Show health report"]["parameters"]["operation"] == "completion"
    # Field rule: URLs live only in MAS — Runtime Config; Health Check reads them, never stores its own.
    runtime = by_name["Runtime endpoints"]
    assert runtime["type"] == "n8n-nodes-base.executeWorkflow"
    assert runtime["typeVersion"] == 1.3
    assert runtime["parameters"]["workflowId"]["value"] == "REPLACE_MAS_RUNTIME_CONFIG_IN_UI"
    assert runtime.get("onError") == "continueRegularOutput"
    assert not any(node["type"] == "n8n-nodes-base.dataTable" for node in health["nodes"])
    assert "Call Orchestrator probe" not in by_name
    assert "Call Trace Writer probe" not in by_name
    http_nodes = [node for node in health["nodes"] if node["type"] == "n8n-nodes-base.httpRequest"]
    assert {node["name"] for node in http_nodes} == {
        "Probe Activity /health",
        "Probe Activity /ready",
        "Probe Excel Tools /health",
        "Probe Schedule Builder /health",
        "Probe Math /health",
        "Probe Orchestrator webhook",
        "Probe Control Plane Proxy webhook",
    }
    for node in http_nodes:
        assert node["parameters"]["url"].startswith("={{ $('Prepare health probes').first().json.urls."), node["name"]
        assert node.get("continueOnFail") is True, node["name"]
    executable = json.dumps([n for n in health["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"], ensure_ascii=False)
    for lab_dns in ("excel-tools:8000", "schedule-builder:8090", "math-service:8100", "mas-activity:8200", "n8n-runners", "n8n:5678"):
        assert lab_dns not in executable, lab_dns
    for name in ("Probe Orchestrator webhook", "Probe Control Plane Proxy webhook"):
        assert by_name[name]["parameters"]["genericAuthType"] == "httpHeaderAuth"
        assert by_name[name]["credentials"]["httpHeaderAuth"]["id"] == "REPLACE_IN_UI"
    prepare = by_name["Prepare health probes"]["parameters"]["jsCode"]
    assert "$('Runtime endpoints')" in prepare
    assert "/mas-control-plane" in prepare
    assert "action: 'probe'" in prepare
    assert "operation: 'list_agents'" in prepare
    report = by_name["Build health report"]["parameters"]["jsCode"]
    assert "where_to_fix" in report
    assert "MAS — Runtime Config" in report
    assert "control_plane_backend" in report
    assert "Header Auth mismatch" in report
    assert "produced no items" in report
    assert "Overall: <strong>" in report and "FAIL — fix these first" in report  # lab_soft_redeploy parses these
    for retired in ("Form — MAS Entry", "engineering_orchestrator_tasks_v1", "mas_trace_events_v1", "CAS — Persist Task State"):
        assert retired not in report, retired
    gate = load_json(workflow_path("mas-human-gate-form.workflow.json"))
    decide = next(n["parameters"]["jsCode"] for n in gate["nodes"] if n["name"] == "Decide resume from status")
    assert "Number.isInteger(expectedVersion)" in decide
    assert "human_response is required for reply" in decide

    task_csv = (DATA_TABLE_CSV / "engineering_orchestrator_tasks_v1.header.csv").read_text(encoding="utf-8")
    trace_csv = (DATA_TABLE_CSV / "mas_trace_events_v1.header.csv").read_text(encoding="utf-8")
    manifest = load_json(IMPORT_MANIFEST)
    task_cols = list(manifest["data_tables"][0]["columns"])
    trace_cols = list(manifest["data_tables"][1]["columns"])
    assert task_csv.strip().split(",") == task_cols
    assert trace_csv.strip().split(",") == trace_cols


def test_workflows_do_not_depend_on_n8n_env_or_global_variables() -> None:
    for path in workflow_files():
        workflow = load_json(path)
        for node in workflow["nodes"]:
            if node.get("type") == "n8n-nodes-base.stickyNote":
                continue
            text = json.dumps(node, ensure_ascii=False)
            assert "$env" not in text, (path.name, node.get("name"))
            assert "$vars" not in text, (path.name, node.get("name"))


def test_delivery_workflows_are_inactive_until_ui_configuration() -> None:
    """An import must not expose webhooks that still contain placeholders."""
    paths = list(workflow_files())
    assert {path.name for path in paths} == (
        EXCEL_DELIVERY_WORKFLOWS
        | MATH_DELIVERY_WORKFLOWS
        | MVP_ENTRY_WORKFLOWS
        | UNIVERSAL_ENGINEERING_WORKFLOWS
        | ERROR_AND_STUB_WORKFLOWS
    )
    for path in paths:
        workflow = load_json(path)
        assert workflow.get("active") is False, path.name


def test_mas_control_plane_proxy_contains_all_activity_operations() -> None:
    workflow = load_json(workflow_path("mas-control-plane-proxy.workflow.json"))
    assert workflow["name"] == "MAS — Control Plane Proxy"
    assert workflow.get("active") is False
    assert workflow["nodes"][1]["parameters"]["path"] == "mas-control-plane"
    code = next(
        node["parameters"]["jsCode"]
        for node in workflow["nodes"]
        if node["name"] == "Normalize control-plane request"
    )
    for operation in (
        "schema", "wipe", "create_case", "get_case", "list_cases", "update_case",
        "append_event", "list_events", "append_error", "list_errors",
        "record_execution", "case_id_for_execution", "list_agents",
        "upsert_agent", "artifact_put", "artifact_get", "snapshot", "batch",
    ):
        assert f"'{operation}'" in code
    assert "batch only supports single-row operations" in code
    assert "editorTest&&flagClear" in code
    assert "webhook-test" in code
    assert "op==='wipe'||(op==='schema'&&" in code
    assert "TRUNCATE TABLE cases" in code
    assert "DO $$" not in code
    assert "jsonb_agg" in code and "AS events FROM cases c" in code
    assert "agent_registry" in code and "TRUNCATE TABLE agent_registry" not in code
    flags = next(node for node in workflow["nodes"] if node["name"] == "Operator flags")
    assert flags["type"] == "n8n-nodes-base.set"
    assignments = flags["parameters"]["assignments"]["assignments"]
    clear_flag = next(item for item in assignments if item["name"] == "clear")
    assert clear_flag["value"] is False
    assert all(item.get("name") != "wipe_data" for item in assignments)
    assert workflow["connections"]["MAS control-plane webhook"]["main"][0][0]["node"] == "Operator flags"
    assert workflow["connections"]["Operator flags"]["main"][0][0]["node"] == "Normalize control-plane request"
    fmt = next(
        node["parameters"]["jsCode"]
        for node in workflow["nodes"]
        if node["name"] == "Format control-plane response"
    )
    assert "op==='schema'||op==='wipe'" in fmt
    assert "dataRows(incoming)" in fmt
    assert "grouped.get(0)||incoming" not in fmt
    assert "operation:'batch'" in fmt.replace(" ", "")
    note = next(node for node in workflow["nodes"] if node["name"] == "edit after import")
    assert "`clear`" in note["parameters"]["content"]
    assert "Test workflow" in note["parameters"]["content"]
    assert "wipe_data" not in note["parameters"]["content"]
    pin = (workflow.get("pinData") or {}).get("MAS control-plane webhook") or []
    assert pin and pin[0]["json"]["body"]["operation"] == "schema"
    assert workflow["settings"].get("saveDataSuccessExecution") == "none"
    assert workflow["settings"].get("saveExecutionProgress") is False
    pg = next(node for node in workflow["nodes"] if node["name"] == "Execute control-plane SQL")
    assert pg["parameters"]["options"]["queryBatching"] == "independently"


def test_mas_runtime_config_is_the_only_url_set() -> None:
    workflow = load_json(workflow_path("mas-runtime-config.workflow.json"))
    assert workflow["name"] == "MAS — Runtime Config"
    assert workflow.get("active") is False
    assert workflow["settings"].get("saveDataSuccessExecution") == "none"
    urls = next(node for node in workflow["nodes"] if node["name"] == "Runtime URLs")
    names = [item["name"] for item in urls["parameters"]["assignments"]["assignments"]]
    assert names == [
        "activity_base_url",
        "excel_tools_url",
        "schedule_service_url",
        "math_url",
        "orchestrator_step_url",
    ]
    assert urls["parameters"]["includeOtherFields"] is False
    blob = json.dumps(workflow)
    assert "excel_tools_api_key" not in blob
    assert "$env" not in blob and "$vars" not in blob
    for filename in (
        "excel-extractor-agent.workflow.json",
        "schedule-builder-agent.workflow.json",
        "mas-orchestrator.workflow.json",
    ):
        other = load_json(workflow_path(filename))
        text = json.dumps(other)
        assert "excel_tools_api_key" not in text
        loaders = [
            node
            for node in other["nodes"]
            if node["name"] in {"Runtime configuration", "Runtime endpoints"}
        ]
        assert loaders
        for node in loaders:
            assert node["type"] == "n8n-nodes-base.executeWorkflow"
            assert node["parameters"]["workflowId"]["value"] == "REPLACE_MAS_RUNTIME_CONFIG_IN_UI"
    excel = load_json(workflow_path("excel-extractor-agent.workflow.json"))
    http_nodes = [
        node
        for node in excel["nodes"]
        if node["type"] in {"n8n-nodes-base.httpRequest", "@n8n/n8n-nodes-langchain.toolHttpRequest"}
    ]
    assert http_nodes
    for node in http_nodes:
        url = str(node.get("parameters", {}).get("url") or "")
        if node["name"].startswith("Activity —") or "/events" in url:
            assert node["parameters"].get("authentication") != "genericCredentialType"
            continue
        assert node["parameters"].get("authentication") == "genericCredentialType"
        assert node["parameters"].get("genericAuthType") == "httpHeaderAuth"
        assert node["credentials"]["httpHeaderAuth"]["name"] == "REPLACE: Excel Tools X-API-Key"


def test_universal_engineering_orchestrator_has_no_service_or_excel_contract() -> None:
    skip = {"tnavigator-schedule-knowledge-ingestion.workflow.json"}
    paths = [
        workflow_path(filename)
        for filename in sorted(UNIVERSAL_ENGINEERING_WORKFLOWS)
        if filename not in skip
    ]
    assert all(path.is_file() for path in paths), (
        "Universal engineering workflow set contains missing files: "
        f"{[path.name for path in paths if not path.is_file()]}"
    )
    forbidden = (
        "fastapi",
        "excel_tools",
        "session_id",
        "workbook_introspect",
        "sheet_preview",
        "detect_tables",
        "query_table",
        "artifact_id",
        "/api/v1",
    )
    for path in paths:
        text = path.read_text(encoding="utf-8").lower()
        for phrase in forbidden:
            assert phrase not in text, (path.name, phrase)


def test_calculation_adapter_posts_dev_batch_and_surface_and_is_statically_bound() -> None:
    adapter = load_json(workflow_path("calculation-specialist-agent.workflow.json"))
    assert adapter["name"] == "Agent — Calculation (Math Service)"
    assert adapter["active"] is False
    by_name = {node["name"]: node for node in adapter["nodes"]}
    trigger = by_name["Receive calculation specialist packet"]
    assert trigger["type"] == "n8n-nodes-base.executeWorkflowTrigger"
    assert trigger["typeVersion"] == 1.2

    config = by_name["Math Service Configuration"]
    config_text = json.dumps(config, ensure_ascii=False)
    assert "http://127.0.0.1:8100/api/v1/math" in config_text
    assert "$env" not in config_text and "$vars" not in config_text

    request = by_name["Call trajectory intersection"]
    assert request["type"] == "n8n-nodes-base.httpRequest"
    assert request["typeVersion"] == 4.4
    assert request["parameters"]["method"] == "POST"
    assert "/trajectory-intersection" in request["parameters"]["url"]
    fields = request["parameters"]["bodyParameters"]["parameters"]
    assert len(fields) == 257
    assert fields[0] == {
        "parameterType": "formBinaryData",
        "name": "trajectory_files",
        "inputDataFieldName": "trajectory_0",
    }
    assert fields[255]["inputDataFieldName"] == "trajectory_255"
    assert fields[-1] == {
        "parameterType": "formBinaryData",
        "name": "surface_file",
        "inputDataFieldName": "surface_file",
    }
    adapter_text = json.dumps(adapter, ensure_ascii=False)
    assert "specialist_result" in adapter_text
    assert "engineering_calculation_specialist" in adapter_text
    assert '"name": "trajectory_files"' in adapter_text
    assert '"name": "surface_file"' in adapter_text
    assert "TRAJECTORY_INTERSECTION_BATCH_COMPUTED" in adapter_text
    assert "result_mode:'computed_batch'" in adapter_text
    assert "MOCK_TRAJECTORY_INTERSECTION" not in adapter_text

    orchestrator = load_json(workflow_path("universal-engineering-orchestrator.workflow.json"))
    orchestrator_by_name = {node["name"]: node for node in orchestrator["nodes"]}
    call = orchestrator_by_name["Call Calculation Specialist"]
    assert call["parameters"]["workflowId"]["value"] == "REPLACE_CALCULATION_AGENT_IN_UI"
    assert call["parameters"]["workflowId"]["cachedResultName"] == adapter["name"]
    allowlist_code = orchestrator_by_name["Resolve allowlisted specialist"]["parameters"]["jsCode"]
    assert "engineering_calculation_specialist:{route:2,configured:true}" in allowlist_code
    assert "specialist_registry" in allowlist_code or "excel_extraction_specialist:{route:0,configured:true}" in allowlist_code
    assert "appendHandoffEvent" in allowlist_code
    assert "DELEGATED" in allowlist_code


def test_universal_orchestrator_enterprise_control_plane() -> None:
    workflow = load_json(workflow_path("universal-engineering-orchestrator.workflow.json"))
    nodes = workflow["nodes"]
    by_name = {node["name"]: node for node in nodes}
    names = {node["name"] for node in nodes}
    types = [node["type"] for node in nodes]
    text = json.dumps(workflow, ensure_ascii=False)

    assert types.count("n8n-nodes-base.dataTable") == 1
    assert by_name["Load task by ID"]["type"] == "n8n-nodes-base.dataTable"
    assert types.count("@n8n/n8n-nodes-langchain.agent") == 2
    assert {node["name"] for node in nodes if node["type"] == "@n8n/n8n-nodes-langchain.agent"} == {
        "Engineering Planner Agent",
        "Independent Verifier Agent",
    }
    cas_calls = [
        "Call CAS persist — insert new task",
        "Call CAS persist — human action then plan",
        "Call CAS persist — terminal human action",
        "Call CAS persist — plan or human gate",
        "Call CAS persist — SCHEDULE evidence retry",
        "Call CAS persist — SCHEDULE resume",
        "Call CAS persist — verification",
        "Call CAS persist — specialist gate or error",
        "Call CAS persist — routing gate",
    ]
    assert set(cas_calls) <= names
    for name in cas_calls:
        node = by_name[name]
        assert node["type"] == "n8n-nodes-base.executeWorkflow"
        assert node["typeVersion"] == 1.3
        assert node["parameters"]["workflowId"]["value"] == "REPLACE_CAS_PERSIST_IN_UI"
        assert node["parameters"]["workflowId"]["cachedResultName"] == "CAS — Persist Task State"
        assert node["parameters"]["options"]["waitForSubWorkflow"] is True
        assert node.get("onError") == "continueRegularOutput"
        assert set(node["parameters"]["workflowInputs"]["value"]) == {"cas_operation", "attempted"}
        assert node["parameters"]["workflowInputs"]["value"]["attempted"] == "={{ $json }}"
    assert not [name for name in names if name.startswith("CAS persist")]
    assert not [name for name in names if name.startswith("Confirm ") and name.endswith(" CAS")]
    assert "Insert durable task state" not in names
    assert "Approved or continued task delegates directly?" in names
    assert "Human action planning CAS succeeded?" in names
    assert (
        by_name["Human action planning CAS succeeded?"]["parameters"]["conditions"]["conditions"][0]["leftValue"]
        == "={{ $json.cas_succeeded }}"
    )
    assert by_name["Call CAS persist — insert new task"]["parameters"]["workflowInputs"]["value"]["cas_operation"] == (
        "={{ 'insert' }}"
    )
    for name in cas_calls[1:]:
        assert by_name[name]["parameters"]["workflowInputs"]["value"]["cas_operation"] == "={{ 'update' }}"
    for node in nodes:
        if node["name"].startswith("Call ") and node["name"].endswith(" Specialist"):
            assert node.get("retryOnFail") is not True, node["name"]

    assert "specialist_id" in text
    assert "specialist_route" in text
    assert "workflow_id" not in json.dumps(
        next(node for node in nodes if node["name"] == "Engineering Planner Agent"),
        ensure_ascii=False,
    ).lower()
    assert "Лимит автоматических повторов исчерпан" in text
    assert "Уточните ввод" in text
    assert "to_role:'User'" in text
    assert "from_role:'Orchestrator'" in text
    assert "status:'NEEDS_DECISION'" in text or "status:hitlStatus" in text
    assert "can_release?'succeeded'" not in text
    pipeline = (TEMPLATES / "schedule_pipeline.py").read_text(encoding="utf-8")
    assert "status:releaseReady?'needs_approval':'needs_input'" in pipeline
    assert "kind:releaseReady?'needs_approval':'needs_input'" in pipeline
    assert "kind='result_approval'" in text
    assert "expected_version" in text
    assert "gate_id" in text
    assert "pre_delegation_approval" in text
    assert "should_delegate" in text
    assert "Approved task has no persisted specialist packet" in text
    assert "result_approval" in text
    assert "Data Table is authoritative durable state" in text
    assert "CAS — Persist Task State" in text
    assert "human_responses" in text
    assert "Payload is too large" in text
    assert "parseStructured" in text
    assert "contains malformed JSON." in text
    assert "parseHumanResponse" in text
    assert "persistedRisk" in text
    assert "riskFloor" in text
    assert "Retry is allowed only for a persisted retryable_error" in text
    assert "Stored task state is malformed" in text
    assert "nextStatus=x.status||'not_found'" in text
    assert "nextStatus=x.stored_status;message='Task is terminal" in text
    assert "risk_class:'low'" in text
    assert "declaredRisk" in text
    assert "request.task?.objective" in text
    assert "packetComplete" in text
    assert "criticalDelegation?'pre_delegation_approval':'needs_approval'" in text
    assert "...req,...row,state_found:true" in text
    assert "SELF_CHECK_REQUIRED" in text
    assert "SELF_CHECK_FAILED" in text
    assert "n8n-nodes-base.wait" not in types
    assert "memoryPostgresChat" not in text

    planner_parser = next(node for node in nodes if node["name"] == "Planner Structured Output")
    planner_schema = json.loads(planner_parser["parameters"]["inputSchema"])
    verifier_parser = next(node for node in nodes if node["name"] == "Verifier Structured Output")
    verifier_schema = json.loads(verifier_parser["parameters"]["inputSchema"])
    assert "decision_record" in planner_schema["properties"]
    assert "decision_record" not in planner_schema.get("required", [])
    assert "decision_record" in verifier_schema["properties"]
    for code_node_name in ("Validate and apply plan", "Apply verification policy"):
        code_text = next(node for node in nodes if node["name"] == code_node_name)["parameters"]["jsCode"]
        assert ".25*scopeFit" in code_text
        assert ".25*evidenceCompleteness" in code_text
        assert ".20*sourceAuthority" in code_text
        assert ".15*entityTemporalConsistency" in code_text
        assert ".15*deterministicValidationHealth" in code_text
        assert "raw_counts" in code_text
        assert "provisional:true" in code_text
        assert "decision_record" in code_text
    packet_schema = planner_schema["properties"]["specialist_packet"]
    packet_props = set((packet_schema.get("properties") or {}))
    assert {
        "contract", "contract_version", "specialist_id", "objective", "inputs", "controls",
        "acceptance_criteria", "artifact_refs",
    } <= packet_props
    assert packet_schema.get("additionalProperties") is True

    form = next(node for node in nodes if node["name"] == "Engineering task form")
    form_fields = {field["fieldName"] for field in form["parameters"]["formFields"]["values"]}
    assert {"request_text", "request_json", "runtime_json", "file"} <= form_fields
    assert ("schedule_file" in form_fields) or ("schedule_files" in form_fields)
    schedule_field = next(
        field
        for field in form["parameters"]["formFields"]["values"]
        if field["fieldName"] in {"schedule_file", "schedule_files"}
    )
    assert schedule_field["fieldType"] == "file"
    connections = workflow["connections"]

    def main_targets(source: str, output_index: int = 0) -> list[str]:
        assert source in connections, f"Missing connection source: {source}"
        outputs = connections[source].get("main", [])
        assert len(outputs) > output_index, (
            f"Missing main output {output_index} for connection source: {source}"
        )
        return [edge["node"] for edge in outputs[output_index]]

    assert main_targets("Mark Form entrypoint") == ["Form has SCHEDULE upload?"]
    assert main_targets("Form has SCHEDULE upload?", 0) == [
        "Materialize SCHEDULE uploads"
    ]
    assert main_targets("Form has SCHEDULE upload?", 1) == ["Materialize SCHEDULE uploads"]
    assert main_targets("Materialize SCHEDULE uploads") == ["Normalize invocation"]
    normalize_code = by_name["Normalize invocation"]["parameters"]["jsCode"]
    assert "baselineBytes<=2097152" in normalize_code
    assert "baseline_schedule_text" in normalize_code
    planner_code = by_name["Prepare planner input"]["parameters"]["jsCode"]
    assert "baseline_schedule_text" in planner_code
    apply_plan_code = by_name["Validate and apply plan"]["parameters"]["jsCode"]
    assert "baseline_schedule_text" in apply_plan_code


CAS_STATE_COLUMNS = [
    "task_id", "version", "status", "risk_class",
    "request_json", "runtime_json", "plan_json", "packet_json", "result_json",
    "verification_json", "gate_json", "retry_count",
    "max_retries", "created_at", "updated_at",
]


def test_cas_persist_task_is_the_single_fail_closed_write_path() -> None:
    workflow = load_json(workflow_path("cas-persist-task.workflow.json"))
    assert workflow["name"] == "CAS — Persist Task State"
    assert workflow.get("active") is False
    assert workflow["meta"]["targetN8nVersion"] == "2.30.8"
    text = json.dumps(workflow, ensure_ascii=False)
    assert "$env" not in text
    assert "$vars" not in text
    by_name = {node["name"]: node for node in workflow["nodes"]}
    names = set(by_name)
    assert {
        "Receive CAS persist request",
        "Validate CAS persist request",
        "CAS request valid?",
        "CAS operation router",
        "Insert durable task row",
        "Update durable task row",
        "Confirm CAS persist",
        "Build invalid CAS persist result",
    } <= names

    tables = [node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.dataTable"]
    assert {node["name"] for node in tables} == {"Insert durable task row", "Update durable task row"}
    for node in tables:
        assert node["typeVersion"] == 1.1
        assert node.get("alwaysOutputData") is True
        assert node["parameters"]["dataTableId"]["value"] == "REPLACE_IN_UI"
        columns = node["parameters"]["columns"]["value"]
        assert list(columns) == CAS_STATE_COLUMNS
        assert "binary" not in columns
        assert "previous_version" not in columns

    insert = by_name["Insert durable task row"]
    assert insert["parameters"]["operation"] == "insert"
    update = by_name["Update durable task row"]
    assert update["parameters"]["operation"] == "update"
    filters = {(item["keyName"], item["keyValue"]) for item in update["parameters"]["filters"]["conditions"]}
    assert filters == {
        ("task_id", "={{ $json.task_id }}"),
        ("version", "={{ $json.previous_version }}"),
    }

    trigger = by_name["Receive CAS persist request"]
    assert trigger["type"] == "n8n-nodes-base.executeWorkflowTrigger"
    assert trigger["typeVersion"] == 1.2
    validate = by_name["Validate CAS persist request"]["parameters"]["jsCode"]
    assert "CAS_OPERATION_INVALID" in validate
    assert "CAS_PREVIOUS_VERSION_REQUIRED" in validate
    assert "CAS_STATE_COLUMNS_MISSING" in validate
    confirm = by_name["Confirm CAS persist"]["parameters"]["jsCode"]
    assert "tableRows.length!==1" in confirm
    assert "cas_attempted===undefined" in confirm
    assert "CAS_CONFLICT" in confirm
    assert "cas_succeeded:true" in confirm
    assert "cas_succeeded:false" in confirm
    invalid = by_name["Build invalid CAS persist result"]["parameters"]["jsCode"]
    assert "INVALID_CAS_REQUEST" in invalid
    assert "cas_succeeded:false" in invalid

    connections = workflow["connections"]
    assert connections["CAS request valid?"]["main"][0][0]["node"] == "CAS operation router"
    assert connections["CAS request valid?"]["main"][1][0]["node"] == "Build invalid CAS persist result"
    assert connections["CAS operation router"]["main"][0][0]["node"] == "Insert durable task row"
    assert connections["CAS operation router"]["main"][1][0]["node"] == "Update durable task row"
    assert connections["Insert durable task row"]["main"][0][0]["node"] == "Confirm CAS persist"
    assert connections["Update durable task row"]["main"][0][0]["node"] == "Confirm CAS persist"

    generator = (TEMPLATES / "generate_universal_engineering_workflows.py").read_text(encoding="utf-8")
    assert "def call_cas_persist(" in generator
    assert "def build_cas_persist(" in generator
    assert 'RETIRED / "cas-persist-task.workflow.json"' in generator
    assert "Insert durable task state" not in generator
    assert "CAS persist human action then plan" not in generator


def test_universal_orchestrator_has_a_static_excel_specialist_route() -> None:
    workflow = load_json(workflow_path("universal-engineering-orchestrator.workflow.json"))
    by_name = {node["name"]: node for node in workflow["nodes"]}
    text = json.dumps(workflow, ensure_ascii=False)

    assert "excel_extraction_specialist" in text
    resolve_code = by_name["Resolve allowlisted specialist"]["parameters"]["jsCode"]
    assert "excel_extraction_specialist:{route:0,configured:true}" in resolve_code
    assert by_name["Configured specialist router"]["parameters"]["numberOutputs"] == 8

    call = by_name["Call Excel Extraction Specialist"]
    assert call["type"] == "n8n-nodes-base.executeWorkflow"
    assert call["typeVersion"] == 1.3
    assert call["parameters"]["workflowId"]["value"] == "REPLACE_EXCEL_EXTRACTION_AGENT_IN_UI"
    assert call["parameters"]["mode"] == "once"
    assert call["parameters"]["options"]["waitForSubWorkflow"] is True
    assert call["onError"] == "continueRegularOutput"
    assert call.get("retryOnFail") is not True
    assert set(call["parameters"]["workflowInputs"]["value"]) == {
        "specialist_packet",
        "previous_specialist_result",
        "latest_human_response",
    }

    prepare_code = by_name["Prepare specialist invocation context"]["parameters"]["jsCode"]
    assert "parse(x.result_json,{})" in prepare_code
    assert "responses[responses.length-1].response" in prepare_code
    assert "$('Normalize invocation').first().binary" in prepare_code
    load_columns = by_name["Load task by ID"]["parameters"]
    assert "binary" not in json.dumps(load_columns)
    cas = load_json(workflow_path("cas-persist-task.workflow.json"))
    cas_by_name = {node["name"]: node for node in cas["nodes"]}
    assert "binary" not in cas_by_name["Insert durable task row"]["parameters"]["columns"]["value"]


def test_universal_orchestrator_has_a_static_schedule_builder_route() -> None:
    workflow = load_json(workflow_path("universal-engineering-orchestrator.workflow.json"))
    by_name = {node["name"]: node for node in workflow["nodes"]}
    text = json.dumps(workflow, ensure_ascii=False).lower()

    assert "schedule_builder_specialist" in text
    resolve_code = by_name["Resolve allowlisted specialist"]["parameters"]["jsCode"]
    assert "schedule_builder_specialist:{route:1,configured:true}" in resolve_code
    call = by_name["Call SCHEDULE Builder Specialist"]
    assert call["type"] == "n8n-nodes-base.executeWorkflow"
    assert call["typeVersion"] == 1.3
    assert call["parameters"]["workflowId"]["value"] == "REPLACE_SCHEDULE_BUILDER_IN_UI"
    assert call["parameters"]["options"]["waitForSubWorkflow"] is True
    assert call["onError"] == "continueRegularOutput"
    assert set(call["parameters"]["workflowInputs"]["value"]) == {"specialist_packet", "previous_specialist_result", "latest_human_response"}
    rag_call = by_name["Call SCHEDULE Hybrid Retrieval"]
    assert rag_call["parameters"]["workflowId"]["value"] == "REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI"
    routing_call = by_name["Call routing Hybrid Retrieval"]
    assert routing_call["parameters"]["workflowId"]["value"] == "REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI"
    excel_rag_call = by_name["Call Excel protocol Hybrid Retrieval"]
    assert excel_rag_call["parameters"]["workflowId"]["value"] == "REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI"
    planner_code = by_name["Prepare planner input"]["parameters"]["jsCode"]
    assert "routing_rag_evidence" in planner_code
    routing_gate = by_name["Build routing RAG evidence gate"]["parameters"]["jsCode"]
    assert "orchestrator_routing" in routing_gate
    assert "routing_card" in routing_gate
    assert "EXCEL_PROTOCOL_RAG_REQUIRED" in by_name["Build Excel protocol RAG evidence gate"]["parameters"]["jsCode"]
    assert "excel_protocol" in by_name["Prepare governed Excel protocol RAG request"]["parameters"]["jsCode"]
    connections = workflow["connections"]
    assert connections["Configured specialist router"]["main"][0][0]["node"] == "Prepare governed Excel protocol RAG request"
    assert connections["Excel protocol RAG evidence ready?"]["main"][0][0]["node"] == "Call Excel Extraction Specialist"
    assert connections["Routing RAG evidence ready?"]["main"][0][0]["node"] == "Prepare planner input"
    assert rag_call["type"] == "n8n-nodes-base.executeWorkflow"
    assert rag_call["typeVersion"] == 1.3
    assert rag_call["parameters"]["workflowId"]["value"] == "REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI"
    assert set(rag_call["parameters"]["workflowInputs"]["value"]) == {"schedule_retrieval_request"}
    assert rag_call["onError"] == "continueRegularOutput"
    assert "SCHEDULE_RAG_EVIDENCE_REQUIRED" in by_name["Build SCHEDULE RAG evidence gate"]["parameters"]["jsCode"]
    assert "schedule_access_scope" not in by_name["Build SCHEDULE RAG evidence gate"]["parameters"]["jsCode"]
    attach = by_name["Attach governed SCHEDULE RAG evidence"]["parameters"]["jsCode"]
    assert "schedule_rag_evidence" in attach
    assert "result.citations.length>0" in attach


def test_universal_orchestrator_has_a_static_redacted_trace_route() -> None:
    workflow = load_json(workflow_path("universal-engineering-orchestrator.workflow.json"))
    by_name = {node["name"]: node for node in workflow["nodes"]}
    call = by_name["Call MAS Trace Event Writer"]
    assert call["type"] == "n8n-nodes-base.executeWorkflow"
    assert call["typeVersion"] == 1.3
    assert call["parameters"]["workflowId"]["value"] == "REPLACE_MAS_TRACE_WRITER_IN_UI"
    assert call["parameters"]["options"]["waitForSubWorkflow"] is True
    assert call["onError"] == "continueRegularOutput"
    assert set(call["parameters"]["workflowInputs"]["value"]) == {
        "mas_trace_event", "mas_trace_events", "passthrough",
    }
    prepare = by_name["Prepare final MAS trace event"]["parameters"]["jsCode"]
    restore = by_name["Restore orchestrator state after trace"]["parameters"]["jsCode"]
    assert "decision_record:decisionRecord" in prepare
    assert "mas_trace_events:events.slice(0,100)" in prepare
    assert "tool_calls" in prepare and "evidence_refs" in prepare and "findings" in prepare
    assert "passthrough:x" in prepare
    assert "Trace writer did not return orchestrator state" in restore


def test_schedule_flow_is_orchestrator_mediated_and_multi_stage() -> None:
    workflow = load_json(workflow_path("universal-engineering-orchestrator.workflow.json"))
    by_name = {node["name"]: node for node in workflow["nodes"]}
    connections = workflow["connections"]
    resolve_code = by_name["Resolve allowlisted specialist"]["parameters"]["jsCode"]
    handoff_code = by_name["Route successful specialist handoff"]["parameters"]["jsCode"]

    assert "EXCEL_EVIDENCE_READY" in handoff_code
    assert "next_specialist:'schedule_builder_specialist'" in handoff_code
    assert "normalizeSourceFactsPacket" in handoff_code
    assert "INVALID_SOURCE_FACTS_PACKET" in handoff_code
    assert "source_facts_packet:packetFacts" in handoff_code
    retry_code = by_name["Prepare SCHEDULE evidence retry"]["parameters"]["jsCode"]
    assert "MALFORMED_EVIDENCE_GAP" in retry_code
    assert "typedEvidenceGaps" in retry_code
    format_code = by_name["Format orchestrator response"]["parameters"]["jsCode"]
    assert "mas_activity_feed/v1" in format_code
    assert "handoff_events" in format_code
    trace_code = by_name["Prepare final MAS trace event"]["parameters"]["jsCode"]
    assert "event_type:'handoff'" in trace_code
    registry = load_json(ROOT / "n8n" / "contracts" / "specialist_registry.v1.json")
    assert registry["contract"] == "specialist_registry"
    assert {s["specialist_id"] for s in registry["specialists"]} <= {
        "excel_extraction_specialist",
        "schedule_builder_specialist",
        "engineering_calculation_specialist",
        "engineering_data_specialist",
        "engineering_document_specialist",
        "cluster_calculation_specialist",
        "binary_results_specialist",
        "presentation_specialist",
    }
    assert {s["specialist_id"] for s in registry["specialists"] if s.get("configured")} == {
        "excel_extraction_specialist",
        "schedule_builder_specialist",
        "engineering_calculation_specialist",
    }
    for sid, route in (
        ("cluster_calculation_specialist", 5),
        ("binary_results_specialist", 6),
        ("presentation_specialist", 7),
    ):
        row = next(s for s in registry["specialists"] if s["specialist_id"] == sid)
        assert row["configured"] is False
        assert row["route"] == route
    assert "Call Cluster Calculation Specialist" in by_name
    assert by_name["Call Cluster Calculation Specialist"]["parameters"]["workflowId"]["value"] == (
        "REPLACE_CLUSTER_CALC_ADAPTER_IN_UI"
    )
    assert set(by_name["Call Binary Results Specialist"]["parameters"]["workflowInputs"]["value"]) == {
        "specialist_packet",
        "previous_specialist_result",
        "latest_human_response",
    }
    assert "cluster_calculation_specialist:{route:5,configured:false}" in resolve_code
    assert connections["Configured specialist router"]["main"][5][0]["node"] == "Call Cluster Calculation Specialist"
    assert connections["Configured specialist router"]["main"][6][0]["node"] == "Call Binary Results Specialist"
    assert connections["Configured specialist router"]["main"][7][0]["node"] == "Call Presentation Specialist"
    next_stage = connections["Successful specialist next stage"]["main"]
    expected_routes = [
        "Prepare governed routing RAG request",
        "Prepare SCHEDULE resume after Excel",
        "Prepare independent verification",
    ]
    assert isinstance(next_stage, list), "Successful specialist next stage outputs must be a list"
    assert len(next_stage) == len(expected_routes), (
        "Successful specialist next stage must expose exactly three ordered outputs; "
        f"expected {len(expected_routes)}, got {len(next_stage)}"
    )
    for output_index, (output_connections, expected_node) in enumerate(
        zip(next_stage, expected_routes, strict=True)
    ):
        assert isinstance(output_connections, list) and output_connections, (
            f"Successful specialist next stage output {output_index} has no connection"
        )
        first_connection = output_connections[0]
        assert isinstance(first_connection, dict) and "node" in first_connection, (
            f"Successful specialist next stage output {output_index} is malformed: "
            f"{first_connection!r}"
        )
        assert first_connection["node"] == expected_node, (
            f"Successful specialist next stage output {output_index} must route to "
            f"{expected_node!r}, got {first_connection['node']!r}"
        )
    builder = load_json(workflow_path("tnavigator-schedule-builder.workflow.json"))
    assert not [node for node in builder["nodes"] if node["type"] == "n8n-nodes-base.executeWorkflow"]

    required_pipeline_nodes = {
        "Run deterministic SCHEDULE intake",
        "Analyze lossless baseline inventory",
        "Decode typed baseline records",
        "Replay baseline prefix into semantic boundary",
        "Query baseline planning context",
        "Query targeted baseline records",
        "SCHEDULE Planner Agent",
        "SCHEDULE Builder Agent",
        "Render typed SCHEDULE IR deterministically",
        "Merge SCHEDULE draft deterministically",
        "Validate merged SCHEDULE package",
        "Run independent SCHEDULE verifier",
        "Build release-ready specialist result",
    }
    assert required_pipeline_nodes <= {node["name"] for node in builder["nodes"]}

    retry_code = by_name["Prepare SCHEDULE evidence retry"]["parameters"]["jsCode"]
    resume_code = by_name["Prepare SCHEDULE resume after Excel"]["parameters"]["jsCode"]
    assert "schedule-builder-evidence-gap-v1" in retry_code
    assert "STALLED_EVIDENCE_LOOP" in retry_code
    assert "EXCEL_EVIDENCE_BUDGET_EXHAUSTED" in retry_code
    assert "schedule_evidence_gap" in retry_code
    assert "source_snapshot_hash" in resume_code
    assert "expected_correlation_id" in retry_code
    assert "corrOk=!expectedCorr||correlation===expectedCorr||factCount>0" in resume_code
    assert "mergedMap" in resume_code
    assert "schedule_builder_specialist" in resume_code

    apply_action = by_name["Apply action and version guard"]["parameters"]["jsCode"]
    apply_plan = by_name["Validate and apply plan"]["parameters"]["jsCode"]
    assert "SCHEDULE release is blocked" in apply_action
    assert "schedule_text:inlineText" in apply_action
    assert "simulator_check_result" not in apply_action
    assert "artifact publication" not in apply_action.lower()
    assert "scheduleTask&&riskRank[risk]<riskRank.high" in apply_plan
    assert "policy_version=clean(c.policy_version)||'petroleum-schedule-policy-v1'" in apply_plan
    assert "idempotency_key:`${base.task_id}:specialist:" in apply_plan
    assert "expected_version:Number(base.version)+1" in apply_plan
    assert "policy_version:'petroleum-schedule-policy-v1'" in resume_code
    assert "blockingQuestions" in apply_plan
    assert "profileQuestion" in apply_plan
    assert "excelOwnedQuestion" in apply_plan
    assert "explicitScheduleConsumer" in apply_plan
    assert "access_scope:clean(c.access_scope)||'petroleum-engineering'" not in apply_plan
    assert "simulator:clean(c.simulator)||'tNavigator'" not in apply_plan
    assert "simulator_version:clean(c.simulator_version)||'22.2'" not in apply_plan
    assert "if(blockingQuestions.length)hardBlockers.push('PLANNER_UNRESOLVED_QUESTIONS')" in apply_plan
    assert ("ENTITY_TEMPORAL_SCOPE_INCOMPLETE" in apply_plan) or ("excelDelegation" in apply_plan)
    assert "planner_questions:questions.length" in apply_plan
    rag_gate = by_name["Build SCHEDULE RAG evidence gate"]["parameters"]["jsCode"]
    assert "schedule_access_scope" not in rag_gate
    assert "schedule_rag_evidence" in rag_gate
    planner_message = by_name["Engineering Planner Agent"]["parameters"]["options"]["systemMessage"]
    assert "It is not a questionnaire" in planner_message
    assert "When to ask vs delegate" in planner_message
    assert "access_scope=petroleum-engineering" in planner_message
    assert "Do not ask the human to confirm these defaults" in planner_message
    hitl_card = next(
        document
        for document in ingestible_operating_guide_documents()
        if document.get("knowledge_id") == "route-hitl-required-evidence"
    )
    assert hitl_card["revision"] == "5"
    assert "delegate excel_extractor, не HITL" in hitl_card["text"]
    assert "Builder RAG evidence gate" in hitl_card["text"]


def test_schedule_builder_is_bounded_and_orchestrator_mediated() -> None:
    workflow = load_json(workflow_path("tnavigator-schedule-builder.workflow.json"))
    by_name = {node["name"]: node for node in workflow["nodes"]}
    text = json.dumps(workflow, ensure_ascii=False).lower()

    assert workflow["active"] is False
    assert by_name["Receive specialist packet"]["type"] == "n8n-nodes-base.executeWorkflowTrigger"
    assert by_name["SCHEDULE Builder Agent"]["parameters"]["options"]["returnIntermediateSteps"] is True
    assert "create" in text and "revise" in text
    assert all(keyword.lower() in text for keyword in (
        "dates", "include", "gruptree", "welspecs", "welltrack", "compdatmd",
        "wconhist", "wconprod", "gconprod", "branprop", "nodeprop",
        "fracture_specs", "fracture_stage", "wecon", "wtest",
    ))
    assert "keep" in text and "modify" in text and "add" in text and "remove" in text
    assert "preserve_unmentioned" in text
    assert "evidence_gap" in text
    assert "remove_requires_accountable_approval" in text
    assert not [node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.executeWorkflow"]
    assert "session_id" not in text
    assert "/api/v1" not in text
    assert "toolhttprequest" not in text
    assert workflow["name"] == "SCHEDULE — Builder"
    stage_order = [
        "Run deterministic SCHEDULE intake",
        "Analyze lossless baseline inventory",
        "Decode typed baseline records",
        "Replay baseline prefix into semantic boundary",
        "Query baseline planning context",
        "Validate SCHEDULE pipeline plan",
        "Query targeted baseline records",
        "Validate SCHEDULE builder stage",
        "Render typed SCHEDULE IR deterministically",
        "Merge SCHEDULE draft deterministically",
        "Validate merged SCHEDULE package",
        "Run independent SCHEDULE verifier",
    ]
    positions = {node["name"]: node["position"][0] for node in workflow["nodes"]}
    assert [positions[name] for name in stage_order] == sorted(positions[name] for name in stage_order)
    assert "schema_catalogue_not_approved" in text
    assert "schedule_rag_evidence_required" in text
    assert "rag.citations.length>0" in text
    prepare_validation = by_name["Prepare SCHEDULE validation"]["parameters"]["jsCode"]
    assert "generatedBoundary" in prepare_validation
    assert "root.request.semantic_baseline_snapshot" not in prepare_validation
    assert "validation_phase:'CANDIDATE'" in prepare_validation
    prepare_replay = by_name["Prepare baseline prefix replay"]["parameters"]["jsCode"]
    assert "validation_phase:'BASELINE_PREFIX'" in prepare_replay
    targeted_query = by_name["Prepare targeted baseline query"]["parameters"]["jsCode"]
    assert "purpose:'BUILD'" in targeted_query
    assert "require_complete:true" in targeted_query
    builder_validation = by_name["Validate SCHEDULE builder stage"]["parameters"]["jsCode"]
    assert "CHANGE_TARGET_OUTSIDE_BASELINE_QUERY" in builder_validation
    assert "CHANGE_TARGET_HASH_MISMATCH" in builder_validation
    assert "TARGETED_BASELINE_QUERY_REQUIRED" in builder_validation

    planner_parser = by_name["SCHEDULE Planner Structured Output"]
    planner_schema = json.loads(planner_parser["parameters"]["inputSchema"])
    builder_parser = by_name["SCHEDULE Builder Structured Output"]
    builder_schema = json.loads(builder_parser["parameters"]["inputSchema"])
    assert ("decision_record" in planner_schema.get("required", [])) or ("decision_record" in planner_schema.get("properties", {}))
    assert ("decision_record" in builder_schema.get("required", [])) or ("decision_record" in builder_schema.get("properties", {}))
    assert "ir_events" in builder_schema["required"]
    assert "schedule_schema_catalogue" in by_name["Render typed SCHEDULE IR deterministically"]["parameters"]["jsCode"]
    assert "SCHEMA_EXPERT_AUTHOR_REQUIRED" in by_name["Render typed SCHEDULE IR deterministically"]["parameters"]["jsCode"]
    assert "relevance_score" not in planner_parser["parameters"]["inputSchema"]
    planner_validation = by_name["Validate SCHEDULE pipeline plan"]["parameters"]["jsCode"]
    builder_validation = by_name["Validate SCHEDULE builder stage"]["parameters"]["jsCode"]
    for code in (planner_validation, builder_validation):
        assert ".25*scopeFit" in code
        assert ".25*evidenceCompleteness" in code
        assert ".20*sourceAuthority" in code
        assert ".15*entityTemporalConsistency" in code
        assert ".15*deterministicValidationHealth" in code
        assert "raw_counts" in code
        assert "provisional:true" in code
        assert "hardBlockers" in code
    assert "relevance_score" not in planner_validation


def test_excel_specialist_adapter_is_a_bounded_native_contract_boundary() -> None:
    workflow = load_json(workflow_path("excel-extraction-agent.workflow.json"))
    by_name = {node["name"]: node for node in workflow["nodes"]}
    names = {node["name"] for node in workflow["nodes"]}

    assert workflow["name"] == "Agent — Excel Extractor"
    assert workflow["active"] is False
    assert "Call native Excel Extraction Agent" not in names
    assert "Receive Excel specialist packet" not in names
    trigger = by_name["When executed by another workflow"]
    assert trigger["type"] == "n8n-nodes-base.executeWorkflowTrigger"
    assert trigger["typeVersion"] == 1.2
    assert trigger["parameters"]["inputSource"] == "passthrough"
    connections = workflow["connections"]
    assert connections["When executed by another workflow"]["main"][0][0]["node"] == "Prepare native Excel invocation"
    assert connections["Native Excel invocation ready?"]["main"][0][0]["node"] == "Mark Execute Sub-workflow entrypoint"
    assert connections["Native Excel invocation ready?"]["main"][1][0]["node"] == "Build Excel adapter input gate"
    assert connections["Return workflow result"]["main"][0][0]["node"] == "Adapt native Excel result"

    prepare_code = by_name["Prepare native Excel invocation"]["parameters"]["jsCode"]
    assert "const previous=parseObject(incoming.previous_specialist_result)" in prepare_code
    assert "const continuation=isObject(previous.continuation)" in prepare_code
    assert "opaque.execution_ref" in prepare_code
    assert "opaque.clarification_ref" in prepare_code
    assert "nativeJson={session_id:opaque.execution_ref" in prepare_code
    assert "packet.session_id" not in prepare_code
    assert "item.binary?.file" in prepare_code
    assert "if(!packet.contract)" in prepare_code
    assert "native_request_ready:true" in prepare_code

    adapt_code = by_name["Adapt native Excel result"]["parameters"]["jsCode"]
    assert "excel-extraction-continuation-v1" in adapt_code
    assert "execution_ref:sessionRef" in adapt_code
    assert "clarification_ref:clarificationRef" in adapt_code
    assert "data.records.slice(0,5)" in adapt_code
    assert "preview.length<=5" in adapt_code
    assert "source_snapshot_hash:sourceSnapshotHash" in adapt_code
    assert "correlation_id:correlationId" in adapt_code
    assert "excludes ephemeral result/artifact IDs" in adapt_code
    assert ".25*scopeFit" in adapt_code
    assert "EXCEL_REQUESTED_FIELDS_MISSING" in adapt_code
    assert "EXCEL_PROVENANCE_REQUIRED" in adapt_code
    assert "empty_result_policy" in adapt_code
    assert "explicitScheduleConsumer" in adapt_code
    assert "result_kind" in adapt_code
    assert "decision_record:decisionRecord" in adapt_code
    assert "trace_summary" in adapt_code
    assert "return $input.all()" in adapt_code


def test_legacy_excel_mas_workflow_is_removed() -> None:
    assert not (workflow_path("excel-mas-orchestrator.workflow.json")).exists()
    assert not (CORE / "excel-engineering-specialist-adapter.workflow.json").exists()
    assert not (CORE / "mas-activity-list-tasks.workflow.json").exists()
    assert not (CORE / "mas-activity-load-feed.workflow.json").exists()
    hydrate = load_json(workflow_path("mas-activity-hydrate.workflow.json"))
    assert hydrate["name"] == "Activity — Hydrate (Data Tables)"
    webhooks = [n for n in hydrate["nodes"] if n["type"] == "n8n-nodes-base.webhook"]
    assert len(webhooks) == 1
    assert webhooks[0]["parameters"]["path"] == "mas-activity-hydrate"
    assert any(n.get("name") == "Need feed?" for n in hydrate["nodes"])


def test_mas_error_handler_is_n8n_error_workflow_and_execute_subworkflow() -> None:
    handler = load_json(workflow_path("mas-error-handler.workflow.json"))
    orch = load_json(workflow_path("universal-engineering-orchestrator.workflow.json"))
    cas = load_json(workflow_path("cas-persist-task.workflow.json"))
    trace = load_json(workflow_path("mas-trace-event-writer.workflow.json"))
    types = {node["type"] for node in handler["nodes"]}
    assert "n8n-nodes-base.errorTrigger" in types
    assert "n8n-nodes-base.executeWorkflowTrigger" in types
    trigger = next(n for n in handler["nodes"] if n["type"] == "n8n-nodes-base.errorTrigger")
    assert trigger["typeVersion"] == 1
    assert (handler.get("settings") or {}).get("errorWorkflow") in ("", None)
    assert (orch.get("settings") or {}).get("errorWorkflow") == handler["id"]
    assert (cas.get("settings") or {}).get("errorWorkflow") in ("", None)
    assert (trace.get("settings") or {}).get("errorWorkflow") in ("", None)


def test_specialist_template_uses_only_universal_boundary() -> None:
    workflow = load_json(workflow_path("engineering-specialist-template.workflow.json"))
    text = json.dumps(workflow, ensure_ascii=False)
    assert "specialist_packet" in text
    assert "specialist_result" in text
    assert "contract_version" in text
    assert "self_check" in text
    assert "artifact_refs" in text
    assert "independent verification" in text.lower()
    assert "allowedKeys" in text
    assert "packetSize<=262144" in text
    assert "n8n-nodes-base.executeWorkflowTrigger" in {node["type"] for node in workflow["nodes"]}
    parser = next(node for node in workflow["nodes"] if node["name"] == "Specialist Work Output")
    assert parser["parameters"]["schemaType"] == "manual"
    schema = json.loads(parser["parameters"]["inputSchema"])
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "status", "summary", "deliverables", "artifact_refs", "compact_data", "assumptions", "warnings",
        "evidence", "self_check", "human_request", "error", "continuation", "decision_record",
    }


def test_universal_engineering_instruction_templates_are_portable() -> None:
    expected = {
        "engineering-task-instruction.template.json",
        "specialist-result-contract.schema.json",
        "orchestrator-instruction.template.md",
        "specialist-workflow-instruction.template.md",
        "generate_universal_engineering_workflows.py",
        "generate_schedule_workflows.py",
        "generate_activity_hydrate_workflows.py",
        "apply_mas_hybrid_rag.py",
            "hitl_user_copy.py",
            "llm_runtime_options.py",
        "schedule_lossless_runtime.py",
        "schedule_baseline_decoder.py",
        "schedule_baseline_query.py",
        "schedule_pipeline.py",
        "schedule_timeline_runtime.py",
        "schedule_package_materialize.py",
        "schedule_rag_workflows.py",
        "schedule_intake_runtime.py",
        "schedule_task_facts.py",
        "schedule_schema_runtime.py",
        "schedule_semantic_runtime.py",
        "schedule_emit_order.py",
        "mas_handoff_contracts.py",
        "generate_mas_error_handler.py",
        "generate_mas_error_traces.py",
        "generate_mas_health_check.py",
        "generate_mas_orchestrator.py",
        "generate_mas_runtime_config.py",
        "generate_schedule_builder_agent.py",
        "generate_excel_extractor_agent.py",
        "mas_state_utils.py",
        "mas_retrieval_client.py",
        "relayout_core_workflows.py",
    }
    assert {path.name for path in TEMPLATES.iterdir() if path.is_file()} == expected
    contract = load_json(TEMPLATES / "specialist-result-contract.schema.json")
    statuses = set(contract["properties"]["status"]["enum"])
    assert statuses == {
        "succeeded",
        "partial",
        "needs_input",
        "needs_decision",
        "needs_approval",
        "retryable_error",
        "fatal_error",
    }


def test_schedule_generator_and_architecture_decisions_are_portable_and_explicit() -> None:
    generator = (TEMPLATES / "generate_schedule_workflows.py").read_text(encoding="utf-8")
    assert "Path(__file__).resolve().parents[2]" in generator
    assert "/home/" not in generator

    docs = (ROOT / "docs.md").read_text(encoding="utf-8")
    assert "никогда не читает Excel-файлы напрямую" in docs
    assert "returnIntermediateSteps=true" in docs
    assert "attention_threshold = 85" in docs
    assert "hitl_threshold = 70" in docs
    assert "**`CREATE`:** Создание нового файла SCHEDULE" in docs
    assert "Handoff фактов" in docs


def ingestible_operating_guide_documents() -> list[dict]:
    documents = load_json(RAG_SOURCE)["documents"]
    return [
        document
        for document in documents
        if document.get("role") != "injection_template" and document.get("do_not_ingest") is not True
    ]


def test_rag_workflow_contains_the_canonical_documents() -> None:
    source = ingestible_operating_guide_documents()
    workflow = load_json(workflow_path("tnavigator-schedule-knowledge-ingestion.workflow.json"))
    by_name = {node["name"]: node for node in workflow["nodes"]}
    collect = by_name["Collect MAS knowledge blocks"]["parameters"]["jsCode"]
    packaged = json.dumps(by_name["Packaged MAS corpus"], ensure_ascii=False)
    assert source, "Packaged MAS knowledge cards must stay in the operating guide"
    assert "corpus_json" in collect
    assert "skipDoc" in collect
    for document in source:
        document_id = document.get("knowledge_id") or document["id"]
        assert document_id in packaged
        assert json.dumps(document["text"], ensure_ascii=False) in packaged
    assert "Select new MAS knowledge" in by_name
    assert "Lookup existing knowledge keys" in by_name
    assert by_name["Activity knowledge ingest webhook"]["type"] == "n8n-nodes-base.webhook"


def test_ingestion_accepts_the_whole_operating_guide_sheet() -> None:
    workflow = load_json(workflow_path("tnavigator-schedule-knowledge-ingestion.workflow.json"))
    by_name = {node["name"]: node for node in workflow["nodes"]}
    form = by_name["SCHEDULE manual ingestion form"]
    fields = form["parameters"]["formFields"]["values"]
    names = [field["fieldName"] for field in fields]
    assert names[0] == "corpus_json"
    assert all(field.get("requiredField") is not True for field in fields)
    collect = by_name["Collect MAS knowledge blocks"]["parameters"]["jsCode"]
    select = by_name["Select new MAS knowledge"]["parameters"]["jsCode"]
    summarize = by_name["Summarize RAG inventory"]["parameters"]["jsCode"]
    loader = by_name["SCHEDULE Default Data Loader"]
    parent_sql = by_name["PostgreSQL — upsert full parent knowledge"]["parameters"]["query"]
    assert "corpus_json" in collect
    assert "skipDoc" in collect
    assert "docs.filter(d=>!skipDoc(d))" in collect
    assert "d.schedule_knowledge_block||d" not in collect
    assert "wconprod-forecast-injection-example" not in collect
    assert "CORPUS_JSON_INVALID" in collect
    assert "collect_empty" in collect
    assert "canon=" in collect
    assert "dedupe=" in collect
    assert "'schedule_mvp'" in collect
    assert "EXISTING_KNOWLEDGE_LOOKUP_FAILED" in select
    assert "collect_failed" in select
    assert "lookup_failed" in select
    assert "seen.has(k)" in select
    assert "schedule_mvp" in select
    assert "collect_failed" in summarize
    assert "lookup_failed" in summarize
    assert "knowledge_status" in parent_sql and "superseded" in parent_sql
    meta_values = [
        item["value"]
        for item in loader["parameters"]["options"]["metadata"]["metadataValues"]
    ]
    assert meta_values
    assert all(value.startswith("={{ $json.metadata.") and value.endswith(" }}") for value in meta_values)
    assert '"={ $json.metadata.' not in json.dumps(loader)
    example = json.loads(by_name["Receive SCHEDULE knowledge document"]["parameters"]["jsonExample"])
    assert "documents" in example
    payload = load_json(RAG_SOURCE)
    template_id = payload["documents"][-1]["schedule_knowledge_block"]["knowledge_id"]
    assert template_id == "wconprod-forecast-injection-example"


def test_ingestible_sheet_skips_template_and_dedupes_keys() -> None:
    import sys

    sys.path.insert(0, str(TEMPLATES))
    from schedule_rag_workflows import ingestible_blocks_from_payload

    payload = load_json(RAG_SOURCE)
    blocks = ingestible_blocks_from_payload(payload)
    ids = [block["knowledge_id"] for block in blocks]
    assert "wconprod-forecast-injection-example" not in ids
    assert len(ids) == len(set(ids))
    first = {k: blocks[0][k] for k in ("contract", "target_base", "knowledge_id", "revision", "text") if k in blocks[0]}
    duped = ingestible_blocks_from_payload({"documents": [blocks[0], dict(blocks[0])]})
    assert len(duped) == 1
    assert duped[0]["knowledge_id"] == first["knowledge_id"]
    single = ingestible_blocks_from_payload(blocks[0])
    assert len(single) == 1
    empty_template = ingestible_blocks_from_payload({"documents": [payload["documents"][-1]]})
    assert empty_template == []


def test_portable_knowledge_block_reads_wrapper_text_like_seeder() -> None:
    import sys

    sys.path.insert(0, str(TEMPLATES))
    from apply_mas_hybrid_rag import _portable_knowledge_block

    wrapped = {
        "id": "wrapper-id",
        "text": "wrapper-level instruction",
        "schedule_knowledge_block": {
            "contract": "schedule_knowledge_block",
            "knowledge_id": "wrapper-id",
            "target_base": "excel_protocol",
            "knowledge_type": "protocol_instruction",
        },
    }
    block = _portable_knowledge_block(wrapped)
    assert block is not None
    assert block["text"] == "wrapper-level instruction"
    nested = {
        "text": "wrapper-level instruction",
        "schedule_knowledge_block": {
            "contract": "schedule_knowledge_block",
            "knowledge_id": "nested-id",
            "text": "nested-text",
        },
    }
    assert _portable_knowledge_block(nested)["text"] == "nested-text"


def test_operating_guide_ends_with_full_injection_template() -> None:
    payload = load_json(RAG_SOURCE)
    documents = payload["documents"]
    assert documents, "operating guide must contain documents"
    template = documents[-1]
    assert template.get("role") == "injection_template"
    assert template.get("do_not_ingest") is True
    block = template["schedule_knowledge_block"]
    required = (
        "contract",
        "contract_version",
        "target_base",
        "knowledge_type",
        "knowledge_id",
        "revision",
        "title",
        "keywords",
        "topics",
        "task_patterns",
        "simulator_family",
        "status",
        "author",
        "access_scope",
        "text",
        "examples",
        "schema_catalogue",
        "source_hash",
        "page",
        "heading",
        "metadata",
    )
    for key in required:
        assert key in block, key
        assert block[key] not in (None, "", []), key
    assert block["contract"] == "schedule_knowledge_block"
    assert block["target_base"] == "schedule_mvp"
    assert block["knowledge_type"] == "keyword_instruction"
    catalogue = block["schema_catalogue"]
    assert catalogue["contract"] == "schedule_schema_catalogue"
    assert catalogue["schemas"]
    packaged_ids = {document.get("knowledge_id") or document["id"] for document in documents[:-1]}
    required_ids = {
        "excel-agent-trust-boundary",
        "excel-agent-discovery-and-tables",
        "excel-agent-query-and-result-protocol",
        "excel-agent-clarification-and-continuation",
        "excel-agent-rag-and-operations",
        "route-excel-extractor",
        "route-schedule-builder",
        "route-calculation",
        "route-hitl-required-evidence",
        "specialist-template-bounded-work",
    }
    assert required_ids <= packaged_ids
    assert documents[-1].get("id") == "_injection-template"
    assert documents[-1].get("do_not_ingest") is True


def test_rag_ingestion_has_ui_only_postgres_inventory_check() -> None:
    workflow = load_json(workflow_path("tnavigator-schedule-knowledge-ingestion.workflow.json"))
    by_name = {node["name"]: node for node in workflow["nodes"]}
    assert "Prepare RAG inventory query" in by_name
    assert "Postgres — inspect RAG table contents" in by_name
    assert "Summarize RAG inventory" in by_name
    inspect = by_name["Postgres — inspect RAG table contents"]
    assert inspect["type"] == "n8n-nodes-base.postgres"
    assert inspect["parameters"]["operation"] == "executeQuery"
    assert inspect["parameters"]["query"] == "={{ $json.query }}"
    prepare = by_name["Prepare RAG inventory query"]["parameters"]["jsCode"]
    summarize = by_name["Summarize RAG inventory"]["parameters"]["jsCode"]
    assert "rag_table_name" in prepare
    assert "LEFT JOIN inv ON TRUE" in prepare
    assert "to_regclass('" in prepare
    assert "rag_inventory_ok" in summarize
    assert "duplicate_ingest_suspected" in summarize
    assert "skipped_existing" in summarize
    assert by_name["Postgres — inspect RAG table contents"].get("alwaysOutputData") is True
    assert "$('Normalize approved SCHEDULE knowledge').all()" in by_name["Prepare full parent knowledge persistence"]["parameters"]["jsCode"]
    assert by_name["Finalize indexes and deduplicate chunks"].get("executeOnce") is True
    assert by_name["Lookup existing knowledge keys"]["parameters"]["query"].startswith("SELECT target_base, knowledge_id, revision")
    connections = workflow["connections"]
    assert connections["PGVector — insert approved SCHEDULE knowledge"]["main"][0][0]["node"] == "Finalize indexes and deduplicate chunks"
    assert connections["Finalize indexes and deduplicate chunks"]["main"][0][0]["node"] == "Prepare full parent knowledge persistence"
    assert connections["Prepare full parent knowledge persistence"]["main"][0][0]["node"] == "PostgreSQL — upsert full parent knowledge"
    assert connections["PostgreSQL — upsert full parent knowledge"]["main"][0][0]["node"] == "Prepare approved schema catalogue persistence"
    assert connections["New knowledge to insert?"]["main"][1][0]["node"] == "Prepare RAG inventory query"
    assert connections["Prepare RAG inventory query"]["main"][0][0]["node"] == "Postgres — inspect RAG table contents"
    assert connections["Postgres — inspect RAG table contents"]["main"][0][0]["node"] == "Summarize RAG inventory"


def test_continuation_protocol_has_no_stale_agent_state_lookup_instruction() -> None:
    stale_phrases = (
        "First inspect get_session_state",
        "On a continuation call first",
        "On a continuation, call get_session_state first",
    )
    paths = [
        workflow_path("excel-extraction-agent.workflow.json"),
        workflow_path("tnavigator-schedule-knowledge-ingestion.workflow.json"),
        RAG_SOURCE,
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for phrase in stale_phrases:
            assert phrase not in text, (path.name, phrase)


def test_schedule_foundation_workflows_implement_roadmap_boundaries() -> None:
    expected_contracts = {
        "tnavigator-schedule-builder.workflow.json": "specialist_result/v1",
        "tnavigator-schedule-knowledge-ingestion.workflow.json": "schedule_knowledge_ingest/v1",
        "tnavigator-schedule-hybrid-retrieval.workflow.json": "schedule_retrieval/v1",
        "mas-trace-event-writer.workflow.json": "mas_trace_event/v1",
    }
    assert SCHEDULE_FOUNDATION_WORKFLOWS == set(expected_contracts)
    for filename, contract in expected_contracts.items():
        workflow = load_json(workflow_path(filename))
        assert workflow["active"] is False
        assert workflow["meta"]["targetN8nVersion"] == "2.30.8"
        assert workflow["meta"]["contractVersion"] == contract
        runtime = [n for n in workflow["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"]
        runtime_types = {n["type"] for n in runtime}
        if filename == "tnavigator-schedule-knowledge-ingestion.workflow.json":
            assert "n8n-nodes-base.webhook" in runtime_types
            assert "n8n-nodes-base.executeWorkflowTrigger" in runtime_types
        else:
            assert runtime[0]["type"] == "n8n-nodes-base.executeWorkflowTrigger"
        runtime_blob = json.dumps(
            [n for n in workflow["nodes"] if n.get("type") != "n8n-nodes-base.stickyNote"],
            ensure_ascii=False,
        ).lower()
        assert "$env" not in runtime_blob and "$vars" not in runtime_blob
        assert "readwritefile" not in runtime_blob and "executecommand" not in runtime_blob

    builder = load_json(workflow_path("tnavigator-schedule-builder.workflow.json"))
    builder_nodes = {node["name"] for node in builder["nodes"]}
    for required in (
        "Run deterministic SCHEDULE intake",
        "Analyze lossless baseline inventory",
        "Decode typed baseline records",
        "Query baseline planning context",
        "Query targeted baseline records",
        "Validate SCHEDULE pipeline plan",
        "Render typed SCHEDULE IR deterministically",
        "Merge SCHEDULE draft deterministically",
        "Validate merged SCHEDULE package",
        "Run independent SCHEDULE verifier",
        "Build release-ready specialist result",
    ):
        assert required in builder_nodes


def _builder_code(name: str) -> str:
    builder = load_json(workflow_path("tnavigator-schedule-builder.workflow.json"))
    return next(node for node in builder["nodes"] if node["name"] == name)["parameters"]["jsCode"]


def test_schedule_foundation_is_fail_closed_and_preserve_by_default() -> None:
    intake = _builder_code("Run deterministic SCHEDULE intake")
    baseline = _builder_code("Analyze lossless baseline inventory")
    decoder = _builder_code("Decode typed baseline records")
    baseline_query = _builder_code("Query baseline planning context")
    merge = _builder_code("Merge SCHEDULE draft deterministically")
    renderer = _builder_code("Render typed SCHEDULE IR deterministically")
    validator = _builder_code("Validate merged SCHEDULE package")
    orchestrator = (workflow_path("universal-engineering-orchestrator.workflow.json")).read_text(encoding="utf-8")
    assert "SCHEDULE_BUILD_CONTRACT_INVALID" in intake
    assert "METRIC_UNIT_SYSTEM_REQUIRED" in intake
    assert "FORECAST_START_DATE_REQUIRED" in intake
    assert "CREATE_BASELINE_CONFLICT_REQUIRES_DECISION" in intake
    assert "EXPERT_KNOWLEDGE_AND_CATALOGUE_BINDING_REQUIRED" in intake
    assert "KEYWORD_INSTRUCTION_SCOPE_INCOMPLETE" in intake
    assert "STAGE_GATE_POLICY_INVALID" in intake
    assert "BASELINE_REQUIRED" in intake
    assert "preservation_token" in baseline and "INCLUDE_CYCLE" in baseline
    assert "BASELINE_RECORD_VARIANT_AMBIGUOUS" in decoder
    assert "INCLUDE_MULTIPLE_EXPANSION" in decoder
    assert "OPAQUE_BASELINE_SEMANTICS_UNAVAILABLE" in decoder
    assert "change_effective_from" in decoder and "prefix_ir_events" in decoder
    assert "BASELINE_QUERY_REFINEMENT_REQUIRED" in baseline_query
    assert "expected_raw_hash" in baseline_query and "query_hash" in baseline_query
    assert "zero_change_byte_identical" in merge
    assert "REMOVE_REQUIRES_ACCOUNTABLE_APPROVAL" in merge
    assert "SCHEMA_EXPERT_AUTHOR_REQUIRED" in renderer
    assert "IR_REQUIRED_FIELD_MISSING" in renderer
    assert "CREATE_REQUIRES_ADD_ONLY" in renderer
    assert "schedule_schema_catalogue" in renderer
    assert "SCHEMA_SEMANTICS_REQUIRED" in renderer
    assert "SCHEMA_CATALOGUE_NOT_APPROVED" in validator
    assert "KEYWORD_SCHEMA_NOT_APPROVED" in validator
    assert "SEMANTIC_BASELINE_SNAPSHOT_REQUIRED" in validator
    assert "SEMANTIC_PRE_CHANGE_BOUNDARY_REQUIRED" in validator
    assert "SEMANTIC_EVENT_NOT_AFTER_BOUNDARY" in validator
    assert "SEMANTIC_EVENT_BEFORE_CHANGE_BOUNDARY" in validator
    assert "BASELINE_DECODER_PREFIX_MISMATCH" in validator
    assert "PRE_CHANGE_BOUNDARY" in validator
    assert "ENTITY_REFERENCE_MISSING" in validator
    assert "KEYWORD_PREREQUISITE_MISSING" in validator
    assert "HIERARCHY_CYCLE" in validator
    assert "CONFLICTING_STATE_ASSIGNMENT" in validator
    assert "SEMANTIC_LIFECYCLE_RULE_INVALID" in validator
    assert "WILDCARD_EXPANSION_REQUIRED" in validator
    assert "INTERVAL_OVERLAP" in validator
    assert "NUMERIC_VALUE_BELOW_MIN" in validator
    assert "interval_assignments" in validator
    assert "HISTORY_EVENT_AFTER_CUTOVER" in validator
    assert "FORECAST_EVENT_BEFORE_START" in validator
    assert "schedule_semantic_snapshot" in validator
    assert "Apply action and version guard" in orchestrator
    assert "schedule_release_result" in orchestrator
    assert "SCHEDULE release is blocked" in orchestrator
    assert "gate_id does not match" in orchestrator
    assert "accountable requested_by identity is required" in orchestrator


def test_schedule_builder_uses_the_same_governed_intake_runtime() -> None:
    builder = load_json(workflow_path("tnavigator-schedule-builder.workflow.json"))
    builder_code = next(node for node in builder["nodes"] if node["name"] == "Run deterministic SCHEDULE intake")["parameters"]["jsCode"]
    assert "schedule_intake_result" in builder_code or "contract:'schedule_intake" in builder_code or "SCHEDULE_BUILD_CONTRACT_INVALID" in builder_code
    prepare = next(node for node in builder["nodes"] if node["name"] == "Prepare deterministic intake")["parameters"]["jsCode"]
    for required in (
        "contract:'schedule_build_request'", "orchestrator_task_id:root.task_id",
        "policy_version", "idempotency_key", "expected_version",
    ):
        assert required in prepare
    gate = next(node for node in builder["nodes"] if node["name"] == "Build SCHEDULE pipeline gate result")["parameters"]["jsCode"]
    assert "suppliedQuestions" in gate


def test_schedule_rag_and_trace_foundations_enforce_governance() -> None:
    ingestion = (workflow_path("tnavigator-schedule-knowledge-ingestion.workflow.json")).read_text()
    retrieval = (workflow_path("tnavigator-schedule-hybrid-retrieval.workflow.json")).read_text()
    trace = load_json(workflow_path("mas-trace-event-writer.workflow.json"))
    assert "TARGET_BASE_NOT_ALLOWLISTED" in ingestion
    assert "FULL_KEYWORD_INSTRUCTION_REQUIRED" in ingestion
    assert "EXPERT_AUTHOR_REQUIRED" in ingestion
    assert "PGVector — insert approved SCHEDULE knowledge" in ingestion
    assert "documentDefaultDataLoader" in ingestion
    assert "textSplitterRecursiveCharacterTextSplitter" in ingestion
    assert "Finalize indexes and deduplicate chunks" in ingestion
    assert "schema_catalogue_json" in ingestion
    assert "tnavigator_schedule_schema_catalogue_v1" in ingestion
    assert "SCHEMA_CATALOGUE_CONTRACT_INVALID" in ingestion
    assert "SCHEMA_SEMANTICS_REQUIRED" in ingestion
    assert "tnavigator_schedule_knowledge_documents_v1" in ingestion
    assert "keyword_instruction" in ingestion and "worked_example" in ingestion
    assert "protocol_instruction" in ingestion and "routing_card" in ingestion
    assert "capability_instruction" in ingestion
    assert "excel_protocol" in ingestion and "orchestrator_routing" in ingestion
    assert "specialist_template" in ingestion
    assert load_json(workflow_path("tnavigator-schedule-knowledge-ingestion.workflow.json"))["name"] == (
        "MAS — Knowledge Ingestion"
    )
    assert load_json(workflow_path("tnavigator-schedule-hybrid-retrieval.workflow.json"))["name"] == (
        "MAS — Knowledge Retrieval"
    )
    assert "TARGET_BASE_NOT_ALLOWLISTED" in retrieval
    assert "ACCESS_SCOPE_REQUIRED" in retrieval
    assert "mas_retrieval_request" in retrieval
    assert "$3::jsonb<>'[]'::jsonb" in retrieval
    assert "require_schema!==true" in retrieval
    assert "PostgreSQL lexical + exact candidates" in retrieval
    assert "PGVector semantic candidates" in retrieval
    assert "PostgreSQL tag candidates" in retrieval
    assert "algorithm:'rrf'" in retrieval
    assert "NO_AUTHORIZED_EVIDENCE" in retrieval
    assert "KEYWORD_INSTRUCTION_COVERAGE_INCOMPLETE" in retrieval
    assert "full_parent_hydration:true" in retrieval
    assert "PostgreSQL approved schema catalogue" in retrieval
    assert "EXPERT_SCHEMA_CATALOGUE_NOT_FOUND" in retrieval
    assert "schema_catalogue:selected" in retrieval
    assert any(n["type"] == "n8n-nodes-base.dataTable" for n in trace["nodes"])
    trace_text = json.dumps(trace, ensure_ascii=False)
    assert "raw_prompt:false" in trace_text
    assert "secret:false" in trace_text
    assert "binary:false" in trace_text
    normalize_trace = next(n for n in trace["nodes"] if n["name"] == "Normalize MAS trace event")
    trace_code = normalize_trace["parameters"]["jsCode"]
    assert "decision_record" in trace_code
    assert "rawDecision.contract==='decision_record'" in trace_code
    assert "prompt|secret|token|password|authorization|binary|content|text" in trace_code
    assert "decision_record:e.decision_record" in trace_code
    assert "root.mas_trace_events" in trace_code
    assert "root.mas_trace_events.length" in trace_code
    assert "source.map((candidate,index)" in trace_code
    assert any(n["name"] == "Prepare MAS activity sync" for n in trace["nodes"])
    assert any(n["name"] == "POST handoffs to MAS Activity" for n in trace["nodes"])
    assert "activity_sync_ready" in trace_text
    assert "/v1/sync" in trace_text
    assert "event_type==='handoff'" in trace_text
    health = load_json(workflow_path("mas-deployment-health-check.workflow.json"))
    health_names = {node["name"] for node in health["nodes"]}
    assert "Prepare Trace Writer probe" not in health_names
    assert "Call Trace Writer probe" not in health_names
    assert "Probe Math /health" in health_names
    execute_targets = {
        node["parameters"]["workflowId"]["value"]
        for node in health["nodes"]
        if node["type"] == "n8n-nodes-base.executeWorkflow"
    }
    assert execute_targets == {"REPLACE_MAS_RUNTIME_CONFIG_IN_UI"}


def test_hybrid_rag_is_the_only_agent_knowledge_path() -> None:
    excel = load_json(workflow_path("excel-extraction-agent.workflow.json"))
    excel_nodes = {node["name"]: node for node in excel["nodes"]}
    excel_text = json.dumps(excel, ensure_ascii=False)
    assert "context_search" not in excel_text
    assert not any("vectorStore" in node["type"] for node in excel["nodes"])
    assert excel_nodes["Call Excel protocol Hybrid Retrieval"]["parameters"]["workflowId"]["value"] == (
        "REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI"
    )
    assert "excel_protocol" in excel_nodes["Prepare excel protocol RAG request"]["parameters"]["jsCode"]
    assert "rag_evidence" in excel_nodes["Attach excel protocol RAG evidence"]["parameters"]["jsCode"]
    assert "EXCEL_PROTOCOL_RAG_REQUIRED" in excel_nodes["Build excel protocol RAG gate"]["parameters"]["jsCode"]
    assert "rag_evidence" in excel_nodes["Prepare AI Agent input"]["parameters"]["jsCode"]
    connections = excel["connections"]
    assert connections["Prepare AI Agent input"]["main"][0][0]["node"] == "Prepare excel protocol RAG request"
    assert connections["Excel protocol RAG ready?"]["main"][0][0]["node"] == "Is clarification continuation before preflight?"
    assert connections["Excel protocol RAG ready?"]["main"][1][0]["node"] == "Build excel protocol RAG gate"

    template = load_json(workflow_path("engineering-specialist-template.workflow.json"))
    template_nodes = {node["name"]: node for node in template["nodes"]}
    assert template_nodes["Call specialist Hybrid Retrieval"]["parameters"]["workflowId"]["value"] == (
        "REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI"
    )
    assert "specialist_template" in template_nodes["Prepare governed specialist RAG request"]["parameters"]["jsCode"]
    assert "rag_evidence" in template_nodes["Attach governed specialist RAG evidence"]["parameters"]["jsCode"]
    assert "SPECIALIST_RAG_EVIDENCE_REQUIRED" in template_nodes["Build specialist RAG evidence gate"]["parameters"]["jsCode"]
    template_connections = template["connections"]
    assert template_connections["Packet contract valid?"]["main"][0][0]["node"] == "Prepare governed specialist RAG request"
    assert template_connections["Specialist RAG evidence ready?"]["main"][0][0]["node"] == "Prepare specialist work"
    assert template_connections["Specialist RAG evidence ready?"]["main"][1][0]["node"] == "Build specialist RAG evidence gate"

    excel_agent = load_json(workflow_path("excel-extraction-agent.workflow.json"))
    adapter_code = next(
        node["parameters"]["jsCode"]
        for node in excel_agent["nodes"]
        if node["name"] == "Prepare native Excel invocation"
    )
    assert "...packet.inputs" in adapter_code

    ingestion = load_json(workflow_path("tnavigator-schedule-knowledge-ingestion.workflow.json"))
    seed_code = next(
        node["parameters"]["jsCode"]
        for node in ingestion["nodes"]
        if node["name"] == "Collect MAS knowledge blocks"
    )
    inventory = next(
        node["parameters"]["jsCode"]
        for node in ingestion["nodes"]
        if node["name"] == "Prepare RAG inventory query"
    )
    assert "tnavigator_schedule_knowledge_v1" in json.dumps(ingestion, ensure_ascii=False)
    assert "corpus_json" in seed_code
    packaged = json.dumps(next(n for n in ingestion["nodes"] if n["name"] == "Packaged MAS corpus"), ensure_ascii=False)
    for document in ingestible_operating_guide_documents():
        block = document.get("schedule_knowledge_block") if isinstance(document.get("schedule_knowledge_block"), dict) else document
        assert block["knowledge_id"] in packaged
        assert block["knowledge_id"] in inventory
        assert json.dumps(block["text"], ensure_ascii=False) in packaged


def test_excel_agent_tools_remain_the_only_workbook_interface() -> None:
    workflow = load_json(workflow_path("excel-extraction-agent.workflow.json"))
    agent = next(node for node in workflow["nodes"] if node["name"] == "Excel Extractor AI Agent")
    assert "rag_evidence (target_base=excel_protocol)" in agent["parameters"]["options"]["systemMessage"]
    assert "PGVector static operating context" not in workflow.get("description", "")


def test_excel_protocol_cards_use_schedule_aligned_skeleton_and_retrieval_surface() -> None:
    required_sections = ("Назначение.", "Когда применять.", "Канон протокола.", "Валидация")
    must_keep = {
        "excel-agent-trust-boundary": ("session_id", "tbl_", "недоверенн"),
        "excel-agent-discovery-and-tables": (
            "ambiguous_columns",
            "suggested_select",
            "MAX_INTERNAL_BLANK_ROWS",
            "Index",
            "n/a",
        ),
        "excel-agent-query-and-result-protocol": (
            "tail=true",
            "save_agent_plan",
            "validate_result",
            "bounded query repair",
        ),
        "excel-agent-clarification-and-continuation": (
            "clarification_needed",
            "continuation_state",
            "get_session_state",
        ),
        "excel-agent-rag-and-operations": ("X-API-Key", "X-Excel-Webhook-Key", "embedding"),
    }
    cards = {
        document.get("knowledge_id") or document.get("id"): document
        for document in ingestible_operating_guide_documents()
        if (document.get("target_base") or (document.get("schedule_knowledge_block") or {}).get("target_base"))
        == "excel_protocol"
        or str(document.get("knowledge_id") or document.get("id") or "").startswith("excel-agent-")
    }
    assert set(must_keep) <= set(cards)
    for kid, needles in must_keep.items():
        block = cards[kid].get("schedule_knowledge_block") if isinstance(cards[kid].get("schedule_knowledge_block"), dict) else cards[kid]
        expected_revision = {
            "excel-agent-trust-boundary": "4",
            "excel-agent-discovery-and-tables": "5",
            "excel-agent-query-and-result-protocol": "4",
            "excel-agent-clarification-and-continuation": "4",
            "excel-agent-rag-and-operations": "4",
        }
        assert str(block.get("revision")) == expected_revision[kid], kid
        assert not str(block.get("title") or "").lower().startswith("excel agent ")
        assert len(block.get("task_patterns") or []) >= 4, kid
        assert len(block.get("examples") or []) >= 2, kid
        text = str(block.get("text") or "")
        for section in required_sections:
            assert section in text, (kid, section)
        blob = text + json.dumps(block.get("examples") or [], ensure_ascii=False)
        for needle in needles:
            assert needle in blob, (kid, needle)


def test_excel_protocol_retrieval_boost_baseline_fixture_is_frozen() -> None:
    baseline_path = ROOT / "n8n" / "tests" / "fixtures" / "excel-protocol-searchable-baseline.json"
    baseline = load_json(baseline_path)
    assert set(baseline) == {
        "excel-agent-trust-boundary",
        "excel-agent-discovery-and-tables",
        "excel-agent-query-and-result-protocol",
        "excel-agent-clarification-and-continuation",
        "excel-agent-rag-and-operations",
    }
    for kid, row in baseline.items():
        assert row["task_patterns"] == []
        assert (row.get("examples") or []) == []
        assert str(row["revision"]) in {"1", "3"}
        assert "searchable" in row and len(row["searchable"]) > 500
