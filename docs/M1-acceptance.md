# ADR-002 M1 验收记录

结论：**PASS：M1 验收标准全部满足，可以进入 M2。** 本次实现停止于 M1，未开始 M2。

这个结论针对工程验收，不代表真实模型效果改善。未调用真实 Groq 生成 API，未复现缺失原始 PDF 的 B0 实验。

## 1. 实际文件变更及原因

修改现有文件：

| 文件 | 修改及目的 |
|---|---|
| `README.md` | 替换旧的默认运行说明，说明 B0 冻结、B1 运行、配置、证据口径、行为变化和限制；保留历史数值并标明 legacy |
| `.gitignore` | 排除本地虚拟环境、安装产物和 B1 运行目录，避免提交本地 trace/输入 |
| `requirements.txt` | 统一为 `-e .[runtime]` 安装入口，避免两套依赖清单 |

新增文件：

| 文件 | 实现及必要性 |
|---|---|
| `.env.example` | 提供不含密钥的 Groq 配置样例 |
| `config.json` | 集中模型、embedding/revision、分块、Top K、上下文预算、生成、prompt 和 evaluation 参数 |
| `pyproject.toml` | 安装包、CLI 和按用途安装的运行／Notebook／历史表格依赖 |
| `docs/ADR-002.md` | 完整保存用户批准的设计附件，不另设架构基线 |
| `docs/M1-acceptance.md` | 本验收记录与边界声明 |
| `experiments/historical/manifest.json` | B0 文件哈希、快照来源提交及历史溯源缺失项 |
| `experiments/historical/phishing_defence_rag.ipynb` | 原始 Notebook 的逐字节快照 |
| `experiments/historical/evaluation_results.xlsx` | baseline evaluation 快照 |
| `experiments/historical/ablation_v1_comparison.xlsx` | cleaning ablation 快照，保留下降结果 |
| `experiments/historical/error_analysis_details.xlsx` | failure analysis 快照，保留失败回答 |
| `experiments/historical/README.md` | 原说明及历史实验解释快照 |
| `experiments/historical/requirements.txt` | 原始依赖清单快照 |
| `experiments/historical/.gitignore` | 原始忽略配置快照 |
| `evaluation/queries.json` | 原十题逐字保留，增加稳定 Q1–Q10 ID |
| `notebooks/B1_baseline.ipynb` | 新实验展示入口，调用与 CLI 完全相同的 `run_b1` |
| `src/phishing_agent/__init__.py` | Python 包入口说明，无 Agent 状态或预留代码 |
| `src/phishing_agent/config.py` | 简单配置读取与边界校验 |
| `src/phishing_agent/rag/__init__.py` | RAG 包入口说明 |
| `src/phishing_agent/rag/ingest.py` | PDF 文本、历史清洗函数、分块、页码映射、文档／数据哈希 |
| `src/phishing_agent/rag/retrieve.py` | 显式 embedding、Chroma 索引、chunk/source/距离/index 关联 |
| `src/phishing_agent/rag/context.py` | 完整片段预算策略、证据偏移、预算不足显式失败 |
| `src/phishing_agent/prompts.py` | 最小确定性定义／对比／建议规则及版本化 prompt |
| `src/phishing_agent/llm.py` | 单次 Groq 请求及成功、空、长度、未知终止和错误记录 |
| `src/phishing_agent/metrics.py` | 仅以实际生成证据计算 legacy 诊断，保存具体评分窗口，按 query ID 对齐 |
| `src/phishing_agent/pipeline.py` | 共用单轮执行链及逐题记录；无隐式 Notebook 全局变量 |
| `src/phishing_agent/trace.py` | 代码与依赖版本、独占导出、回读一致性检查 |
| `src/phishing_agent/cli.py` | 本地执行／读取结果入口与明确退出码 |
| `tests/fixtures/q8_historical.json` | 历史 E9 单元格原文及来源哈希，原 PDF／页码明确 unknown |
| `tests/test_m1.py` | 36 项默认无依赖、无密钥、无网络单元／回归测试 |
| `tests/smoke_runtime.py` | 3 项真实 PDF／Chroma／SDK 离线接口测试 |
| `tests/smoke_embedding.py` | 显式运行的真实 embedding 冒烟脚本，合成 PDF 和 Mock LLM；不进入默认测试 |

没有创建后续里程碑的空目录、占位类或工作流抽象。

## 2. B0 保留

原始 Notebook 和三个 Excel 均原地保留，字节及 SHA-256 不变。七个原始仓库文件另有完整快照，快照哈希全部通过自动测试。

没有覆盖或删除任何旧实验数据；修改的 README／requirements／gitignore 原版本也已快照保存。原 Notebook 中存在的固定 `[:300]`、固定输出文件名和旧评价解释作为历史代码保留，不用于 B1。

历史缺失的 run ID、实际执行代码／配置、PDF 哈希、索引版本、实际生成上下文、finish reason、usage 标记为 unknown 或 unavailable。快照来源提交 `512e2ec` 不冒充历史运行提交。没有推测或补造历史 PDF 页码。

## 3. B1 完整执行链

1. CLI 或新 Notebook 调用 `run_b1`，加载同一 JSON 配置及固定 query ID。
2. PyMuPDF 提取本地 PDF，每页保留物理页序号；计算文件和数据版本哈希。
3. 可选使用历史清洗规则；按 200 词／30 重叠分块，保存文档、页范围、词偏移及 chunk ID。
4. SentenceTransformer 同时用于 Chroma 入库和查询；embedding revision 解析为不可变提交并保存；检索带距离及 index/data version。
5. Context Builder 按排名拼接全部完整片段，预算计入标签和分隔符；超预算时该题不生成，记录失败，绝不静默丢弃证据。
6. 确定性规则选择问题类型；单次 Groq 请求，保存原样 messages、实际上下文、回答、终止信息与 usage。
7. 逐题计算允许的 legacy diagnostics；实际证据由保存上下文的偏移切片取得。人工 grounding 为 `not_assessed`／null。
8. 以唯一 B1 run ID 独占创建 JSON 结果，保留失败题。回读核对 query ID、上下文、来源、评价 hash、完成状态和汇总。

## 4. 关键问题修复

| 问题 | 验收结果 |
|---|---|
| 固定 `[:300]` | B1 生成路径已移除；旧 Notebook 原样保留 |
| Q8 | 原始 Excel 片段的定义位于 300 字符之后；测试断言实际请求包含完整定义 |
| 真实检索链 Q8 | 合成 PDF + 真实 embedding/Chroma 冒烟中，实际上下文 615 字符，定义从位置 431 开始，完整进入 Mock LLM 请求 |
| 来源链 | document/hash → 1-based page range → chunk ID → cosine distance → index/data version → context offsets 已建立 |
| Prompt 冲突 | Q8 走 comparison；定义／解释与防御建议各自有规则，不再强制三条建议 |
| 响应完整性 | 空回答、length、未知／过滤终止、请求异常均显式记录；只有非空 stop 视为完整 API 回答 |
| 评测证据错位 | 新评分证据仅来自发送上下文；保存其 hash、证据 ID 和诊断实际输入；无未见证据用于新 grounding |
| 旧指标误用 | CR／AR／Faithfulness proxy 仍为 legacy cosine diagnostics；事实支持未经人工审查不赋分 |

## 5. 实际测试

| 检查 | 总数 | 通过 | 失败 | 跳过 |
|---|---:|---:|---:|---:|
| `PYTHONPATH=src python3 -m unittest discover -s tests -v` | 36 | 36 | 0 | 0 |
| `.venv/bin/python tests/smoke_runtime.py -v` | 3 | 3 | 0 | 0 |
| 真实 embedding 冒烟场景 | 1 | 1 | 0 | 0 |
| 合计独立测试／场景 | 40 | 40 | 0 | 0 |

默认 36 项测试不需要安装运行依赖或 API key。3 项接口测试需要运行依赖，但不联网；真实 embedding 场景显式下载公开权重，没有调用 Groq。

另外通过：`pip check`、Python 编译检查、CLI help、真实冒烟结果的 CLI 回读、`git diff --check`、B0 哈希检查。首次依赖／模型下载受沙箱网络限制，授权后完成；这不是被隐藏的测试失败。最终没有失败或跳过测试。

## 6. 实验结果和可追溯产物

没有重新运行真实生成模型实验，没有新的 B0/B1 效果、事实支持率或幻觉率结论。

实际运行了真实 embedding + Chroma 工程冒烟，使用一个从历史 Q8 摘录构造的合成 PDF、原十题和固定 Mock LLM：

- run ID：`B1-8e91f5cf5821441e84b1c1235bc84ee3`
- 本地结果：`experiments/b1/B1-8e91f5cf5821441e84b1c1235bc84ee3/run.json`（按 ADR 忽略，不提交运行 trace）
- execution mode：`synthetic_pdf_real_embedding_chroma_mock_llm`
- embedding：`sentence-transformers/all-MiniLM-L6-v2`
- 实际 revision：`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`
- 完整 Mock 响应 10/10，执行错误 0，诊断执行错误 0。这是软件执行检查，不是知识问答成功率。
- returned model 明确是 `mock-llm`；人工 grounding 未评价。

## 7. 回归及预期行为变化

原十题逐字不变，ID 顺序保持；真实 embedding/Chroma + Mock 生成执行十题通过。旧单页分块函数和清洗正则函数另有与原 Notebook 源码的等价测试。

预期变化：显式 SentenceTransformer/cosine 距离可能改变排名；页边界不再粘连单词；清洗按页进行以保留来源；发送完整片段；问题类型 prompt；默认输出额度 300 → 800；不完整响应不再当正常结果；评价窗口只来自实际上下文；导出目录按 run 隔离。

这些同时发生的基础修复不被包装成单一因素效果改善。没有观察到非预期软件回归；原始五 PDF 上的模型效果未验证。

## 8. 未解决问题与范围

- **M1 必需工程项：无未完成项。**
- **真实实验前置条件**：原始五份 PDF 及其历史哈希未提供；真实 Groq 生成未运行。无法重建 B0 缺失的 provenance；保留 unknown。此限制不影响用户指定的 Mock／边界／回读 M1 验收。
- **已知运行边界**：字符预算不是完整 token-window 估计；超过上下文预算明确失败。空 PDF 文本不做 OCR。provider length/context-limit/rate-limit 等错误被记录，M1 不自动恢复。
- **后续 M5**：原语料 B0/B1 对照、人工断言支持评价和更广泛消融尚未开展。
- **后续 M2–M6**：邮件工具、状态工作流、Planning／Reflection／Memory／Multi-Agent 等均未实现。

## 9. Git Diff 审计

已有文件只修改 README、requirements 和 gitignore；原 Notebook／三个 Excel 没有 diff。新增文件均直接服务 M1 或保存用户 ADR。

暂存区检查发现历史 `requirements.txt` 快照保留了原件 CRLF 换行，默认 `git diff --cached --check` 将其中 8 行 CR 视为尾随空白。未为消除提示而修改历史字节；使用 `git -c core.whitespace=cr-at-eol diff --cached --check` 审计通过，其余空白错误规则仍有效。这是已保留的原始格式，不是 B1 代码回归。

运行依赖沿用原项目的 Chroma、Groq、PyMuPDF、SentenceTransformer、dotenv。`huggingface-hub` 已是 embedding 栈依赖，这里显式声明，用于记录准确权重 revision；未引入新的 RAG／Agent 框架。pandas/openpyxl 只留在历史查看可选依赖中；默认测试使用标准库。没有 LangGraph、邮件解析、IOC、Planner、Verifier、Registry 或预留 Agent 代码。

## 10. 最终状态

**PASS：M1 验收标准全部满足，可以进入 M2。** 未开始 M2；是否开展后续里程碑由下一轮任务决定。
