FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY agentic.yaml /app/
COPY src /app/src

RUN pip install --no-cache-dir -e '.[dev]'

CMD ["python", "-m", "agentic_coder.worker"]
