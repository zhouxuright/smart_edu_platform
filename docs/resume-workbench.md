# 简历工作台

## 为什么这样做

求职链路里，学生最常卡在两件事：**不知道简历该写成什么样**，以及**改完一版就找不回上一版**。
所以这里只解决这两个：结构化填写 + 实时预览 + 版本快照 + 一键 PDF。

## 数据模型

```
resumes            id, user_id, title, target_role, data(JSONB), version, created_at, updated_at
resume_versions    id, resume_id, version, note, data(JSONB), created_at
```

**正文用 JSONB 整存整取**，不给教育/实习/项目各开一张关联表：
段落数量天然不定，拆表后「保存一次」要十几次写入和级联更新，
而这一层没有按字段做 SQL 查询的需求——真正要检索的只有标题和目标岗位，单独开列即可。

**版本存整份快照**，不存 diff：简历的改动常是「整体重写第 2 段」，
存 diff 恢复时要链式回放，容易错；快照恢复只需一条 UPDATE。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/resumes` | 列表（不含正文） |
| POST | `/resumes` | 新建，自动留 v1 |
| GET | `/resumes/{id}` | 详情 + 完整度 |
| PATCH | `/resumes/{id}?snapshot=1` | 更新；带 snapshot 才留档 |
| DELETE | `/resumes/{id}` | 删除（版本级联清空） |
| GET | `/resumes/{id}/versions` | 版本列表 |
| POST | `/resumes/{id}/versions` | 手动留档 |
| POST | `/resumes/{id}/versions/{v}/restore` | 恢复 |
| POST | `/resumes/{id}/polish` | AI 润色（计额度） |
| GET | `/resumes/{id}/pdf` | 导出 PDF |

### 版本策略

- **自动保存不留档**（前端停手 1.5s 落一次），否则改十个字就多十条历史。
- **显式动作才留档**：「保存并留档」按钮、手动留档、AI 润色前。
- 恢复时**先备份当前内容**再覆盖，避免「恢复错了回不去」。
- 每个简历最多保留 30 个版本。

### 完整度算法

只数招聘方真的会看的 7 项，空列表不刷分：
姓名 / 联系方式 / 自我介绍 / 教育经历 / 实习或项目 / 有要点的条目 / 技能。

## PDF 导出

`reportlab` 服务端排版，A4、2cm 边距，不依赖浏览器打印对话框。

**中文字体是唯一的坑**：reportlab 内置字体没有汉字，不注册 TTF 会输出空白。
`resume_pdf.py` 按候选路径找（simhei → msyh → 宋体 → Noto/PingFang），
找不到再扫字体目录，最后仍找不到就抛 `no_cjk_font` → 接口返回 503 并给中文提示，
而不是悄悄输出乱码。

另一个坑：`Content-Disposition` 头只能 latin-1，中文文件名必须按 RFC 5987 百分号编码，
否则 Starlette 写 header 时抛 `UnicodeEncodeError`（表现为 500）。

## AI 润色

提示词硬约束：**只使用原文已有事实，不编造数字、公司、奖项、时间**；
原文缺量化结果时用可核实的定性表述。输出 JSON 数组，后端再洗一遍 Markdown 标记。

实测（输入「在公司做产品实习生，帮忙做竞品分析和写需求文档…」）：

```
协助开展竞品分析，梳理竞品功能与差异，为产品决策提供参考依据
参与撰写需求文档，整理功能需求并推动团队对齐
搭建数据看板，汇总关键数据便于团队跟踪，获负责人认可
```

没有凭空出现的百分比——这是刻意设计的，编造数字的简历在面试时是负资产。

## 验收

```
GET  /resumes                     200 {items: []}
POST /resumes                     200 id=1 v=1 完整度 0
PATCH?snapshot=true               200 → v2
GET  /resumes/1                   完整度 100
GET  /versions                    [(2,'填完初稿',True),(1,'创建简历',False)]
POST /versions                    200 v3
POST /versions/1/restore          200 → 追加 v4(恢复前备份) + v5(恢复到 v1)
GET  /pdf                         200 application/pdf 9356 bytes, %PDF-
POST /polish (text<8字)            400 内容太短
```

浏览器：`frontend/scripts/resume_check.cjs` 全流程通过，PDF 下载为 `张一鸣-简历.pdf`，无控制台错误。

---

# 附件简历（上传 / 批量上传 / 解析导入）

结构化填写适合从零写，但大多数学生手上已经有一份 PDF 或 Word。附件模块解决的是「把已有的东西搬进来」，而不是再抄一遍。

## 为什么附件不放在 `backend/uploads`

`backend/uploads` 被 `main.py` 挂到了 `/static`，拿到路径就能匿名下载。简历里有手机号、邮箱、住址，属于个人敏感信息，必须走鉴权接口。所以附件单独落在：

```
backend/storage/resumes/<user_id>/<uuid>.<ext>
```

- 目录按 `user_id` 分片，删用户数据时可以整目录清理；
- 磁盘名用 uuid，原始文件名只存库（中文名、重名、路径穿越一次绕开）；
- 只由 `GET /resumes/{id}/attachments/{aid}/download` 吐出，响应带 `Cache-Control: private, no-store`；
- `backend/storage/` 已加入 `.gitignore`。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/resumes/{id}/attachments` | `multipart/form-data`，字段名 `files`，传 1 个就是单传，传多个就是批传 |
| GET | `/resumes/{id}/attachments` | 列表 + 当前限额（上限、单文件大小、允许格式） |
| GET | `/resumes/{id}/attachments/{aid}/text` | 提取出的纯文本，用于预览 |
| GET | `/resumes/{id}/attachments/{aid}/download` | 下载原件（带鉴权） |
| DELETE | `/resumes/{id}/attachments/{aid}` | 删除记录 + 磁盘文件 |
| POST | `/resumes/{id}/attachments/{aid}/import?apply=` | AI 解析成结构化字段；`apply=false` 只返回预览 |

限额：一份简历 20 个附件、单次最多 10 个、单文件 10MB、单批合计 30MB；格式 `pdf / docx / doc / txt / md`。

## 几个刻意的设计

**逐文件返回结果，不是整批成败。** 10 个文件里 1 个是图片，不该让另外 9 个白传。响应里每个文件带 `status: ok | duplicated | failed` 和具体原因，前端把未入库的挑出来列在提示条里。

**按 sha256 去重，靠数据库唯一约束兜底。** 判重键是 `(resume_id, sha256)`：同一份简历里重复传同一个文件没有意义，但同一个文件挂到另一份简历（比如投不同岗位）是合理需求。并发上传时唯一约束会冲突，用 `SAVEPOINT`（`db.begin_nested()`）只回滚这一条，不影响同批其他文件。

**解析失败不阻断上传。** 扫描版 PDF 提不出文字、旧版 `.doc` 解析不了，文件照样存下来可下载，只是 `parse_error` 里写明原因、禁用「AI 解析」按钮。用户至少能拿到原件。

**导入默认只预览，不直接覆盖。** 用户手填的内容被 AI 悄悄改掉是不可接受的。流程是：解析 → 弹窗列出「姓名 / 教育 N 段 / 实习 N 段 / 完整度」→ 确认 → 覆盖。覆盖前自动 `_snapshot("导入附件前备份")`，导入后再留一版，走的是和「恢复版本」同一条链路，随时能回退。

**删库再删盘。** 顺序反过来会出现「记录还在、文件没了」的坏状态；库删成功而文件删失败只会留个孤儿文件，不影响用户。删整份简历时先把 `stored_name` 查出来再删，否则级联删完就查不到文件名，磁盘上会永久堆积。

## 前端交互

`components/ResumeAttachments.jsx`：

- `Upload.Dragger` 多选 + 拖拽，`beforeUpload` 返回 `Upload.LIST_IGNORE` 拦住 antd 自己的上传；
- 选中的文件先进队列，**400ms 内的一起发**——一次选 5 个文件只产生 1 个请求；
- 超过 10 个 / 30MB 时前端自己按 `chunkFiles()` 切片顺序发，用户不必手动分批；
- 进度条按「第几批 + 批内百分比」折算，多批时不会反复回零；
- 每行四个操作：预览文字、下载原件、AI 解析、删除。

移动端（≤575px）把操作按钮挪到单独一行：挤在右侧会把 `赵敏-前端.docx` 压成「赵敏-前 / 端.docx」。这里踩了个坑——antd v5 的样式是运行时注入的，和组件 CSS 同权重时后者胜，所以选择器要多带一层 `.ant-list-item` 才压得住。

## 验收

```
POST 批量 4 个文件             200 {ok:3, duplicated:0, failed:1}  ← png 被拒，其余入库
POST 重复上传同一批            200 {ok:0, duplicated:3}
GET  列表                     3 条，总 53544 B
GET  text                     chars=173，首行「[第 1 页] 李四 / 13900000000 ...」
GET  download                 200 application/pdf 16580 B，magic=%PDF-
                                Content-Disposition 带 RFC 5987 编码的中文名
POST import?apply=false       200 姓名=李四，教育 1 段、实习 1 段，要点与原文一致
POST import?apply=true        200 applied=true，版本追加「导入附件前备份」+「从附件导入：…」
DELETE 单个附件               磁盘 2 → 1 个文件
DELETE 整份简历               磁盘 → 0
越权（另一账号）               列表/下载/删除 均 404；无 token 401
限额                          11 个文件 → 400；11MB → 单文件超过 10MB；0 字节 → 文件是空的
```

浏览器：`frontend/scripts/attach_check.cjs` 走完「一次选 3 个 → 列表 → 文本预览 → AI 解析弹窗 → 确认导入 → 左栏回填 → 移动端」，导入后姓名/手机/邮箱/城市/GitHub/教育/实习/项目/技能/奖项全部就位，完整度 100%，无横向溢出。

已知遗留：小程序端简历页暂未接附件上传（小程序需走 `wx.chooseMessageFile` + `wx.uploadFile`，且 PDF 预览要 `wx.openDocument`），Web 端已完整可用。
