# 部署上线

目标：给一个能公网访问的地址，别人打开就能用。

> **结论：一个容器全包。** 前端构建产物和后端打进同一个镜像，
> 同一个 URL 同时提供页面和 API。不需要 Vercel，不需要配 CORS，
> 只需要一个平台账号 + 一个数据库。

推荐组合：**Railway（容器 + 持久卷 + 自带 Postgres）**，约 **$5/月**。

---

## 当前线上实例

| 项 | 值 |
| --- | --- |
| 地址 | <https://qicheng-production.up.railway.app> |
| 平台 | Railway，项目 `qicheng`（`3cbf4947-b10f-4645-9525-e1b153a19006`） |
| 服务 | `qicheng`，单副本，卷 `qicheng-volume` 挂 `/data` |
| 数据库 | 同项目 `Postgres` 服务，`DATABASE_URL=${{Postgres.DATABASE_URL}}` |
| 演示账号 | `admin@example.com` / `parent@example.com`（密码在交付时单独给，不写进仓库） |
| 验收 | `smoke_remote.py` 12/12，`single_service_check.cjs` 10/10 |

重新部署（改了代码之后）：

```bash
git archive HEAD | tar -x -C /tmp/deploy     # 生成干净快照，避开本地 node_modules
cd /tmp/deploy && railway up -d -y -s qicheng
```

> 直接在当前目录 `railway up` 可能报 `拒绝访问 (os error 5)`——
> 本地有被占用的文件（数据库卷、锁文件）时 CLI 索引会失败。用快照目录最稳。

---

## 一、为什么后端上不了 Vercel

不是"配置麻烦"，是这个项目有三处和 Serverless 的模型正面冲突：

| 冲突点 | 代码位置 | 后果 |
| --- | --- | --- |
| 常驻后台任务 | `main.py` 的 lifespan 里三个 `asyncio.create_task`（预热模型、每小时重建 FAISS、每分钟扫计划提醒） | Serverless 函数执行完就冻结，这三个循环要么不跑，要么被反复重启 |
| 本地磁盘 | `services/resume_files.py` 附件、`/static` 图片、`faiss.index` | 函数实例随时销毁，用户上传的简历附件**下次请求就没了** |
| 依赖体积 | `faiss-cpu` + `numpy` + `pymilvus` + `langchain` + `reportlab` | 解压后逼近 Vercel 单函数 250MB 上限，装 torch 直接超 |

理论上可以用 Mangum 把 FastAPI 塞进 Vercel Functions，代价是删掉向量检索、
附件改对象存储、后台任务改 Cron、依赖砍一半——**那是重写，不是部署**。

---

## 二、两种形态

| | 单服务（当前） | 前后端分离 |
| --- | --- | --- |
| 形态 | 一个容器同时给页面和 API | 前端 Vercel + 后端 Railway |
| 账号数 | 2（平台 + 数据库） | 3 |
| CORS | 同源，不用配 | 必须配白名单 |
| 静态资源 | 走容器（无 CDN） | 走 Vercel 全球 CDN |
| 部署配置 | 根目录 `Dockerfile` | `frontend/vercel.json` + `railway.json` |

默认按**单服务**走。前端体积不到 1MB，走不走 CDN 对首屏影响很小，
而少一个服务、少一类跨域问题，对第一次上线更划算。

---

## 三、单服务部署步骤

### 0. 前置

代码已在 GitHub：`zhouxuright/smart_edu_platform`。
Railway 和 Neon 都用 GitHub 账号登录即可，不需要信用卡（Railway 有 30 天 $5 试用）。

### 1. 数据库：用 Railway 自带 Postgres（推荐）

在同一个 Railway 项目里 New → Database → **Add PostgreSQL**，然后把应用服务的
`DATABASE_URL` 设成变量引用：

```
DATABASE_URL = ${{Postgres.DATABASE_URL}}
```

好处是数据库和应用在同一个私有网络里（`postgres.railway.internal:5432`），
不用管 SSL、不用管跨区延迟、也不多一个账号。

> 用 Neon 也可以（见本节末尾的备选说明），但没必要多一个外部依赖。

<details>
<summary>备选：Neon 免费库</summary>

1. <https://neon.tech> 用 GitHub 登录 → Create project → 区域选 **Singapore**。
2. 复制 **Direct connection** 的连接串。

**必须用直连串，不要用带 `-pooler` 的：**

```
postgresql://user:pass@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require        ← 用这个
postgresql://user:pass@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require  ← 不要用
```

原因：`-pooler` 走 PgBouncer 事务池，asyncpg 默认开预编译语句缓存，两者会打架，
表现为偶发的 `prepared statement "__asyncpg_stmt_x__" already exists`。
单实例部署根本用不上连接池。

连接串**原样粘**就行，`config.py` 会自动补 `+asyncpg` 并把 `sslmode` 改写成 `ssl`。

</details>


### 2. Railway：部署容器

1. <https://railway.app> 用 GitHub 登录 → New Project → Deploy from GitHub repo。
2. **Root Directory 留空**（就是仓库根目录）。构建上下文是根目录，`Dockerfile` 在根上。
   > 之前如果按旧文档设过 `backend`，一定要清掉，否则找不到 `frontend/` 导致构建失败。
3. 服务页 → **Volumes** → Add Volume → Mount path 填 **`/data`**。
4. Variables 里加：

| 变量 | 值 |
| --- | --- |
| `DATABASE_URL` | `${{Postgres.DATABASE_URL}}`（Railway 变量引用） |
| `LLM_API_KEY` | 你的 DeepSeek key |
| `LLM_API_BASE` | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | `deepseek-chat` |
| `SECRET_KEY` | 随机 48 字节，见下方命令 |
| `DATA_DIR` | `/data` |
| `CORS_ORIGINS` | `*`（同源，配不配都不影响） |
| `EMBEDDING_PROVIDER` | `local`（镜像没装 torch，会自动降级，见第五节） |
| `DB_ECHO` | `false` |

> **在 Git Bash 里设 `DATA_DIR` 要小心。** MSYS 会把 `/data` 当成路径改写成
> `C:/Program Files/Git/data`，变量就废了。要么加 `MSYS_NO_PATHCONV=1`，
> 要么直接用 PowerShell / 网页控制台设。设完 `railway variables` 核对一眼。

> **不要设 `VITE_API_URL`。** 单服务下前端和 API 同源，前端默认用相对路径 `/api`。
> 设了反而会把绝对地址写死进 JS。

生成 SECRET_KEY：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

5. Settings → Networking → **Generate Domain**，拿到 `xxx.up.railway.app`。

首次构建约 **10-12 分钟**（要装 node_modules 和 Python 依赖）。

### 3. 初始化

**建表不用管**：`main.py` 的 lifespan 里跑 `Base.metadata.create_all`，
服务第一次起来就把 23 张表建好。

**演示账号要手动造一次**（注册接口只会建学生账号，管理员/家长只能脚本建）。

数据库在 Railway 私有网络里，本地连不上，所以直接在容器里跑：

```bash
railway ssh -s <服务名> -- sh -c \
  'DEMO_ADMIN_PASSWORD="换成一个强密码" \
   DEMO_PARENT_PASSWORD="换成一个强密码" \
   python scripts/seed_demo_accounts.py'
```

首次用 `railway ssh` 会提示没有密钥，按顺序做一次即可：

```bash
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""
railway ssh keys add -k id_ed25519        # 传私钥文件名/注释，会自动找同名 .pub
ssh-keyscan ssh.railway.com >> ~/.ssh/known_hosts   # 否则 Host key verification failed
```

脚本幂等，重复跑只更新密码和角色。

### 4. 验收

```bash
cd backend
python scripts/smoke_remote.py https://你的域名 --origin https://你的域名
```

12 项断言，覆盖部署后最常出问题的四类：

| 断言 | 挂了说明什么 |
| --- | --- |
| 根路径 200 | 进程没起来 / 端口没绑对 |
| OpenAPI 75 个接口 | 路由没挂全，通常是启动时 import 报错 |
| CORS 预检放行 | `CORS_ORIGINS` 没填或填错 |
| 注册可用 | 数据库没连上，或建表失败 |
| 带 token 读简历 | 鉴权链路断 |
| `/static` 返回 404 | 正常；返回 500 说明上传目录没建出来 |

再用浏览器打开域名，走一遍：注册 → 登录 → 建简历 → 上传附件 → 让 AI 改一条要点。

想看前端有没有真的渲染出来（而不是只返回了 HTML），跑：

```bash
cd frontend
UI_BASE=https://你的域名 UI_EMAIL=xx UI_PASSWORD=xx node scripts/single_service_check.cjs
```

---

## 四、备选：Render

仓库根目录有 `render.yaml`，走 Blueprint：

1. Render 控制台 → New → **Blueprint** → 选仓库。
2. 按提示填 `DATABASE_URL`、`LLM_API_KEY` 两个 `sync: false` 的变量。
3. `SECRET_KEY` 由 Render 自动生成。

`plan: starter`（$7/月）。想省钱改成 `free`，但**必须同时删掉 `disk:` 那段**，
免费实例不支持持久盘，留着会直接创建失败。

---

## 五、必须知道的三个降级

### 1. RAG 语义检索会降级

镜像默认**不装** `torch + sentence-transformers`（约 2GB，免费档 512MB 内存扛不住）。

`EMBEDDING_PROVIDER=local` 但模型加载不到时会退回**哈希词袋**：
链路不断，但没有语义能力。启动日志会明确写 `semantic: False`。

三种处理：
- **接受降级**，在界面上标明「知识库检索为降级模式」，别装作正常；
- **换远程 embedding**：`EMBEDDING_PROVIDER=openai` + 一个 OpenAI 兼容的
  `/embeddings` 接口（DeepSeek 不支持，可用硅基流动的 BGE-M3）；
- **自己构建重镜像**：`--build-arg WITH_EMBEDDING=true`，镜像涨到约 3.5GB，
  实例内存至少给 2GB。

### 2. 上传的图片是公开的

`/static` 是 `StaticFiles` 裸挂，没有鉴权，拿到 URL 谁都能看。

- 简历附件**不受影响**：存在 `DATA_DIR/storage/resumes`，只走带鉴权的
  `/api/resumes/{id}/attachments/{aid}/download`，不在 `/static` 下。
- `/api/upload/image` 传的图（作业截图、头像）是公开的。

### 3. 没有数据库迁移

建表靠 `create_all`，它只**新建**表，不改已有表结构。

上线后再改模型（加列、改类型），`create_all` 不会生效，服务会报列不存在。
`requirements.txt` 里有 `alembic` 但项目没初始化迁移目录。多人用之前应该补：

```bash
cd backend && alembic init migrations && alembic revision --autogenerate -m "init"
```

### 4. 挂载卷的权限要自己修（踩过）

平台把持久卷挂到 `/data` 时，**挂载点是 root 拥有的**，会盖掉镜像里
`RUN chown -R appuser:appuser /data` 的结果。镜像里如果最后是 `USER appuser`，
启动就会死在 import 阶段：

```
PermissionError: [Errno 13] Permission denied: '/data/uploads'
```

表现是 Railway 里部署状态显示 SUCCESS，但访问域名一律 502。

所以镜像用 `backend/entrypoint.sh` 作入口：以 root 起 → `mkdir -p` 数据目录 →
`chown -R appuser:appuser /data` → `exec gosu appuser uvicorn ...`。
既保住非 root 运行，又不依赖平台挂卷的具体实现。

> 同类问题在 Render / Fly 上一样会出现。判断方法：看运行时日志里有没有
> `PermissionError`，而不是只看平台给的状态颜色。

### 5. 并发上限分布在哪三处

线上不会无限接请求，三个闸门各管一段，都可以用环境变量调：

| 闸门 | 默认值 | 环境变量 | 超了会怎样 |
| --- | --- | --- | --- |
| 用户请求频率 | 120 次/分钟 | `RATE_LIMIT_PER_MINUTE` | 429 + `Retry-After` |
| 登录/注册 | 10 次/分钟 | `RATE_LIMIT_AUTH_PER_MINUTE` | 429（防撞库） |
| 同时在跑的模型调用 | 20 个 | `LLM_MAX_CONCURRENCY` | 排队，超 `LLM_QUEUE_TIMEOUT`（15s）返回 503 |
| 数据库连接 | 10 + 20 溢出 | `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | 等待，超 `DB_POOL_TIMEOUT`（30s）抛错 |

调之前先想清楚瓶颈在哪。这个应用是 I/O 密集型，CPU 不是瓶颈，
**供应商的并发额度通常比服务器先到**——所以 `LLM_MAX_CONCURRENCY` 不要盲目调大，
先确认 DeepSeek 那边的限额。

加副本（`numReplicas > 1`）之前必须先做完无状态化：索引进 pgvector、
定时任务拆 cron、文件进对象存储。否则会出现「副本 A 索引旧、副本 B 索引新」
和「提醒被推送 3 遍」。连接数也要重算：副本数 × 30 必须小于 Postgres 的
`max_connections`。

---

## 六、国内访问

Railway 的默认域名国内访问偏慢但通常可达（不像 Vercel 那样时通时不通）。

如果目标用户主要在**国内**，只有一条稳的路：**域名 + 备案 + 国内云**
（阿里云/腾讯云轻量服务器约 ¥60-100/月）。备案要 2-3 周。

如果只是给老师、面试官、简历上看，Railway 默认域名够用。

---

## 七、成本

| 组件 | 免费档 | 付费起价 | 这个项目实际用量 |
| --- | --- | --- | --- |
| Railway | 30 天 $5 试用 | Hobby $5/月 | 512MB 内存 + 1GB 卷，够 |
| Neon | 0.5GB 存储，永久 | Launch $19/月 | 几十 MB |
| Render | 512MB，休眠，无盘 | Starter $7/月 | — |

**正式跑起来约 $5/月（¥36）。**

---

## 八、上线后立刻做的三件事

1. **换掉演示账号密码**，或直接删掉 `admin@` / `parent@`。
2. **确认 `LLM_DAILY_QUOTA`**（默认 30 次/天/人）。公开上线后有人刷接口就是真金白银。
3. **开 Neon 的自动备份**（免费档有 7 天 PITR），别等数据丢了才想。
