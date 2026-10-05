# M5 运行记录与验收状态

## 当前 HEAD 批次

- 运行 ID：`M5-98428c89cc5c4278802f69c971808c15`；代码提交：`8db5acce07b34dd07c97e770da7ca44bb02fc09a`，启动时 Git clean。
- 数据：development 12 Case × B1、W0、A1、A1-no-planning、A1-no-reflection；预期 60 条，实际保存于 `experiments/m5/<run_id>/partial.json` 的结果为 **57/60**。运行被人工停止；该批次不是完整对照，不能用于三项核心 KPI。
- 固定配置 SHA-256：`e8d8284dbaf891f1d6282f2c12b0656d6a3bcca8d4be29c436d780ed0bc98c89`；知识快照指纹：`a3e8143e24a4fa5e2c53b22222a905d92ae299e54271160356a77d13aae9781a`；源码树指纹：`75f6293928b506af5636ceb01f0694abf1be8477646eded5c010a2bebc1bf8bf`。服务探测成功，消耗 100 token，未归入模式成本。
- 服务错误：HTTP 429 每日 token 配额已接近 200,000，单个长请求还需要约 30,000 token；部分请求返回 HTTP 413，另有 timeout。调用的 usage 缺失，故成本不可完整计算。错误随真实执行顺序发生，不能解释为模式能力差异。

| 模式 | 已保存/预期 | 最终 completed | 模型错误次数（429 / 413 / timeout） | 缺 usage 调用 | 已知 token（成本下界） |
| --- | ---: | ---: | ---: | ---: | ---: |
| B1 | 11/12 | 8 | 5 / 0 / 1 | 6 | 20,618 |
| W0 | 12/12 | 3 | 6 / 6 / 0 | 12 | 36,335 |
| A1 | 12/12 | 0 | 13 / 13 / 1 | 27 | 64,893 |
| A1-no-planning | 11/12 | 0 | 12 / 10 / 0 | 22 | 80,962 |
| A1-no-reflection | 11/12 | 1 | 6 / 6 / 1 | 13 | 35,162 |

这些是运行状态诊断，不是 Case Success Rate。`completed` 不等于达到 Case 成功条件；已知 token 不是完整成本。

可核验例子：`PP-2-1` 的 A1 结果为 `limited`、回答状态 `request_error`。保存结果的 `calls[1]`、`calls[2]`、`calls[4]`、`calls[5]` 均是 HTTP 413；`trace[7]` 和 `trace[10]` 可定位到回答后的验证节点，`trace[26]` 是最终状态。它说明服务拒绝了请求，不能据此断定 Planning 或 Reflection 的质量。旧格式尚未把模型调用作为单独 trace 事件；下一版 runner 已将服务调用附入 trace 并校验 `service/provider` 失败引用。

## 验收结论

当前没有可发布的 B1/W0/A1 三项 KPI、逐 Case 人工复核或 held_out 重复运行。`evaluation/M5.md` 中的冻结复核口径未用于修改任何 holdout Case 或成功条件。完成验收仍需在服务额度足够且请求大小被服务接受的条件下，用同一固定配置重新运行完整对照，逐条复核并保存独立评分文件。若该模型持续无法接受长邮件请求，必须先将这一限制作为独立设计问题处理，不能用删 Case、改 holdout 或把无 usage 记零的方式制造结果。
