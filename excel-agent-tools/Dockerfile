FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SESSION_DIR=/data/sessions

WORKDIR /service
RUN addgroup --system app && adduser --system --ingroup app app
COPY requirements.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt \
    && rm -rf /root/.cache
COPY app ./app
RUN mkdir -p /data/sessions && chown -R app:app /service /data

USER app
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

FROM runtime AS test
USER root
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt
# The image tests only the FastAPI service. Cross-component workflow contracts
# live in tests/test_workflow_contracts.py and run from the repository root,
# because the production image intentionally has no n8n/RAG files.
COPY tests/test_api.py tests/test_complex_corpus.py ./tests/
COPY tests/fixtures ./tests/fixtures
USER app
CMD ["python", "-m", "pytest", "-q", "tests"]
