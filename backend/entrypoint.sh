#!/bin/sh
# 容器入口。
#
# 为什么需要它：云平台（Railway / Render / Fly）把持久卷挂到 /data 时，
# 挂载点由 root 拥有，会覆盖镜像里 RUN chown 过的权限。若直接以 appuser
# 启动，import 阶段 os.makedirs('/data/uploads') 就 PermissionError 崩掉。
#
# 所以这里以 root 起，先把数据目录建好并交给 appuser，再用 gosu 降权运行
# 真正的进程——既保住非 root 运行，又不依赖平台挂卷的具体实现。
set -e

DATA_ROOT="${DATA_DIR:-/data}"

mkdir -p "$DATA_ROOT/uploads" "$DATA_ROOT/storage/resumes"

# 只修数据目录的属主；-R 是为了让上一轮以 root 写下的文件也能被接管。
# 数据量小（简历 PDF、索引），这点开销可以忽略。
chown -R appuser:appuser "$DATA_ROOT" 2>/dev/null || true

exec gosu appuser uvicorn edu_app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers 1 \
    --proxy-headers \
    --forwarded-allow-ips='*'
