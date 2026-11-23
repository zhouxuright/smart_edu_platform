# 单服务镜像：前端构建产物 + FastAPI 后端打进同一个容器。
#
# 一个 URL 同时提供页面和 API，因此：
#   - 不需要 CORS（同源）
#   - 不需要额外托管前端（Vercel 之类）
#   - 只需要部署一个服务、挂一个卷
#
# 构建（在仓库根目录，不是 backend/）：
#   docker build -t qicheng:latest .
#   docker build -t qicheng:latest --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple .

# ---------------------------------------------------------------- 前端
FROM node:20-slim AS web

WORKDIR /web

# 先只拷依赖清单：改业务代码时这一层能命中缓存，不用重装 node_modules
COPY frontend/package.json frontend/package-lock.json ./
# npm 源同理，国内直连 registry.npmjs.org 很慢
ARG NPM_REGISTRY=https://registry.npmjs.org
RUN npm ci --no-audit --no-fund --registry="$NPM_REGISTRY"

COPY frontend/ ./
# 单服务模式下前端与 API 同源，baseURL 走默认的相对路径 /api，
# 不注入 VITE_API_URL —— 注入了反而会把地址写死进 JS
RUN npm run build


# ---------------------------------------------------------------- 后端
FROM python:3.12-slim

# WITH_EMBEDDING=true 时额外装 torch + sentence-transformers 并在构建期下载 BGE-M3。
# 默认 false：RAG 退回哈希词袋（无语义能力），镜像约 700MB。
# 打开后镜像约 3.5GB，运行时内存需求约 2GB。
ARG WITH_EMBEDDING=false

# pip 源。国内直连 pypi.org 常被限到 100-200 kB/s，一次构建要半小时以上。
# 本地构建可以传 --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
# 云平台在海外，用默认源反而更快，所以不写死。
ARG PIP_INDEX_URL=https://pypi.org/simple

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    DATA_DIR=/data \
    FRONTEND_DIST=/app/frontend_dist

WORKDIR /app

# libgomp1 是 faiss / torch 的 OpenMP 运行时，slim 镜像里没有，
# 缺了会在 import faiss 时报 "libgomp.so.1: cannot open shared object file"
# gosu 用于入口脚本里从 root 降权到 appuser（见 entrypoint.sh 的说明）
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl gosu \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements.txt

COPY backend/requirements-embedding.txt ./
RUN if [ "$WITH_EMBEDDING" = "true" ]; then \
        pip install --no-cache-dir -i "$PIP_INDEX_URL" -r requirements-embedding.txt && \
        python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('BAAI/bge-m3')" ; \
    fi

COPY backend/ ./

# 前端产物由上一阶段构建好，直接搬进来
COPY --from=web /web/dist ./frontend_dist

# 运行期数据目录：/data 是要挂持久卷的位置，先建好并交给 appuser。
# 注意：镜像里的 chown 会被平台挂卷覆盖（挂载点是 root 的），
# 所以真正的修权限动作放在 entrypoint.sh 里，以 root 启动后再降权。
RUN mkdir -p /data/uploads /data/storage/resumes /app/.cache/huggingface \
    && useradd -m -u 10001 appuser \
    && chown -R appuser:appuser /data /app

EXPOSE 8000

# 云平台会注入 $PORT，本地不加也能起
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/" || exit 1

# 入口脚本以 root 起：修正 /data 属主 → gosu 降权到 appuser 跑 uvicorn。
# --proxy-headers：Render / Railway / Fly 都在前面挡了一层反代，不加的话
# request.base_url 会是 http://0.0.0.0:8000，上传接口返回的图片地址就是错的。
# 单进程：FAISS 索引常驻内存，后台还有两个循环任务（每小时重建索引、
# 每分钟检查计划提醒），多 worker 会让它们各跑一份。
CMD ["sh", "/app/entrypoint.sh"]
