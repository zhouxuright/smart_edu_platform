# Agent 运行时：记忆、工作流、HITL、Skill、MCP

这份文档回答一个问题：**要把 Agent 做成工程，缺的是哪几块，按什么顺序补。**

它和 `architecture.md` 是同一套判断标准——每个能力都写清触发条件，
到了再做，没到就先别碰。区别是那份讲「部署与性能」，这份讲「运行时」。

最后更新：2026-11-24

---

## 一、现状审计：名字有，实体没有

| 概念 | 状态 | 证据 | 差在哪 |
| --- | --- | --- | --- |
| 短期对话记忆 | **已搬到服务端（P0，2026-11-23）** | `agent_sessions` / `agent_messages`；`AgentDock` 只存 `session_id` | 原本在 `AgentDock.jsx:217` 的 `useState([])`，刷新即失、无法审计 |
| 长期记忆 | **已接写入路径（P2，2026-11-24）** | `user_memories`，版本链 + 来源 + 过期；`services/memory.py` 管读写，`services/memory_events.py` 由业务代码直写 | 抽取质量从未用真实会话验证；`memory_layer.py` 已删除 |
| 工作流 | **已建（P1，2026-11-23）** | `agent_runs` / `agent_steps` + `services/workflow.py`；两个流程：`jd_match`（4 步）、`mock_interview`（12 步）；`sweep_loop` 每 10 分钟清理过期 run | 线性流程，没有真分支 |
| HITL | **已扩到流程内（P2，2026-11-24）** | `AgentProposal.run_id / step`；`services/proposals.py` 是唯一执行入口 | 还没有审批人角色（`approver_id`），家长/老师代批未做 |
| Skill | **等价物存在** | `agents/tools.py` 的 `ToolSpec` 注册表，14 个工具，read/write 两类，带 validate/preview/apply | 没有版本、没有作用域（页面/角色）、不能组合多个工具为一个原子操作 |
| MCP | **无** | 依赖与代码中零出现 | —— 而且现在不该有，见第六节 |

一句话：**`ToolSpec` + `AgentProposal` 是这个项目真正的资产，其余都是待建。**

---

## 二、先分清四组容易混的概念

这四组混淆是「上了框架但没解决问题」的主要来源。

**1. 对话历史 ≠ 记忆。**
历史是流水（谁在第几轮说了什么），记忆是结论（这个学生弱在立体几何，且他自己说过想考公）。
历史用于当轮上下文和审计，**不能当长期记忆检索**——检索历史会把三个月前的一句客套话
当成事实。两张表，两条生命周期。

**2. 工作流 ≠ 多 Agent。**
工作流是「步骤有顺序、状态要落库、中断能恢复」。
多 Agent 是「多个模型实例各持不同工具和人格」。
前者是工程问题，后者是成本问题。**先做前者，后者多半不需要。**

**3. Skill ≠ 工具。**
工具是一次可校验的读写（`update_bullets`）。
Skill 是「一个业务动作的完整编排」（改写简历 = 读 → 校验 → 生成 → 写 → 版本快照）。
现在有工具，没有 Skill 这一层。

**4. MCP ≠ 架构升级。**
MCP 是「把能力以标准协议暴露给外部客户端」。它是**对外接口**，不是内部解耦手段。
内部模块之间用 Python 函数调用，比走 MCP 快、稳、好调试。

---

## 三、目标形态：五张表 + 一个执行器

### 3.1 表结构

```sql
-- 会话：短期记忆的容器。一个用户在一个页面可以有多条会话。
agent_sessions (
  id             varchar(36)  pk,
  user_id        int          not null,
  page           varchar(40)  not null,
  title          text,                          -- 首条用户消息截断，或归档摘要
  status         varchar(16)  default 'active', -- active / archived
  summary        text,                          -- 归档时生成，注入下一条会话
  turn_count     int          default 0,
  token_total    int          default 0,
  created_at     timestamptz,
  last_active_at timestamptz
);
create index on agent_sessions (user_id, last_active_at desc);

-- 消息：流水。只做上下文与审计，不做检索。
agent_messages (
  id          bigserial    pk,
  session_id  varchar(36)  not null,
  role        varchar(12)  not null,   -- user / assistant / tool
  content     text,
  tool_name   varchar(40),
  tool_args   jsonb,
  proposal_id varchar(36),
  tokens      int          default 0,
  created_at  timestamptz
);
create index on agent_messages (session_id, id);

-- 长期记忆：结论。带来源、置信度、版本链、过期。
user_memories (
  id            bigserial    pk,
  user_id       int          not null,
  kind          varchar(16)  not null,  -- preference / fact / weakness / goal / constraint
  mem_key       varchar(64)  not null,  -- 归一化的键，如 "target_city"
  value         text         not null,
  confidence    real         default 0.7,
  source        varchar(32)  not null,  -- user_stated / event / summary
  source_ref    varchar(64),            -- 事件 id 或 message id，能追溯
  last_seen_at  timestamptz,
  expires_at    timestamptz,            -- 可空；偏好类不设，状态类设
  superseded_by bigint,                 -- 版本链：新记忆指向旧的，不覆盖
  created_at    timestamptz
);
-- 部分唯一索引：同一 (用户, 类型, 键) 只有一条「当前有效」的
create unique index on user_memories (user_id, kind, mem_key)
  where superseded_by is null;

-- 工作流：可恢复的状态机
agent_runs (
  id            varchar(36)  pk,
  user_id       int          not null,
  kind          varchar(40)  not null,  -- jd_match / mock_interview / batch_report
  status        varchar(20)  not null,  -- running / awaiting_human / succeeded / failed / cancelled
  current_step  varchar(40),
  state         jsonb        not null default '{}',
  version       int          not null default 0,  -- 乐观锁
  parent_run_id varchar(36),                      -- 子流程，不做 Agent 间自由对话
  created_at    timestamptz,
  updated_at    timestamptz,
  expires_at    timestamptz
);

-- 每一步留痕：这是「可恢复」和「可审计」的分界线
agent_steps (
  id         bigserial   pk,
  run_id     varchar(36) not null,
  step       varchar(40) not null,
  status     varchar(16) not null,   -- running / succeeded / failed / skipped
  input      jsonb,
  output     jsonb,
  error      text,
  started_at timestamptz,
  ended_at   timestamptz,
  unique (run_id, step)              -- 幂等去重，重复调用不会重复副作用
);
```

### 3.2 执行模型：step-driven，不引入 broker

每一步是一个幂等函数，由一次 HTTP 请求或一个后台任务推进：

```
run_step(run_id):
  1. UPDATE agent_runs SET version = version + 1
       WHERE id = :id AND version = :expected AND status = 'running'
     → 影响 0 行就退出（防两个标签页同时推进）
  2. INSERT INTO agent_steps (run_id, step) ... ON CONFLICT DO NOTHING
     → 影响 0 行说明这一步跑过了，直接返回上次 output
  3. 执行步骤函数，得到 (next_state, next_step | AwaitingHuman | Done)
  4. 写回 state / current_step / status
  5. 若 AwaitingHuman：创建 AgentProposal（带 run_id），status = 'awaiting_human'
```

为什么不用 Celery / Temporal：你的工作流是「用户点一下，走 3~5 步，中间可能要确认」，
不是「每天几十万任务」。状态已经在 PG 里，进程重启不丢，重放靠 `agent_steps`，
不需要 broker、不需要 worker、不需要另一个要监控的组件。

为什么暂时不用 LangGraph：它解决的是「图 + 分支 + checkpoint 后端」。
你现在只有线性 3~5 步，引入图框架等于为未来的分支付现在的调试成本。
**触发条件**：单条流程步骤超过 8 个且出现真正的条件分支时，再考虑用它替换
`run_step`，届时表结构不用改（checkpointer 可以写同一套表）。

### 3.3 记忆的写入与检索

**写入只在三个时机，不是每轮：**

| 触发 | 方式 | 成本 |
| --- | --- | --- |
| 用户显式说「记住…」 | 一次小模型抽取，`source=user_stated`，`confidence=0.9` | 每次一句 |
| 会话归档（20 轮或闲置 30 分钟） | 一次摘要调用，产出 `session.summary` + 0~3 条记忆 | 每 20 轮一次 |
| 领域事件（新增错题、计划完成、投递推进） | 业务代码直接写，**不经过模型** | 零 |

**检索不走向量：**

```sql
select kind, mem_key, value
from user_memories
where user_id = :uid
  and superseded_by is null
  and (expires_at is null or expires_at > now())
order by confidence desc, last_seen_at desc
limit 8;
```

八条结论拼进 system prompt，比「检索 20 段相似历史」更准也更便宜。
向量检索长期记忆在这个规模是过度设计，而且会把你正想拆掉的 embedding 依赖又绑回来。

**版本链而不是覆盖**：学生改主意说「不想考公了」，新增一条
`kind=goal, mem_key=target_job, value=考研`，把旧记录的 `superseded_by` 指向新记录。
这样能回答「为什么三个月前推荐的是考公岗位」——覆盖式 UPDATE 永远答不了。

### 3.4 HITL 扩展 ✅ 2026-11-24

`AgentProposal` 只加了两个字段，没有重写：`run_id` / `step`。

状态机：`agent_runs.status = 'awaiting_human'` → 用户确认 → 恢复时从
`current_step` 的下一步继续，**不重跑前面的步骤**（靠 `agent_steps` 去重）。

两类确认点走同一个入口 `workflow.resume_run()`，区别只在 `_pending` 里带了什么：

| 类型 | `_pending` 里有什么 | 恢复时做什么 |
| --- | --- | --- |
| 回答型（模拟面试每一题） | `kind=answer`、题号、题干 | 把 `payload` 写进 `state["_human_input"]`，下一步消费并 `drop` |
| 写操作型（改简历） | `kind=proposal`、`proposal_id` | **先应用提案，再推进流程**；应用失败不推进 |

三条实现约束，改代码时别破坏：

1. **提案执行只有一个入口。** `services/proposals.py` 的 `apply()` 同时服务
   `/agent/chat/confirm` 和 `workflow.resume_run`。两边各写一遍，很快就会出现
   「聊天里能应用、流程里应用不了」。
2. **先抢占状态再执行。** `UPDATE ... WHERE status='pending'` 影响 0 行就是别人
   先点了，直接 409。没有这一步，两个标签页同时点「应用」会把同一段简历改写
   执行两遍——第二遍的 before 已经是第一遍的 after。
3. **最后一步的确认点，同意即结束。** `_pending.next_step` 为 None 时必须把
   run 置为 `succeeded`。如果置成 `running` + `current_step=None`，下一次
   `run_once` 会回落到 `flow.first`，整场面试从头再跑一遍。

`approver_id` 仍然没做：家长/老师代批学生的操作还没建模，现在加了也没人填。

---

## 四、实施顺序

按「收益 ÷ 风险」排，每一阶段都能独立上线。

### P0 会话与记忆落地（1~2 天）

1. 建 `agent_sessions` / `agent_messages` / `user_memories`
2. `/agent/chat` 入参从 `history` 改为 `session_id`（不传则新建并返回 id）
3. 前端把 `session_id` 存 state + localStorage，卸载后能续上
4. 会话归档任务：复用 `reminder_loop` 的模式（asyncio 循环 + 幂等），不引 broker

**验收**：刷新页面对话还在；手机登录能看到同一会话；`user_memories` 每条都有
`source` 和 `last_seen_at`；人工抽查 10 条记忆，至少 8 条是对的。

### P1 工作流状态机（2~3 天）✅ 2026-11-23

1. 建 `agent_runs` / `agent_steps`，写 `run_step()` 执行器
2. 第一个流程选 **JD 匹配**（纯只读、4 步、无写操作，失败也不脏数据）✅
3. 第二个选 **模拟面试**（多轮、需要中断恢复，最能验证状态机）✅ 2026-11-24

**验收**：跑到第 2 步时 kill 进程，重启后能继续；同一 step 调两次不产生重复
副作用；两个标签页同时推进只有一个成功（另一个拿 409）。

### P2 HITL 扩展（1 天）✅ 2026-11-24

`AgentProposal` 加 `run_id` / `step`，执行逻辑收口到 `services/proposals.py`；
拒绝和过期能正确回写 run 状态。**验收**：提案挂在 run 上，拒绝后 run 状态为
`cancelled`，过期提案自动 `expired` 且不阻塞后续流程。`approver_id` 仍待做。

### P3 Skill 化（按需）

`ToolSpec` 加 `version` / `scopes` / `compose`：
一个 Skill 在**同一个事务**里调多个工具，失败整体回滚。
触发条件：出现第一个「一个业务动作需要改两张表以上」的需求。

---

## 五、多 Agent 协同：大概率不需要

先说结论：**这个项目现在不该做「多个 Agent 互相说话」。**

理由很直接——每次 Agent 间交互都是一次模型调用。你现在单次对话 1 次调用、
成本结构很干净；改成 planner / executor / critic 三体对话，一次用户请求变 4~6 次调用，
**成本 ×5，延迟 ×3，而收益无法证明**。在拿到付费意愿之前，这是最差的投入方向。

真正需要「协同」的场景，本质是**一个编排器 + 多个专用工具集**：

```
orchestrator（1 次调用，决定下一步）
  ├─ resume_tools     （简历页的 4 个工具）
  ├─ job_tools        （投递看板的 2 个工具）
  └─ plan_tools       （计划页的 2 个工具）
```

这用 `agent_runs` 就能表达：一个 run 在不同 step 切换工具白名单。
**工具白名单按 step 收窄，比按 Agent 收窄更安全**——模型在第 2 步根本看不到
第 4 步才该用的写工具，越权的可能性从「靠 prompt 约束」变成「结构上不可能」。

若将来真要多 Agent（例如「模拟面试官」需要独立人格 + 独立记忆），最小形态是：
`parent_run_id` 串起来，子 run 各自有工具白名单，**Agent 之间只交换结构化 state，
不自由对话**。自由对话是成本和不可控性的双重来源。

---

## 六、MCP：现在不做，写清触发条件

MCP 是「把能力以标准协议暴露给外部客户端」。它解决的是**对外复用**，不是对内解耦。

| 触发条件 | 到那时做什么 |
| --- | --- |
| 要把能力开放给第三方（学校/机构的系统） | 把 `ToolSpec` 包一层 MCP server，复用现有 validate/apply |
| 要接外部工具生态（企业微信、飞书、教务系统） | 作为 MCP client 接入，工具注册进 `REGISTRY` |
| 团队里有人用 Cursor / Claude Code 想直接调你的工具 | 同上，先只读工具 |

三个都没有，上 MCP 只是多一层协议、多一个调试面。
而且 `ToolSpec` 的四段式（read/validate/preview/apply）**本身就是 MCP tool 的理想实现**——
将来接的时候是「加一层适配器」，不是「重写工具层」。这就是当初分层分对了的红利。

---

## 七、三个最容易做错的地方

**1. 把对话历史当记忆。**
把 `agent_messages` 直接塞进 prompt 当长期上下文，三个月后模型会拿一句客套话当事实。
历史表只服务当轮上下文和审计。

**2. 每轮都让模型总结记忆。**
成本翻倍、噪声翻倍。用「显式指令 + 会话归档 + 领域事件」三个触发点，
其余时候一个字都不写。

**3. 记忆没有来源和过期。**
一条没有 `source`、没有 `last_seen_at` 的记忆，无法判断该不该信、该不该留。
状态类记忆（「正在准备考研」）必须设 `expires_at`，否则一年后它还在影响推荐。

---

## 八、这份文档怎么用

- **有人问「记忆层做了吗」**：看第一节，`MemoryLayerEngine` 是查询聚合器，不是记忆系统。
- **准备做多 Agent**：先看第五节，算一遍调用次数再决定。
- **要接 MCP**：看第六节，对照三个触发条件。
- **要动手**：按第四节 P0 → P1 → P2，每阶段独立可上线。

---

## 附：P0 落地记录（2026-11-23）

### 改了什么

| 文件 | 内容 |
| --- | --- |
| `models/memory.py`（新） | 三张表 + 部分唯一索引 `uq_user_memories_current` |
| `services/memory.py`（新） | 会话读写、记忆 upsert（版本链）、注入、归档循环 |
| `api/agent_chat.py` | 入参支持 `session_id`；新增 `GET /agent/chat/history`；响应带 `session_id` |
| `AgentDock.jsx` / `api.js` | 会话 id 存本地（按账号 + 页面），刷新从服务端恢复 |
| `scripts/agent_memory_check.py`（新） | 23 项服务层回归 |

### 验收结果

- 服务层：23/23（会话、版本链、部分唯一索引、过期不注入、归档与重试）
- HTTP：两轮对话后追问「我刚才问了你什么」，模型准确复述第一轮；`history` 返回 4 条；
  另一个账号读同一 `session_id` 返回 404
- 显式记忆：说「记住：我目标城市是杭州，想找后端开发」→ 后台抽出
  `goal/target_city=杭州`、`goal/target_role=后端开发`，`source=user_stated`
- 回归：`agent_tools_unit` 27/27、`agent_tools_next_action` 43/43、smoke 11/11
- 前端 `npm run build` 通过（未做浏览器点击验证，见下）

### 两个踩出来的坑（都已写进代码注释）

**1. 版本链必须「先让旧行退出当前集合，再插新行」。**
部分唯一索引在 `INSERT` 那一刻就检查，`superseded_by IS NULL` 的旧行还在，
新行必然撞 `uq_user_memories_current`。反过来写（先插后更新）在 PostgreSQL 上不成立。
做法是先把旧行标成占位值 `-1`（事务内可见），插入成功后立刻改成真实 id，
整个过程包在一个 savepoint 里，失败则一起回滚。

**2. `begin_nested()` 进入时会 autoflush。**
把 `db.add(row)` 写在 `async with db.begin_nested()` 之前，冲突会在 savepoint
建立之前就抛出来，外层的 `except IntegrityError` 接住了，但事务已经废了——
下一次 `commit()` 直接 `PendingRollbackError`。`add` 必须放在 savepoint 内部。

### 还没验证的

浏览器点击链路（打开 dock → 发消息 → 刷新 → 对话仍在）只做了构建与接口验证，
没有跑真机 UI 自动化。首次上线后建议人工过一遍这条路径。

---

## 附二：P1 落地记录（2026-11-23）

### 改了什么

| 文件 | 内容 |
| --- | --- |
| `models/workflow.py`（新） | `agent_runs` / `agent_steps`，`(run_id, step)` 唯一约束 |
| `services/workflow.py`（新） | `Flow` / `Step` / `StepOutcome` 注册表、`run_once()`、`advance()`、`resume_run()` |
| `flows/jd_match.py`（新） | 4 步：读简历 → 拆 JD（模型）→ 核对（代码）→ 给建议（模型） |
| `api/agent_runs.py`（新） | 启动 / 推进 / 确认 / 查询 / 列表 |
| `pages/JdMatch.jsx`（新） | 粘 JD、看步骤进度、覆盖率、命中与缺失、补强建议 |

### 一个刻意的分工

**模型只做语义，代码做判定。** 拆 JD 时让模型给每条要求配 1~3 个「能在简历里
搜到的词」，核对时用字符串命中算覆盖率。这样同一份 JD 和简历永远给同一个数字——
让模型直接打分会漂，用户问两次得到两个覆盖率，产品就不可信了。

### 三个实现细节（都写进注释了）

**1. 额度必须在锁内扣。** 先扣额度再抢锁的写法有个漏洞：重复执行一个已完成步骤时
会白扣一次额度，而那一步根本没调模型。正确顺序是「抢锁 → 查幂等 → 扣额度 → 执行」，
扣不到就回滚锁，run 停在原地等明天。

**2. 步骤记录、state、current_step 必须同一个事务提交。** 先提交步骤成功、再提交
状态推进，中间崩掉会留下「步骤标记成功但 state 没合并」的半截流程，恢复时就会
跳过这一步的真实产出。

**3. 并发不是靠重试，是靠行锁。** `UPDATE ... WHERE version = :v` 会锁住那一行，
第二个请求阻塞到第一个提交后重新求值，发现版本变了就返回 409。**不要重试**——
重试会把两步的副作用叠在一起。

### 验收结果

- 服务层 20/20：失败落库、断点恢复、幂等、并发冲突、HITL 同意/拒绝、越权
- 并发实测：两个请求同时推进同一步，恰好 1 个成功、1 个 `RunConflict`
- HTTP：注册探针账号 → 建简历 → `POST /agent/runs` 一次走完 4 步，
  覆盖率 0.83、4 条建议；`GET /agent/runs/{id}`、列表、404 均正确
- 回归：记忆 23/23、工具 27/27 与 43/43 未受影响；前端构建通过

### 还没做的

- 前端只做了「一次走完」，没有接 `advance` 的逐步进度；断网重连后恢复靠刷新
- `agent_runs.expires_at` 已写但还没有清理任务

---

## 附三：P2 + 模拟面试落地记录（2026-11-24）

### 改了什么

| 文件 | 内容 |
| --- | --- |
| `models/agent.py` | `AgentProposal` 加 `run_id` / `step`；状态多一个 `applying` 中转态 |
| `core/indexes.py` | 补两列 + 一个索引；**每条 DDL 各自包 savepoint**，并改成先列后索引 |
| `services/proposals.py`（新） | 提案的唯一执行入口：`create` / `apply` / `reject`，带状态抢占与 TTL |
| `services/workflow.py` | `StepOutcome.drop`；`resume_run(accept, payload)`；应用提案后再推进；末步确认即结束 |
| `flows/_support.py`（新） | 两个流程共用的取简历、拍平、调模型、抠 JSON |
| `flows/mock_interview.py`（新） | 12 步：准备 → 出题 → 4×(提问 + 归档) → 总评 → 提议改简历 |
| `api/agent_chat.py` | 确认走 `proposals.apply`；提案挂 run 时确认完自动推进流程 |
| `api/agent_runs.py` | `resume` 接收 `payload`（多轮流程的用户输入） |
| `pages/MockInterview.jsx`（新） | 逐题作答、总评、提案卡片；菜单挂在「求职」组 |

### 成本设计：一场面试只调 3 次模型

出题 1 次、总评 1 次、提议改进 1 次。**没有逐题调模型**——4 题各调一次规划，
成本翻 4 倍，题目质量不会更好；逐题点评看着热闹，但用户真正要看的是
「这场面试整体怎么样、下一场怎么改」。回归脚本里有一条断言盯着这个数字。

### 两个踩出来的坑

**1. 补索引跑在补列之前，会让后面全部静默失败。**
新索引引用新列 `run_id`，先建索引直接报 `column does not exist`；而 PostgreSQL
里一条语句失败会把整个事务标记为 aborted，之后所有 `ALTER TABLE` 都报
`InFailedSQLTransactionError`。原实现只在日志里留一行 warning，看起来像
「只有那一条出问题」，实际是**后面全部没执行**。
修法两处：`ensure_columns` 先于 `ensure_indexes`；每条 DDL 各自包一层 savepoint。

**2. `begin_nested()` 进入时会 autoflush。** 上一轮已记过，这轮在提案路径上又碰到：
`db.add(proposal)` 必须放在 savepoint 内部，否则冲突在 savepoint 建立前就抛出。

### 验收结果

- 服务层 19/19（`scripts/agent_mock_interview_check.py`）：每题都真的停下、
  人工输入被消费不残留、总评分数在 0~100、提案挂在正确的 run 与 step 上、
  确认后流程直接结束、简历版本号前进、重复应用不二次写入、
  一场面试只扣 3 次额度、拒绝路径取消流程并把提案标 rejected、越权取不到
- 回归：JD 匹配 20/20、记忆 23/23、工具 27/27 与 43/43、smoke 11/11
- HTTP 全链路：注册 → 建简历 → 开始面试（停在 `ask_1`）→ 4 轮 `resume` 带
  `payload` → 停在 `propose_improvement` → `/agent/chat/confirm` 应用提案并
  把 run 推到 `succeeded`

### 还没做的

- `approver_id`：家长/老师代批未建模

（「刷新恢复」与「过期清扫」已在附四补上，不再列为缺口。）

---

## 附四：记忆层收口（2026-11-24）

上一轮审计的结论是「长期记忆只有表，没有写入路径」。附四把这条补完，
同时删掉一处会误导后来人的旧实现。

### 1. 领域事件直写记忆 —— `services/memory_events.py`

错题收录、错题订正、计划全部完成，这三个动作的事实**已经在数据库里写清楚了**。
再让模型读一遍再总结，只会引入幻觉并多付一次调用费。所以分成两条路径：

| 输入 | 谁写 | 成本 |
| --- | --- | --- |
| 用户说的话（非结构化） | 模型抽取 → `services/memory.py: extract_memories` | 一次调用 |
| 加错题 / 订正 / 计划完成（结构化） | 业务代码直写 `memory_events.py` | 零 |

三条约束写进了模块 docstring，改的时候别破坏：

1. **不 commit**，跟随调用方事务。业务回滚，记忆一起回滚——
   否则会出现「错题没加成，但记忆里记了一笔」。
2. **不抛异常**，只记日志。记忆是增强不是主流程，不能让「加一道错题」失败。
3. **value 要稳定**，不拼 id / 时间戳 / 计数。否则每次都是新版本，
   版本链退化成流水账。同一知识点反复错时值相同，只刷新 `last_seen_at`。

接入点：OCR 批量、单条创建、批量导入、工具层 `add_mistake` / `resolve_mistake`、
`plan_store.set_task_completed`。

### 2. 删掉 `agents/memory_layer.py` 与 `POST /memory/insights`

它每次请求重查画像 + 错题 + 学习记录拼成一段字符串，和 `user_memories`
是**两套事实来源**，而前端从未调用过。留着只会让后来的人不知道该信哪个。
「一份事实一个来源」比「多一条路径」重要。

### 3. 过期清扫 —— `workflow.sweep_expired` / `sweep_loop`

run 挂过 TTL 后一直停在 `awaiting_human`，用户不点，它就一直占着，
挂在它上面的提案也一直能点。现在每 10 分钟扫一次。

**顺序不能反**：先作废挂在过期 run 上的 `pending` 提案，再取消 run。
反了会留下「流程已取消、提案还能点」的孤儿——用户点下去只会拿到 409，
但他不知道原因。

### 4. 面试页刷新恢复

`run_id` 按账号存 `qicheng:mock_interview:{sub}`，挂载时
`GET /agent/runs/{id}` 恢复。只保留 `running` / `awaiting_human` / `succeeded`
三种状态，其余（已取消、已失败）丢弃重开——恢复一个死流程比重新开始更让人困惑。

### 顺带修掉的真 bug：`normalize_key` 吃掉中文

原来的 `[^a-zA-Z0-9_]` 会把**所有中文知识点归一成同一个键**
（「立体几何」→ `topic`），于是不同知识点互相覆盖，记忆里只剩最后一条。
改用 `[^\w]+` 并显式 `re.UNICODE`——Python 3 的 `\w` 默认含中文。

这个 bug 的特征值得记住：**不报错、不丢数据、只是悄悄变少**。
所以断言不能只测「写进去了」，还要测「两条不同的知识点是两条」。

### 验收

- 记忆 32/32（新增：加错题直接写记忆、事件记忆可追溯到来源、
  中文知识点不会被归一成同一个键、同一知识点重复错不刷版本、
  不同知识点分开记、订正后旧结论被新版本覆盖、旧行保留并指向新行、
  计划完成写进度记忆、事件记忆会进 prompt）
- 工作流 27/27（新增：过期流程被作废、作废时写明原因、
  过期流程上的提案一起作废、超过 TTL 的独立提案被作废、
  未超时的提案不动、清扫返回真实影响行数、重复清扫不重复处理）
- 模拟面试 19/19；工具 27/27 与 43/43；smoke 11/11；前端 build 通过
- 生产库清理后 3 个账号（探针账号已清）



