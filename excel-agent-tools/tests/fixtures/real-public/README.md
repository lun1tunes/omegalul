# Real public Excel eval pack

Official workbooks with merged headers, missing values, notes, and many sheets. Used to exercise `Agent — Excel Extractor`, not the synthetic `complex/` corpus.

## Files

Download (not committed):

```bash
python3 excel-agent-tools/tests/fixtures/real-public/download_public_workbooks.py
```

| File | Source |
|---|---|
| `worldbank-cmo-monthly.xlsx` | [World Bank Pink Sheet / CMO monthly](https://www.worldbank.org/en/research/commodity-markets) |
| `ons-hi00-regions.xlsx` | [ONS HI00 regional labour market](https://www.ons.gov.uk/employmentandlabourmarket/peopleinwork/employmentandemployeetypes/datasets/headlinelabourforcesurveyindicatorsforallregionshi00/current) |

EIA well-rate Appendix B was 503 at eval time.

## Run against live n8n

Needs `Agent — Excel Extractor` active and a runtime env file with `excel_webhook_api_key` (never commit it):

```bash
export REAL_PUBLIC_XLSX_DIR=/path/to/downloaded/xlsx
export EXCEL_RUNTIME_ENV=/path/to/runtime.env
export EXCEL_WEBHOOK_URL=http://127.0.0.1:15678/webhook/excel-extract
python3 excel-agent-tools/tests/fixtures/real-public/run_agent_eval.py
```

`queries.json` is the query pack. Queries with `continue_with` answer the deterministic table-selection clarification, then wait for the LLM agent.
