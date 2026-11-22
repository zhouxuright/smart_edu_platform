# 前端外壳：主题、导航、埋点

说明全局布局这一层的约定。改配色、加页面、看行为数据时先读这一份。

## 一、主题：只有一套浅蓝

历史上并存过三套皮肤：全局深色玻璃拟态、计划页浅蓝、导师页深色面板。跳转时观感割裂，
现在统一到浅蓝。

色板有两处，**必须同值**：

| 位置 | 作用 |
|---|---|
| `src/theme.js` 的 `palette` | 喂给 antd `ConfigProvider`，控制组件默认色 |
| `src/index.css` 的 `:root` 变量 | 给自定义样式和 JSX 内联样式用 |

改色只改这两处，不要去页面里写死 `#fff` 或 `rgba(255,255,255,...)`。
深色主题残留的典型症状是「白字配浅底看不见」，或低透明度白边框在浅色下完全消失。

常用变量：

```
--plan-bg / --plan-surface   页面底色、卡片底色
--plan-ink / --plan-muted    主文字、次要文字
--plan-line                  边框
--plan-accent                主色（按钮、链接、选中态）
```

`src/index.css` 里另有一组旧命名（`--glass-bg`、`--text-primary` 等）保留为别名，
历史页面还在引用，不要直接删。

## 二、导航与角色过滤

菜单配置在 `src/components/layout.jsx` 的 `MENU_ITEMS`，每一项声明 `roles`：

```js
{ key: '/parent', label: '家长门户', roles: ['parent', 'admin'], group: '成长' }
```

**加新页面必须在这里登记**，否则路由能进但导航里没有入口。

角色来自 `localStorage.role`，由登录接口写入（注册接口固定发 `student`）。
`buildMenuItems(role)` 过滤后按 `学习 / 内容 / 成长` 分组，只剩一组时不显示分组标题。

三种角色的实际菜单：

| 角色 | 可见项 |
|---|---|
| student | 9 项（不含家长门户） |
| parent | 3 项（仪表盘、学习评估、家长门户） |
| admin | 10 项（全部） |

页面内部也要跟着角色走。仪表盘已经按角色区分：家长看到「孩子的学习概览」和「打开家长门户」，
不显示「推荐行动」和「记录学习活动」——那两块是给学生本人的。

### 演示账号

注册接口只能创建学生账号，家长和管理员要用脚本造：

```bash
cd backend
DEMO_ADMIN_PASSWORD=xxx DEMO_PARENT_PASSWORD=yyy python scripts/seed_demo_accounts.py
```

不传环境变量时会随机生成密码并打印一次（刻意不在代码里写死默认口令）。
账号是 `admin@example.com` / `parent@example.com`，邮箱域名必须是能通过校验的，
`.local`、`.test` 会被 pydantic 的邮箱校验直接拒掉。

## 三、行为埋点

链路：`src/services/telemetry.js` → `POST /api/events/ui` → `ui_events` 表。

- **页面浏览**由 `src/components/PageViewTracker.jsx` 统一采集，挂在 Router 内。
  放在布局里会漏掉「我的计划」这类不经过布局的独立整页。
- **关键动作**手动调 `track('事件名', { ...附加字段 })`，现有：`rag_search`、`mistake_create`。
- 埋点是 fire-and-forget：失败静默、不 await、不弹错。页面浏览做了 4 秒去重，
  避免 StrictMode 双渲染和快速来回跳转把表刷满。
- 后端有每人每日 500 条上限，超了返回 `recorded: false` 而不是报错。

看数据：

```
GET /api/events/ui/summary?days=7     # 学生只看自己，管理员看全量
```

埋点和 `plan_events` 是两张表，**不要混用**：`plan_events` 是试用漏斗（同一事件每天只记一次），
`ui_events` 是通用行为、不去重。混在一起会让漏斗统计被页面浏览污染。

## 四、本地验收

```bash
cd frontend
node scripts/ui_check.cjs                    # 默认 5 个页面
node scripts/ui_check.cjs dashboard rag      # 指定页面
UI_EMAIL=parent@example.com UI_PASSWORD=xxx node scripts/ui_check.cjs dashboard
```

会登录、逐页截图（`frontend/ui_*.png`，已 gitignore）、打印菜单项、统计「近白文字节点」
（用来发现浅色主题下看不见的白字），最后汇总控制台错误。

## 五、踩过的坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 仪表盘一直转圈 | `didInitRef` 拦住第二次 effect，首次又被 StrictMode 清理函数 abort | 去掉标记，只用 AbortController 保证单请求 |
| 左下角出现黑色块 | antd `Sider` 折叠按钮默认深色底 | 覆盖 `.ant-layout-sider-trigger` |
| 家长看到「生成我的 7 天计划」 | 空状态引导没按角色分支 | 按 `getRole()` 区分文案与动作 |
| 推荐行动全是中学数学物理 | 后端默认画像写死 `高一 / 数学,物理` | 改为 `大三 / 互联网产品,数据分析` |
| 控制台一直报 404 | 缺 favicon.ico | `index.html` 内联 SVG 图标 |
| `bordered` 警告 | antd v5 已废弃 | 改 `variant="borderless"` |
