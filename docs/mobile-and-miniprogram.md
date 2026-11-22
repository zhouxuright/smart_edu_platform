# 移动端与小程序

同一套 FastAPI 后端，两种客户端：**PWA**（Web 端直接装到桌面）和**微信小程序**。

## 一、PWA（Web 端）

| 文件 | 作用 |
| --- | --- |
| `frontend/public/manifest.webmanifest` | 名称、图标、`standalone` 启动、快捷方式（计划 / 投递） |
| `frontend/public/sw.js` | 离线壳：静态资源缓存优先，**接口一律走网络** |
| `frontend/public/icon-*.png` | 192 / 512 / maskable |
| `frontend/index.html` | `viewport-fit=cover`、`theme-color`、apple 系列 meta |
| `frontend/src/main.jsx` | 仅生产构建注册 SW |

**接口不缓存**是刻意的：用户看到过期的额度、投递状态或计划进度，
比看到「加载失败」更糟。

**SW 只在 PROD 注册**：开发环境频繁改文件，缓存会让人看到旧页面，排查非常费劲。

### 移动端布局

- `layout.jsx` 增加底部标签栏 `TABS`（student 5 项 / parent 3 项），
  最高频的入口一步可达，其余仍走抽屉。
- 底部内边距用 `env(safe-area-inset-bottom)`，刘海屏不遮挡。
- 窄屏下顶栏只留图标（用户名、角色标签、退出文字隐藏），标题单行省略——
  否则「仪表盘」会被挤成竖排。
- 仪表盘统计卡 `span={8}` → `xs={12} sm={8}`，手机上两列。

## 二、微信小程序

目录：`miniprogram/`

```
app.js / app.json / app.wxss      全局配置与主题（与 Web 同一套浅蓝）
utils/request.js                  请求封装：token 注入、401 重登、错误归一
assets/tab/*.png                  tabBar 图标（81×81，normal / active）
pages/index                       仪表盘：欢迎、统计、计划摘要、投递漏斗、快捷入口
pages/plan                        7 天计划：生成表单、逐条勾选完成
pages/mentor                      AI 导师：生涯/学习双模式、额度提示
pages/resume                      简历：基本信息 + 技能编辑、导出 PDF
pages/applications                投递看板：统计、新增、点条目切状态
pages/login                       邮箱密码登录
```

### 关键差异

**1. 没有 SSE** — 小程序 `wx.request` 不支持流式，导师页改用非流式
`POST /agent/mentor`，拿完整回答后一次性渲染。
Markdown 在小程序里没有渲染器，`mentor.js` 的 `plain()` 做轻量清洗
（去 `**`、`#`、`---`，`- ` 转 `· `，句中 `--` 转 `——`）。

**2. 域名校验** — 本地调试必须在开发者工具里勾选
「详情 → 本地设置 → 不校验合法域名」；上线前把 `utils/request.js` 的
`BASE_URL` 换成已备案的 https 域名。

**3. 状态机跟着后端走** — `applications.js` 的 `NEXT` 与
`backend/edu_app/api/applications.py` 的 `TRANSITIONS` 一一对应，
前端只展示合法的下一步，越级跳转由后端 400 兜底。

**4. 简历编辑范围收敛** — 小程序只改基本信息 + 技能；
教育/实习/项目请到网页端（有实时预览和 AI 润色）。
保存时会先 `GET` 详情再整体回传，**避免 PATCH 把未编辑的板块清空**。

**5. PDF 用 `wx.downloadFile`** — 带 Authorization 头下载到临时目录，
再 `wx.openDocument` 预览，可直接转发或用其他应用打开。

### 结构自检

```bash
cd miniprogram
node --check pages/**/*.js                 # 语法
node -e "JSON.parse(...)"                  # JSON
# tabBar 图标存在性 + 页面四件套 + bindtap 处理函数是否都已定义
```

实测结果：图标、四件套、事件处理函数全部对齐。

## 三、接口契约实测

小程序用到的接口全部对真实后端跑通：

```
POST /login                 200 access_token/role/user_id
GET  /dashboard/summary     200 name, study_hours, achievements, recommendations
GET  /internship-plans/current  200 {plan|null}
GET  /applications          200 {items, status_label}
POST /applications          200 id=3 status=applied
GET  /applications/stats    200 submitted/interview_count/offer_count
GET  /resumes               200 {items}
POST /agent/mentor          200 {response(1317字), source:'llm'}
```

---

## 四、小程序端附件简历

Web 端的附件模块补齐后，小程序也接上了同一套接口，只是交互受平台限制，做法不同。

### 平台差异与对应处理

| 能力 | Web | 小程序 | 处理方式 |
|---|---|---|---|
| 选文件 | `<input type=file multiple>` | `wx.chooseMessageFile`（从聊天记录里选，可多选） | 一次最多 9 个，受剩余额度约束 |
| 批量上传 | 一个请求带多个文件 | `wx.uploadFile` 只支持单文件 | 页面里顺序循环，逐个发，进度显示「正在上传 2/5」 |
| 文件名 | 浏览器自动带原始名 | 服务端拿到的是临时文件名 | 用 `formData.filenames` 传原名，后端按顺序覆盖 |
| 预览原件 | 浏览器原生 PDF 预览 | `wx.downloadFile` + `wx.openDocument` | pdf/doc/docx 走系统预览；txt/md 打不开，改为展示提取出的文字 |
| 文字预览 | 弹窗 | 底部浮层 `scroll-view` | 长简历可滚动，不撑破页面 |

**文件名这一条是必须补的。** `wx.uploadFile` 没有提供指定 multipart 文件名的参数，服务端 `upload.filename` 拿到的是 `tmp_a1b2c3.docx` 这类临时名。不处理的话，附件列表里全是乱码文件名，用户根本认不出哪个是哪个。后端因此加了可选的 `filenames` 字段（JSON 字符串数组，按顺序对齐），只有客户端传了才覆盖，Web 端不传不受影响；传了畸形 JSON 也只记一条 warning，不会 500。

### 接口

复用的就是 Web 那套：

```
POST   /resumes/{id}/attachments                  multipart，字段名 files，可带 filenames
GET    /resumes/{id}/attachments                  列表
GET    /resumes/{id}/attachments/{aid}/text       提取文字
GET    /resumes/{id}/attachments/{aid}/download   原件（带 Authorization 头）
POST   /resumes/{id}/attachments/{aid}/import     解析；?apply=true 才落库
DELETE /resumes/{id}/attachments/{aid}
```

### 一个状态一致性修正

`loadOne()` 里原本是 fire-and-forget 地拉附件列表，导致刚进页面立刻点上传时，`attachments.length` 还是 0，额度判断和「3/20」显示都会短暂不准。改成 `await this.loadAttachments(id)`：多等一个请求，换来页面状态一致。

### 验收方式（本机没有微信开发者工具）

装不了开发者工具，就无法模拟器预览。所以写了 `miniprogram/scripts/smoke_resume.js`：桩掉全局 `wx` 与 `Page`，把 `pages/resume/resume.js` 当普通模块加载，按生命周期逐个调方法，请求打真实 FastAPI。

```
✅ loadList 拿到简历 — id=5
✅ 批量上传 2 个文件 — 求职备忘.txt / 周洋-产品实习.docx
✅ 临时文件名被纠正为原始名
✅ 附件带上了大小/字数摘要
✅ 文本预览 — len=193
✅ 下载并交给系统预览（download → openDocument docx）
✅ 解析确认弹窗 — 姓名：周洋 / 教育 1 段 · 实习 1 段 / 完整度 100%
✅ 导入后回填姓名、完整度 100%
✅ 删除附件
```

布局方面，用 WXSS 转 px 做了一次 375px 宽的近似渲染（`rpx / 2`），确认长文件名不溢出、四个操作平铺不挤压、「无法解析」标签位置正常。这只是布局近似，**不等于真机效果**，上线前仍需在开发者工具里过一遍。

触摸热区也调过：操作文字从 `line-height: 56rpx` 提到 `72rpx`（约 36px），加上内边距接近 44px 的下限。

已知限制：`wx.chooseMessageFile` 只能从聊天记录里选文件，用户需要先把简历发到「文件传输助手」；这是微信的平台限制，不是产品选择。
