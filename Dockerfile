# Runs the aiwatch dashboard over a mounted database:
#   docker build -t aiwatch . && docker run --rm -p 127.0.0.1:8765:8765 -v "$PWD/.aiwatch:/data" aiwatch
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev
ENV AIWATCH_DB=/data/aiwatch.db
EXPOSE 8765
# Inside the container the server must listen on all interfaces; publish it to 127.0.0.1 on the host.
CMD ["uv", "run", "--no-dev", "python", "-c", "import uvicorn; from pathlib import Path; from aiwatch.dashboard import create_app; uvicorn.run(create_app(Path('/data/aiwatch.db')), host='0.0.0.0', port=8765)"]
