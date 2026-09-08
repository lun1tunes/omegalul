# Lab-only image (compose runs the service from a plain python image with a bind mount).
# Build from the repository root so the shared kit folder is in the context:
#   docker build -f excel-agent-tools/Dockerfile -t excel-tools .
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SESSION_DIR=/data/sessions

WORKDIR /service
RUN addgroup --system app && adduser --system --ingroup app app
# mas_agent_kit is a plain repo folder (not pip-installed); app/__init__.py finds it at /mas-agent-kit.
COPY mas-agent-kit/mas_agent_kit /mas-agent-kit/mas_agent_kit
COPY excel-agent-tools/requirements.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt
COPY excel-agent-tools/app ./app
RUN mkdir -p /data/sessions && chown -R app:app /service /data

USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test
USER root
COPY excel-agent-tools/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
# The image tests only the FastAPI service. Cross-component workflow contracts
# live in tests/test_workflow_contracts.py and run from the repository root,
# because the production image intentionally has no n8n/RAG files.
COPY excel-agent-tools/tests/test_api.py excel-agent-tools/tests/test_complex_corpus.py ./tests/
COPY excel-agent-tools/tests/fixtures ./tests/fixtures
USER app
CMD ["python", "-m", "pytest", "-q", "tests"]
