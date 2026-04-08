FROM python:3.12-slim

WORKDIR /app

COPY src/requirements.txt ./src/requirements.txt
RUN pip install --no-cache-dir -r src/requirements.txt

COPY src/ ./src/

ENV NEXO_HOME=/app
ENV NEXO_CODE=/app/src
ENV NEXO_MCP_TRANSPORT=stdio

EXPOSE 8000

ENTRYPOINT ["python", "src/server.py"]
