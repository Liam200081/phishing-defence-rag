# M5 运行记录与验收状态

外部评测限制：development 已保存 57/60 条，但 429、413 和 timeout 使部分调用缺少 usage；完整 holdout 与重复运行尚未完成。保留原始部分结果及 `usage_incomplete` 标记，不把缺失 usage 算零，也不补跑以替代完整对照。这些是外部评测限制，不是本轮确认的代码缺陷；面试前不以补齐正式 KPI 为完成条件。

## 已保存批次（代码提交 8db5acc）

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

当前没有可发布的 B1/W0/A1 三项正式 KPI、逐 Case 人工签署复核或 held_out 重复运行。`evaluation/M5.md` 中的冻结复核口径未用于修改任何 holdout Case 或成功条件。日后如需发布正式 KPI，应在服务额度足够且请求大小被服务接受的条件下，用同一固定配置重新运行完整对照，逐条人工复核并保存独立评分文件；此项外部评测工作不列为本轮代码修复或面试前收敛条件。若该模型持续无法接受长邮件请求，应将这一限制作为独立设计问题处理，不能用删 Case、改 holdout 或把无 usage 记零的方式制造结果。

## 收敛补跑：复用有效结果

按“无模型服务错误、usage 完整”筛选，上述 development 部分记录可直接复用 B1 5 条、W0 3 条、A1 1 条；没有同一 Case 的三模式完整配对。`SA-00151-1` 的 A1 结果保留原记录，只补跑 B1/W0（运行 `M5-c8621d3b69bd44cc94a38dab4b8b38b6`）。配置与知识快照指纹相同，业务图、工具和 prompt 未变；M5 runner 的提交版本不同，执行时间也不同，因此只用于诊断，不声称总体质量效应。

短邮件 holdout Case `SA-00003-1` 在冻结配置下完成三模式配对（运行 `M5-11eafe7f2c24481fbbfbc0c32a42926e`，提交 `bc947b1`）。[逐断言复核草稿](reviews/SA-00003-1-codex-draft.json)已逐条将原 `false` 判定复核为 `unsupported`：原理由均为证据未能证明断言，并无相反证据，故没有标记 `contradictory`。用 `--allow-historical` 对同一保存运行重新评分，结果为本地 `score-432e170daad18416.json`；旧 `score-cb1d0d6e1c4441c7.json` 是布尔复核格式的历史评分，不再引用作当前分类结果。草稿由 Codex 编制，**没有人类复核签署**，不得作为正式 holdout KPI。

| 模式 | 有效 Case | 草稿 Case Success | 草稿 Evidence Support | 已知 token | LLM / Tool 调用 | Cost per Successful Case |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| B1 | 1/1 | 0/1 | 1/5 | 2,276 | 1 / 1 | 无成功 Case，不可计算 |
| W0 | 1/1 | 0/1 | 7/9 | 1,897 | 1 / 2 | 无成功 Case，不可计算 |
| A1 | 1/1 | 0/1 | 0/0（N/A） | 3,717 | 2 / 2 | 无成功 Case，不可计算 |

新评分的断言分类计数：B1 为 `supported=1, unsupported=4, contradictory=0`；W0 为 `7, 2, 0`；A1 无关键事实断言，Case 级状态为 `N/A`。Evidence Support Rate 的原二元分子/分母未变。

B1 只收到问题，输出通用清单而非该邮件的观察；W0 观察到邮件字段，但将未核实的发件身份描述为“routine”；A1 的验证与修复后仍未给出有引用的观察。单 Case 草稿不能代表 12 个 holdout Case 的效果。Case Success 与 Evidence Support 已在评分实现中解耦：非关键的不受支持断言只降低 Evidence Support；预定义条件未满足或关键错误才使 Case 失败。

Reflection OFF 只补跑同一 holdout Case（运行 `M5-f4c7bbc17763400b9423b680f1145e2b`，提交 `99631c7`）：1 次 LLM、2 次工具、2,432 token，回答完整；Reflection ON 为 2 次 LLM、2 次工具、3,717 token，最终受限。该 Case 的 Reflection 增加 **1 次 LLM 与 1,285 token**，并未带来可验收回答；仅此一例不证明总体趋势。Planning OFF 的相同 Case 补跑保存于 `M5-be3dfa66798f482dbeaaa2f862d24d1e/partial.json`，遇每日配额 429 后停止，**没有有效邮件场景 Planning 消融**。

代表性原因链：

- `SA-00003-1` A1：`trace[8]` 的验证 finding 指向未受证据支持的邮件观察草稿，修复后 `trace[11]` 仍报告缺少引用，最终回答是受限说明。类型：`verification/reflection` 与 `answer/grounding`；对应的 B1/W0/A1 调用和证据均保存在该 run 中。
- `PP-5-2` W0：原 development 部分记录的 `trace[4]` 中 `analyze_iocs` 明确为脚本注入的 `TimeoutError`，工具引用 `384c3e7b1be942aabe1ba0db69361c2a:analyze_iocs:2`；最终回答却推断高风险并提供未在证据中确认的银行报告地址。类型：`tool routing/execution` 后接 `answer/grounding`。这是故障注入 Case，不混同正常业务成功率。
- `PP-2-1` A1：原部分记录的 `calls[1]`、`calls[2]`、`calls[4]`、`calls[5]` 为 HTTP 413，最终回答 `request_error`；类型：`service/provider`。这是运行有效性问题，不能归因于 Agent 策略。

完整 holdout（12 Case）与既定重复运行仍未完成；每日 token 额度在本轮再次返回 429。所有有效结果原样保留，未为提高指标改动 holdout、prompt、阈值或成功条件。
