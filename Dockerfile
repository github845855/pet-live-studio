FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8775

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY core ./core
COPY adapters ./adapters
COPY vendor ./vendor
COPY web ./web
COPY webm ./webm

RUN mkdir -p /app/runtime \
    && chown -R 1000:100 /app/runtime

USER 1000:100

EXPOSE 8775

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8775/health', timeout=3)"

CMD ["python", "app.py"]
