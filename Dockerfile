FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    DAILYTIMES_DB=/data/dailytimes.db
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir ".[relay,llm]" && useradd --system --uid 10001 dailytimes \
    && mkdir /data && chown dailytimes /data

USER dailytimes
VOLUME /data
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8787/healthz')"
CMD ["dailytimes", "serve", "--host", "0.0.0.0", "--port", "8787"]
