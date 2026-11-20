FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY mcp_bridge.py .

RUN useradd --create-home --shell /usr/sbin/nologin bridge
USER bridge

ENTRYPOINT ["python", "-u", "mcp_bridge.py"]
