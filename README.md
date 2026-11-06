# SIMS project 问数试点

本项目实现当前约定的最小链路：

```text
本轮问题＋近期成功问题＋待澄清问题 → 千问改写独立问题并识别明细／指标／非问数
     → 必要时澄清问题本身 → 阿里云 Embedding
     → ES 关键词和向量两路召回 → RRF 合并 → 千问选择配置或澄清配置歧义
       ├─ 选中配置：问题 + 完整配置生成 MQL
       └─ 无适用配置且允许探索：进入探索占位节点 → 返回未探索到信息
     → 必要时澄清用户可补充的条件
     → 千问按 MQL 生成 Doris SQL → SQL 语句校验 → Doris 只读查询
     → 千问根据真实结果生成纯文本回答 → 输出整理节点 → 返回回答
```

仅支持 SIMS `project` 试点，每次请求处理一个问题。可选 `session_id` 启用最近 5 轮成功问题的上下文和跨请求澄清；没有完整权限体系、前端和报表。非问数返回固定说明，不调用 ES 或 Doris。FastAPI 和 MCP 入口使用同一条 LangGraph 工作流。

## 运行

本机使用 **标准 Python `venv`**：独立的 Python 3.12.16 位于 `D:\PythonRuntimes`，项目环境位于 `.venv`，不是 Conda 环境。项目依赖已安装。用 VS Code 打开 `D:\sims_qa_pilot` 时，工作区设置会指向 `.venv/Scripts/python.exe`。如果没有自动切换，运行 `Python: Select Interpreter` 并选择该路径。

本机运行：

```powershell
Set-Location D:\sims_qa_pilot
.\.venv\Scripts\python.exe --version
Copy-Item .env.example .env  # 仅首次需要
```

在 `.env` 中填写千问、Embedding、ES、Doris 的真实连接信息，然后启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn sims_qa.api:app --host 127.0.0.1 --port 8000
```

接口：

```http
POST /ask
Content-Type: application/json

{"question":"9月创建了多少安装工单？","session_id":"example-chat-1","allow_exploration":false}
```

响应正文是 JSON，格式如下。正常回答的 `type` 是 `answer`，需要用户补充时是 `clarify`，已处理的失败或非问数是 `error`，其 `code` 给出稳定原因。`is_exploratory` 标明本次是否进入探索分支；治理配置正常命中时为 `false`。HTTP 响应头仍包含 `X-Request-ID`。下一轮用相同 `session_id` 可提问“那 10 月呢？”或回答澄清；不传 `session_id` 就按独立单轮问题处理，澄清后必须重新提交完整问题。

```json
{"type":"clarify","text":"你想统计创建的工单，还是已完结的工单？","request_id":"...","code":null,"is_exploratory":false}
```

`GET /health` 只检查必填环境变量是否填写，不代表外部服务连通。

成功回答经过独立的 `format_output` 节点。当前节点只读取回答模型生成的纯文本，并原样写入 `output_text` 供接口返回；它不接收 Doris 行、MQL 或治理配置。澄清和故障回复不经过该节点。后续确定展示协议时，可在此扩展输出格式。

短期记忆使用完整 LangGraph 工作流上的内存 Checkpointer。每轮开始清空本轮配置、MQL、SQL、结果及失败状态，保留 `history` 和 `pending_clarification`。问题理解节点只读取最近 5 轮成功问数的原问题和独立问题，以及当前待澄清问题。澄清状态包含来源、待补全问题、提问和必要的配置选项；用户补充后重新理解、重新检索并重跑后续链路。完整重问或新话题会清除旧澄清。失败和澄清轮次不写入成功历史。服务重启后会话消失，多进程之间也不共享记忆；Checkpointer 的状态快照仍可能包含本轮 SQL 和查询结果，接入真实敏感数据或持久化存储前需确定保留策略。

### 链路排查

当前使用项目内的 JSONL 日志，不依赖 LangSmith。每次请求生成 `request_id`；HTTP 响应的 `X-Request-ID` 响应头可用于查找该次请求。日志默认写入 `logs/trace.jsonl`，一行一个事件：请求开始、每个工作流节点的状态与耗时、请求结束。节点耗时用单调时钟记录节点执行前后的差值，总耗时包括工作流调度时间。节点记录失败代码、选中的配置 ID 和查询返回行数等必要信息，不记录凭证或完整 Doris 结果。

`request_id` 放在本轮工作流 State 中供节点关联日志；日志事件不放进跨轮会话历史。默认 `TRACE_LOG_QUESTIONS=false`，不把原问题和改写问题写入日志；本地排查时可临时设为 `true`。`logs/` 已被 Git 忽略。此日志只用于当前单进程试点；多进程部署时需要改用集中日志或追踪服务。

### 只检查 ES 连接

ES 治理配置尚未入库时，可以先在 `.env` 填写 `ES_URL`、`ES_USERNAME` 和 `ES_PASSWORD`，运行：

```powershell
.\.venv\Scripts\python.exe -m sims_qa.es_check
```

该命令只请求 ES 根接口，显示集群名称和版本；无需 `ES_INDEX`，也不会调用千问、Embedding 或 Doris。当前 ES 使用用户名密码的 Basic Auth。若测试环境的 HTTPS 证书尚未被本机信任，可在本机 `.env` 中设置 `ES_VERIFY_TLS=false`；有可信证书时保持 `true`。不要把密码放进 `.env.example` 或提交到仓库。

如调用方要求标准 MCP 协议，可用 stdio 启动：

```powershell
.\.venv\Scripts\python.exe -m sims_qa.mcp_server
```

工具名 `ask_sims_project`，输入 `question`、可选 `session_id` 和默认 `false` 的 `allow_exploration`，输出与 HTTP 相同的结果字段。若对方实际说的“mcp 接口”是 HTTP 转发而非标准 MCP，先使用 `/ask`，再按接口协议补适配层。

## ES 入库契约

同事负责将治理配置写入 ES。正式索引中的文档必须包含：

- 唯一的 `config_id`、`config_type`（`detail`／`metric`）、`status=enabled`；
- `name`、`description`，以及用于匹配的 `search_text` 和 `embedding`；
- 完整的 `_source.query`，供 MQL 生成。

项目在 ES 上分别执行 `match(search_text, question)` 和 `knn(embedding, question_vector)`，两路都按 `config_type`、`status=enabled` 过滤，然后在 Python 中做 RRF。Top K 和 RRF 常数从环境变量读取。ES 版本需支持当前 `knn` 查询格式；如公司版本不同，调整 `retrieval.py` 中的向量查询体即可。入库向量和提问向量必须由同一 Embedding 模型产生，维度一致。

完整 MQL 的结构在 `src/sims_qa/mql.py` 中固定：来源表、选择字段或聚合表达式、关联、过滤、分组、排序、条数及指标计算。MQL 模型节点生成该结构，SQL 节点只接收结构化后的 MQL。生成后的独立 `validate_sql` 节点读取 SQL 和服务统一的最大返回行数，用 `sqlglot` 拒绝无法解析、多语句及非只读 SELECT，并在外层加 `LIMIT N+1`（多读取一行用于判断结果是否截断）。通过后产出 `validated_sql`；失败记录校验代码和简短原因，直接结束本轮。该节点暂不做 SQL 与 MQL、治理配置的一致性校验，也不自动重试生成。

配置修改时新增唯一 `config_id` 的记录，旧记录停用。非问数、无适用配置或各节点失败时，工作流进入统一回复节点，返回简明原因并停止后续查询。问题指代不明、配置业务含义有歧义或 MQL 缺少用户可补充的必要条件时进入澄清节点。桌面《SIMS问数配置与工作流契约.md》记录了较早的单轮约定；澄清与结构化返回以当前项目代码和本文为准。

`project` 试点配置尚未完成业务审核。示例里的 Doris 库名占位不能直接用于正式问数；要先核对 Doris 真实表、字段类型和业务口径，再启用配置。

## 探索性问数

`allow_exploration` 是每次请求的开关，默认 `false`。只有 ES 正常返回但没有候选，或候选中没有适用配置时，才进入探索分支；ES 超时、响应错误及配置歧义不会触发探索。默认关闭时仍返回 `no_config`。

探索能力目前是占位：进入 `explore` 节点后固定返回 `exploration_unavailable`，文案为“没有匹配到治理配置，也没有探索到可用于回答的信息。”，并设置 `is_exploratory=true`。此分支不读取 Doris 元数据，不生成 MQL、SQL，也不查询 Doris。后续接入探索能力时，在此节点补充数据来源和判断，再决定如何汇入 MQL 链路。

该开关只表达本次请求的选择，不承担用户授权。Java 平台接入时需决定谁能开启探索。

## 代码职责

- `api.py`、`mcp_server.py`：HTTP 和 MCP 输入输出适配，共用同一工作流。
- `contracts.py`：工作流 State、澄清记录、返回结果及失败文案。
- `workflow.py`：节点连接、条件路由、耗时追踪、会话 Checkpointer 和调用入口。
- `nodes.py`：问题理解、检索、配置选择、探索占位、MQL/SQL、执行、回答与记忆节点。
- `ai.py`：千问与 Embedding 请求、模型响应解析；`mql.py`：MQL 数据结构。
- `retrieval.py`：ES 双路检索与 RRF；`sql_validation.py`：SQL 校验；`doris.py`：查询执行；`trace.py`：JSONL 日志；`settings.py`：环境配置。
- `tests/`：模拟外部服务，验证路由、澄清、检索、SQL 校验和接口行为，不连接真实 ES 或 Doris。

## Doris 查询边界

Doris 连接必须使用**只读账号**，只授予试点需要的表。独立 SQL 校验节点完成语句检查和行数上限处理；Doris 执行器只接收 `validated_sql`，读取最多 `N+1` 行，返回最多 `N` 行及截断标记。当前不做 SQL 与治理配置的表范围对应检查，也不代替业务口径审核或数据库权限设计。外层行数限制不能限制 Doris 的实际扫描量和查询耗时。

## 本地验证

在项目根目录运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

测试用模拟模型、ES 和 Doris 跑完整工作流，不需要真实账号。接入真实环境后，还需由人工标准 SQL 核对首批问题的结果。
