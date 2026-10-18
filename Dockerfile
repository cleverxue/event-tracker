# 多阶段构建（规格第八章）：
#   阶段 1 node:20 构建前端 → 阶段 2 python:3.13-slim 装后端依赖
#   → 复制前端 dist 到 /app/static → uvicorn main:app --host 0.0.0.0 --port 8000
FROM node:20 AS frontend-build

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
# lock 文件不存在时退化为普通 install（单人工具，够用）
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ------------------------------------------------------------------ #
FROM python:3.13-slim AS runtime

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# curl 仅用于容器内健康检查；ta-lib 等编译依赖一律不需要（规格禁 TA-Lib）
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 后端代码 + 前端构建产物（阶段 1 的 dist）
COPY app/ ./app/
COPY main.py ./
COPY --from=frontend-build /build/dist ./static

EXPOSE 8000

# 无认证、内网工具；绑定 0.0.0.0 由 compose 的 127.0.0.1:8000 映射保护
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
