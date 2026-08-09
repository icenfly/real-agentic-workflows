# 项目纲要完成度与验收边界

本文把 `PROJECT_OUTLINE.md` 和 `HANDOFF.md` 的非暂缓要求映射到可运行实现，防止把概念演示误当成框架交付。机器行为仍以 Schema、CLI 和测试为准。

## 项目纲要映射

| 纲要要求 | 已交付能力 | 验收入口 |
|---|---|---|
| B2B 重复任务通用框架 | 纯数据 Workflow、确定性节点与 LLM/Tool 节点、输入输出契约 | `agent init/validate/compile/run` |
| 可观测、模块化、工具适配、易用 | Run/Node Trace、节点 Hash、版本化注册、HTTP/MCP/OpenAI 适配 | `real trace`、`tests/test_runtime.py`、`tests/test_adapters.py` |
| 流程图即 Agent | Canvas 与 CLI 读写同一 Workflow；布局只存于非语义 metadata | `real canvas`、`tests/test_canvas.py` |
| CLI 与 Coding Agent | 全生命周期结构化 CLI、Dry Run、退出码、审计、仓库 Skill | `real --help`、`skills/real/` |
| 节点 Prompt/Tool 可插拔 | 节点独立配置，应用显式注册 Provider/Tool 及精确版本 | `docs/workflow-spec.md` |
| 不做节点容器 | 单进程异步 Runtime；隔离能力通过 MCP/HTTP 外置 | ADR-0001 |
| 在线 Workflow A/B | 稳定业务单位分流、真实执行后 Exposure、延迟 Outcome、SRM、Guardrail | `real experiment ...`、`tests/test_store_experiments.py` |
| 多 Workflow 证据驱动迭代 | 不可变 Variation/Iteration、结构 Diff、离线排名、晋升与回滚 | `agent diff/eval/experiment iterate/promote/rollback` |
| 借鉴和兼容现有生态 | JSON Schema 2020-12、MCP 2025-06-18、OpenAI wire format、OTel 属性 | ADR-0001、适配器测试 |
| Agent 定义语言与 IR | JSON/可选 YAML → 校验 → 内容 Hash → 不可变 Plan → Runtime | `real schema/compile` |
| Memory 非核心 | 无内建 Memory/RAG；知识库作为普通 Tool 或子 Workflow 接入 | ADR-0001 |
| 一体化与执行效率 | 零必需运行依赖、单进程异步并行、编译 Plan 缓存、Wheel 交付 | `tests/test_runtime.py`、CI Build |

## 线上实验不变量

- 随机化单位由调用方显式声明；同一 Experiment Iteration 内保持 Sticky Assignment。
- Assignment、Exposure、Run Trace 与 Outcome 有可查询外键链；未落库的 Run 不能产生 Exposure。
- Workflow、节点、Prompt、模型、Provider、Tool、Evaluator 和 Experiment Iteration 均记录版本或 Hash。
- 指标先在 Assignment 单位内聚合；SRM 使用已曝光的去重随机化单位，而不是请求次数。
- Guardrail 越界自动停止新 Assignment并写审计；正式晋升保持人工确认，部署可回滚。

## 可复现发布检查

CI 在 Python 3.10 和 3.13 上执行：

```bash
ruff check src tests
ruff format --check src tests
pytest
python -m compileall -q src tests
python -m build
real schema --output schema/workflow-0.1.json
```

本地发布验收还应从新构建的 Wheel 创建干净虚拟环境，并完成 `validate`、`compile`、`eval` 和 `run` 烟测。Canvas 的 API 使用 ETag 防覆盖；内嵌 JavaScript 另用 `node --check` 做语法检查。

## 明确不冒充“已完成”的能力

以下内容在原交接文档中属于已暂缓、长期优化或需要独立 ADR 的边界，不纳入当前核心交付：节点级容器、内建 Memory/RAG、Common-subgraph/KV Cache 复用、Checkpoint/HITL、完整 Sequential/Bayesian 统计、自动晋升、多租户商业控制平面。单节点 SQLite 数据面适合一体化部署；多实例高写入吞吐需要替换 Store 后端。生产公网入口还需要外部 TLS、企业身份、限流和 Secret Manager。
