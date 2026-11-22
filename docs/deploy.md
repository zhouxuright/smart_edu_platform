# 部署上线

目标：给一个能公网访问的地址，别人打开就能用。

先说结论，省得你按错的方案折腾半天：

> **Vercel 能放前端，放不了这个后端。** 后端要换到能跑常驻容器、能挂持久盘的地方。
> 推荐组合：**Vercel（前端）+ Railway（后端）+ Neon（Postgres）**。

---

## 一、为什么后端上不了 Vercel

不是"配置麻烦"，是这个项目有三处和 Serverless 的模型正面冲突：

| 冲突点 | 代码位置 | 后果 |
| --- | --- | --- |
| 常驻后台任务 | `main.py:50/89/95` 三个 `asyncio.create_task`（预热模型、每小时重建 FAISS、每分钟扫计划提醒） | Serverless 函数执行完就冻结，这三个循环要么不跑，要么被反复重启 |
| 本地磁盘 | `services/resume_files.py` 附件、`main.py` 的 `/static` 图片、`faiss.index` | 函数实例随时销毁，用户上传的简历附件**下次请求就没了** |
| 依赖体积 | `faiss-cpu` + `numpy` + `pymilvus` + `langchain` + `reportlab` + `argon2` | 解压后逼近 Vercel 单函数 250MB 上限，装上 torch 直接超 |

理论上可以用 Mangum 把 FastAPI 塞进 Vercel Functions，代价是：删掉向量检索、附件改存对象存储、后台任务改 Cron、依赖砍一半。**那是重写，不是部署。**

---

## 二、方案对比

| 方案 | 前端 | 后端 | 数据库 | 附件能否持久 | 月成本 | 适合 |
| --- | --- | --- | --- | --- | --- | --- |
| **A（推荐）** | Vercel | Railway | Neon | ✅ Railway Volume | ≈ $5（约 ¥36） | 想认真给人用 |
| B | Vercel | Render Starter | Neon | ✅ Render Disk | ≈ $7（约 ¥50） | 习惯 Render 面板 |
| C（纯免费） | Vercel | Render Free | Neon | ❌ 重启即丢 | $0 | 只求"点开能看" |
| D | Vercel | 任意容器 | Neon | ✅ | $0~ | 已有服务器 |

**为什么数据库单独用 Neon**：Render 的免费 Postgres **30 天到期直接删库**，Railway 的库算在 $5 额度里。Neon 免费档 0.5GB 存储、永久有效、不删数据，正好够这个项目。

**为什么不用 Render Free 当正式方案**（方案 C 的代价，必须知道）：
- 512MB 内存，`faiss + langchain` 常驻后余量很少，并发几个请求就吃紧；
- 15 分钟没人访问就休眠，下次访问**冷启动约 1 分钟**（首屏白屏一分钟，很多人以为站挂了）；
- 免费实例**不能挂持久盘**，`/data` 每次重启清空 —— 用户传的简历附件、上传的图片全部消失；
- 每月 5GB 出网流量，图片多了很容易超。

---

## 三、方案 A 的完整步骤

### 0. 前置：代码要有一个 GitHub 仓库

Vercel 和 Railway 都只认 GitHub / GitLab / Bitbucket，**不认 Gitee**。当前 `origin` 指向 Gitee，需要另推一份：

```bash
cd E:/smart_edu_platform

# 在 GitHub 上先建一个空仓库（不要勾 README），然后
git remote add github git@github.com:<你的账号>/qicheng-ai.git
git push github master
```

> 推之前确认 `backend/.env` 没被带上去。仓库里 `.gitignore` 已经排除了 `.env`，可以放心推。
> 推完顺手去 GitHub 仓库页面搜一下 `sk-`，确认没有 key 泄漏。

**不想用 GitHub 也可以**：Vercel 和 Railway 都有 CLI 直传（见第五节），但后面每次改代码都得手动重推，不如 Git 省事。

---

### 1. Neon：建数据库

1. 打开 <https://neon.tech>，用 GitHub 账号登录（免费，不用信用卡）。
2. Create project → 区域选 **Singapore**（离国内和 Railway 新加坡节点都近）。
3. 建好后在 Connection Details 里复制连接串。

**这里有个坑，务必用「Direct connection」那一栏**，不要用带 `-pooler` 的：

```
postgresql://user:pass@ep-xxx.ap-southeast-1.aws.neon.tech/neondb?sslmode=require   ← 用这个
postgresql://user:pass@ep-xxx-pooler.ap-southeast-1.aws.neon.tech/neondb?sslmode=require   ← 不要用
```

原因：`-pooler` 走 PgBouncer 事务池，asyncpg 默认开预编译语句缓存，两者会打架，表现为偶发的 `prepared statement "__asyncpg_stmt_x__" already exists`。单实例部署根本用不上连接池，直连更稳。

连接串**原样粘**就行，`config.py` 会自动把 `postgresql://` 补成 `postgresql+asyncpg://`，并把 `sslmode` 改写成 asyncpg 认的 `ssl`。

---

### 2. Railway：部后端

1. <https://railway.app> 用 GitHub 登录。新账号有 **30 天 $5 试用，不用信用卡**。
2. New Project → Deploy from GitHub repo → 选刚推的仓库。
3. 进服务 → Settings：
   - **Root Directory** 填 `backend`（仓库根目录不是后端，不填会找不到 Dockerfile）
   - Railway 会自动识别 `backend/Dockerfile` 和根目录的 `railway.json`
4. 加持久卷：服务页 → **Volumes** → Add Volume → Mount path 填 **`/data`**。
5. Variables 里逐个加：

| 变量 | 值 |
| --- | --- |
| `DATABASE_URL` | 第 1 步 Neon 的直连串 |
| `LLM_API_KEY` | 你的 DeepSeek key |
| `LLM_API_BASE` | `https://api.deepseek.com/v1` |
| `LLM_MODEL` | `deepseek-chat` |
| `SECRET_KEY` | 随机 48 字节，见下方生成命令 |
| `DATA_DIR` | `/data` |
| `CORS_ORIGINS` | 先留空，等第 4 步拿到 Vercel 域名再填 |
| `EMBEDDING_PROVIDER` | `local`（镜像里没装 torch，会自动降级，见第六节） |
| `DB_ECHO` | `false` |

生成 SECRET_KEY：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

6. Settings → Networking → **Generate Domain**，拿到类似 `qicheng-api-production.up.railway.app` 的地址。

7. 等构建完成（首次约 5-8 分钟，装依赖占大头），然后本地验一下：

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://你的域名/
# 期望 200
```

---

### 3. 初始化：建表 + 演示账号

**建表不用管**：`main.py` 的 `lifespan` 里跑 `Base.metadata.create_all`，服务第一次起来就把 20 多张表建好了。

**演示账号要手动造一次**（注册接口只会建学生账号，管理员/家长只能脚本建）。最省事的办法是在本地直接连线上库：

```bash
cd backend
DATABASE_URL="<Neon 直连串>" \
DEMO_ADMIN_PASSWORD="换成一个强密码" \
DEMO_PARENT_PASSWORD="换成一个强密码" \
python scripts/seed_demo_accounts.py
```

脚本是幂等的，重复跑只会更新密码和角色，不会报错。

> 学生账号不用造，前端注册页直接注册。

---

### 4. Vercel：部前端

1. <https://vercel.com> 用 GitHub 登录。
2. Add New → Project → 选同一个仓库。
3. **Root Directory 改成 `frontend`**（不改的话它会在仓库根找 `package.json`，找不到直接失败）。
4. Framework Preset 会自动识别成 **Vite**，Build Command `npm run build`，Output Directory `dist`，都不用动。
5. Environment Variables 加一条，**这条必须在第一次构建前加**：

| 变量 | 值 |
| --- | --- |
| `VITE_API_URL` | `https://你的Railway域名/api` |

> `VITE_` 前缀的变量是**构建期**写死进 JS 里的，不是运行时读的。
> 地址填错或后改，都必须 **Redeploy** 才生效 —— 改环境变量不会自动重建。

6. Deploy，拿到 `https://qicheng-xxx.vercel.app`。

`frontend/vercel.json` 已经配好了 SPA 回退和 PWA 两个文件的缓存头，不用额外设置。

---

### 5. 回填 CORS（最容易漏的一步）

回到 Railway，把 `CORS_ORIGINS` 改成 Vercel 的域名：

```
https://qicheng-xxx.vercel.app
```

保存后 Railway 会自动重启服务。**不做这一步的后果**：浏览器控制台报 `blocked by CORS policy`，但用 curl 测接口一切正常 —— 这类问题最容易查半天。

有自定义域名就都写上，逗号分隔：

```
https://qicheng.vercel.app,https://qicheng.cn
```

---

### 6. 验收

```bash
cd backend
python scripts/smoke_remote.py https://你的Railway域名 --origin https://你的Vercel域名
```

这个脚本查的就是部署后最常出问题的四类事：

| 断言 | 挂了说明什么 |
| --- | --- |
| 根路径 200 | 进程没起来 / 端口没绑对 |
| OpenAPI 74 个接口 | 路由没挂全，通常是启动时 import 报错 |
| CORS 预检放行 | `CORS_ORIGINS` 没填或填错 |
| 注册可用 | 数据库没连上，或建表失败 |
| 带 token 读简历 | 鉴权链路断 |
| `/static` 返回 404 | 正常；返回 500 说明上传目录没建出来 |

全绿之后，再用浏览器打开 Vercel 域名，走一遍：注册 → 登录 → 建简历 → 上传附件 → 让 AI 改一条要点。

---

## 四、方案 B：用 Render 代替 Railway

仓库根目录已有 `render.yaml`，走 Blueprint 部署：

1. Render 控制台 → New → **Blueprint** → 选仓库。
2. 它会读 `render.yaml` 建出「容器 + 1GB 持久盘」。
3. 创建时按提示填 `DATABASE_URL`、`LLM_API_KEY`、`CORS_ORIGINS` 三个 `sync: false` 的变量。
4. `SECRET_KEY` 由 Render 自动生成，不用管。

`render.yaml` 里 `plan: starter`（$7/月）。想省钱改成 `free`，但**必须同时删掉 `disk:` 那一段**，因为免费实例不支持持久盘，留着会直接创建失败。

---

## 五、不用 GitHub：CLI 直传

```bash
# 前端
npm i -g vercel
cd frontend
vercel login
VITE_API_URL=https://你的后端域名/api vercel --prod

# 后端（Railway）
npm i -g @railway/cli
cd backend
railway login
railway init
railway up
```

CLI 方式的缺点：没有 Git 集成，以后每次改代码都要手动重推，也不会有自动部署。

---

## 六、必须知道的三个降级

### 1. RAG 语义检索会降级

镜像默认**不装** `torch + sentence-transformers`（那俩加起来约 2GB，免费档 512MB 内存也扛不住）。

`EMBEDDING_PROVIDER=local` 但模型加载不到时，会自动退回**哈希词袋**：检索链路不断，但没有语义能力，问"简历怎么写出结果感"可能召回不到相关文档。

三种处理：
- **接受降级**，在界面上标明「知识库检索为降级模式」，别装作正常；
- **换远程 embedding**：`EMBEDDING_PROVIDER=openai` + 一个 OpenAI 兼容的 `/embeddings` 接口（DeepSeek 不支持，可用硅基流动的 BGE-M3）；
- **自己构建重镜像**：`docker build --build-arg WITH_EMBEDDING=true`，镜像涨到约 3.5GB，实例内存至少给 2GB。

### 2. 上传的图片是公开的

`/static` 是 `StaticFiles` 裸挂，没有鉴权，拿到 URL 谁都能看。

- 简历附件**不受影响**：它存在 `DATA_DIR/storage/resumes`，只走带鉴权的 `/api/resumes/{id}/attachments/{aid}/download`，路径不在 `/static` 下。
- 但 `/api/upload/image` 传的图（作业截图、头像等）是公开的。上线前想清楚能不能接受；不能就换成对象存储 + 预签名 URL。

### 3. 没有数据库迁移

建表靠 `create_all`，它只会**新建**表，不会改已有表结构。

上线后如果再改模型（加一列、改类型），`create_all` **不会生效**，服务会报列不存在。`requirements.txt` 里有 `alembic` 但项目没初始化迁移目录。在真正多人用之前应该补上：

```bash
cd backend && alembic init migrations && alembic revision --autogenerate -m "init"
```

---

## 七、国内访问的问题（这条别忽略）

Vercel 的默认域名在国内**不稳定**，时通时不通；Railway 的域名国内访问也偏慢。

"全世界所有人都能看到"如果包含**国内用户**，只有两条路：

1. **域名 + 备案 + 国内云**：阿里云/腾讯云轻量服务器（约 ¥60-100/月）+ 域名备案。备案要 2-3 周，需要身份证和企业/个人主体。
2. **Vercel 绑自定义域名 + 国内 CDN 回源**：能缓解静态资源，但 API 请求还是要跨境，仍然慢。

如果目标用户主要在海外，或者只是给老师/面试官演示，Vercel 默认域名够用。

---

## 八、成本小结

| 组件 | 免费档 | 付费起价 | 这个项目实际会用到 |
| --- | --- | --- | --- |
| Vercel | 100GB 流量/月，永久 | Pro $20/月 | 静态资源，远用不完 |
| Neon | 0.5GB 存储，永久 | Launch $19/月 | 几十 MB |
| Railway | 30 天 $5 试用 | Hobby $5/月 | 512MB 内存 + 1GB 卷，够 |
| Render | 512MB，休眠，无盘 | Starter $7/月 | — |

**正式跑起来约 $5/月（¥36）。** 只求能打开看，方案 C 可以压到 $0，代价是冷启动 1 分钟 + 附件重启即丢。

---

## 九、上线后建议立刻做的三件事

1. **换掉演示账号密码**，或者直接删掉 `admin@` / `parent@`，用真实账号。
2. **确认 `LLM_DAILY_QUOTA`**（默认 30 次/天/人）。公开上线后有人刷接口就是真金白银，按预算调。
3. **开 Neon 的自动备份**（免费档有 7 天 PITR），别等数据丢了才想。
