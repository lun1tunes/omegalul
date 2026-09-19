from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / "n8n" / "workflows"
CORE = WORKFLOWS / "core"
SUPPORT = WORKFLOWS / "support"
TEMPLATES = ROOT / "n8n" / "templates"
RAG_SOURCE = ROOT / "n8n" / "rag" / "excel-agent-operating-guide.documents.json"
IMPORT_MANIFEST = ROOT / "n8n" / "import-manifest.json"
LIVE_WORKFLOWS = {
    "tnavigator-schedule-knowledge-ingestion.workflow.json",
    "tnavigator-schedule-hybrid-retrieval.workflow.json",
    "mas-runtime-config.workflow.json",
    "schedule-builder-agent.workflow.json",
    "excel-extractor-agent.workflow.json",
    "tnav-cluster-agent.workflow.json",
    "mas-error-traces.workflow.json",
    "mas-control-plane-proxy.workflow.json",
    "mas-orchestrator.workflow.json",
    "mas-deployment-health-check.workflow.json",
    "demo-agent.workflow.json",
}


def workflow_files() -> list[Path]:
    return sorted(WORKFLOWS.glob("**/*.workflow.json"))


def workflow_path(name: str) -> Path:
    for folder in (CORE, SUPPORT, WORKFLOWS):
        candidate = folder / name
        if candidate.is_file():
            return candidate
    return CORE / name


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
    "n8n-nodes-base.httpRequest": {4.2, 4.4},
    # HTTP Request "as tool": the only HTTP tool AI Agent v3 can execute in 2.30.8
    # (toolHttpRequest is hidden there and has no `execute` method).
    "n8n-nodes-base.httpRequestTool": {4.4},
    "n8n-nodes-base.if": {2.2, 2.3},
    "n8n-nodes-base.manualTrigger": {1},
    "n8n-nodes-base.merge": {3.2},
    "n8n-nodes-base.postgres": {2.6},
    "n8n-nodes-base.respondToWebhook": {1.4},
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
    assert imported == {path.name for path in workflow_files()} == LIVE_WORKFLOWS
    assert len(imported) == 11  # 10 core (incl. tNav Cluster Agent) + demo-agent
    assert {path.name for path in CORE.glob("*.workflow.json")} == {
        Path(value).name for value in manifest["runtime_import_order"]
    }
    assert {path.name for path in SUPPORT.glob("*.workflow.json")} == {
        Path(item["workflow"]).name for item in manifest["optional_or_non_runtime"]
    }
    assert "retired_not_imported" not in manifest
    assert "retired_execute_workflow_bindings" not in manifest
    assert "data_tables" not in manifest
    assert not list(WORKFLOWS.glob("*.workflow.json"))

    workflows_by_name = {
        workflow["name"]: workflow
        for path in workflow_files()
        for workflow in [load_json(path)]
    }
    workflow_names = set(workflows_by_name)
    bindings = manifest["mandatory_execute_workflow_bindings"]
    future_bindings = manifest["future_enterprise_or_optional_bindings"]
    assert [binding["node"] for binding in bindings] == [
        "Runtime endpoints",
        "Runtime configuration",
        "Runtime configuration",
        "Runtime configuration",
        "Call Knowledge Retrieval",
        "Call Knowledge Retrieval",
        "Call Knowledge Retrieval",
        "Call Knowledge Retrieval",
        "Retrieve knowledge",
        "Retrieve knowledge",
        "Retrieve knowledge",
    ]
    assert [binding["owner"] for binding in bindings] == [
        "Orchestrator — MAS",
        "Agent — Excel Extractor",
        "Agent — Schedule Builder",
        "Agent — tNav Cluster Agent",
        "Orchestrator — MAS",
        "Agent — Excel Extractor",
        "Agent — Schedule Builder",
        "Agent — tNav Cluster Agent",
        "Agent — Excel Extractor",
        "Agent — Schedule Builder",
        "Agent — tNav Cluster Agent",
    ]
    assert any("agent_workflow_ids" in line for line in manifest["ui_configuration"])
    assert future_bindings == []
    assert manifest["health_check"]["ui_name"] == "Form — MAS Deployment Health Check"
    assert (ROOT / "docs.md").is_file()
    header_auth = next(item for item in manifest["required_credentials"] if item["type"] == "httpHeaderAuth")
    assert "Authorization" in header_auth["use"]
    assert "X-API-Key" not in header_auth["use"]
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
    assert any("expert-authored" in blocker for blocker in manifest["mvp_external_blockers"])


def test_workflows_use_current_n8n_2_30_8_ai_node_versions() -> None:
    for path in workflow_files():
        workflow = load_json(path)
        for node in workflow["nodes"]:
            if node["type"] == "@n8n/n8n-nodes-langchain.agent":
                assert node["typeVersion"] == 3.1, (path.name, node["name"])
                assert node["parameters"].get("hasOutputParser") is False
            elif node["type"] == "@n8n/n8n-nodes-langchain.chainLlm":
                assert node["typeVersion"] == 1.9, (path.name, node["name"])
                if path.name == "mas-orchestrator.workflow.json":
                    assert node["parameters"].get("hasOutputParser") is True
            elif node["type"] == "@n8n/n8n-nodes-langchain.lmChatOpenAi":
                assert node["typeVersion"] == 1.3, (path.name, node["name"])
                options = node["parameters"].get("options") or {}
                live_chat = path.name in {
                    "mas-orchestrator.workflow.json",
                }
                expect_timeout = 600000 if live_chat else 300000
                assert options.get("timeout") == expect_timeout, (path.name, node["name"], options)
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
    for filename in LIVE_WORKFLOWS:
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
    for filename in LIVE_WORKFLOWS:
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

def test_tool_instructions_use_structured_arguments_not_legacy_input_wrapper() -> None:
    paths = [
        workflow_path("excel-extractor-agent.workflow.json"),
        workflow_path("tnavigator-schedule-knowledge-ingestion.workflow.json"),
        RAG_SOURCE,
    ]
    stale = ('{"input":', "one input string", "exactly one input JSON string", "Use [] for arrays")
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for phrase in stale:
            assert phrase not in text, (path.name, phrase)

def test_hitl_and_health_forms_are_hand_authored_not_generator_emitted() -> None:
    """Health Check is generated; schedule generator must not emit extra forms."""
    import re

    schedule_source = (TEMPLATES / "generate_schedule_workflows.py").read_text(encoding="utf-8")
    schedule_emitted = set(re.findall(r'["\']([^"\']+\.workflow\.json)["\']', schedule_source))
    assert schedule_emitted <= {
        "tnavigator-schedule-knowledge-ingestion.workflow.json",
        "tnavigator-schedule-hybrid-retrieval.workflow.json",
    }
    health = "workflows/core/mas-deployment-health-check.workflow.json"
    assert health in load_json(IMPORT_MANIFEST)["full_clean_import_set"]
    assert health in load_json(IMPORT_MANIFEST)["runtime_import_order"]
    assert not (ROOT / "n8n" / "workflows" / "retired").exists()


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
    assert {path.name for path in paths} == LIVE_WORKFLOWS
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


def test_schedule_builder_prompt_retrieves_before_apply_dataset() -> None:
    """8.4 / S1: Builder asks the KB before apply_dataset; commissioning stays retrieve-free."""
    text = (TEMPLATES / "agents" / "schedule_builder.py").read_text(encoding="utf-8")
    assert "Перед apply_dataset всегда retrieve_knowledge" in text
    assert "apply_commissioning для дат ввода — без retrieve" in text
    assert "retrieve_knowledge не пропускай" in text
    assert "knowledge_required" in text


def test_agent_retrieval_filters_js_does_not_regex_the_task_text() -> None:
    """R13: Prepare filters come from inspect / expected_output, not /дат|ввод/ on the goal (8.3)."""
    for name in ("excel_extractor.py", "schedule_builder.py"):
        text = (TEMPLATES / "agents" / name).read_text(encoding="utf-8")
        start = text.index("retrieval_filters_js=")
        end = text.index("planner_extra_js=", start)
        chunk = text[start:end]
        assert ".test(low)" not in chunk, name
        assert "blob.match" not in chunk, name
        assert "/дат" not in chunk, name
        assert "inspect.tables" in chunk or "keywords_present" in chunk, name

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
        "tnav_cluster_url",
        "demo_agent_url",
        "orchestrator_step_url",
            "max_steps",
            "chat_model",
            "chat_base_url",
            "chat_extra_params",
            "agent_workflow_ids",
            "mas_version",
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
        assert node["parameters"].get("authentication") != "genericCredentialType"
        assert "httpHeaderAuth" not in (node.get("credentials") or {})

def test_orchestrator_routing_cards_plan_follows_the_goal_not_output_provides() -> None:
    """CASE-6aa052eb: Luna copied Excel output_provides (facts+new_wells) into expected_output on a dates-only goal."""
    cards = {
        document.get("knowledge_id"): document
        for document in ingestible_operating_guide_documents()
        if str(document.get("knowledge_id") or "").startswith("route-")
    }
    excel = cards["route-excel-extractor"]
    thin = cards["route-mas-thin-orchestrator"]
    builder = cards["route-schedule-builder"]
    cluster = cards["route-cluster-calculation"]
    assert excel["revision"] == "9"
    assert thin["revision"] == "7"
    assert builder["revision"] == "12"
    assert cluster["revision"] == "6"
    for card in (excel, thin, builder, cluster):
        assert "baseline" not in card["text"]
        for agent_id in ("excel_extractor", "schedule_builder", "calculation_agent", "tnav_cluster"):
            assert agent_id not in card["text"]
    assert "не заказывай параметры новых скважин «на всякий случай»" in excel["text"]
    assert "не копируй все ключи output_provides" in excel["text"]
    assert "не каталога агента" in thin["text"]
    assert "не ожидая ключей output_provides, которых цель не требовала" in thin["text"]
    assert "для сдвига дат — факты ввода" in builder["text"]
    assert "именованный набор" in builder["text"]
    assert "гидродинамическом кластере" in builder["text"]
    assert cluster["topics"] == ["model_version", "run_status", "run_results"]
    assert "absent_agent" not in (cluster.get("topics") or [])
    assert "приложенный готовый файл расписания" in cluster["text"].lower()

def test_legacy_excel_mas_workflow_is_removed() -> None:
    gone = (
        "excel-mas-orchestrator.workflow.json",
        "excel-engineering-specialist-adapter.workflow.json",
        "mas-activity-list-tasks.workflow.json",
        "mas-activity-load-feed.workflow.json",
        "mas-activity-hydrate.workflow.json",
        "excel-extraction-agent.workflow.json",
        "tnavigator-schedule-builder.workflow.json",
        "universal-engineering-orchestrator.workflow.json",
    )
    for name in gone:
        assert not workflow_path(name).is_file(), name
    assert not (ROOT / "n8n" / "workflows" / "retired").exists()
    assert not (ROOT / "n8n" / "templates" / "retired").exists()


def test_universal_engineering_instruction_templates_are_portable() -> None:
    expected = {
        "generate_schedule_workflows.py",
        "llm_runtime_options.py",
        "schedule_lossless_runtime.py",
        "schedule_timeline_runtime.py",
        "schedule_rag_workflows.py",
        "schedule_schema_runtime.py",
        "schedule_emit_order.py",
        "generate_mas_error_traces.py",
        "generate_mas_health_check.py",
        "generate_mas_orchestrator.py",
        "generate_mas_runtime_config.py",
        "generate_mas_control_plane_proxy.py",
        "mas_agent_registry.py",
        "mas_agent_spec.py",
        "mas_agent_workflow.py",
        "generate_schedule_builder_agent.py",
        "generate_excel_extractor_agent.py",
        "generate_demo_agent.py",
        "generate_tnav_cluster_agent.py",
        "mas_state_utils.py",
        "mas_retrieval_client.py",
        "mas_knowledge_spaces.py",
        "mas_tool_nodes.py",
        "relayout_core_workflows.py",
    }
    assert {path.name for path in TEMPLATES.iterdir() if path.is_file()} == expected
    assert not (TEMPLATES / "retired").exists()


def test_schedule_generator_and_architecture_decisions_are_portable_and_explicit() -> None:
    generator = (TEMPLATES / "generate_schedule_workflows.py").read_text(encoding="utf-8")
    assert "Path(__file__).resolve().parents[2]" in generator
    assert "/home/" not in generator

    # docs.md is the field engineer's runbook (deploy + use), not the coding-agent contract.
    docs = (ROOT / "docs.md").read_text(encoding="utf-8")
    assert "Как это устроено" in docs
    assert "flowchart" in docs
    assert "MAS — Runtime Config" in docs
    assert "chat_base_url" in docs
    assert "chat_extra_params" in docs
    assert "tool-call-parser" in docs
    assert "IMPORT_ORDER.txt" in docs
    assert "mas-deployment-health-check" in docs
    assert "Agent — Excel Extractor" in docs
    assert "Agent — Schedule Builder" in docs
    assert "0.0.0.0" in docs
    assert "## 6. Интеграция нового агента" not in docs
    assert "### 3.2. Allowlist keywords" not in docs

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
        "summary",
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
        "worked-commissioning-dates-v1",
        "worked-group-rebind-v1",
        "worked-dataset-wefac-v1",
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
    lookup_sql = by_name["Lookup existing knowledge keys"]["parameters"]["query"]
    assert "in_vector" in lookup_sql
    assert "stored_text" in lookup_sql
    assert "tnavigator_schedule_knowledge_v2" in lookup_sql
    ensure_sql = by_name["Ensure parent knowledge tables"]["parameters"]["query"]
    assert "CREATE TABLE IF NOT EXISTS tnavigator_schedule_knowledge_v2" in ensure_sql
    connections = workflow["connections"]
    assert connections["PGVector — insert approved SCHEDULE knowledge"]["main"][0][0]["node"] == "Finalize indexes and deduplicate chunks"
    assert connections["Finalize indexes and deduplicate chunks"]["main"][0][0]["node"] == "Prepare full parent knowledge persistence"
    assert connections["Prepare full parent knowledge persistence"]["main"][0][0]["node"] == "PostgreSQL — upsert full parent knowledge"
    assert connections["PostgreSQL — upsert full parent knowledge"]["main"][0][0]["node"] == "Prepare approved schema catalogue persistence"
    assert connections["New knowledge to insert?"]["main"][1][0]["node"] == "Prepare superseded chunk purge"
    assert connections["PostgreSQL — upsert approved schema catalogue"]["main"][0][0]["node"] == "Prepare superseded chunk purge"
    assert connections["Prepare superseded chunk purge"]["main"][0][0]["node"] == "Postgres — purge superseded chunks"
    assert connections["Postgres — purge superseded chunks"]["main"][0][0]["node"] == "Prepare RAG inventory query"
    assert connections["Prepare RAG inventory query"]["main"][0][0]["node"] == "Postgres — inspect RAG table contents"
    assert connections["Postgres — inspect RAG table contents"]["main"][0][0]["node"] == "Summarize RAG inventory"


def test_rag_embeddings_use_bge_m3_and_v2_table() -> None:
    """Field contour baai/bge-m3 (1024-d) on tnavigator_schedule_knowledge_v2; skip only matching text/hash in_vector."""
    ingestion = load_json(workflow_path("tnavigator-schedule-knowledge-ingestion.workflow.json"))
    retrieval = load_json(workflow_path("tnavigator-schedule-hybrid-retrieval.workflow.json"))

    def check(wf: dict, embed_name: str, pg_name: str) -> None:
        embed = next(node for node in wf["nodes"] if node["name"] == embed_name)
        store = next(node for node in wf["nodes"] if node["name"] == pg_name)
        assert embed["parameters"]["model"] == "baai/bge-m3"
        assert "dimensions" not in embed["parameters"]
        assert (embed["parameters"].get("options") or {}).get("timeout") == -1
        assert "dimensions" not in (embed["parameters"].get("options") or {})
        assert store["parameters"]["tableName"] == "tnavigator_schedule_knowledge_v2"

    check(
        ingestion,
        "SCHEDULE Embeddings — configure same model in retrieval",
        "PGVector — insert approved SCHEDULE knowledge",
    )
    check(
        retrieval,
        "SCHEDULE Retrieval Embeddings — same model as ingestion",
        "PGVector semantic candidates",
    )
    select_js = next(
        node["parameters"]["jsCode"]
        for node in ingestion["nodes"]
        if node["name"] == "Select new MAS knowledge"
    )
    assert "CONTENT_HASH_CHANGED_BUMP_REVISION" in select_js
    assert "stored_text" in select_js
    assert "inVector.has(k)" not in select_js
    inventory = next(
        node["parameters"]["jsCode"]
        for node in ingestion["nodes"]
        if node["name"] == "Prepare RAG inventory query"
    )
    assert "tnavigator_schedule_knowledge_v2" in inventory
    assert "VECTOR_TABLE_PLACEHOLDER" not in inventory


def test_continuation_protocol_has_no_stale_agent_state_lookup_instruction() -> None:
    stale_phrases = (
        "First inspect get_session_state",
        "On a continuation call first",
        "On a continuation, call get_session_state first",
    )
    paths = [
        workflow_path("excel-extractor-agent.workflow.json"),
        workflow_path("tnavigator-schedule-knowledge-ingestion.workflow.json"),
        RAG_SOURCE,
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for phrase in stale_phrases:
            assert phrase not in text, (path.name, phrase)

def test_schedule_foundation_workflows_implement_roadmap_boundaries() -> None:
    expected_contracts = {
        "tnavigator-schedule-knowledge-ingestion.workflow.json": "schedule_knowledge_ingest/v1",
        "tnavigator-schedule-hybrid-retrieval.workflow.json": "schedule_retrieval/v1",
    }
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


def test_schedule_rag_and_trace_foundations_enforce_governance() -> None:
    ingestion = (workflow_path("tnavigator-schedule-knowledge-ingestion.workflow.json")).read_text()
    retrieval = (workflow_path("tnavigator-schedule-hybrid-retrieval.workflow.json")).read_text()
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
    excel = load_json(workflow_path("excel-extractor-agent.workflow.json"))
    excel_text = json.dumps(excel, ensure_ascii=False)
    assert "context_search" not in excel_text
    assert not any("vectorStore" in node["type"] for node in excel["nodes"])
    call = next(n for n in excel["nodes"] if n["name"] == "Call Knowledge Retrieval")
    assert call["parameters"]["workflowId"]["value"] == "REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI"

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
    assert "tnavigator_schedule_knowledge_v2" in json.dumps(ingestion, ensure_ascii=False)
    assert "corpus_json" in seed_code
    packaged = json.dumps(next(n for n in ingestion["nodes"] if n["name"] == "Packaged MAS corpus"), ensure_ascii=False)
    assert "$('Collect MAS knowledge blocks')" in inventory
    for document in ingestible_operating_guide_documents():
        block = document.get("schedule_knowledge_block") if isinstance(document.get("schedule_knowledge_block"), dict) else document
        assert block["knowledge_id"] in packaged
        assert block["knowledge_id"] not in inventory
        assert json.dumps(block["text"], ensure_ascii=False) in packaged


def test_excel_protocol_cards_use_schedule_aligned_skeleton_and_retrieval_surface() -> None:
    required_sections = ("Назначение.", "Когда применять.", "Канон протокола.", "Валидация")
    must_keep = {
        "excel-agent-trust-boundary": ("session_id", "tbl_", "недоверенн"),
        "excel-agent-discovery-and-tables": ("open_session", "unpivot", "n/a", "Index"),
        "excel-agent-query-and-result-protocol": (
            "extract_table",
            "query_table",
            "expected_output",
            "save_agent_plan",
        ),
        "excel-agent-clarification-and-continuation": ("ask_engineer", "engineer_answers", "get_session_state"),
    }
    cards = {
        document.get("knowledge_id") or document.get("id"): document
        for document in ingestible_operating_guide_documents()
        if (document.get("target_base") or (document.get("schedule_knowledge_block") or {}).get("target_base"))
        == "excel_protocol"
        or str(document.get("knowledge_id") or document.get("id") or "").startswith("excel-agent-")
    }
    assert set(cards) == set(must_keep)
    for kid, needles in must_keep.items():
        block = cards[kid].get("schedule_knowledge_block") if isinstance(cards[kid].get("schedule_knowledge_block"), dict) else cards[kid]
        expected_revision = {
            "excel-agent-trust-boundary": "5",
            "excel-agent-discovery-and-tables": "7",
            "excel-agent-query-and-result-protocol": "6",
            "excel-agent-clarification-and-continuation": "5",
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
        for retired in ("clr_", "continuation_state"):
            assert retired not in blob, (kid, retired)


def test_excel_protocol_retrieval_boost_baseline_fixture_is_historical() -> None:
    baseline_path = ROOT / "n8n" / "tests" / "fixtures" / "excel-protocol-searchable-baseline.json"
    baseline = load_json(baseline_path)
    assert set(baseline) == {
        "excel-agent-trust-boundary",
        "excel-agent-discovery-and-tables",
        "excel-agent-query-and-result-protocol",
        "excel-agent-clarification-and-continuation",
    }
    for kid, row in baseline.items():
        assert "searchable" in row and len(row["searchable"]) > 500, kid


def test_mas_corpus_8_5_worked_examples_stubs_and_no_retired_tokens() -> None:
    """8.5: worked_example ×3, stubs absent_agent, comments without INCLUDE/DATES, no retired tokens."""
    docs = ingestible_operating_guide_documents()
    by_id = {document.get("knowledge_id") or document.get("id"): document for document in docs}
    assert {
        "worked-commissioning-dates-v1",
        "worked-group-rebind-v1",
        "worked-dataset-wefac-v1",
    } <= set(by_id)
    for kid in (
        "worked-commissioning-dates-v1",
        "worked-group-rebind-v1",
        "worked-dataset-wefac-v1",
    ):
        assert by_id[kid]["knowledge_type"] == "worked_example"
        assert by_id[kid]["target_base"] == "schedule_mvp"
        assert len(by_id[kid].get("task_patterns") or []) >= 3
        assert len(by_id[kid].get("examples") or []) >= 2
    comments = by_id["schedule-model-file-comments-v1"]
    assert comments["keywords"] == []
    assert str(comments["revision"]) == "4"
    assert by_id["route-cluster-calculation"]["topics"] == ["model_version", "run_status", "run_results"]
    assert "absent_agent" not in (by_id["route-cluster-calculation"].get("topics") or [])
    for kid in (
        "route-binary-results",
        "route-presentation",
        "route-calculation",
    ):
        assert by_id[kid]["topics"] == ["absent_agent"], kid
        assert "маршрутизация" not in (by_id[kid].get("topics") or [])
    blob = json.dumps(docs, ensure_ascii=False)
    for tok in ("clr_", "continuation_state", "specialist_packet"):
        assert tok not in blob, tok
    assert "agent_task" in by_id["specialist-template-bounded-work"]["text"]

def _demo_spec():
    import sys

    sys.path.insert(0, str(TEMPLATES))
    from mas_agent_spec import AgentSpec, FallbackTexts

    return AgentSpec(
        agent_id="demo_agent",
        title="Demo Agent",
        when_to_use="Демонстрационный агент: считает скважины в задаче.",
        input_required=[],
        output_provides=["well_count"],
        service_url_key="demo_agent_url",
        lab_url="http://demo-agent:8300",
        slug="demo",
        system_prompt="Ты — демо-агент. Вызови count_wells, затем заверши ответ одним предложением.",
        tools=[
            ("count_wells", "Посчитать скважины в тексте задачи.", [("text", "string", True, "Текст задачи")]),
            ("ask_engineer", "Спросить инженера одним вопросом по-русски.", [("question", "string", True, "Вопрос прозой")]),
        ],
        rag_selector="excel",
        result_tools=["count_", "ask_engineer"],
        texts=FallbackTexts(
            no_result_question="Демо-агент не понял задачу. Опишите, что именно посчитать.",
            repeated_question="Демо-агент несколько раз пробовал, но не справился. Уточните задачу.",
            done_message="Демо-агент завершил работу.",
            no_result_issue="no_count",
            question_id="Q-demo",
        ),
        accepted_message="Демо-агент принял задачу.",
        progress_message="Демо-агент читает задачу.",
    )

def test_agent_spec_generates_the_uniform_agent_workflow_and_registry_row() -> None:
    """Phase 4.2: one AgentSpec → n8n workflow + agent_registry row + Runtime Config field, no orchestrator edit."""
    import sys

    sys.path.insert(0, str(TEMPLATES))
    from mas_agent_workflow import build_workflow

    spec = _demo_spec()
    wf = build_workflow(spec)
    assert wf["name"] == "Agent — Demo Agent"
    assert wf["id"] == spec.registry_row()["invoke"]["workflow_id"]
    names = {n["name"] for n in wf["nodes"]}
    for must in (
        "When executed by another workflow",
        "Runtime configuration",
        "Normalize demo task",
        "Open demo session",
        "Session ready?",
        "Format missing demo",
        "Activity — Demo Agent accepted",
        "Prepare AI Agent input",
        "Call Knowledge Retrieval",
        "Attach demo RAG evidence",
        "Activity — Demo Agent RAG",
        "Restore after Demo Agent RAG",
        "Build chat request",
        "Demo Agent Agent chat",
        "Parse agent chat",
        "Agent loop router",
        "Call agent tool",
        "Retrieve knowledge",
        "Summarize AI steps",
        "Fetch demo result",
        "Format demo result",
        "Close demo session",
    ):
        assert must in names, must
    by_name = {n["name"]: n for n in wf["nodes"]}
    assert "Demo Agent AI Agent" not in names
    assert "Result stored?" not in names
    assert "count_wells" not in names
    chat = by_name["Demo Agent Agent chat"]
    assert chat["type"] == "n8n-nodes-base.httpRequest"
    assert chat["parameters"]["nodeCredentialType"] == "openAiApi"
    call_tool = by_name["Call agent tool"]
    assert call_tool["type"] == "n8n-nodes-base.httpRequest"
    assert "$json.tool_url" in call_tool["parameters"]["url"]
    build_js = by_name["Build chat request"]["parameters"]["jsCode"]
    assert '"name": "count_wells"' in build_js
    assert '"name": "ask_engineer"' in build_js
    assert '"name": "retrieve_knowledge"' in build_js
    assert spec.system_prompt in build_js
    assert wf["connections"]["Restore after AI tools"]["main"][0][0]["node"] == "Fetch demo result"
    assert "retrieve_knowledge" in by_name["Summarize AI steps"]["parameters"]["jsCode"]
    assert by_name["Open demo session"]["parameters"]["url"] == "={{ $json.demo_agent_url }}/agent-tools/open_session"
    # No service credential → no auth on HTTP nodes; Activity events never carry service auth.
    assert "authentication" not in by_name["Open demo session"]["parameters"]
    assert "authentication" not in by_name["Activity — Demo Agent accepted"]["parameters"]
    # Registry row: what the orchestrator plans with.
    row = spec.registry_row()
    assert row["invoke"] == {"kind": "n8n_workflow", "workflow_id": wf["id"], "workflow_name": "Agent — Demo Agent"}
    assert row["hitl_policy"] == "agent_asks" and row["enabled"] is True
    # The real agents are specs too, and the registry seed / Runtime Config fields come from them.
    from agents import ALL, EXCEL_EXTRACTOR, SCHEDULE_BUILDER
    from generate_mas_runtime_config import LAB_URLS
    from mas_agent_registry import SEED

    assert [r["agent_id"] for r in SEED] == [s.agent_id for s in ALL]
    keys = [k for k, _ in LAB_URLS]
    for s in ALL:
        if s.service_url_key:
            assert s.service_url_key in keys, s.agent_id
    assert EXCEL_EXTRACTOR.service_credentials is None and SCHEDULE_BUILDER.service_credentials is None
    excel_wf = load_json(CORE / "excel-extractor-agent.workflow.json")
    assert excel_wf["id"] == EXCEL_EXTRACTOR.resolved_workflow_id == SEED[0]["invoke"]["workflow_id"]
    from mas_agent_workflow import openai_tools_for_spec

    for spec, filename in (
        (EXCEL_EXTRACTOR, "excel-extractor-agent.workflow.json"),
        (SCHEDULE_BUILDER, "schedule-builder-agent.workflow.json"),
    ):
        live = load_json(workflow_path(filename))
        types = {node["type"] for node in live["nodes"]}
        assert "@n8n/n8n-nodes-langchain.agent" not in types, filename
        assert "@n8n/n8n-nodes-langchain.lmChatOpenAi" not in types, filename
        assert "n8n-nodes-base.httpRequestTool" not in types, filename
        tools_schema = openai_tools_for_spec(spec)
        schema_names = [t["function"]["name"] for t in tools_schema]
        assert schema_names[-1] == "retrieve_knowledge"
        assert schema_names[:-1] == [name for name, _d, _f in spec.tools]
        for (_name, _desc, fields), schema in zip(spec.tools, tools_schema[:-1], strict=True):
            for key, typ, _req, _field_desc in fields:
                expect = "number" if typ == "number" else "string"
                assert schema["function"]["parameters"]["properties"][key]["type"] == expect, (spec.agent_id, key)


def test_n8n_agent_transport_keeps_langchain_graph() -> None:
    """llm_transport=n8n_agent is the one-revision rollback; default http_loop must not use it."""
    import sys

    sys.path.insert(0, str(TEMPLATES))
    from mas_agent_workflow import build_workflow

    spec = _demo_spec()
    spec.llm_transport = "n8n_agent"
    wf = build_workflow(spec)
    types = {node["type"] for node in wf["nodes"]}
    assert "@n8n/n8n-nodes-langchain.agent" in types
    assert "n8n-nodes-base.httpRequestTool" in types
    assert "Demo Agent AI Agent" in {node["name"] for node in wf["nodes"]}
    assert "Result stored?" in {node["name"] for node in wf["nodes"]}


def test_keyword_instruction_cards_have_bounded_summary() -> None:
    import sys

    sys.path.insert(0, str(TEMPLATES))
    from schedule_rag_workflows import ingestible_blocks_from_payload

    blocks = ingestible_blocks_from_payload(load_json(RAG_SOURCE))
    keywords = [b for b in blocks if b.get("knowledge_type") == "keyword_instruction"]
    assert len(keywords) >= 40
    for block in keywords:
        summary = str(block.get("summary") or "").strip()
        assert summary, block.get("knowledge_id")
        assert len(summary) <= 600, (block.get("knowledge_id"), len(summary))
        assert "Когда применять" in summary or "когда применять" in summary.lower()
    wcon = next(b for b in keywords if b.get("knowledge_id") == "wconprod-forecast-control-v1")
    assert "WELTARG" in wcon["summary"]

