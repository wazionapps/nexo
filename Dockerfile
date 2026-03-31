FROM python:3.12-slim

WORKDIR /app

COPY src/requirements.txt ./src/requirements.txt
RUN pip install --no-cache-dir -r src/requirements.txt

COPY src/ ./src/

ENV NEXO_HOME=/app

ENTRYPOINT ["python", "src/server.py"]
