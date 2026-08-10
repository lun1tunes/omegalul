from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / "n8n" / "workflows"
TEMPLATES = ROOT / "n8n" / "templates"
RAG_SOURCE = ROOT / "n8n" / "rag" / "excel-agent-operating-guide.documents.json"
IMPORT_MANIFEST = ROOT / "n8n" / "import-manifest.json"
EXCEL_DELIVERY_WORKFLOWS = {
    "ai-components.workflow.json",
    "excel-engineering-specialist-adapter.workflow.json",
    "excel-extraction-agent.workflow.json",
    "excel-extraction-form-adapter.workflow.json",
    "excel-mas-orchestrator.workflow.json",
    "excel-rag-ingestion.workflow.json",
}
MATH_DELIVERY_WORKFLOWS = {
    "calculation-specialist-adapter.workflow.json",
}
MVP_ENTRY_WORKFLOWS = {
    "mvp-entry-form.workflow.json",
}
SCHEDULE_FOUNDATION_WORKFLOWS = {
    "mas-trace-event-writer.workflow.json",
    "tnavigator-schedule-baseline-analyzer.workflow.json",
    "tnavigator-schedule-baseline-decoder.workflow.json",
    "tnavigator-schedule-baseline-query.workflow.json",
    "tnavigator-schedule-hybrid-retrieval.workflow.json",
    "tnavigator-schedule-intake.workflow.json",
    "tnavigator-schedule-knowledge-ingestion.workflow.json",
    "tnavigator-schedule-merge.workflow.json",
    "tnavigator-schedule-planner.workflow.json",
    "tnavigator-schedule-renderer.workflow.json",
    "tnavigator-schedule-release.workflow.json",
    "tnavigator-schedule-validator.workflow.json",
    "tnavigator-schedule-verifier.workflow.json",
}
UNIVERSAL_ENGINEERING_WORKFLOWS = {
    "engineering-specialist-template.workflow.json",
    "tnavigator-schedule-builder.workflow.json",
    "universal-engineering-orchestrator.workflow.json",
} | SCHEDULE_FOUNDATION_WORKFLOWS

# Registry names and versions verified against the node packages bundled in
# the official n8nio/n8n:2.30.8 image. These are export JSON identifiers, not
# the shorter labels shown by the node picker UI.
N8N_2_30_8_PORTABLE_NODE_VERSIONS = {
    "@n8n/n8n-nodes-langchain.agent": {3.1},
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
    assert imported == {path.name for path in WORKFLOWS.glob("*.workflow.json")}
    assert len(imported) == 24

    workflows_by_name = {
        workflow["name"]: workflow
        for path in WORKFLOWS.glob("*.workflow.json")
        for workflow in [load_json(path)]
    }
    workflow_names = set(workflows_by_name)
    bindings = manifest["mandatory_execute_workflow_bindings"]
    future_bindings = manifest["future_enterprise_or_optional_bindings"]
    assert len(bindings) == 7
    assert future_bindings == []
    all_static_bindings = bindings + future_bindings
    assert len({binding["placeholder"] for binding in all_static_bindings}) == len(all_static_bindings)
    for binding in all_static_bindings:
        assert binding["owner"] in workflow_names
        assert binding["target"] in workflow_names
        owner = workflows_by_name[binding["owner"]]
        owner_nodes = {node["name"]: node for node in owner["nodes"]}
        assert binding["node"] in owner_nodes, (
            f"Mandatory binding node {binding['node']!r} is missing from workflow "
            f"{binding['owner']!r}; available nodes: {sorted(owner_nodes)}"
        )
        call = owner_nodes[binding["node"]]
        assert call["type"] == "n8n-nodes-base.executeWorkflow"
        assert call["parameters"]["workflowId"]["value"] == binding["placeholder"]
    assert any("expert-authored" in blocker for blocker in manifest["mvp_external_blockers"])


def test_ai_tool_nodes_bind_session_from_prepared_workflow_context() -> None:
    workflow = load_json(WORKFLOWS / "excel-extraction-agent.workflow.json")
    tool_nodes = [
        node
        for node in workflow["nodes"]
        if node["type"] == "@n8n/n8n-nodes-langchain.toolHttpRequest"
    ]
    assert {node["name"] for node in tool_nodes} == {
        "workbook_introspect",
        "sheet_preview",
        "detect_tables",
        "describe_table",
        "list_column_values",
        "query_table",
        "save_agent_plan",
    }
    required_model_fields = {
        "workbook_introspect": set(),
        "sheet_preview": {"sheet"},
        "detect_tables": set(),
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


def test_workflows_use_current_n8n_2_30_8_ai_node_versions() -> None:
    for path in WORKFLOWS.glob("*.workflow.json"):
        workflow = load_json(path)
        for node in workflow["nodes"]:
            if node["type"] == "@n8n/n8n-nodes-langchain.agent":
                assert node["typeVersion"] == 3.1, (path.name, node["name"])
                expected_parser = path.name in UNIVERSAL_ENGINEERING_WORKFLOWS
                assert node["parameters"].get("hasOutputParser") is expected_parser
            elif node["type"] == "@n8n/n8n-nodes-langchain.lmChatOpenAi":
                assert node["typeVersion"] == 1.3, (path.name, node["name"])
            elif node["type"] == "@n8n/n8n-nodes-langchain.memoryPostgresChat":
                assert node["typeVersion"] == 1.4, (path.name, node["name"])


def test_delivery_workflows_use_only_verified_n8n_2_30_8_registry_ids() -> None:
    """Prevent UI display labels or old unscoped package names entering exports."""
    for filename in EXCEL_DELIVERY_WORKFLOWS | MATH_DELIVERY_WORKFLOWS | UNIVERSAL_ENGINEERING_WORKFLOWS:
        workflow = load_json(WORKFLOWS / filename)
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
        workflow = load_json(WORKFLOWS / filename)
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
    workflow = load_json(WORKFLOWS / "excel-extraction-agent.workflow.json")
    assert len(workflow["nodes"]) == 56
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
    assert names <= connected


def test_tool_instructions_use_structured_arguments_not_legacy_input_wrapper() -> None:
    paths = [
        WORKFLOWS / "excel-extraction-agent.workflow.json",
        WORKFLOWS / "excel-rag-ingestion.workflow.json",
        RAG_SOURCE,
    ]
    stale = ('{"input":', "one input string", "exactly one input JSON string", "Use [] for arrays")
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for phrase in stale:
            assert phrase not in text, (path.name, phrase)


def test_form_adapter_uses_real_trigger_and_real_form_page() -> None:
    """In 2.30.8 ``form`` is a page node and cannot replace formTrigger."""
    workflow = load_json(WORKFLOWS / "excel-extraction-form-adapter.workflow.json")
    by_type = {node["type"]: node for node in workflow["nodes"]}
    assert by_type["n8n-nodes-base.formTrigger"]["typeVersion"] == 2.6
    assert by_type["n8n-nodes-base.form"]["typeVersion"] == 2.5
    assert by_type["n8n-nodes-base.executeWorkflow"]["typeVersion"] == 1.3


def test_workflows_do_not_depend_on_n8n_env_or_global_variables() -> None:
    for path in WORKFLOWS.glob("*.workflow.json"):
        text = path.read_text(encoding="utf-8")
        assert "$env" not in text, path.name
        assert "$vars" not in text, path.name


def test_delivery_workflows_are_inactive_until_ui_configuration() -> None:
    """An import must not expose webhooks that still contain placeholders."""
    paths = list(WORKFLOWS.glob("*.workflow.json"))
    assert {path.name for path in paths} == EXCEL_DELIVERY_WORKFLOWS | MATH_DELIVERY_WORKFLOWS | MVP_ENTRY_WORKFLOWS | UNIVERSAL_ENGINEERING_WORKFLOWS
    for path in paths:
        workflow = load_json(path)
        assert workflow.get("active") is False, path.name


def test_universal_engineering_orchestrator_has_no_service_or_excel_contract() -> None:
    paths = [WORKFLOWS / filename for filename in sorted(UNIVERSAL_ENGINEERING_WORKFLOWS)]
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
    adapter = load_json(WORKFLOWS / "calculation-specialist-adapter.workflow.json")
    assert adapter["name"] == "Engineering Calculation Specialist Adapter — Math Service"
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
    assert "name:'trajectory_files'" in adapter_text
    assert "name:'surface_file'" in adapter_text
    assert "TRAJECTORY_INTERSECTION_BATCH_COMPUTED" in adapter_text
    assert "result_mode:'computed_batch'" in adapter_text
    assert "MOCK_TRAJECTORY_INTERSECTION" not in adapter_text

    orchestrator = load_json(WORKFLOWS / "universal-engineering-orchestrator.workflow.json")
    orchestrator_by_name = {node["name"]: node for node in orchestrator["nodes"]}
    call = orchestrator_by_name["Call Calculation Specialist"]
    assert call["parameters"]["workflowId"]["value"] == "REPLACE_CALCULATION_ADAPTER_IN_UI"
    assert call["parameters"]["workflowId"]["cachedResultName"] == adapter["name"]
    allowlist_code = orchestrator_by_name["Resolve allowlisted specialist"]["parameters"]["jsCode"]
    assert "engineering_calculation_specialist:{route:2,configured:true}" in allowlist_code


def test_universal_orchestrator_enterprise_control_plane() -> None:
    workflow = load_json(WORKFLOWS / "universal-engineering-orchestrator.workflow.json")
    nodes = workflow["nodes"]
    by_name = {node["name"]: node for node in nodes}
    names = {node["name"] for node in nodes}
    types = [node["type"] for node in nodes]
    text = json.dumps(workflow, ensure_ascii=False)

    assert types.count("n8n-nodes-base.dataTable") >= 8
    assert types.count("@n8n/n8n-nodes-langchain.agent") == 2
    assert {node["name"] for node in nodes if node["type"] == "@n8n/n8n-nodes-langchain.agent"} == {
        "Engineering Planner Agent",
        "Independent Verifier Agent",
    }
    assert {
        "CAS persist human action then plan",
        "CAS persist terminal human action",
        "CAS persist plan or human gate",
        "CAS persist verification",
        "CAS persist specialist gate or error",
        "CAS persist routing gate",
    } <= names
    for node in nodes:
        if node["name"].startswith("CAS persist"):
            filters = node["parameters"]["filters"]["conditions"]
            assert {item["keyName"] for item in filters} == {"task_id", "version"}, node["name"]
            assert node.get("alwaysOutputData") is True, node["name"]

    assert {
        "Confirm human action planning CAS",
        "Confirm terminal human action CAS",
        "Confirm plan CAS",
        "Confirm verification CAS",
        "Confirm specialist gate CAS",
        "Confirm routing gate CAS",
    } <= names
    assert "Approved or continued task delegates directly?" in names
    assert text.count("cas_succeeded") >= 6
    for node in nodes:
        if node["name"].startswith("Call ") and node["name"].endswith(" Specialist"):
            assert node.get("retryOnFail") is not True, node["name"]

    assert "specialist_id" in text
    assert "specialist_route" in text
    assert "workflow_id" not in json.dumps(
        next(node for node in nodes if node["name"] == "Engineering Planner Agent"),
        ensure_ascii=False,
    ).lower()
    assert "Bounded retry budget exhausted" in text
    assert "expected_version" in text
    assert "gate_id" in text
    assert "pre_delegation_approval" in text
    assert "should_delegate" in text
    assert "Approved task has no persisted specialist packet" in text
    assert "result_approval" in text
    assert "Data Table is authoritative durable state" in text
    assert "updatedRows.length!==1" in text
    assert "human_responses" in text
    assert "Payload is too large" in text
    assert "parseStructured" in text
    assert "contains malformed JSON." in text
    assert "parseHumanResponse" in text
    assert "persistedRisk" in text
    assert "riskFloor" in text
    assert "Retry is allowed only for a persisted retryable_error" in text
    assert "Stored task state is malformed" in text
    assert "state_integrity" in text
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
    assert "decision_record" in planner_schema["required"]
    assert "decision_record" in verifier_schema["required"]
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
    assert {
        "contract", "contract_version", "specialist_id", "objective", "inputs", "controls",
        "acceptance_criteria", "artifact_refs",
    } == set(packet_schema["required"])
    assert packet_schema["additionalProperties"] is False

    form = next(node for node in nodes if node["name"] == "Engineering task form")
    form_fields = {field["fieldName"] for field in form["parameters"]["formFields"]["values"]}
    assert {"request_text", "request_json", "context_json", "file", "schedule_file"} <= form_fields
    schedule_field = next(
        field
        for field in form["parameters"]["formFields"]["values"]
        if field["fieldName"] == "schedule_file"
    )
    assert schedule_field["fieldType"] == "file"
    assert schedule_field["acceptFileTypes"] == ".data, .inc, .sch, .txt"
    extractor = by_name["Extract SCHEDULE upload as UTF-8 text"]
    assert extractor["type"] == "n8n-nodes-base.extractFromFile"
    assert extractor["typeVersion"] == 1.1
    assert extractor["parameters"]["operation"] == "text"
    assert extractor["parameters"]["binaryPropertyName"] == "schedule_file"
    assert extractor["parameters"]["destinationKey"] == "baseline_schedule_text"
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
        "Extract SCHEDULE upload as UTF-8 text"
    ]
    assert main_targets("Form has SCHEDULE upload?", 1) == ["Normalize invocation"]
    assert main_targets("Extract SCHEDULE upload as UTF-8 text") == ["Normalize invocation"]
    normalize_code = by_name["Normalize invocation"]["parameters"]["jsCode"]
    assert "baselineBytes<=2097152" in normalize_code
    assert "baseline_schedule_text:uploadedSchedule" in normalize_code
    planner_code = by_name["Prepare planner input"]["parameters"]["jsCode"]
    assert "delete request.baseline_schedule_text" in planner_code
    apply_plan_code = by_name["Validate and apply plan"]["parameters"]["jsCode"]
    assert "baseline_schedule_text:typeof originalSchedule.baseline_schedule_text" in apply_plan_code


def test_universal_orchestrator_has_a_static_excel_specialist_route() -> None:
    workflow = load_json(WORKFLOWS / "universal-engineering-orchestrator.workflow.json")
    by_name = {node["name"]: node for node in workflow["nodes"]}
    text = json.dumps(workflow, ensure_ascii=False)

    assert "excel_extraction_specialist" in text
    resolve_code = by_name["Resolve allowlisted specialist"]["parameters"]["jsCode"]
    assert "excel_extraction_specialist:{route:0,configured:true}" in resolve_code
    assert by_name["Configured specialist router"]["parameters"]["numberOutputs"] == 5

    call = by_name["Call Excel Extraction Specialist Adapter"]
    assert call["type"] == "n8n-nodes-base.executeWorkflow"
    assert call["typeVersion"] == 1.3
    assert call["parameters"]["workflowId"]["value"] == "REPLACE_EXCEL_ADAPTER_IN_UI"
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
    state_columns = by_name["Insert durable task state"]["parameters"]["columns"]["value"]
    assert "binary" not in state_columns


def test_universal_orchestrator_has_a_static_schedule_builder_route() -> None:
    workflow = load_json(WORKFLOWS / "universal-engineering-orchestrator.workflow.json")
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
    assert rag_call["type"] == "n8n-nodes-base.executeWorkflow"
    assert rag_call["typeVersion"] == 1.3
    assert rag_call["parameters"]["workflowId"]["value"] == "REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI"
    assert set(rag_call["parameters"]["workflowInputs"]["value"]) == {"schedule_retrieval_request"}
    assert rag_call["onError"] == "continueRegularOutput"
    assert "SCHEDULE_RAG_EVIDENCE_REQUIRED" in by_name["Build SCHEDULE RAG evidence gate"]["parameters"]["jsCode"]
    attach = by_name["Attach governed SCHEDULE RAG evidence"]["parameters"]["jsCode"]
    assert "schedule_rag_evidence" in attach
    assert "result.citations.length>0" in attach


def test_universal_orchestrator_has_a_static_redacted_trace_route() -> None:
    workflow = load_json(WORKFLOWS / "universal-engineering-orchestrator.workflow.json")
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
    workflow = load_json(WORKFLOWS / "universal-engineering-orchestrator.workflow.json")
    by_name = {node["name"]: node for node in workflow["nodes"]}
    connections = workflow["connections"]
    handoff_code = by_name["Route successful specialist handoff"]["parameters"]["jsCode"]

    assert "EXCEL_EVIDENCE_READY" in handoff_code
    assert "next_specialist:'schedule_builder_specialist'" in handoff_code
    assert "source_facts_packet:result.compact_data" in handoff_code
    next_stage = connections["Successful specialist next stage"]["main"]
    expected_routes = [
        "Prepare planner input",
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
    builder = load_json(WORKFLOWS / "tnavigator-schedule-builder.workflow.json")
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
    assert "correlation===String(loop.expected_correlation_id" in resume_code
    assert "schedule_builder_specialist" in resume_code

    apply_action = by_name["Apply action and version guard"]["parameters"]["jsCode"]
    apply_plan = by_name["Validate and apply plan"]["parameters"]["jsCode"]
    assert "SCHEDULE release is blocked" in apply_action
    assert "schedule_text:inlineText" in apply_action
    assert "simulator_check_result" not in apply_action
    assert "artifact publication" not in apply_action.lower()
    assert "scheduleTask&&riskRank[risk]<riskRank.high" in apply_plan
    assert "policy_version:'petroleum-schedule-policy-v1'" in apply_plan
    assert "idempotency_key:`${base.task_id}:specialist:" in apply_plan
    assert "expected_version:Number(base.version)+1" in apply_plan
    assert "policy_version:'petroleum-schedule-policy-v1'" in resume_code


def test_schedule_builder_is_bounded_and_orchestrator_mediated() -> None:
    workflow = load_json(WORKFLOWS / "tnavigator-schedule-builder.workflow.json")
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
    assert workflow["name"] == "tNavigator SCHEDULE Builder — governed CREATE/REVISE pipeline"
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
    assert "decision_record" in planner_schema["required"]
    assert "decision_record" in builder_schema["required"]
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
    workflow = load_json(WORKFLOWS / "excel-engineering-specialist-adapter.workflow.json")
    by_name = {node["name"]: node for node in workflow["nodes"]}

    assert workflow["name"] == "Excel Extraction Specialist Adapter — universal contract"
    assert workflow["active"] is False
    trigger = by_name["Receive Excel specialist packet"]
    assert trigger["type"] == "n8n-nodes-base.executeWorkflowTrigger"
    assert trigger["typeVersion"] == 1.2

    call = by_name["Call native Excel Extraction Agent"]
    assert call["type"] == "n8n-nodes-base.executeWorkflow"
    assert call["typeVersion"] == 1.3
    assert call["parameters"]["workflowId"]["value"] == "REPLACE_EXCEL_EXTRACTION_AGENT_IN_UI"
    assert call["parameters"]["mode"] == "once"
    assert call["parameters"]["options"]["waitForSubWorkflow"] is True
    assert call["onError"] == "continueRegularOutput"
    assert call.get("retryOnFail") is not True

    prepare_code = by_name["Prepare native Excel invocation"]["parameters"]["jsCode"]
    assert "const previous=parseObject(incoming.previous_specialist_result)" in prepare_code
    assert "const continuation=isObject(previous.continuation)" in prepare_code
    assert "opaque.execution_ref" in prepare_code
    assert "opaque.clarification_ref" in prepare_code
    assert "nativeJson={session_id:opaque.execution_ref" in prepare_code
    assert "packet.session_id" not in prepare_code
    assert "item.binary?.file" in prepare_code

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
    assert "decision_record:decisionRecord" in adapt_code
    assert "trace_summary" in adapt_code


def test_legacy_excel_mas_is_explicitly_not_a_deployment_entrypoint() -> None:
    workflow = load_json(WORKFLOWS / "excel-mas-orchestrator.workflow.json")
    assert workflow["name"] == "LEGACY — Excel MAS Orchestrator — do not deploy"
    assert workflow["active"] is False


def test_specialist_template_uses_only_universal_boundary() -> None:
    workflow = load_json(WORKFLOWS / "engineering-specialist-template.workflow.json")
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
        "schedule_lossless_runtime.py",
        "schedule_baseline_decoder.py",
        "schedule_baseline_query.py",
        "schedule_pipeline.py",
        "schedule_rag_workflows.py",
        "schedule_intake_runtime.py",
        "schedule_schema_runtime.py",
        "schedule_semantic_runtime.py",
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

    roadmap = (ROOT / "docs" / "architecture" / "petroleum-mas-research-and-roadmap.md").read_text(
        encoding="utf-8"
    )
    assert "Прямой вызов Excel Extractor из Schedule Builder запрещён" in roadmap
    assert "returnIntermediateSteps=true" in roadmap
    assert "attention_threshold = 85" in roadmap
    assert "hitl_threshold      = 70" in roadmap
    assert "`CREATE`: создание SCHEDULE с нуля" in roadmap
    assert "Excel→RAG→Builder handoff" in roadmap


def test_rag_workflow_contains_the_canonical_documents() -> None:
    source = load_json(RAG_SOURCE)["documents"]
    workflow = load_json(WORKFLOWS / "excel-rag-ingestion.workflow.json")
    node = next(node for node in workflow["nodes"] if node["name"] == "RAG documents — portable operating guide")
    code = node["parameters"]["jsCode"]
    for document in source:
        assert document["id"] in code
        assert json.dumps(document["text"], ensure_ascii=False) in code


def test_continuation_protocol_has_no_stale_agent_state_lookup_instruction() -> None:
    stale_phrases = (
        "First inspect get_session_state",
        "On a continuation call first",
        "On a continuation, call get_session_state first",
    )
    paths = [
        WORKFLOWS / "excel-extraction-agent.workflow.json",
        WORKFLOWS / "excel-rag-ingestion.workflow.json",
        RAG_SOURCE,
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for phrase in stale_phrases:
            assert phrase not in text, (path.name, phrase)


def test_schedule_foundation_workflows_implement_roadmap_boundaries() -> None:
    expected_contracts = {
        "tnavigator-schedule-intake.workflow.json": "schedule_intake/v1",
        "tnavigator-schedule-baseline-analyzer.workflow.json": "baseline_analysis/v1",
        "tnavigator-schedule-baseline-decoder.workflow.json": "baseline_decode_result/v1",
        "tnavigator-schedule-baseline-query.workflow.json": "baseline_inventory_query_result/v1",
        "tnavigator-schedule-planner.workflow.json": "schedule_plan/v1",
        "tnavigator-schedule-merge.workflow.json": "schedule_merge/v1",
        "tnavigator-schedule-renderer.workflow.json": "schedule_render_result/v1",
        "tnavigator-schedule-validator.workflow.json": "schedule_validation/v1",
        "tnavigator-schedule-verifier.workflow.json": "schedule_verifier/v1",
        "tnavigator-schedule-release.workflow.json": "schedule_release/v1",
        "tnavigator-schedule-knowledge-ingestion.workflow.json": "schedule_knowledge_ingest/v1",
        "tnavigator-schedule-hybrid-retrieval.workflow.json": "schedule_retrieval/v1",
        "mas-trace-event-writer.workflow.json": "mas_trace_event/v1",
    }
    assert SCHEDULE_FOUNDATION_WORKFLOWS == set(expected_contracts)
    for filename, contract in expected_contracts.items():
        workflow = load_json(WORKFLOWS / filename)
        assert workflow["active"] is False
        assert workflow["meta"]["targetN8nVersion"] == "2.30.8"
        assert workflow["meta"]["contractVersion"] == contract
        runtime = [n for n in workflow["nodes"] if n["type"] != "n8n-nodes-base.stickyNote"]
        assert runtime[0]["type"] == "n8n-nodes-base.executeWorkflowTrigger"
        text = json.dumps(workflow, ensure_ascii=False).lower()
        assert "$env" not in text and "$vars" not in text
        assert "readwritefile" not in text and "executecommand" not in text


def test_schedule_foundation_is_fail_closed_and_preserve_by_default() -> None:
    intake = (WORKFLOWS / "tnavigator-schedule-intake.workflow.json").read_text()
    baseline = (WORKFLOWS / "tnavigator-schedule-baseline-analyzer.workflow.json").read_text()
    decoder = (WORKFLOWS / "tnavigator-schedule-baseline-decoder.workflow.json").read_text()
    baseline_query = (WORKFLOWS / "tnavigator-schedule-baseline-query.workflow.json").read_text()
    merge = (WORKFLOWS / "tnavigator-schedule-merge.workflow.json").read_text()
    renderer = (WORKFLOWS / "tnavigator-schedule-renderer.workflow.json").read_text()
    validator = (WORKFLOWS / "tnavigator-schedule-validator.workflow.json").read_text()
    release = (WORKFLOWS / "tnavigator-schedule-release.workflow.json").read_text()
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
    assert "ACCOUNTABLE_APPROVAL_REQUIRED" in release and "GATE_MISMATCH" in release


def test_schedule_builder_uses_the_same_governed_intake_runtime() -> None:
    intake = load_json(WORKFLOWS / "tnavigator-schedule-intake.workflow.json")
    builder = load_json(WORKFLOWS / "tnavigator-schedule-builder.workflow.json")
    intake_code = next(node for node in intake["nodes"] if node["name"] == "Validate SCHEDULE intake")["parameters"]["jsCode"]
    builder_code = next(node for node in builder["nodes"] if node["name"] == "Run deterministic SCHEDULE intake")["parameters"]["jsCode"]
    assert builder_code == intake_code
    prepare = next(node for node in builder["nodes"] if node["name"] == "Prepare deterministic intake")["parameters"]["jsCode"]
    for required in (
        "contract:'schedule_build_request'", "orchestrator_task_id:root.task_id",
        "policy_version", "idempotency_key", "expected_version",
    ):
        assert required in prepare
    gate = next(node for node in builder["nodes"] if node["name"] == "Build SCHEDULE pipeline gate result")["parameters"]["jsCode"]
    assert "suppliedQuestions" in gate


def test_schedule_rag_and_trace_foundations_enforce_governance() -> None:
    ingestion = (WORKFLOWS / "tnavigator-schedule-knowledge-ingestion.workflow.json").read_text()
    retrieval = (WORKFLOWS / "tnavigator-schedule-hybrid-retrieval.workflow.json").read_text()
    trace = load_json(WORKFLOWS / "mas-trace-event-writer.workflow.json")
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
    assert "TARGET_BASE_NOT_ALLOWLISTED" in retrieval
    assert "ACCESS_SCOPE_REQUIRED" in retrieval
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
    assert "source.map((candidate,index)" in trace_code
