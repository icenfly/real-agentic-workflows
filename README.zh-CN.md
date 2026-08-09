# REAL Framework

**R**eproducible **E**xperimentation for **A**gentic **L**ogic：面向 Agentic Logic 的可复现实验框架。

REAL 面向重复性 B 端任务，把 Workflow 定义、不可变执行、完整 Trace、离线评测和线上 A/B 放进同一套轻量框架。它不是一个聊天式多 Agent Demo，而是用于持续创建、比较、发布和回滚 Agent Workflow 的工程底座。

## 1. 安装

```bash
git clone https://github.com/icenfly/real-agentic-workflows.git
cd real-agentic-workflows
python -m venv .venv
source .venv/bin/activate
pip install -e '.[yaml,otel]'
```

- 发布包名：`real-agentic-workflows`
- 主命令：`real`
- 兼容命令：`agent`
- Python import：`agent_infra`
- 默认状态库：`.real/state.db`，可用 `REAL_DB` 指定其他位置

## 2. 最短使用路径

```bash
real init customer-triage --name customer_triage
cd customer-triage
real validate workflow.json
real compile workflow.json
real run workflow.plan.json --input '{"message":"refund requested"}'
real trace RUN_ID
real eval workflow.plan.json --dataset dataset.jsonl
```

CLI 成功输出统一为 JSON；校验失败返回退出码 2，执行失败返回退出码 3。`compile`、`deploy`、实验控制和回滚等写操作可先使用 `--dry-run`。

## 3. Workflow 如何定义

Workflow 使用 JSON；安装 `[yaml]` 可选依赖后也支持 YAML。主要内容包括：

- `input_schema` / `output_schema`：输入输出 JSON Schema；
- `nodes` / `edges` / `entry`：节点、连线和入口；
- `template`、`tool`、`llm`、`branch`、`join`、`subworkflow`、`output` 等节点；
- 条件、有限循环、并行、显式 Join；
- Retry、Timeout、Idempotency、Side Effect；
- Prompt、Model、Provider 和 Tool 的精确版本。

常用命令：

```bash
real schema
real validate workflow.json
real diff old-workflow.json new-workflow.json
real compile workflow.json --dry-run
real compile workflow.json
real canvas workflow.json
```

Canvas 与 CLI 直接读写同一个 Workflow 文件，不维护第二份图数据。布局保存在 `metadata.canvas`，不会改变语义 Digest。

## 4. 接入 Tool 与模型

Workflow 只声明逻辑名称和版本，不允许从定义文件执行任意 Python 路径。宿主应用显式注册实现：

```python
from agent_infra import Runtime, WorkflowSpec, compile_workflow

plan = compile_workflow(WorkflowSpec.from_dict(workflow_dict))
runtime = Runtime()
runtime.register_tool("lookup", lookup, version="2026-08-09")
runtime.register_provider("gateway", generate, version="gateway-v2")
result = runtime.run(plan, {"ticket_id": "T-42"})
```

CLI 也可以注册函数：

```bash
real run workflow.plan.json \
  --tool lookup=my_app.tools:lookup@2026-08-09 \
  --provider gateway=my_app.models:generate@gateway-v2 \
  --input '{"ticket_id":"T-42"}'
```

或者通过 `--adapters` 使用内建 HTTP JSON、MCP Streamable HTTP 和 OpenAI-compatible 适配器。凭证必须来自环境变量；远程 Host Allowlist 必填。

## 5. Trace 与隐私

```bash
real run workflow.plan.json --input @request.json
real trace RUN_ID
real audit --limit 100
```

每次运行记录：Plan Digest、Workflow Version、Node/Prompt Hash、Provider/Model/Tool Version、节点输入输出、错误和延迟。敏感节点可设置 `config.trace_content=false`；Workflow 可用 `metadata.runtime.trace_input/trace_output=false` 关闭内容保存。应用也可组合 `OpenTelemetrySink` 导出 Span。

## 6. 离线评测

Dataset 使用 JSONL，每行包含 `input` 和 `expected`：

```bash
real eval control.plan.json treatment.plan.json \
  --dataset examples/triage-dataset.jsonl \
  --output eval-result.json
```

默认使用 Exact Match；也可用 `--evaluator name=module:function@version` 注入自定义评分器。评测产物记录 Dataset Digest、Evaluator Version、每个 Case 分数和 Workflow 排名。

## 7. 线上 A/B

```bash
real experiment start experiment.json --dry-run
real experiment start experiment.json

real run --experiment reply_quality \
  --unit-name organization_id \
  --unit-value acme \
  --input '{"message":"hello"}'

real outcome ASSIGNMENT_ID resolved 1 --idempotency-key ticket-42
real experiment status reply_quality
real experiment stop reply_quality
real experiment iterate next-iteration.json
real experiment promote reply_quality treatment
real rollback customer_triage
```

关键不变量：

1. Variation 引用不可变 Plan Digest；
2. 按组织、客户、工单等稳定业务对象分流，不默认按 Request 随机；
3. Assignment 不等于 Exposure，只有真实运行并落库后才曝光；
4. 延迟业务 Outcome 关联 Assignment，并支持幂等键；
5. 状态包含去重分析单位、Exposure、Primary/Guardrail 指标和 SRM；
6. Guardrail 越界自动停止新 Assignment，正式晋升保持人工确认。

## 8. HTTP 数据面

```bash
real deploy workflow.plan.json --environment prod
export REAL_API_KEY='replace-me'
real serve --host 0.0.0.0 --port 8080 --api-key-env REAL_API_KEY
```

主要接口：

- `GET /healthz`
- `POST /v1/runs`
- `GET /v1/runs/{run_id}`
- `GET /v1/experiments/{name}`
- `POST /v1/outcomes`

公网运行应在反向代理后配置 TLS、企业身份、限流和 Secret Manager。内建 SQLite Store 适合单控制进程；多实例高写入场景应替换为事务数据库后端。

## 9. 开发与贡献

```bash
python -m pip install -e '.[dev,yaml]'
ruff check src tests
ruff format --check src tests
pytest
python -m build
```

完整资料见 [Workflow 语义](docs/workflow-spec.md)、[实验归因](docs/experimentation.md)、[生产运维](docs/operations.md)、[完成度审计](docs/completion-audit.md) 和 [开源发布手册](docs/open-source-release.md)。

项目采用 Apache-2.0 License。
