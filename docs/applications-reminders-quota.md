# 投递看板、站内提醒与 AI 配额

三块都是「让产品从能演示变成能运营」的补课。本文记录口径与运维要点。

## 一、投递看板（ATS-lite）

### 为什么做

产品叫「第一份实习」，但用户做完 7 天计划之后产出的东西无处安放。
求职者最痛的也不是「不会写简历」，而是「投了三十家，忘了每家到哪一步」。

### 状态机

```
draft(待投) → applied(已投递) → screening(笔试/筛选) → interview(面试中) → offer
                     ↓                  ↓                    ↓
                  rejected / withdrawn（任意阶段都可进入）
```

`rejected` 和 `withdrawn` 可以回到 `applied`——同一家公司换岗位再投是常见情况。

**刻意不允许 `draft` 直接跳到 `interview`**：中间状态丢了，漏斗就算不出转化率。
真遇到跳过流程的情况，先点「已投递」再改。

状态迁移表在 `edu_app/api/applications.py` 的 `TRANSITIONS`，
前端 `src/pages/Applications.jsx` 的 `NEXT_STATUS` 必须与之保持一致，
否则会出现「下拉里有选项但点了报错」。

### 统计口径

- `submitted` = 除 `draft` 外的全部记录。**草稿不拉低转化率。**
- `interview_rate` = 进入面试或拿到 offer 的数量 / submitted
- `due_followups` = `next_action_at` 已过期且未到终态的数量

### 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/applications` | 列表，支持 `status` 过滤 |
| POST | `/api/applications` | 新建，非 draft 会自动填 `applied_at` |
| PATCH | `/api/applications/{id}` | 更新，含状态流转校验 |
| DELETE | `/api/applications/{id}` | 删除 |
| GET | `/api/applications/stats` | 漏斗与渠道转化 |

所有接口按 `user_id` 隔离，越权返回 404（不暴露记录是否存在）。

## 二、站内提醒

### 补的是什么洞

之前 `plan_reminders` 只有 `enabled` 和 `hour` 两个字段，
`due` 是前端打开页面时用 `now.hour >= hour` 算出来的——
**用户不主动打开页面就永远收不到提醒**，等于没实现。

### 现在的链路

```
reminder_loop（每 10 分钟）
  → 查 enabled 且 hour <= 当前小时的提醒
  → 取该计划第一个未完成任务（全部完成则跳过）
  → 写入 notifications（dedupe_key = plan_reminder:{plan_id}:{日期}）
  → 前端顶栏铃铛轮询未读数（60 秒）
```

### 幂等

靠 `notifications.dedupe_key` 的唯一约束，不靠内存标记。
进程重启、定时任务重跑都不会重复发。并发写入撞唯一约束时吞掉 `IntegrityError`。

### 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/notifications` | 列表，`unread_only` / `limit` |
| GET | `/api/notifications/unread-count` | 未读数，铃铛轮询用 |
| POST | `/api/notifications/{id}/read` | 单条已读 |
| POST | `/api/notifications/read-all` | 全部已读 |

### 手动补发

服务重启后如果当天的提醒还没生成，循环会在 15 秒后自动补扫。
需要立刻触发时：

```bash
cd backend
python -c "
import asyncio, sys; sys.path.insert(0,'.')
from edu_app.services.reminder_scheduler import dispatch_due_reminders
print(asyncio.run(dispatch_due_reminders()))
"
```

## 三、AI 配额

### 补的是什么洞

之前除 `webrtc_agent.py` 里一份内存字典在模拟限流外，其余调模型的接口全部敞开，
拿到 token 就能无限刷，一个脚本能把 DeepSeek 额度跑光。

### 口径

- 按 `(user_id, day)` 计数，存在 `llm_usage` 表，原子自增。
- 管理员不限（`role=admin`），便于运营排查。
- 只有真正触发推理的接口才计数；**检索不计数**（走本地 BGE-M3，无 API 成本）。
- 默认每天 30 次，由 `LLM_DAILY_QUOTA` 配置。

已挂配额的接口：

```
POST /agent/mentor
POST /agent/mentor/stream
POST /agent/collaborative/stream
POST /agent/knowledge/stream
POST /agent/orchestrate
POST /knowledge/extract
POST /internship-plans/generate
```

新增调模型的接口时，在函数签名加 `_quota: bool = Depends(require_llm_quota)`。

### 注意两个实现细节

1. **配额用独立的 `AsyncSessionLocal`，不复用请求的 db。**
   计数需要 commit，共用 session 会让路由里的 ORM 对象过期，
   后续访问属性触发同步 IO，异步上下文里直接抛 `MissingGreenlet`。

2. **额度按请求计，不按成功计。**
   依赖在函数体之前执行，所以参数非法（如空消息）的请求也会扣一次。
   这是为了防刷做的取舍——如果要求「只对成功调用计费」，需要把扣减移到每个函数体内、
   放在参数校验之后，流式接口还要处理生成器中途失败的情况，复杂度不划算。

### 前端

- `GET /api/llm/quota` 返回 `{used, limit, remaining, unlimited}`。
- AI 导师页顶部显示「今日剩余 AI 额度 x/y」。
- 超限返回 429，后端 detail 是中文可读文案，导师页单独处理并原样展示，
  同时把用户输入回填到输入框，方便明天重发。

## 四、回归清单

改完这三块后至少跑一遍：

```bash
cd backend
# 投递状态机 + 越权隔离
python -c "..."   # 见 docs 历史；关键断言：draft→interview 返回 400、B 用户改 A 的记录返回 404

# 提醒幂等
python -c "
import asyncio, sys; sys.path.insert(0,'.')
from edu_app.services.reminder_scheduler import dispatch_due_reminders
print(asyncio.run(dispatch_due_reminders()), asyncio.run(dispatch_due_reminders()))
"   # 第二次必须是 0
```

前端：`node scripts/ui_check.cjs applications mentor`，确认无控制台错误。
