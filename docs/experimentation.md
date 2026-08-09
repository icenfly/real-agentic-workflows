# 在线实验与归因

## 不变量

1. 一个 Variation 必须引用已注册的不可变 Plan Digest。
2. 调用方必须显式传 `assignment_unit` 名称与值；框架不默认按 Request 随机。
3. 分流使用 `experiment_id + iteration + unit_value` 的 SHA-256 bucket，同一迭代内稳定。
4. Assignment 不等于 Exposure。只有 Run 已持久化且实际 Plan Digest 与分配一致，才能记录 Exposure。
5. Outcome 关联 Assignment，可晚于请求到达；`idempotency_key` 防止业务事件重放。
6. Trace 保存 Experiment、Iteration、Variation、Assignment 和 Plan 血缘。

## 配置示例

```json
{
  "name": "reply_quality",
  "assignment_unit": "organization_id",
  "primary_metric": "resolved",
  "guardrails": [
    {"metric": "agent.error", "direction": "max", "threshold": 0.05, "min_units": 30}
  ],
  "variations": [
    {"key": "control", "source": "control.plan.json", "weight": 1},
    {"key": "treatment", "source": "treatment.plan.json", "weight": 1}
  ]
}
```

停止后可用 `real experiment iterate` 在同一 Experiment 下开始新迭代；历史 Variation 映射、Assignment、Exposure 与 Run 保留。

## 指标与健康

`status` 按 Variation 同时报告去重随机化单位数和 Exposure 事件数。SRM 使用前者，避免一个客户的重试或多次执行扭曲样本比例。Outcome 先在 Assignment/分析单位内聚合，再计算 Variation 的 count/mean/min/max。达到 100 个随机化单位后，用期望权重计算最大标准化偏差；`|z| >= 3.29` 标记 `sample_ratio_mismatch`。这是运维健康信号，不是完整的假设检验或胜者判定。

框架不以 LLM Judge 取代业务指标。`primary_metric` 应是业务结果；Judge/结构评分作为 Secondary；错误、延迟、成本与合规作为 Guardrail。正式晋升由 `experiment promote` 显式触发，随后可用 `rollback` 恢复上一 Plan。

HTTP 数据平面对实验 Run 自动记录 `agent.error`、`agent.latency_ms`，存在模型成本属性时也记录 `agent.cost`。每条 Guardrail 声明 `max`/`min`、阈值和最小分析单位；任一 Variation 达到样本门槛并越界时，实验自动转为 stopped，停止新 Assignment。系统不会据此自动晋升。

## 统计边界

当前框架负责可靠采样和归因数据，不声称提供完整 Sequential/Bayesian 推断、MDE 规划、多重比较校正、异质性分析、Global Holdout 或自动晋升。将这些能力接入成熟实验平台比在 Runtime 内临时发明统计方法更安全；加入任何自动决策前应单独建立 ADR 和验证数据。
