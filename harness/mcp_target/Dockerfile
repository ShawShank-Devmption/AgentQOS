FROM python:3.11-slim

WORKDIR /app
COPY harness /app/harness
COPY common /app/common
ENV PYTHONPATH=/app
EXPOSE 8080
CMD ["python", "-m", "harness.mcp_target.server", "--port", "8080", "--log-path", "/data/requests.jsonl"]
