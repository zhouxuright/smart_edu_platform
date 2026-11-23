# 页面级 Agent（启程助手）

给「AI 智能对话」做 Agent 化时的落地说明。这里记的不是设想，是已经跑起来的那一套。

## 一、为什么是「全局 dock + 页面上下文」，不是每页一个聊天框

对 12 个内容页做过一轮盘点，结论是：**能对话的页面只有 1 个（AI 学习导师），但值得 Agent 化的有 2 个（简历工作台、投递看板）**。
如果每页塞一个聊天框，会出现三个问题：

1. 落地页、登录页、仪表盘这些页面没有可操作对象，聊天框只能尬聊；
2. 同一套对话能力复制 12 份，改一处要改 12 处；
3. 用户不知道「该问哪个框」。

所以做成：**一个全局悬浮入口，跟着路由切换上下文**。页面不实现对话，只登记「我现在在编辑哪个对象」，
Agent 自己去查这个对象的数据。

## 二、能力分三级，本项目做到 L2

| 级别 | 能力 | 现状 |
| --- | --- | --- |
| L1 | 纯问答，模型不知道页面数据 | 所有页面都有（兜底） |
| L2 | 读该页数据 + 调该页写接口 | 简历工作台（4 个工具）、投递看板（2）、我的计划（2）、错题本（3）、家长门户（1，只读）、知识图谱（1，只读）、仪表盘（1，只读跨页聚合）已接 |
| L3 | 主动发起（定时巡检、主动提醒） | 未做 |

没接数据的页面（学习评估、企业知识库、知识抽取、协作学习）会明确提示「这个页面还没接入数据」，而不是让模型编。

### 仪表盘的跨页聚合

`get_next_action` 和别的读工具不一样：它不读「这一页」的数据，而是把计划、错题本、
投递、薄弱点四处合成一个结论——现在最该做的一件事。优先级写死在常量里
（计划到期 90 > 错题该复习 80 > 投递停滞 70 > 薄弱知识点 60 > 计划待办 50），
不让模型自己排：同一份数据问两次必须给出同一个答案。

两处刻意的取舍：

- **不复用 `/dashboard/summary`**。那个接口内部会再调一次模型生成建议，
  而读工具是在调主模型之前跑的——套进去就是一次对话两次模型调用，
  延迟和配额都翻倍，还会把另一个模型的结论当成事实喂给主模型。
- **区分「什么都还没做」和「该做的都做完了」**。后者返回 `all_clear: true`，
  提示语说的是完成状态，不能说成没数据。


## 三、一次对话的完整链路

```
前端 dock
  │  POST /api/agent/chat {page, page_id, message, history}
  ▼
服务端
  ├─ 1. 按 page 取「读工具」，自己跑一遍（模型不参与，也不申请权限）
  ├─ 2. 把读到的数据 + 该页可用的写工具清单塞进 system prompt
  ├─ 3. 模型一次性返回 {"reply": "...", "action": null | {tool, args}}
  ├─ 4. 有 action 就校验（归属、参数、状态机）→ 落一条 AgentProposal（pending）
  ▼
前端渲染 reply + 提案卡片（改前 / 改后 对比）
  │  用户点「应用」
  ▼
  POST /api/agent/chat/confirm {proposal_id, accept}
  ├─ 校验提案归属、状态、TTL（30 分钟）
  ├─ 重新跑一次 validate（生成到确认之间数据可能已经变了）
  ├─ 执行 → 写库 → 提案置 applied
  ▼
前端广播 qicheng:agent-applied → 页面自己重拉数据
```

### 为什么不做逐字流式

回答和动作必须在同一个 JSON 里返回。如果先流式吐文字、再解析动作，会出现「用户已经看到改好的措辞，动作却被校验拒绝」的割裂。
所以是一次性返回，前端用 loading 态过渡。要流式得等升级成原生 function calling。

### 为什么不用 OpenAI 原生工具调用跑多轮

「改一条要点」「推进一个状态」都是原子动作，一次规划 + 一次校验足够。
原生工具循环要处理流式工具增量、并行调用、参数截断，出错时的表现很难向用户解释。
注册表保留了完整的工具声明，后续要升级，只改调度层，工具本身不动。

## 四、代码位置

| 文件 | 职责 |
| --- | --- |
| `backend/edu_app/agents/tools.py` | 工具注册表：声明、读、校验、预览、执行 |
| `backend/edu_app/api/agent_chat.py` | 三个接口：`/agent/chat`、`/agent/chat/confirm`、`/agent/chat/tools` |
| `backend/edu_app/models/agent.py` | `AgentProposal` 表（uuid 主键，pending/applied/rejected/expired） |
| `backend/edu_app/services/plan_store.py` | 计划读写与漏斗埋点的唯一实现，HTTP 接口与 Agent 工具共用 |
| `frontend/src/services/agentPage.js` | 页面上下文总线（登记 page_id、广播执行完成） |
| `frontend/src/components/AgentDock.jsx` | 悬浮入口 + 对话面板 + 提案卡片 |
| `frontend/src/services/api.js` | `pageAgentService`（注意：不是原有的 `agentService`） |

### 一个容易踩的坑：计划页是独立路由

`/plan/internship` 不在 `MainLayout` 里（它有自己的全页布局），所以布局层的 dock 挂不到它。
这一页在 `InternshipPlan.jsx` 里自己渲染 `<AgentDock standalone />`，`standalone` 只做一件事：
手机端不再为底部标签栏让位（`bottom: 78px` → `16px`）。

新增独立路由页面时记得同样处理，否则「全局 dock」在那页会静默消失。

## 五、加一个新工具要改哪里

以「给简历加一段项目经历」为例，只在 `tools.py` 里加一个 `ToolSpec` 就够了：

```python
async def _validate_add_project(db, user, args):
    resume = await _load_resume(db, user, args.get("resume_id"))
    if not resume:
        raise ToolError("找不到这份简历")
    return {"resume": resume, "item": {...}}   # 上下文，只在内存里用

def _preview_add_project(ctx):
    return {"kind": "bullets", "title": f"新增项目「{ctx['item']['name']}」",
            "before": [], "after": ctx["item"]["bullets"]}

async def _apply_add_project(db, user, args):
    ctx = await _validate_add_project(db, user, args)   # 确认时再校验一次
    ...
    await snapshot(db, resume, "AI 新增项目")

register(ToolSpec(
    name="add_project", kind="write", pages=("resume",),
    description="给简历新增一段项目经历",
    params='{"resume_id":1,"name":"...","bullets":["..."]}',
    validate=_validate_add_project, preview=_preview_add_project, apply=_apply_add_project,
))
```

三条硬约束：

1. **`pages` 必须写**。写工具只对列出的页面开放，简历页的会话拿不到投递页的工具。
2. **`validate` 要查归属**。所有查询都要带 `user_id == user.id`，不能只按 id 查。
3. **`apply` 里重新 `validate`**。从生成提案到用户点确认之间，数据可能已经变了。

## 六、安全边界

| 风险 | 处理 |
| --- | --- |
| 模型越权改别人数据 | 每个工具内部按 `user.id` 过滤；`page_id` 只是「建议改哪个」，不是权限凭据 |
| 模型自作主张改数据 | 写工具不直接执行，只产出提案；`confirm` 才落库 |
| 跨页调用 | `ToolSpec.pages` 白名单，`_create_proposal` 里二次检查 |
| 提案被重放 | 状态机 `pending → applied/rejected/expired`，非 pending 一律 409 |
| 陈旧的提案 | TTL 30 分钟，过期置 `expired` 并返回 409 |
| 额度滥用 | `/agent/chat` 挂 `require_llm_quota`，超限 429 |
| 改动无法回退 | 写工具走 `resume_store.snapshot`，和手动编辑同一套版本机制 |

## 七、已接的工具

| 工具 | 类型 | 页面 | 说明 |
| --- | --- | --- | --- |
| `get_resume` | read | 简历工作台 | 读基本信息、教育、实习、项目、技能、荣誉，每段带 index |
| `update_bullets` | write | 简历工作台 | 改写某段实习/项目的要点列表 |
| `add_project` | write | 简历工作台 | 新增一段项目经历（上限 10 段，和 `_normalize` 对齐） |
| `polish_section` | write | 简历工作台 | 整块润色：`summary`（传 `text`）/ `skills`、`awards`（传 `items`） |
| `list_applications` | read | 投递看板 | 读最近 10 条投递，带 `can_move_to` 供模型判断 |
| `advance_application` | write | 投递看板 | 推进状态，只能走状态机允许的路径 |
| `get_plan` | read | 我的计划 | 读目标岗位、阶段、进度、7 天任务、提醒设置 |
| `toggle_task` | write | 我的计划 | 勾选/取消某一天的任务 |

### 提案卡片的四种形态

`preview.kind` 决定卡片怎么画，新增 kind 要在 `AgentDock.jsx` 的 `PreviewBody` 里补渲染：

| kind | 用于 | 长什么样 |
| --- | --- | --- |
| `bullets` | 改要点、润色技能/荣誉 | 改前（划线）/ 改后（高亮）两段列表 |
| `text` | 润色一句话自我介绍 | 改前（划线段落）/ 改后（高亮段落） |
| `item` | 新增项目 | 角色 · 时间 + 即将写入的要点 |
| `status` | 推进投递、勾选任务 | 旧状态 → 新状态 两个 chip |

### 为什么 `polish_section` 不开放公司/学校/时间

事实性字段（公司名、任职时间、学校、GPA）不交给模型改。模型改写这类字段时很容易顺手「补」一个
看起来合理的日期或职级，而这种错误在简历上代价最高。`polish_section` 只开放整段叙述类和列表类字段。

## 八、验收

后端（需先启动在 8000）：

```bash
cd backend
python scripts/agent_tools_unit.py          # 工具直接验收（不过模型），27 项
python scripts/agent_e2e.py                 # 简历工具链，20 项
python scripts/agent_e2e_applications.py    # 投递工具链，10 项
python scripts/agent_e2e_plan.py            # 计划工具链 + 漏斗埋点，18 项
```

`agent_tools_unit.py` 直接打注册表的 validate/preview/apply，覆盖各种拒绝路径（越权 id、类型错、
重复设置同一状态、超上限），跑完会把演示简历与临时计划还原，可以反复执行。

前端（需先启动在 5173）：

```bash
cd frontend
UI_EMAIL=demo@example.com UI_PASSWORD='Demo1234!' node scripts/agent_dock_check.cjs
UI_EMAIL=demo@example.com UI_PASSWORD='Demo1234!' node scripts/agent_dock_layout.cjs
UI_EMAIL=demo@example.com UI_PASSWORD='Demo1234!' node scripts/agent_tools_check.cjs
```

截图落在 `frontend/scripts/out/agent-*.png` 与 `tools-*.png`。

## 九、已知未做

- **没有流式输出**：长回答要等 3-8 秒，靠 loading 态撑。
- **只有单轮工具调用**：模型不能在「读完发现不够」之后再读一次。
- **仪表盘没接工具**：只有 L1 问答，提示语是诚实的。
- **提案表只增不删**：`agent_proposals` 里的过期记录没有定时清理，量大了要加一个保留期任务。
- **小程序端没有 dock**：只在 Web 端。
- **`polish_section` 只覆盖三个字段**：教育经历的 highlights、实习要点的整段重写还没开放，后者可以先用 `update_bullets`。
