FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
# Lean image: no torch, no sentence-transformers.
RUN pip install "mcp>=1.2,<2" "pydantic>=2.8" "pydantic-settings>=2.4" "boto3>=1.35" "pymongo>=4.10" "httpx>=0.27"
COPY app/__init__.py ./app/__init__.py
COPY app/config.py ./app/config.py
COPY app/mcp_servers ./app/mcp_servers
COPY app/jobs ./app/jobs
COPY app/github_api.py ./app/github_api.py
COPY data/jobs_fallback.json ./data/jobs_fallback.json
COPY app/rag/manifest.py ./app/rag/manifest.py
COPY app/storage ./app/storage
RUN touch app/rag/__init__.py && useradd -m appuser && chown -R appuser /srv
USER appuser
EXPOSE 8001
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD python -c "import socket; socket.create_connection(('127.0.0.1', 8001), 3)" || exit 1
CMD ["python", "-m", "app.mcp_servers.careerpilot_server"]
