# 项目启动指南

## 1. 环境准备

确保您的电脑已安装以下软件：
- **Python 3.10+**
- **Node.js 18+**
- **微信开发者工具**

## 2. 快速启动 (Windows)

在项目根目录 (`e:\smart_edu_platform`) 下，右键点击 `start_project.ps1` 并选择 "使用 PowerShell 运行"，或者在终端中运行：

```powershell
.\start_project.ps1
```

此脚本会自动打开两个新窗口，分别运行后端和前端服务。

---

## 3. 手动启动步骤

如果脚本无法运行，请按照以下步骤手动启动：

### 3.1 启动后端 (Backend)

1. 打开终端，进入 `backend` 目录：
   ```powershell
   cd backend
   ```
2. 安装依赖 (首次运行)：
   ```powershell
   pip install -r requirements.txt
   ```
3. 启动服务：
   ```powershell
   uvicorn edu_app.main:app --reload --host 0.0.0.0 --port 8000
   ```
   - Swagger 文档: [http://localhost:8000/docs](http://localhost:8000/docs)

### 3.2 启动前端 (Frontend)

1. 打开新终端，进入 `frontend` 目录：
   ```powershell
   cd frontend
   ```
2. 安装依赖 (首次运行)：
   ```powershell
   npm install
   ```
3. 启动开发服务器：
   ```powershell
   npm run dev
   ```
   - 访问地址: [http://localhost:5173](http://localhost:5173)

### 3.3 启动微信小程序 (Mini Program)

1. 打开 **微信开发者工具**。
2. 点击 **"导入项目"** (或加号)。
3. 选择目录: `e:\smart_edu_platform\miniprogram`。
4. **AppID**: 使用您的测试 AppID 或点击 "测试号"。
5. **重要设置**:
   - 点击右上角 "详情" -> "本地设置"。
   - **勾选**: "不校验合法域名、web-view (业务域名)、TLS版本以及HTTPS证书"。
   - 原因: 本地开发使用的是 `http://localhost` 和 `ws://localhost`，微信默认要求 HTTPS。

## 4. 常见问题

- **端口被占用**: 如果 8000 或 5173 端口被占用，请关闭相关进程或修改启动端口。
- **数据库**: 后端默认使用 SQLite (`sql_app.db`)，启动时会自动创建，无需额外配置。
- **AI 功能**: 
  - 默认情况下，如果没有配置 API Key，系统会使用 "本地回退模式" (Fallback)，返回预设的规则回复。
  - 如需真实 AI 回复，请在 `backend/.env` 文件中配置 `LLM_API_KEY`。
