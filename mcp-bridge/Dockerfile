# Keep the minor version pinned; pin the digest before a production release:
#   FROM python:3.12-slim@sha256:<digest>
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY mcp_bridge.py healthcheck.py ./

RUN useradd --create-home --shell /usr/sbin/nologin bridge
USER bridge

# The bridge is an outbound WebSocket client with no listening port, so the only
# meaningful in-container liveness signal is that its process is still running.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "healthcheck.py"]

ENTRYPOINT ["python", "-u", "mcp_bridge.py"]
