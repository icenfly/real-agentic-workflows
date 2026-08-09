# 运行、部署与安全

## 数据平面

先 `real deploy PLAN --environment prod`，再运行：

```bash
REAL_API_KEY=replace-me real serve \
  --host 0.0.0.0 --port 8080 \
  --api-key-env REAL_API_KEY \
  --max-concurrency 32
```

非 loopback 监听强制要求 Bearer API Key。生产环境仍应在反向代理后启用 TLS、身份系统、请求速率限制和集中 Secret Manager。

### API

- `GET /healthz`：存活检查；
- `POST /v1/runs`：按 `workflow + environment`、`plan_digest` 或 `experiment` 执行；
- `GET /v1/runs/{run_id}`：读取完整 Trace；
- `GET /v1/experiments/{name}`：读取实验状态；
- `POST /v1/outcomes`：记录延迟业务指标。

Server 限制请求体大小和并发数；达到并发上限返回 503。CLI 和 Server 使用同一 Store、Runtime、权限边界与实验代码。

包含 Side Effect 的调用应给 `POST /v1/runs` 发送 `Idempotency-Key`。相同 Key 和请求体只返回第一次 Run；相同 Key 搭配不同请求返回 409；仍在执行的重复请求也返回 409。

## Tool 与 Model

Workflow 只引用逻辑名称。应用通过 SDK 或 CLI `--tool NAME=module:callable@version`、`--provider ...` 显式注册实现。版本进入节点 Trace。

内建适配器：

- `HTTPJSONTool`：JSON POST，支持 Host Allowlist、Timeout 和 Header；
- `MCPStreamableHTTPTool`：MCP 2025-06-18 initialize/initialized/tools.call；
- `OpenAICompatibleProvider`：Chat Completions wire format；API Key 可来自环境变量。

CLI 可用 `--adapters examples/adapters.example.json` 声明这些适配器。配置强制填写实现版本和非空 Host Allowlist；敏感 Header 用 `headers_env` 映射环境变量，不能把 Secret 写进文件。

所有内建远程适配器在 SDK 中同样强制非空 Host Allowlist，不跟随 HTTP Redirect，并把响应体限制为 10 MB，避免重定向绕过和无界响应占用内存。

不要把 Token、Cookie 或 API Key 放在 Workflow。远程 Tool 必须设置 Allowlist；MCP Tool Annotation 按协议视为不可信。高风险 Side Effect 应由业务 Tool 实现审批或双重确认，且 Workflow 标记 `side_effect`/`idempotent`。

## 可观测性与隐私

SQLite 默认记录 Run 输入输出和节点内容。敏感节点设置 `trace_content=false`；Workflow 级输入输出分别用 `metadata.runtime.trace_input=false`、`trace_output=false` 控制。部署前依据数据分类制定 Retention/Deletion 策略。可将 `OpenTelemetrySink` 与 Store 放入 `CompositeTraceSink`，向应用已经配置的 OTel Provider 发出 Workflow/Node Span；框架本身不强制选择观测后端。

## 备份与回滚

`.real/state.db` 是单节点控制状态。生产部署应放在持久卷并使用 SQLite 在线备份或停写快照；多实例写入和高吞吐控制平面应把 Store 接口替换为事务数据库实现后再横向扩展。

`deploy` 保存上一 Digest；`rollback` 交换当前和上一 Digest，因而连续调用会在两个版本间切换。实验版本永不原地修改。

部署、实验创建/迭代/停止/晋升、Guardrail 自动暂停与回滚都会写入 `audit_log`。CLI 默认 Actor 是 `local-cli`，可通过 `REAL_ACTOR` 注入企业身份；`real audit` 读取最近事件。旧的 `AGENT_INFRA_ACTOR` 仍作为兼容变量。
