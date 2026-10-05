# Phishing-Defence RAG

本项目用可追溯的知识检索和邮件静态分析，辅助回答网络钓鱼防护问题。当前核心实现覆盖 M1–M5：保留 B1 单轮 RAG 基线，并实现带状态、澄清、规划、工具路由、验证和有限恢复的 **Single-Agent LangGraph** 工作流。回答应区分邮件中可观察的事实、用户报告的操作和知识来源；静态 IOC 观察不构成恶意判定，也不能证明账户已被入侵。

## 架构

`src/phishing_agent/graph.py` 构建一个以 SQLite checkpoint 保存状态的 LangGraph。主路径为 `intake → plan → act → assess → answer → verify → finish`；需要用户事实时，`assess` 进入 `clarify` 并通过 interrupt 暂停，随后可用 `resume` 继续。验证结果可以在预算内引导回答修复、重建上下文、补充检索、工具重试或重新规划；达到限制时返回受限结论。这里的 Planning 是经过校验的确定性任务计划，默认不增加规划模型调用。

三个只读工具由 `src/phishing_agent/tools/` 提供：

| 工具 | 作用 |
|---|---|
| `parse_email` | 解析内联邮件或本地 `.eml` 的 RFC 822/MIME 内容，提取头字段、正文、URL、附件元数据和解析警告；不抓取链接或执行附件。 |
| `analyze_iocs` | 对邮件 URL/域名做离线归一化和结构性观察，保留来源位置；输出不是信誉查询或恶意性裁决。 |
| `retrieve_knowledge` | 使用 M1 的 PDF/Chroma 检索器返回带文档、页码、chunk 和索引来源信息的知识证据。 |

`src/phishing_agent/state.py` 记录请求、事实修订、工具历史、证据、计划、澄清、回答、验证结果和预算。用户更正事实后，相关计划任务及其下游结果会失效；可复用的工具结果留在缓存中。邮件场景先解析邮件，仅在提取到 URL 时分析 IOC，并检索相关防护知识；知识问答直接检索。需要判断用户后续操作或账户影响时，工作流可澄清是否点击链接及是否提交凭据。工具执行、澄清和恢复均受 `src/phishing_agent/planning.py` 中的预算约束。

`src/phishing_agent/verification.py` 检查回答状态、证据引用和部分不受支持或过度确定的说法，生成结构化 finding 与恢复动作。Reflection 使用这些 finding 驱动有限次数的修复；验证通过或预算耗尽后结束。验证规则不能替代人工的逐断言证据复核。

## B1 / W0 / A1 评测框架

`src/phishing_agent/evaluation.py` 和 `src/phishing_agent/eval_cli.py` 支持固定 Case、运行记录、人工复核评分及失败分析：

| 模式 | 输入与流程 |
|---|---|
| `B1` | 单轮知识 RAG 基线；邮件 Case 只给问题，不给邮件。 |
| `W0` | 邮件和问题进入同一工作流，使用固定工具路径与固定知识查询，不启用 Planning/Reflection 开关。 |
| `A1` | 邮件和问题进入 Single-Agent 工作流，按已确认事实调整计划/查询，并启用验证与有限恢复。 |

还提供 `A1-no-planning`、`A1-no-reflection` 两个消融模式。事件 Case 分为 `development` 和 `held_out`；知识回归使用 `evaluation/queries.json`。框架保存配置、代码和知识快照指纹、逐步 trace、模型调用及 usage，并通过独立复核文件计算 Case Success、Evidence Support 和 Cost per Successful Case；缺失 usage 不按零成本处理。正式评测要求 clean Git 工作区；模型服务预检失败会阻止批次启动，运行中遇到服务错误则停止并保存部分记录。

**当前状态：**M1–M5 核心开发完成，当前已报告 **94 tests passed**。`evaluation/M5-results.md` 记录的是部分 development 运行和少量诊断性配对；外部模型 API 的配额/服务限制使完整 `held_out` 对照及重复运行尚未完成。因此目前没有可发布的完整 holdout KPI，也不能从现有样本推出 A1 相对 B1/W0 的总体效果。具体评测口径见 `evaluation/M5.md`。Multi-Agent 仅列为 Future Work，当前未实现。

## 快速启动

从仓库根目录运行，需要 Python 3.10+。运行时安装使用 `requirements.txt`（对应 `.[runtime]`）；Groq 调用需要在 `.env` 中设置 `GROQ_API_KEY`。PDF 语料放在 `data/`，邮件评测 Case 与本地邮件样本位于 `evaluation/cases/` 和 `data/emails/`。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# 编辑 .env，设置 GROQ_API_KEY
```

运行 B1 基线（结果保存到 `experiments/b1/`）：

```bash
phishing-rag --config config.json --queries evaluation/queries.json --pdf-dir data
```

运行 M5 development 评测需要可用的外部模型额度；正式命令与复核格式见 `evaluation/M5.md`。例如运行三种核心模式：

```bash
python -m phishing_agent.eval_cli run --suite event --split development --modes B1 W0 A1 --config evaluation/m5_config.json --pdf-dir data
```

本地测试不需要模型 API：

```bash
python -m unittest discover -s tests -v
```

上述 **94 tests passed** 是目前项目已报告的测试状态，并非模型质量或完整 holdout 结果。

## 项目结构

| 路径 | 内容 |
|---|---|
| `src/phishing_agent/graph.py`、`state.py`、`planning.py`、`verification.py` | Single-Agent 工作流、状态、计划及验证。 |
| `src/phishing_agent/tools/`、`rag/` | 邮件/IOC/知识工具与 PDF 检索。 |
| `src/phishing_agent/pipeline.py`、`cli.py` | B1 单轮基线和命令行入口。 |
| `src/phishing_agent/evaluation.py`、`eval_cli.py` | M5 运行、评分和分析。 |
| `config.json`、`evaluation/m5_config.json` | B1 与 M5 配置。 |
| `evaluation/cases/`、`evaluation/M5.md`、`evaluation/M5-results.md` | 固定 Case、评测协议和结果状态。 |
| `tests/` | M1–M5 自动化测试。 |
| `docs/ADR-002.md`、`docs/M1-acceptance.md`、`experiments/historical/` | 设计与历史基线记录。 |

## Future Work

- 在外部 API 额度与服务条件允许时，完成冻结条件下的完整 holdout、重复运行与人工复核。
- 探索 Multi-Agent 架构；仓库当前只有 Single-Agent 工作流。
