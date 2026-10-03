FROM python:3.12-slim
WORKDIR /app

# poppler-utils: the gse fetcher parses Fannie Mae / Freddie Mac monthly
# summary PDFs with `pdftotext -layout` (pypdf interleaves the side-by-side
# tables and is unusable). Without it the gse job raises a clear error and
# degrades; the dashboard GSE panel shows empty rather than crashing.
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY collector/pyproject.toml /app/collector/pyproject.toml
COPY collector/src /app/collector/src
RUN pip install --no-cache-dir /app/collector

COPY config.yaml /app/config.yaml
COPY ui /app/ui

ENV CONFIG_PATH=/app/config.yaml
ENV DB_PATH=/tmp/bloom.db
ENV SERVE_UI=1
ENV UI_PATH=/app/ui
ENV PORT=10000

EXPOSE 10000
CMD ["python", "-m", "collector.main"]
