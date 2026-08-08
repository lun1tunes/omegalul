# Build from the repository root:
# docker build -f context-seeder/Dockerfile -t excel-context-seeder .
FROM python:3.13-slim@sha256:6771159cd4fa5d9bba1258caf0b82e6b73458c694d178ad97c5e925c2d0e1a91

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY context-seeder/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY context-seeder/seed_excel_agent_context.py .
COPY n8n/rag/excel-agent-operating-guide.documents.json .

USER 65532:65532
ENTRYPOINT ["python", "/app/seed_excel_agent_context.py"]
