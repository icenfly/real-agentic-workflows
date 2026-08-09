# Agent Infra 项目研究与实现交接

> 交接日期：2026-08-07
> 本文记录项目前期讨论、竞品研究、已经确认的方向、暂缓事项及后续实现约束。项目正式纲要见 [PROJECT_OUTLINE.md](./PROJECT_OUTLINE.md)。

## 一、最高优先级工程原则

### 1. 简洁优先：若无必要，勿增实体

实现过程中应始终遵守：

> **若无必要，勿增实体。**

这里的“实体”包括但不限于：

- 新的领域对象、数据库表、服务、进程和部署单元；
- 新的抽象层、接口、适配器、配置项和状态；
- 新的协议、文件格式、DSL 语法和框架概念；
- 为尚未出现的需求预留的扩展点；
- 与当前核心目标无关的 Memory、RAG、容器调度、多 Agent 社交协作等能力。

优先选择可以被删除、合并或复用的设计。一个概念能够解决问题时，不应拆成三个概念；现有数据结构可以表达时，不应新增数据结构；单体进程足够时，不应提前拆分微服务。

### 2. 不凭直觉发明架构：重要实现必须先检索和对比

> **若无必要，不应想到一种实现方式就直接开工。先搜索互联网，研究其他热门 Agent 框架如何实现，货比三家，再选择其中优秀、简洁且适合本项目的架构。**

对于任何非平凡能力，实施前至少完成：

1. 明确要解决的问题、约束和验收标准。
2. 搜索当前主流项目的官方文档、源码和架构说明。
3. 至少比较三个具有代表性的实现；若市场上不足三个，应记录实际找到的数量。
4. 说明每种实现的优点、缺点、成熟度、锁定风险和与本项目的适配程度。
5. 选择最小可行方案，并记录为什么没有选择其他方案。
6. 优先兼容、复用或适配现有标准；只有现有方案确实不能满足核心目标时，才自行设计。

不得只阅读营销页面。技术判断应尽量以官方文档、源码、测试、Issue、发布记录和可复现行为为依据。由于 Agent 生态变化很快，本文所列结论在实现时仍需重新联网核实。

建议每项重要架构决策建立简短 ADR，至少包含：

```text
问题：
约束：
候选 A / B / C：
官方资料和源码链接：
比较结果：
最终选择：
未选择其他方案的原因：
可以删除或推迟的部分：
```

### 3. 借鉴不等于照抄

本项目的宗旨之一是吸收热门框架的成功经验，但不应为了“对标”而复制其全部功能。只借鉴已被验证、确实服务于本项目目标的部分，并维持统一、可解释的核心模型。

## 二、项目定位

本项目拟建设一个面向 B 端的通用 Agent 框架，服务于重复执行特定业务任务的 Agent，例如审核、归类、调查、生成、数据处理、客服或业务流程自动化。

核心期待包括：

- **可观测性**：能够观察完整运行过程、各节点行为、成本、延迟、错误、质量和业务结果。
- **在线实验**：在线 A/B 测试是核心能力，不只是离线数据集评测。
- **模块性**：每个模块可独立设置 Prompt、模型、工具等，并可替换、组合和比较。
- **适应性**：兼容不同模型、工具和外部系统，避免被单一供应商绑定。
- **易用性**：支持流程图表达 Agent；CLI 覆盖框架能力。
- **Coding Agent 原生**：客户可以使用自己的 Codex、Claude Code，通过 CLI 创建、修改、测试和部署 Agent Workflow。
- **整体执行**：Workflow 最终可作为一体化 Agent 运行，避免无意义的运行时开销。

项目不以开放式、长期陪伴型 Agent 为主要场景，也不把长期语义 Memory 作为框架核心。客户如需要知识库或记忆，可以通过普通模块调用外部知识库、检索系统或存储服务。

## 三、对“自进化 Agent”的理解

本项目不把“自进化”理解为一个线上 Agent 在原地持续修改自身 Prompt 和代码，最后形成难以理解和维护的复杂系统。

本项目所说的自进化是：

1. 线上运行产生 Trace、质量指标和业务结果。
2. 客户或客户授权的 Codex、Claude Code 通过 CLI 创建新的 Workflow 候选版本。
3. 候选版本可以改变 Prompt、模型、工具、节点、边和整体 Workflow 结构。
4. 候选版本经过检查、离线评测、Shadow 或 Canary 后进入在线 A/B。
5. 根据真实线上业务反馈决定晋升、回滚、淘汰或继续修改。

因此，进化对象是**多个可追溯的 Workflow 版本**，而不是一个不断原地突变的 Agent 实例。

建议理解为：

```text
线上证据
  → Coding Agent 生成候选 Workflow
  → 静态检查与离线评测
  → Shadow / Canary
  → 在线 A/B
  → 晋升或淘汰
  → 产生下一代候选
```

必须避免实验版本在运行中被原地修改。任何 Prompt、模型、工具或拓扑变化都应形成新版本和新实验迭代，否则实验组定义漂移，无法进行可信归因。

## 四、当前优先级与暂缓事项

### 当前最高优先级

1. Workflow 的清晰定义、版本化与可复现执行。
2. 在线 A/B 分流、曝光记录、指标归因和实验决策。
3. 完整 Trace 与 Workflow/节点版本血缘。
4. CLI 全能力覆盖以及 Codex/Claude Code 友好性。
5. 流程图和文本定义使用同一个底层表示，避免双重真相源。
6. 借鉴并兼容主流工具、协议和 Agent 框架。

### 已决定暂缓

- 每个模块独立容器运行。

此前考虑过节点级独立容器，以支持依赖隔离、插拔和独立扩缩容。但主流 Agent 框架普遍没有采用这一默认模型，原因可能包括冷启动、网络序列化、调度、部署和运维复杂度。当前阶段先把 Workflow 作为整体执行单元；外部工具可通过 MCP、HTTP 等协议访问。确有安全、GPU、浏览器或依赖冲突需求时，再局部隔离。

### 当前不应主动扩展

- 框架内建长期 Memory 系统；
- 自建向量数据库或完整 RAG 平台；
- 为展示效果而做的多 Agent 群聊；
- 没有业务需求支撑的微服务拆分；
- 自创模型协议、工具协议或可观测协议；
- 尚未验证价值的自动修改、自动晋升和无人审批机制。

## 五、竞品研究结论

截至前期研究完成时，没有一个成熟框架同时完整满足：流程图创建、强可观测、结构级在线 A/B、全能力 CLI、Coding Agent 创建 Workflow 和高效整体执行。

市场能力主要分散在以下类别。

### 1. Flowise

适合借鉴：

- 流程图创建 Agent；
- Agentflow 的节点、分支、循环和路由；
- MCP、模型、工具和数据源生态；
- 节点级 Trace、可视化调试；
- Dataset、Evaluator 和多 Flow 离线比较。

不足：

- 主要是整个平台部署，不是轻量 Workflow 编译产物；
- 节点不是独立执行单元；
- CLI 不足以覆盖所有平台能力；
- 在线结构级 A/B 不是其核心。

资料：

- [Flowise 文档](https://docs.flowiseai.com/)
- [Flowise Evaluations](https://docs.flowiseai.com/using-flowise/evaluations)
- [Flowise Analytics](https://docs.flowiseai.com/using-flowise/analytics)

### 2. Dify

适合借鉴：

- 成熟的 Workflow Canvas；
- 单节点试运行、缓存输入和节点日志；
- Tool、Model、Agent Strategy、Datasource 等插件分类；
- 知识库作为可选节点，而非所有 Agent 的必需核心；
- Workflow 发布为 API 的产品体验。

不足：

- 原生实验与 A/B 能力相对弱；
- CLI 更偏插件开发，而不是全平台控制；
- Workflow 依赖 Dify 平台 Runtime。

资料：

- [Dify Workflow 快速入门](https://docs.dify.ai/en/guides/application-orchestrate/creating-an-application)
- [Dify Plugin Debugging](https://docs.dify.ai/en/develop-plugin/features-and-specs/plugin-types/remote-debug-a-plugin)
- [Dify Workflow Logs API](https://docs.dify.ai/api-reference/workflows/list-workflow-logs)

### 3. LangGraph + LangSmith

适合借鉴：

- Graph、条件、循环、并行、Checkpoint 和长任务执行；
- Trace、Token、成本、延迟、工具调用和在线 Evaluator；
- Dataset、Experiment、Regression 和逐样本对比；
- Prompt 版本、环境、晋升和回滚；
- CLI 构建和部署 Agent Server 镜像；
- 编译后的 Graph 在 Worker 中复用。

不足：

- Studio 更接近可视化调试器，不是完整低代码创建器；
- 实验强项主要是离线比较和评测，不是完整的 Workflow 结构在线实验控制平面；
- Graph 节点通常在同一 Worker Runtime 内执行。

资料：

- [LangGraph CLI](https://docs.langchain.com/langsmith/cli)
- [LangSmith Agent Server](https://docs.langchain.com/langsmith/agent-server)
- [LangSmith Experiment Comparison](https://docs.langchain.com/langsmith/compare-experiment-results)
- [LangSmith Evaluation Types](https://docs.langchain.com/langsmith/evaluation-types)

### 4. Mastra + Studio

适合借鉴：

- TypeScript-first 的开发体验；
- Workflow、Agent、Tool 和 MCP；
- Dataset 版本和 Experiment；
- Scorer、在线评分、Trace 和 CI 质量门禁；
- Prompt、模型、工具和代码变更的实验比较。

不足：

- 以代码定义为主；
- Studio 主要用于观察、调试和实验，不是完整流程图创建器；
- 缺少结构级在线实验与稳定分流的完整闭环。

资料：

- [Mastra Experiments](https://mastra.ai/blog/mastra-experiments)
- [Mastra Studio](https://mastra.ai/studio)

### 5. VoltAgent + VoltOps

适合借鉴：

- TypeScript 类型系统和工具 Schema；
- Workflow、Tool、MCP、Evaluator 和可观测；
- Prompt 版本、A/B、回滚及 Eval Dashboard；
- MCP 文档服务器，让 Coding Agent 理解框架；
- Node、Serverless、Edge、Docker/Kubernetes 等部署适配。

不足：

- Workflow 主要以代码定义；
- Console 更偏运行、调试和运维；
- 在线 A/B 的完整统计和 Workflow 结构实验能力需要进一步核实。

资料：

- [VoltAgent GitHub](https://github.com/VoltAgent/voltagent)

### 6. CrewAI + AMP/Studio

适合借鉴：

- 企业部署、Crew Studio、REST API 和 CLI；
- Agent/Task/Tool Repository；
- 运行 Trace、日志、触发器和外部系统集成；
- 低代码创建与代码创建并存。

不足：

- 核心模型更偏角色型多 Agent 协作；
- 对确定性、重复性 B2B Workflow 不一定是最小模型；
- 原生在线 A/B 和完整实验体系相对弱。

资料：

- [CrewAI AMP](https://docs.crewai.com/enterprise/introduction)

### 7. NVIDIA NeMo Agent Toolkit

适合借鉴：

- Tool、Agent、Workflow、Evaluator 和 Dataset Loader 的统一插件体系；
- CLI 可扩展；
- 本地与远端 Workflow Evaluation；
- 延迟、Token、错误、并发、瓶颈、Gantt 和 Trace Profiler。

不足：

- 没有流程图创建器；
- 没有原生在线 A/B 控制平面；
- 更适合作为评测和 Profiler 的架构参考。

资料：

- [NeMo Agent Evaluation](https://docs.nvidia.com/nemo/agent-toolkit/latest/workflows/evaluate.html)
- [NeMo Plugin System](https://docs.nvidia.com/nemo/agent-toolkit/latest/extend/plugins.html)

### 8. LaunchDarkly AgentControl：目前最直接的在线实验竞品

LaunchDarkly 在 2026 年推出 AgentControl 和 Agent Graphs，已经覆盖：

- Prompt、Model、Tool、Agent Config Variations；
- Agent Graph 可视化；
- Targeting、在线 A/B、A/A 和随机化单位；
- Judges、在线 Evaluation 和业务指标；
- 成本、延迟、错误、Token、满意度等指标；
- Progressive/Guarded Rollout 和自动回滚；
- Coding Agent Skills。

这是实现在线 A/B 时必须重点研究的产品。不要在没有对比 LaunchDarkly 的情况下自行设计实验分流、曝光或 Guarded Rollout。

其关键缺口也是本项目的潜在机会：

- LaunchDarkly 主要负责 Graph/Config 的定义、分发、Targeting 和观测；
- 实际模型调用、状态、边选择、终止和工具执行仍由客户应用负责；
- Graph 可以表达拓扑，但完整执行语义和 Runtime 不由 LaunchDarkly 负责。

资料：

- [LaunchDarkly AgentControl](https://launchdarkly.com/docs/home/agentcontrol)
- [LaunchDarkly Agents](https://launchdarkly.com/docs/home/agentcontrol/agents)
- [LaunchDarkly Agent Graphs](https://launchdarkly.com/docs/home/agentcontrol/agent-graphs)
- [LaunchDarkly Experiments](https://launchdarkly.com/docs/home/experimentation/create)
- [LaunchDarkly Randomization Units](https://launchdarkly.com/docs/home/experimentation/randomization)
- [LaunchDarkly Guarded Rollouts](https://launchdarkly.com/docs/home/releases/creating-guarded-rollouts)

### 9. 新兴项目

AgentOven、Brockley 等项目在 Agent Registry、DAG、Prompt 版本、A/B、CLI、OpenTelemetry 和控制平面方向上与本项目有相似描述，但成熟度和真实生产能力仍需谨慎验证，不应只根据营销声明选型。

- [AgentOven](https://github.com/agentoven/agentoven)
- [Brockley](https://brockley.ai/)

## 六、Agent 定义语言方向

项目提出过借鉴 SGLang，在 Agent 领域建立一种定义语言。这一方向可行，但不能把目标简化为“再发明一种 YAML”。

### 应借鉴 SGLang 的部分

SGLang 的价值来自前端语言与后端 Runtime 的联合设计：语言暴露结构，Runtime 利用结构做缓存、调度和性能优化。其壁垒不是语法，而是语言、编译表示和执行系统的配合。

- [SGLang 设计介绍](https://www.lmsys.org/blog/2024-01-17-sglang/)

对本项目而言，更合理的目标是：

```text
Agent Definition Language
  → Agent Graph IR
  → Validation / Instrumentation / Optimization
  → Immutable Execution Plan
  → Runtime
```

### 已有语言和规范必须先研究

在自创语法前，至少比较：

1. **Microsoft Declarative Workflows**：YAML 转可执行 Workflow Graph。
2. **Salesforce Agent Script**：Canvas/Script 编译为 Agent Graph，再由 Runtime 执行；同时表达确定性逻辑与 LLM 推理。
3. **Oracle Open Agent Spec**：框架无关的 Agent/Flow JSON/YAML 表示及 Runtime Adapter。
4. **Dify/Flowise 的 Workflow 导出格式**。
5. **LangGraph/Mastra/CrewAI 的代码级 Graph 模型**。

资料：

- [Microsoft Declarative Workflows](https://learn.microsoft.com/en-us/agent-framework/workflows/declarative)
- [Salesforce Agent Script](https://developer.salesforce.com/docs/ai/agentforce/guide/agent-script.html)
- [Salesforce Agent Graph IR](https://architect.salesforce.com/docs/architect/fundamentals/guide/hybrid-reasoning-agentforce-builder-agent-script)
- [Oracle Open Agent Spec](https://github.com/oracle/agent-spec)

优先考虑兼容或扩展 Open Agent Spec，而不是无依据地创建不兼容的新生态。但 Open Agent Spec 仍较新，不能未经验证就将内部核心绑定给它。

### 建议的最小资源模型

此前讨论形成过以下候选资源类型，但它们尚未正式确认，实施时必须继续做减法：

- `Workflow`：拓扑和执行语义；
- `Component`：可复用 Agent、LLM、Tool、Router 或 Evaluator；
- `Metric`：业务指标、质量指标和 Guardrail；
- `Experiment`：版本、分流、假设、统计和晋升条件；
- `Policy`：权限、成本、工具和审批约束；
- `Environment`：dev/staging/prod 绑定。

其中任何类型只有在不能被现有类型清晰表达时才应新增。尤其要避免把每个小配置都升级为一等实体。

### 语言/IR 至少应表达

- 输入输出 JSON Schema；
- 节点、边、条件、循环、并行和子 Workflow；
- Prompt、模型和工具引用；
- 确定性逻辑与 LLM 决策边界；
- Timeout、Retry、Idempotency 和 Side Effect；
- 版本、内容 Hash 和依赖版本；
- 自动注入 Trace 所需的稳定 ID；
- 可被 CLI、Canvas 和 SDK 无损读取的统一表示。

凭证和环境机密不应进入 Workflow 源文件。

### 可能的系统级差异：Variant-aware Execution

SGLang 通过共同 Prompt 前缀复用 KV Cache。本项目可以研究 Workflow Variant 之间的共同子图复用：

```text
extract → normalize → retrieve → decision-A
                               ↘ decision-B
```

在离线评测和 Shadow 模式中，共同前缀只执行一次，从分叉点分别执行候选版本。这可能带来：

- Common-subgraph reuse；
- Fork-from-checkpoint evaluation；
- Tool/Retrieval 结果复用；
- 更低的实验成本；
- 更准确的变更归因。

这是长期优化方向，不应阻塞第一版 Runtime。

## 七、在线 A/B 测试的关键要求

在线 A/B 是项目核心，不应退化为“将流量随机发到两个 Endpoint”。

### 1. 不可变版本和血缘

每次运行至少应能归因到：

- Workflow Plan Digest；
- Graph/Workflow Version；
- 各节点 Prompt Hash；
- Model、Provider 和 Model Version；
- Tool Version；
- Evaluator Version；
- Experiment、Iteration 和 Variation；
- Assignment Unit；
- Exposure 时间。

### 2. 正确的随机化单位

B2B 场景通常不应默认按单次 Request 随机，否则同一客户或业务对象会在不同 Workflow 间跳动。需要根据业务选择：

- `organization_id`；
- `customer_account_id`；
- `ticket_id`；
- `case_id`；
- `order_id`；
- `workflow_run_id`。

实验随机化单位与指标分析单位应一致。

### 3. 正确记录 Exposure

只有真正执行了对应 Variation 时才记录 Exposure。读取配置但没有运行 Workflow，不能算作曝光。

建议的关联链：

```text
assignment_id
  → exposure
  → workflow trace
  → intermediate outcome
  → delayed business outcome
```

### 4. 指标分层

- **Primary Metric**：真实业务结果，例如正确率、完成率、人工接管率或业务转化。
- **Secondary Metric**：LLM Judge、结构正确率、工具调用质量等代理指标。
- **Guardrail Metric**：错误率、P95 延迟、成本、越权和合规问题。

LLM Judge 不应成为唯一优化目标，避免 Workflow 学会迎合 Judge 而损害真实业务。

### 5. 实验健康与发布控制

至少应研究并逐步支持：

- A/A Test；
- Sample Ratio Mismatch；
- Sequential Testing 或 Bayesian 方法；
- 最小样本量和 Minimum Detectable Effect；
- Experiment Exclusion Layers；
- Global Holdout；
- 分群和异质性分析；
- Guarded Rollout；
- 自动暂停和回滚；
- 多实验/多重比较校正。

统计方法优先借鉴成熟实验平台，不应凭直觉自行发明。

### 6. 自动化边界

第一阶段建议：

- Coding Agent 可以自动生成候选；
- 系统可以自动静态检查、离线 Eval 和小流量 Canary；
- Guardrail 违规可以自动回滚；
- 正式晋升默认需要人工确认。

是否开放全自动晋升，应在积累足够可靠的业务指标、实验健康检查和审计能力后再决定。

## 八、CLI 与 Coding Agent 体验

CLI 是正式产品接口，不是 UI 的附属脚本。Canvas、CLI、SDK 和 Coding Agent 应共享同一后端能力和权限模型。

候选命令包括：

```text
agent init
agent schema
agent validate
agent compile
agent diff
agent run
agent trace
agent eval
agent experiment start
agent experiment status
agent experiment stop
agent experiment promote
agent rollback
```

CLI 应优先具备：

- 稳定、结构化的 `--json` 输出；
- `--dry-run`；
- 明确的退出码；
- 幂等命令；
- 可读的错误位置和修复提示；
- Schema、Examples 和可机读帮助；
- 非交互模式；
- 最小权限和审计记录。

应为 Codex、Claude Code 等提供：

- 官方 Skill/Instructions；
- 文档 MCP Server 或可检索文档索引；
- 完整示例和错误修复指南；
- 用于创建、修改和评测 Workflow 的安全命令集合。

可参考：

- VoltAgent 的 MCP 文档服务；
- GitHub Agentic Workflows 的“CLI 初始化 Skill → Coding Agent 生成声明 → CLI 编译”流程；
- Salesforce Agent Script 的 Coding Agent Skill 和编辑器/LSP 支持。

- [GitHub Agentic Workflows](https://docs.github.com/en/copilot/how-tos/github-agentic-workflows/creating-github-agentic-workflows)

## 九、建议的实施顺序

以下是研究阶段形成的建议，不是不可更改的最终计划：

1. 建立最小 Workflow Spec 和 Graph IR。
2. 实现 `validate`、`compile`、`run`、`diff` 的最小 CLI 闭环。
3. 选择一个成熟框架作为首个执行 Backend，或实现极小的内部解释 Runtime；两者需先比较。
4. 建立不可变 Workflow Version、Plan Digest 和 Trace Lineage。
5. 实现稳定分流、Exposure 和业务结果归因。
6. 实现实验的创建、状态、停止、晋升和回滚。
7. 提供 Codex/Claude Code Skill 与结构化 CLI。
8. Canvas 读取和修改同一个 Workflow 表示。
9. 再增加其他热门框架 Importer/Adapter。
10. 最后再做 Common-subgraph Reuse、上下文裁剪、并行规划等优化。

每一步都应检查是否能够删减，不应一开始同时建设语言、Canvas、Runtime、实验统计、插件市场和多云控制平面。

## 十、尚未确认的关键问题

以下问题不能由后续实现人员自行假设为已决定：

1. 第一版以哪个语言生态为主：TypeScript、Python，或语言无关服务？
2. 第一版 Runtime 是内部实现，还是编译到 LangGraph、Mastra 等现有框架？
3. Agent 定义是扩展 Open Agent Spec，还是设计独立格式并提供兼容层？
4. 在线实验统计引擎自行实现到什么程度，哪些能力直接集成现有实验平台？
5. 第一版是否需要 Canvas，还是先由 CLI/代码验证核心闭环？
6. 默认随机化单位和主要业务指标如何由具体客户配置？
7. Workflow 整体 A/B 与节点级 A/B 的优先顺序和归因边界是什么？
8. 自动生成、自动 Canary、自动回滚和自动晋升分别开放到什么权限级别？
9. 首批需要兼容哪些框架、Tool 协议和部署环境？
10. 哪些能力属于开源 Runtime，哪些属于商业控制平面？

这些问题应通过用户验证、竞品研究和最小原型决定。

## 十一、给后续实现人员的工作检查表

开始任何较大功能前，请确认：

- [ ] 该功能直接服务于已确认的项目纲要。
- [ ] 不做它是否会阻塞当前核心闭环？
- [ ] 是否可以删除一个实体、表、服务或抽象？
- [ ] 是否查阅了最新官方资料和源码？
- [ ] 是否至少比较了三个热门实现？
- [ ] 是否记录了为什么选择当前方案？
- [ ] 是否优先采用 MCP、OpenTelemetry、JSON Schema 等现有标准？
- [ ] 是否避免把 Memory、RAG 或容器编排塞进核心？
- [ ] 是否有可复现测试和明确验收标准？
- [ ] 是否能通过 CLI 完成同样操作？
- [ ] Codex/Claude Code 是否能通过结构化接口安全使用？
- [ ] 是否保留完整版本、实验和 Trace 血缘？
- [ ] 是否能够安全回滚？

## 十二、一句话交接

本项目不是要再造一个功能繁多的 Agent 框架，而是要建立一个**简洁、可定义、可观测、可由 Coding Agent 修改，并能通过真实线上 A/B 选择更优 Workflow 的 B2B Agent 系统**。

语言是入口，IR 是基础，在线实验是产品核心；任何实现都应先研究成熟项目、货比三家、选择最简优秀方案，若无必要，勿增实体。
