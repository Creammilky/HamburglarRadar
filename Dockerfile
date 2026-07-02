FROM python:3.12-slim

WORKDIR /app

# 先装依赖（利用层缓存）
COPY pyproject.toml README.md ./
COPY src ./src
COPY config ./config

RUN pip install --no-cache-dir -e .

# SQLite / 缓存 / fastembed 模型持久化目录
ENV TZ=Asia/Singapore \
    SQLITE_PATH=/app/data/research_agent.sqlite3 \
    CACHE_DIR=/app/data/cache \
    FASTEMBED_CACHE_PATH=/app/data/fastembed
VOLUME ["/app/data"]

# 默认启动飞书群聊长连接；晨报作业见 docker-compose 或 README
CMD ["python", "-m", "src.main", "serve", "--mode", "ws"]
