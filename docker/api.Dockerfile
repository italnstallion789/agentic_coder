FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY agentic.yaml /app/
COPY src /app/src

RUN pip install --no-cache-dir -e '.[dev]'

CMD ["uvicorn", "agentic_coder.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
