FROM python:3.12-slim

ARG MEM0AI_VERSION=2.0.19

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./

# The upstream server requirement is intentionally broad (mem0ai>=...). Pin the
# runtime package here so rebuilding the same deployment cannot silently install
# a different memory engine.
RUN python -m pip install --no-cache-dir \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    -r requirements.txt \
    "mem0ai==${MEM0AI_VERSION}"

# Copy an explicit source allowlist. The uploaded server directory also contains
# its legacy .env and history/history.db; neither may be baked into an image.
COPY alembic.ini ./
COPY alembic ./alembic
COPY routers ./routers
COPY scripts ./scripts
COPY *.py ./

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
