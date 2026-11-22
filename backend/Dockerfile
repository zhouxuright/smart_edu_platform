# 后端镜像。单进程启动，因为 FAISS 索引常驻内存、后台还有两个循环任务
# （每小时重建索引、每分钟检查计划提醒），多 worker 会让它们各跑一份。
FROM python:3.12-slim

# WITH_EMBEDDING=true 时额外装 torch + sentence-transformers 并在构建期下载 BGE-M3。
# 默认 false：镜像 ~600MB，RAG 退回哈希词袋（无语义能力）。
# 打开后镜像 ~3.5GB，运行时内存需求约 2GB。
ARG WITH_EMBEDDING=false

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    DATA_DIR=/data

WORKDIR /app

# libgomp1 是 faiss / torch 的 OpenMP 运行时，slim 镜像里没有，
# 缺了会在 import faiss 时报 "libgomp.so.1: cannot open shared object file"
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY requirements-embedding.txt ./
RUN if [ "$WITH_EMBEDDING" = "true" ]; then \
        pip install --no-cache-dir -r requirements-embedding.txt && \
        python -c "from sentence_transformers import SentenceTransformer; \
SentenceTransformer('BAAI/bge-m3')" ; \
    fi

COPY . .

# 运行期数据目录：上传的图片挂 /static，简历附件走鉴权接口
# /data 是要挂持久卷的位置，先建好并交给 appuser，否则非 root 挂载后写不进去
RUN mkdir -p /data/uploads /data/storage/resumes /app/uploads /app/.cache/huggingface \
    && useradd -m -u 10001 appuser \
    && chown -R appuser:appuser /data /app
USER appuser

EXPOSE 8000

# 云平台会注入 $PORT，本地不加也能起
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/" || exit 1

# --proxy-headers：Render / Railway / Fly 都在前面挡了一层反代，不加的话
# request.base_url 会是 http://0.0.0.0:8000，上传接口返回的图片地址就是错的
CMD ["sh", "-c", "uvicorn edu_app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips=*"]
