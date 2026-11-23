FROM python:3.12-slim AS builder

WORKDIR /app
# 使用锁定依赖（跨平台 universal lock，含 hash 无关的精确版本与平台标记）
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir --prefix=/install -r requirements.lock

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY --from=builder /install /usr/local
COPY main.py ./
COPY src ./src
COPY mcp-endpoint-server.cfg ./

RUN mkdir -p logs data \
    && adduser --disabled-password --gecos "" appuser \
    && chown -R appuser:appuser /app
USER appuser

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8004/mcp_endpoint/').read()" || exit 1

EXPOSE 8004
CMD ["python", "main.py"]
