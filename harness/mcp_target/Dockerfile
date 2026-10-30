FROM python:3.11-slim
WORKDIR /app
COPY harness/mcp_target /app/harness/mcp_target
RUN mkdir /logs && chown 65534:65534 /logs
USER 65534:65534
EXPOSE 8080
CMD ["python", "-m", "harness.mcp_target.server", "--host", "0.0.0.0", "--port", "8080", "--log", "/logs/requests.jsonl"]
