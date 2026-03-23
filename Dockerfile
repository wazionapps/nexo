FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir fastembed numpy "mcp[cli]"

COPY src/ ./src/

ENV NEXO_HOME=/app

ENTRYPOINT ["python", "src/server.py"]
