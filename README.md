# 启程 AI · 智能教育个性化学习平台

面向高校学生的求职与学习平台：简历工作台、投递看板、实习计划、导师对话、知识库检索，
并在一套统一的 Agent 框架下把这些页面的能力接给对话助手。

> 这是一个**在开发中的项目**，不是成品。哪些能真用、哪些还没做完，见文末「当前状态」。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 前端 | React + Vite + Ant Design 5，PWA（Service Worker 离线壳） |
| 后端 | FastAPI + SQLAlchemy 2（async）+ PostgreSQL，单进程 + 常驻后台任务 |
| 向量检索 | FAISS + BGE-M3（可切 OpenAI 兼容 embedding 接口，加载不到时降级为哈希词袋） |
| 大模型 | 任意 OpenAI 兼容接口（开发时用 DeepSeek） |
| 小程序 | 微信原生小程序（`miniprogram/`） |

## 目录结构

```
Dockerfile        单服务镜像：前端构建 + 后端运行打成一个容器
backend/          FastAPI 服务
  edu_app/
    api/          路由层（endpoints / resumes / applications / internship_plans / agent_chat ...）
    agents/       Agent 工具注册表（按页面授权，读写分离）
    models/       SQLAlchemy 模型
    services/     领域逻辑（简历存储、计划存储、向量化、额度、鉴权）
  scripts/        验收脚本与运维脚本
frontend/         React 前端
  src/components/AgentDock.jsx   全局对话助手 + 提案确认卡片
  scripts/        Puppeteer 端到端验收
miniprogram/      微信小程序
docs/             设计与运维文档
deploy/           nginx 反代配置
```

## 本地启动

需要 Python 3.10+、Node.js 18+、PostgreSQL。

```bash
# 1. 配置后端环境变量
cp backend/.env.example backend/.env
#    填 DATABASE_URL / LLM_API_KEY / SECRET_KEY 三项

# 2. 后端
cd backend
pip install -r requirements.txt
python -m uvicorn edu_app.main:app --reload --port 8000

# 3. 前端
cd frontend
npm install
npm run dev
```

完整步骤（含演示账号、小程序、PWA）见 [`docs/run-local.md`](docs/run-local.md)。

## 部署

**一个容器全包**：前端构建产物和后端打进同一个镜像，同一个 URL 同时提供页面和 API。
不需要单独托管前端，也不存在跨域问题。

后端不能放 Vercel——项目有常驻后台任务、写本地盘、依赖体积超函数上限。
推荐 **Railway（容器 + 持久卷）+ Neon（Postgres）**，约 $5/月。
步骤和踩坑见 [`docs/deploy.md`](docs/deploy.md)。

```bash
# 本地验证镜像（在仓库根目录，不是 backend/）
docker build -t qicheng:latest .
docker run -p 8000:8000 --env-file backend/.env -e DATA_DIR=/data -v "$PWD/data:/data" qicheng:latest
```

部署后跑一遍冒烟：

```bash
python backend/scripts/smoke_remote.py https://你的域名 --origin https://你的域名
```

## Agent 框架

对话助手不是每个页面塞一个聊天框，而是**一个全局 dock + 页面上下文注入**：

- 工具按 `pages` 授权，简历页的会话拿不到投递页的写工具；
- **读工具由服务端先执行**，模型不申请读权限，读不到别人或别的页面的数据；
- **写工具只出提案**，用户点「应用」才落库，执行前重新校验一次；
- 提案卡片按类型渲染 diff（改要点 / 整段润色 / 新增条目 / 状态流转）。

8 个工具、4 种卡片、3 个页面已接。细节见 [`docs/agent-dock.md`](docs/agent-dock.md)。

## 文档

| 文档 | 内容 |
| --- | --- |
| [`docs/run-local.md`](docs/run-local.md) | 四端本地启动 |
| [`docs/deploy.md`](docs/deploy.md) | 上线部署与成本 |
| [`docs/agent-dock.md`](docs/agent-dock.md) | Agent 框架与工具表 |
| [`docs/resume-workbench.md`](docs/resume-workbench.md) | 简历工作台（含附件上传/解析） |
| [`docs/mobile-and-miniprogram.md`](docs/mobile-and-miniprogram.md) | 移动端适配与小程序 |
| [`docs/rag-retrieval-ops.md`](docs/rag-retrieval-ops.md) | 检索链路与运维 |

## 当前状态

**能真用的**：注册登录、简历编辑与版本、附件上传（单传/批量/去重/AI 解析导入）、
投递看板与状态机、实习计划、Agent 改写简历与推进状态、小程序端简历与附件。

**还没做完的**：

- 无流式输出，长回答等 3-8 秒；
- Agent 只有单轮工具调用，不能「读完发现不够再读一次」；
- 计划页、仪表盘只有问答，没有写工具；
- 提案表只增不删，没有过期清理任务；
- 没有数据库迁移（靠 `create_all` 建表，改模型不会自动同步）；
- 小程序端没有 Agent dock。

## 许可

仓库暂未声明开源许可。未加许可前默认保留所有权利，如需引用请先联系作者。
