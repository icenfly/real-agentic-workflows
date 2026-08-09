# ADR-0001：第一版 IR、Runtime 与实验存储

- 状态：Accepted
- 日期：2026-08-07

## 问题与约束

项目需要同一个底层表示服务 CLI、Canvas、SDK 和 Coding Agent；运行要可复现、可观测，并原生记录在线实验血缘。第一版不能被单一云或 Agent 框架锁定，也不能为尚未验证的需求引入控制平面微服务。

## 候选实现

| 候选 | 优点 | 缺点与锁定 | 适配结论 |
|---|---|---|---|
| LangGraph | 图、持久化、流式、HITL 和生态成熟 | 代码级 Graph 是主要入口；在线结构实验仍需自建控制与归因 | 后续 Backend Adapter 候选，不作为核心 IR |
| Microsoft Agent Framework | Programmatic 与 Declarative Workflow 并存；Executor/Edge 模型清晰；原生工具与 MCP Action | 仍在快速演进，Python/C# 声明格式不同；绑定其 Runtime 语义 | 借鉴声明转 Graph 与显式 Tool 注册 |
| Oracle Open Agent Spec | 纯数据、框架无关，Agent/Flow 与 Runtime Adapter 分层 | 规范仍新；部分框架适配能力不齐；直接绑定会让内部演进受限 | 保持数据模型可映射，兼容层后置 |
| 小型内部解释 Runtime | 能精确定义实验、Trace、Digest 与一体化执行；零必需依赖 | 必须自行测试图语义；高级 Checkpoint/HITL 不应重复建设 | 选择，用严格小语义控制范围 |

实验控制对比 LaunchDarkly AgentControl：其文档明确区分 Experiment 与 Guarded Rollout，并要求随机化单位、Exposure/Metric context 一致和 SRM 健康检查。第一版复用这些不变量，不复制其完整统计平台。

## 官方资料

均于 2026-08-07 重新核验：

- [Microsoft Declarative Workflows](https://learn.microsoft.com/en-us/agent-framework/workflows/declarative)
- [Microsoft Workflow Builder & Execution](https://learn.microsoft.com/en-us/agent-framework/workflows/workflows)
- [LangGraph Reference](https://langchain-ai.github.io/langgraph/reference/)
- [Oracle Open Agent Specification](https://github.com/oracle/agent-spec)
- [LaunchDarkly AgentControl Experimentation](https://launchdarkly.com/docs/home/agentcontrol/experimentation)
- [LaunchDarkly Experiment Health Checks](https://launchdarkly.com/docs/home/experimentation/health-checks)
- [OpenTelemetry GenAI semantic attributes](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
- [JSON Schema 2020-12](https://json-schema.org/specification)
- [MCP 2025-06-18 tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)

## 决策

1. Python 3.10+，标准库为必需依赖；YAML 与 OTel 是可选依赖。
2. Workflow 源是纯 JSON/YAML 数据；内部 `WorkflowSpec` 是唯一真相源。
3. 编译产生包含 SHA-256 Plan Digest、Node Hash、Prompt Hash 的不可变 Plan。
4. Runtime 在单进程内异步执行，外部隔离通过 MCP/HTTP Tool 实现。
5. SQLite 同时保存版本、Trace、实验与部署状态，避免第一版拆服务/表族。
6. 实验按调用方明确提供的业务随机化单位稳定分流；只有 Run 已落库且 Digest 与 Assignment 一致时才允许 Exposure。
7. Canvas 直接编辑 Workflow 源；布局存在 metadata 中且不影响执行 Digest。

## 未选择项

- 不内建 Memory、RAG、向量库或节点容器。
- 不执行 Workflow 内声明的任意代码路径。
- 不在缺少成熟统计证据时声称自动晋升或完整序贯推断。
- 不将 Open Agent Spec 的快速演进类型体系复制到核心模型。

## 可删除或推迟

高级 Checkpoint/HITL、Common-subgraph reuse、更多 Runtime Adapter、商业级排除层和 Guarded Rollout 调度均不阻塞当前端到端执行与在线 A/B 归因；引入前需要各自 ADR。
