# MAS Architecture Overview: Hydrodynamic Orchestrator

This document outlines the architecture of the Multi-Agent System (MAS) designed to automate reservoir engineering workflows (specifically data extraction and tNavigator SCHEDULE generation).

The architecture follows a **Stateful Orchestrator-Workers** pattern with explicit validation and independent verification loops.

## High-Level System Architecture

```mermaid
graph TD
    User([User / Webhook]) --> |Task Input| O["Universal Engineering Orchestrator<br/><small><i>universal-engineering-orchestrator</i></small>"]
    
    subgraph "Core Orchestration & Infra"
        O -.-> |Uses| Tpl["Specialist Template<br/><small><i>engineering-specialist-template</i></small>"]
        O -.-> |Logs| Trace["Trace Writer<br/><small><i>mas-trace-event-writer</i></small>"]
        O -.-> |Shared| AI["AI Components<br/><small><i>ai-components</i></small><br/>[LLM REQUIRED]"]
    end

    subgraph "Excel Subsystem"
        E_Adapt["Excel Specialist Adapter<br/><small><i>excel-engineering-specialist-adapter</i></small>"]
        E_Form["Excel Form Adapter<br/><small><i>excel-extraction-form-adapter</i></small>"]
        
        E_Mas["Excel MAS Orchestrator<br/><small><i>excel-mas-orchestrator</i></small>"]
        E_Agent["Excel Extraction Agent<br/><small><i>excel-extraction-agent</i></small><br/>[LLM REQUIRED]"]
        E_RAG["Excel RAG Ingestion<br/><small><i>excel-rag-ingestion</i></small><br/>[EMBEDDINGS REQUIRED]"]
        
        O --> |Route| E_Adapt
        User_Form([UI Form]) --> E_Form
        E_Adapt --> E_Mas
        E_Form --> E_Mas
        E_Mas --> E_Agent
        E_RAG -.-> |DB| E_Agent
        E_Agent <--> |API| FastAPI["FastAPI (app/main.py)"]
    end

    subgraph "SCHEDULE Subsystem"
        S_K_Ing["Knowledge Ingestion<br/><small><i>tnavigator-schedule-knowledge-ingestion</i></small><br/>[EMBEDDINGS REQUIRED]"]
        S_H_Ret["Hybrid Retrieval<br/><small><i>tnavigator-schedule-hybrid-retrieval</i></small><br/>[EMBEDDINGS REQUIRED]"]
        S_Bld["Schedule Builder pipeline<br/><small><i>tnavigator-schedule-builder</i></small><br/>intake→baseline→plan→render→merge→validate→verify<br/>[LLM REQUIRED]"]
        S_Rel["Accountable release<br/><small><i>orchestrator Apply action and version guard</i></small>"]
        S_K_Ing -.-> |DB| S_H_Ret
        O --> |Route| S_H_Ret
        O --> |Route| S_Bld
        S_H_Ret --> S_Bld
        S_Bld --> S_Rel
    end
```

## Тезисы для презентации: Почему базовые подходы к LLM не работают

Наивный подход "отправь промпт в ChatGPT" неприменим в гидродинамическом моделировании. Наша MAS-архитектура спроектирована специально для решения следующих enterprise-проблем:

### 1. Миф менеджеров: "Зачем сложности? Просто напиши промпт"
* **Миф:** Можно просто загрузить Excel в LLM и попросить её написать файл SCHEDULE.
* **Реальность (Галлюцинации):** LLM — это вероятностные генераторы текста, а не инженеры. Если заставить их считать математику, парсить сложную геометрию или генерировать ключевые слова гидродинамики без жестких ограничений, они начинают придумывать координаты, несуществующие скважины и выдавать невалидный синтаксис.
* **Решение MAS:** Строгое разделение ответственности (Contract-Driven Architecture). LLM-Оркестратор **никогда** не считает математику и не формирует итоговые файлы SCHEDULE. Он делегирует эти задачи изолированным детерминированным микросервисам (FastAPI для Excel и математики), общаясь с ними через строгие JSON-контракты. Независимый LLM-Верификатор проверяет результат перед выдачей готового файла SCHEDULE.

### 2. Миф IT-отдела: "У нас есть парсеры (Docling), мы всё переведем в Markdown"
* **Миф:** У нас уже есть инструменты, переводящие таблицы в текст/Markdown. Давайте скормим этот текст нейросети, и она сама во всем разберется.
* **Реальность (Context Collapse):** Реальный инженерный Excel-файл (например, график бурения) содержит 5000+ строк. Конвертация его в Markdown создает гигантскую "простыню" текста. Загрузка этого текста в LLM переполняет контекстное окно, приводит к критическим провалам памяти модели ("lost-in-the-middle") и сжигает колоссальный бюджет на токены.
* **Решение MAS:** Наш `Excel Extraction Agent` не читает весь файл глазами LLM. Он использует детерминированный Python-код (Pandas через FastAPI), чтобы умно запрашивать схему файла, фильтровать нужные строки и агрегировать данные на стороне сервера. Модель получает только *сухую выжимку* (например: "Скважина А пробурена 12.05"), экономя время, токены и исключая ошибки чтения.

### 3. Миф безопасников: "Нейросеть — это черный ящик"
* **Реальность:** Бизнес не может доверять системе, которая генерирует входные данные для гидродинамики "вслепую", без возможности аудита.
* **Решение MAS:** Прозрачность и Human-In-The-Loop (HITL). Оркестратор останавливает работу и запрашивает явное подтверждение человека перед любыми необратимыми или критическими действиями. Каждое решение LLM, её логика рассуждений и история вызовов тулов записываются модулем `Trace Event Writer` в базу данных. Это обеспечивает полный лог аудита: мы всегда знаем, *почему* ИИ сгенерировал конкретное ключевое слово или строку в файле SCHEDULE.

## Component Breakdown

### 1. Universal Engineering Orchestrator
The central nervous system of the MAS. It maintains state across asynchronous calls (HITL - Human in the loop) and dictates the workflow.
*   **Planner:** An LLM responsible for analyzing the current state and deciding which Specialist to invoke next.
*   **Independent Verifier:** A separate LLM that evaluates the output of a Specialist against the original request. If the output is hallucinated or incorrect, the Verifier forces the Planner to replan.
*   **Router:** Executes specific sub-workflows (`Execute Workflow` nodes) based on the Planner's decision. Uses placeholder IDs (e.g., `REPLACE_EXCEL_ADAPTER_IN_UI`) that must be mapped to actual workflow IDs during deployment.

### 2. Excel Subsystem (Data Extraction)
Designed to handle unstructured, "dirty" engineering data.
*   **Adapters:** `excel-engineering-specialist-adapter` translates the Orchestrator's standard contract into the specific prompt required by the Excel Agent.
*   **Excel Extraction Agent (`excel-extraction-agent`):** The core ReAct agent utilizing a 9B model for reasoning.
*   **FastAPI Backend (`excel-agent-tools`):** The python microservice executed outside n8n. It provides deterministic tools (`/schema`, `/extract`) to parse Excel files, handling locking and session states.

### 3. SCHEDULE Subsystem (Code Generation)
A highly specialized RAG-driven code generation pipeline with three importable workflows.
*   **Knowledge Ingestion / Hybrid Retrieval:** expert keyword instructions and schema catalogue into PostgreSQL/PGVector, then RRF retrieval.
*   **Builder pipeline (`tnavigator-schedule-builder`):** single specialist that owns intake, baseline analyze/decode/query, planner, typed-IR render, merge, validate and independent verify as Code stages.
*   **Release:** accountable human gate lives in Universal Orchestrator (`Apply action and version guard`), not a separate SCHEDULE workflow.

### 4. Infrastructure
*   **Trace Event Writer (`mas-trace-event-writer`):** Logs state transitions and LLM reasoning steps for debugging and audit trails.

## Deployment Guide: Step-by-Step Setup

Follow this sequence to deploy the MAS architecture into your corporate n8n instance. 

### Step 1: Subsystems & Adapters (Import First)
Import all specialist and infrastructure workflows so they generate internal IDs.
**Excel Subsystem:**
- `excel-engineering-specialist-adapter`
- `excel-extraction-form-adapter`
- `excel-mas-orchestrator`
- `excel-extraction-agent`
- `excel-rag-ingestion`

**SCHEDULE Subsystem:**
- `tnavigator-schedule-knowledge-ingestion`
- `tnavigator-schedule-hybrid-retrieval`
- `tnavigator-schedule-builder`

**Math / Infra:**
- `calculation-specialist-adapter`
- `mas-trace-event-writer`
- `ai-components`
- `engineering-specialist-template`

**User-facing forms (native n8n 2.30.8):**
- `mvp-entry-form` — start a task; completion HTML explains success or human_gate
- `mas-human-gate-form` — resume HITL with Task ID + answer; `expected_version` / `gate_id` are bound from Orchestrator status automatically
- `mas-deployment-health-check` — press-and-read readiness report (`where_to_fix` for every FAIL)

**Corporate UI deploy from scratch:** [`docs/DEPLOY_N8N_UI.md`](../DEPLOY_N8N_UI.md) (import order, Data Table click-paths, Execute Workflow bindings, CSV templates under `n8n/data-tables/`).

*Note: After importing these, bind Execute Workflow and Data Table nodes using the tables in DEPLOY_N8N_UI.md — do not scroll the picker guessing names. Run Health Check before activating Entry/Human Gate.*

### Step 2: Bind Execute Workflow nodes (not one router node)
1. Import `universal-engineering-orchestrator.workflow.json` (and the rest per `docs/DEPLOY_N8N_UI.md`).
2. Bind **each** `Call …` Execute Workflow node individually — there is no single picker on `Route invocation action` (that node is Code routing only).
3. Use the exact owner → node → target table in [`docs/DEPLOY_N8N_UI.md`](../DEPLOY_N8N_UI.md) Step 4 (Orchestrator Call Excel / Retrieval / Builder / Calculation / Trace; Entry; Human Gate; Health Check).

### Step 3: Service URLs (UI-only — no Global Variables / `$env`)
Do **not** use n8n Variables / `$vars` / `$env`. Set URLs in visible workflow config nodes:
- **Excel Agent** → `Runtime configuration`: `excel_tools_url` = `http://<windows-ip>:8000/api/v1` + API key
- **Calculation Adapter** → `Math Service Configuration`: `math_service_url` = `http://<windows-ip>:8100/api/v1/math`

### Step 4: Infrastructure & Credentials
Within the n8n UI, ensure you have configured:
- **PostgreSQL / PGVector Credential:** For State/Memory tracking and Knowledge Retrieval.
- **LLM Credentials:** Set up your chosen models (e.g., GPT/Claude) for the Planner, Verifier, and Sub-agents.

### Step 5: Start the Microservices (Windows PC)
Open two separate terminals on the machine where the Python services reside and start both:

**Terminal 1 (Excel Parser):**
```bash
cd ~/omegalul/excel-agent-tools
# prefer start-windows.bat; or:
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 (Math Service):**
```bash
cd ~/omegalul/fastapi-math-service
python -m uvicorn app.main:app --host 0.0.0.0 --port 8100 --reload
```

### Step 6: Test the Entry Form
1. Go to your n8n Workflows.
2. Open `mvp-entry-form` and click **"Test Workflow"** (or use the production Webhook URL).
3. Fill out the "Task Description" and attach your files (`.xlsx`, `.dev`, `.cps3`, etc.).
4. Click Submit and watch the Orchestrator route your task!