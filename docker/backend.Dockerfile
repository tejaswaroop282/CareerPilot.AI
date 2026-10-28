FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/models
WORKDIR /srv
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu torch && pip install -r requirements.txt
# create the user first and download the models as that user: a later "chown -R /models" would copy them into a second layer (+535 MB)
RUN useradd -m appuser && mkdir -p /models /data/chroma && chown appuser /models /data /data/chroma /srv
USER appuser
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-base-en-v1.5')"
COPY --chown=appuser app ./app
COPY --chown=appuser scripts ./scripts
COPY --chown=appuser data ./data
COPY --chown=appuser evals ./evals
COPY --chown=appuser tests ./tests
COPY --chown=appuser pytest.ini .
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 CMD curl -f http://127.0.0.1:8000/health || exit 1
CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
