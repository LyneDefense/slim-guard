FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --no-cache-dir \
    --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
    .

RUN addgroup --system slimguard \
    && adduser --system --ingroup slimguard slimguard \
    && chown -R slimguard:slimguard /app

USER slimguard

EXPOSE 8000

CMD ["uvicorn", "slim_guard.main:app", "--host", "0.0.0.0", "--port", "8000"]
