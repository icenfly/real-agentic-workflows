# Workflow 0.1 定义与执行语义

Workflow 是 JSON 对象；安装 `real-agentic-workflows[yaml]` 后也可读取 YAML。权威机器 Schema 由 `real schema` 输出。

## 顶层字段

`spec_version`、`name`、`version`、`entry`、`nodes`、`edges` 必填。`input_schema` 与 `output_schema` 使用 JSON Schema 2020-12；Runtime 内建校验常用子集（type、required、properties、additionalProperties、items、enum）。需要完整方言校验时，可在 CI 额外运行标准 JSON Schema Validator。

`metadata.canvas.positions` 保存图布局。描述和 Canvas metadata 不参与 Plan Digest，因此布局变化不会制造新的执行版本。`metadata.runtime` 会改变运行行为并参与 Digest；可用 `trace_input=false` 或 `trace_output=false` 禁止 Store 保存 Workflow 级内容。

## 值引用

配置中的 `${$.input.name}`、`${$.nodes.node_id}`、`${$.vars.name}` 在执行时解析。字符串完全等于引用时保留 JSON 类型；嵌入普通字符串时非字符串值编码为 JSON。引用不存在会明确失败。

## 节点

| type | 必需 config | 行为 |
|---|---|---|
| `constant` | `value` | 解析并返回常量 |
| `template` | `template` | 解析模板 |
| `tool` | `tool`,`tool_version` | 调用显式注册且版本完全匹配的 Tool；`arguments` 为 JSON 对象 |
| `llm` | `provider`,`provider_version`,`model`,`model_version`,`prompt` | 调用显式注册且版本匹配的 Provider；可带 `system`,`parameters` |
| `branch` | `value` | 计算供条件边读取的值 |
| `join` | `wait_for` | 等待列出的活跃节点并返回其结果映射 |
| `subworkflow` | `plan_digest`,`input` | 从 Store 加载不可变子 Plan并在同一 Runtime 执行 |
| `output` | `value` | 产生 Workflow 输出 |
| `passthrough` | 无 | 返回指定 `value`，默认返回输入 |

通用字段：`retry`、`timeout_ms`、`idempotent`、`side_effect`。Side-effecting 且非幂等节点禁止 Retry。`config.trace_content=false` 可禁止 Trace 保存该节点输入输出。

## 边、分支、并行与循环

无 `when` 的出边全部激活，因此天然形成并行扇出。条件对象包含 `path`、`op` 和可选 `value`；支持 `eq/ne/gt/gte/lt/lte/in/contains/exists/truthy`。

一个批次的活跃节点用 `asyncio.gather` 并发执行。汇合点必须用 `join.wait_for` 明确等待对象，避免条件分支下的隐式 Join 歧义。

任何环上的边都必须声明 `max_iterations`。Runtime 对每条边计数并停止超限转换，静态校验拒绝无界环。

## 编译与不可变性

`real compile` 先校验，再对规范化执行内容计算 SHA-256。相同内容产生相同 Digest；同一 `name + version` 不能注册不同 Digest。Plan 加载时重新计算 Digest 和 Node/Prompt Hash 以发现篡改。
