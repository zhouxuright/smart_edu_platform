# 本地跑通全项目

四端（后端 / Web 前端 / APP 端 PWA / 小程序）在 Windows 本机的启动方式与验收结果。

## 一、服务与端口

| 端 | 地址 | 端口 | 说明 |
|---|---|---|---|
| 后端 API | http://127.0.0.1:8000 | 8000 | FastAPI + Uvicorn，接口文档 `/docs` |
| Web 前端（开发） | http://127.0.0.1:5173 | 5173 | Vite dev server，改代码热更新 |
| APP 端（PWA） | http://127.0.0.1:4173 | 4173 | 生产构建 + preview，只有这里才注册 Service Worker |
| 小程序 | 微信开发者工具 | — | 独立工程 `miniprogram/`，连 8000 的接口 |
| 数据库 | localhost:5432 | 5432 | PostgreSQL 18（服务名 `postgresql-x64-18`），库 `smart_edu` |

前端两个端口都是 **同一份代码**：5173 用于开发调试，4173 用于「装到桌面/手机」的正式形态。不要用 5173 测试安装，dev 模式不注册 SW。

## 二、一键启动

| 脚本 | 作用 |
|---|---|
| `start-all.bat` | 后端 + 前端开发服务（都监听 0.0.0.0，手机可访问） |
| `start-pwa.bat` | 构建 + 启动 PWA 预览（4173） |
| `start-miniprogram.bat` | 检测微信开发者工具并打开工程；未安装则打印安装步骤 |

双击 `start-all.bat` 会弹两个命令行窗口，**这两个窗口不能关**，关掉就等于停服务。

## 三、手动启动（等价命令）

```bash
# 1. 数据库（Windows 服务，通常开机自启）
#    确认：Get-Service postgresql-x64-18

# 2. 后端
cd E:/smart_edu_platform/backend
python -m uvicorn edu_app.main:app --host 0.0.0.0 --port 8000

# 3. 前端（开发）
cd E:/smart_edu_platform/frontend
npm run dev:lan          # = vite --host 0.0.0.0

# 4. APP 端 / PWA
cd E:/smart_edu_platform/frontend
npm run pwa              # = vite build && vite preview --host 0.0.0.0
```

`--host 0.0.0.0` 是为了让手机能通过局域网 IP 访问；只用电脑的话 `npm run dev` 即可（只监听 127.0.0.1，更安全）。

## 四、访问地址

- 本机：http://127.0.0.1:5173
- 手机（同一 WiFi）：http://192.168.1.3:5173
- 接口文档：http://127.0.0.1:8000/docs
- PWA：http://127.0.0.1:4173

本机局域网 IP 用 `ipconfig` 查「IPv4 地址」。IP 由 DHCP 分配，换网络会变。

## 五、演示账号

| 账号 | 密码 | 角色 | 能看到什么 |
|---|---|---|---|
| `demo@example.com` | `Demo1234!` | 学生 | 11 个菜单：计划、导师、简历、投递看板等 |
| `parent@example.com` | `Demo1234!` | 家长 | 3 个菜单：只看进度，没有编辑入口 |
| `admin@example.com` | `Demo1234!` | 管理员 | 10 个菜单，AI 额度不限 |

密码是本机开发库里的演示密码，只用于本地。生产环境必须换掉。

## 六、验收结果（本次实测）

```
后端        GET /docs                          200
前端        http://192.168.1.3:5173 登录        200 → /dashboard，11 个菜单，0 控制台错误
PWA 资源    /manifest.webmanifest /sw.js        200
            /icon-192.png /icon-512.png         200
PWA 代理    POST /api/login（经 4173）           200
PWA 安装    Service Worker                      scope=/ active=activated
PWA 离线    断网 reload                         页面正常渲染（缓存 3 个壳 + 11 个静态资源）
移动端布局  390×844                             底部 5 个标签、无横向溢出
小程序      smoke_resume.js                     11 项断言全绿
```

## 七、APP 端（PWA）的两个前提

**1. 必须用 4173，不能用 5173。**
`main.jsx` 里 `import.meta.env.PROD` 才注册 Service Worker，dev 模式故意不注册——否则改代码后缓存会让人一直看到旧页面，排查起来非常费劲。

**2. 装到手机上需要 HTTPS。**
浏览器规定：Service Worker 只在安全上下文可用（HTTPS，或 localhost）。所以：

- 电脑上 `http://127.0.0.1:4173` → 是安全上下文，地址栏会出现「安装」图标，可以装成桌面应用；
- 手机访问 `http://192.168.1.3:4173` → 不是安全上下文，**页面能用，但不会注册 SW、不能添加到主屏幕**。

要在手机上真正安装，三选一：

```bash
# A. 内网穿透（最省事，需装 cloudflared）
cloudflared tunnel --url http://localhost:4173

# B. 自签证书（手机要手动信任，否则 SW 仍然失败，不推荐）

# C. 部署到有域名的服务器（正式做法）
```

只想在手机上看页面效果，不需要安装，那 `http://192.168.1.3:5173` 就够——移动端布局、底部标签栏、安全区留白都已适配。

## 八、小程序

工程在 `miniprogram/`，独立于前端构建。

1. 安装[微信开发者工具](https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html)（稳定版）；
2. 「导入项目」→ 目录选 `E:\smart_edu_platform\miniprogram`；
3. AppID 选「测试号」（`project.config.json` 里已填 `touristappid`，无需注册）；
4. 「详情 → 本地设置」勾选「不校验合法域名」（`urlCheck` 已置 false，通常无需手动改）；
5. 先跑 `start-all.bat` 把后端起来。

**接口地址**：`miniprogram/utils/request.js` 默认 `http://192.168.1.3:8000/api`。
用局域网 IP 而不是 127.0.0.1，是因为真机上 127.0.0.1 指向手机自己。换网络后 IP 变了不用改代码：

```js
// 开发者工具 Console 里执行，然后重新编译
wx.setStorageSync('qicheng_base_url', 'http://新IP:8000/api')
```

**本机没有微信开发者工具时怎么验证**：`miniprogram/scripts/smoke_resume.js` 用 Node 桩掉 `wx` 和 `Page`，把页面当普通模块加载跑生命周期，请求打真实后端：

```bash
cd backend && python -c "
import requests; print(requests.post('http://127.0.0.1:8000/api/login',
  json={'email':'demo@example.com','password':'Demo1234!'}, timeout=30).json()['access_token'])
" > mp_tok.txt

cd ../miniprogram
MP_TOKEN=$(cat ../backend/mp_tok.txt) node scripts/smoke_resume.js
```

这能验业务逻辑和接口契约，但**验不了真机渲染**——WXML 布局仍需在开发者工具里过一遍。

## 九、常见问题

**端口被占用**
```powershell
Get-NetTCPConnection -LocalPort 8000,5173,4173 -State Listen |
  Select-Object LocalPort, OwningProcess
Stop-Process -Id <PID> -Force
```

**登录 401 / 页面一直转圈**
先看后端窗口有没有报错。数据库没起来时后端会在启动阶段就抛 `connection refused`。

**改了代码但页面没变**
1. 后端没有 `--reload`，改路由/模型后必须重启 uvicorn；
2. Vite dev server 偶尔收不到编辑写入事件，会继续服务旧 transform（表现为新路由 404）。`touch` 一下目标文件或重启 dev server。生产构建不受影响。

**PWA 看到旧页面**
Service Worker 有缓存。DevTools → Application → Service Workers → Unregister，再硬刷新。改了 `sw.js` 记得改里面的 `VERSION`。

**导出 PDF 报 503**
服务器找不到中文字体（`simhei` / `msyh` / `Noto`）。接口会返回中文提示而不是输出乱码，装上任意一款中文字体即可。

**AI 导师报 429**
每日额度用完了（默认 30 次/天，管理员不限）。改 `backend/.env` 的 `LLM_DAILY_QUOTA` 后重启。
