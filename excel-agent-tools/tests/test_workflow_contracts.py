from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / "n8n" / "workflows"
TEMPLATES = ROOT / "n8n" / "templates"
RAG_SOURCE = ROOT / "n8n" / "rag" / "excel-agent-operating-guide.documents.json"

EXCEL_DELIVERY_WORKFLOWS = {
    "ai-components.workflow.json",
    "excel-engineering-specialist-adapter.workflow.json",
    "excel-extraction-agent.workflow.json",
    "excel-extraction-form-adapter.workflow.json",
    "excel-mas-orchestrator.workflow.json",
    "excel-rag-ingestion.workflow.json",
}
UNIVERSAL_ENGINEERING_WORKFLOWS = {
    "engineering-specialist-template.workflow.json",
    "universal-engineering-orchestrator.workflow.json",
}

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
    "n8n-nodes-base.form": {2.5},
    "n8n-nodes-base.formTrigger": {2.6},
    "n8n-nodes-base.httpRequest": {4.4},
    "n8n-nodes-base.if": {2.2, 2.3},
    "n8n-nodes-base.manualTrigger": {1},
    "n8n-nodes-base.merge": {3.2},
    "n8n-nodes-base.set": {3.4},
    "n8n-nodes-base.stickyNote": {1},
    "n8n-nodes-base.webhook": {2, 2.1},
    "n8n-nodes-base.switch": {3.4},
}


def load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as file:
        return json.load(file)


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
    for filename in EXCEL_DELIVERY_WORKFLOWS | UNIVERSAL_ENGINEERING_WORKFLOWS:
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
    for filename in EXCEL_DELIVERY_WORKFLOWS | UNIVERSAL_ENGINEERING_WORKFLOWS:
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
    assert {path.name for path in paths} == EXCEL_DELIVERY_WORKFLOWS | UNIVERSAL_ENGINEERING_WORKFLOWS
    for path in paths:
        workflow = load_json(path)
        assert workflow.get("active") is False, path.name


def test_universal_engineering_orchestrator_has_no_service_or_excel_contract() -> None:
    paths = [WORKFLOWS / name for name in UNIVERSAL_ENGINEERING_WORKFLOWS]
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


def test_universal_orchestrator_enterprise_control_plane() -> None:
    workflow = load_json(WORKFLOWS / "universal-engineering-orchestrator.workflow.json")
    nodes = workflow["nodes"]
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
    assert "idempotency_key" not in text
    assert "n8n-nodes-base.wait" not in types
    assert "memoryPostgresChat" not in text

    planner_parser = next(node for node in nodes if node["name"] == "Planner Structured Output")
    planner_schema = json.loads(planner_parser["parameters"]["inputSchema"])
    packet_schema = planner_schema["properties"]["specialist_packet"]
    assert {
        "contract", "contract_version", "specialist_id", "objective", "inputs", "controls",
        "acceptance_criteria", "artifact_refs",
    } == set(packet_schema["required"])
    assert packet_schema["additionalProperties"] is False

    form = next(node for node in nodes if node["name"] == "Engineering task form")
    form_fields = {field["fieldName"] for field in form["parameters"]["formFields"]["values"]}
    assert {"request_text", "request_json", "context_json", "file"} <= form_fields


def test_universal_orchestrator_has_a_static_excel_specialist_route() -> None:
    workflow = load_json(WORKFLOWS / "universal-engineering-orchestrator.workflow.json")
    by_name = {node["name"]: node for node in workflow["nodes"]}
    text = json.dumps(workflow, ensure_ascii=False)

    assert "excel_extraction_specialist" in text
    resolve_code = by_name["Resolve allowlisted specialist"]["parameters"]["jsCode"]
    assert "excel_extraction_specialist:{route:0,configured:true}" in resolve_code
    assert by_name["Configured specialist router"]["parameters"]["numberOutputs"] == 4

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
        "evidence", "self_check", "human_request", "error", "continuation",
    }


def test_universal_engineering_instruction_templates_are_portable() -> None:
    expected = {
        "engineering-task-instruction.template.json",
        "specialist-result-contract.schema.json",
        "orchestrator-instruction.template.md",
        "specialist-workflow-instruction.template.md",
        "generate_universal_engineering_workflows.py",
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
