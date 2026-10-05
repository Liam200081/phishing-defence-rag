# Phishing-Defence RAG — B1 baseline

This repository implements **ADR-002 M1 only**: a single-round knowledge RAG baseline with explicit sources, complete budgeted context, response termination records and reproducible offline tests. The approved design is [ADR-002](docs/ADR-002.md). No Agent workflow, planning, verification loop, memory, email parsing or IOC analysis is implemented.

## B0 is frozen; B1 is separate

The original `phishing_defence_rag.ipynb`, `evaluation_results.xlsx`, `ablation_v1_comparison.xlsx` and `error_analysis_details.xlsx` remain byte-for-byte unchanged. Do not run the old notebook in the repository root: its historical code writes fixed output filenames. Use the B1 notebook for new experiments.

`experiments/historical/` contains exact snapshots of all seven original repository files, their SHA-256 manifest, and explicit unknown/unavailable provenance fields. The snapshot commit identifies the saved artifacts, **not** their historical execution version. Notebook source parameters cannot establish which configuration produced an old spreadsheet.

Historical B0 cleaning-ablation values remain:

| Legacy cosine diagnostic | Baseline mean | V1 cleaned mean | Delta |
|---|---:|---:|---:|
| Context Relevance | 0.5424 | 0.5541 | +0.0118 |
| Answer Relevance | 0.5286 | 0.3570 | -0.1716 |
| Faithfulness proxy | 0.5461 | 0.3691 | -0.1770 |

These are preserved historical diagnostics, **not** factual accuracy or hallucination rates. They do not establish any B1 improvement. V1 in that spreadsheet denotes historical cleaning, not B1.

## Run offline tests (no dependencies or API key required)

From the repository root, Python 3.10+:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Tests preserve the exact ten original questions and use fixed Mock responses. The Q8 regression uses the actual 500-character excerpt from `error_analysis_details.xlsx`, cell E9. Its definition begins after character 300. Its original PDF/page are unknown; test source mappings are explicitly synthetic. Passing these tests establishes software behavior, not model quality or original-corpus retrieval performance.

## Install and run B1 with local PDFs

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `GROQ_API_KEY` in `.env`, or export it in the shell. Place source PDFs in `data/`. The five historical sources were:

- Anti-Phishing Training Program Support — CISA
- Avoiding Social Engineering and Phishing Attacks — CISA
- Counter-Phishing Guidance for Non-Federal Organizations — CISA
- A Review of Phishing Attacks and Countermeasures for Internet of Things-Based Environments
- Phishing-Resistant Multi-Factor Authentication (MFA) Success Story: USDA FIDO Implementation — CISA

The originals and their historical hashes are unavailable in this checkout; third-party PDFs are not redistributed. B1 records the hashes of the files actually supplied. Do not represent substitute PDFs as the original B0 dataset.

```bash
phishing-rag --config config.json --queries evaluation/queries.json --pdf-dir data
# Or: python -m phishing_agent.cli --config config.json
# Read and validate a saved result without model calls:
phishing-rag --read-run experiments/b1/B1-<run-id>/run.json
```

CLI exit codes: `0` all responses completed and no evaluation errors; `2` incomplete response or evaluation error; `1` setup/read failure. A completed API response is not proof of factual correctness. Retrieval failures, no evidence and insufficient context budget produce explicit per-query failures; they are not silently turned into normal answers. API errors are saved without secret-bearing exception messages. There are no hidden SDK retries or repair calls.

For the presentation notebook:

```bash
python -m pip install -e '.[notebook]'
jupyter lab notebooks/B1_baseline.ipynb
```

Select the environment containing the installed package. Run All calls `phishing_agent.pipeline.run_b1`, exactly as the CLI does. The notebook contains no duplicate RAG implementation or dependency-install cells.

Optional dependency integration checks (offline, after runtime installation):

```bash
python tests/smoke_runtime.py -v
python -m pip check
```

These checks use actual PyMuPDF and Chroma on a generated synthetic PDF, fixed embedding vectors, and the actual Groq SDK with a Mock HTTP transport. They do not contact Groq or measure model quality.

## Configuration and intentional B1 behavior changes

All controls are in `config.json`, with a small validating loader. No configuration framework is required.

| Control | Default / meaning |
|---|---|
| Model | `openai/gpt-oss-20b` via Groq, as in the old source |
| Embedding | Explicit `sentence-transformers/all-MiniLM-L6-v2` for both indexing and querying |
| Embedding revision | `main`, resolved to an immutable Hugging Face commit and saved with each index/run; set that saved commit in config to repeat |
| Chunking | 200 words, 30 overlap, >50 characters; historical window/filter retained |
| Cleaning | Off; set `cleaning` to true in a copied config to use the original cleaning rules |
| Retrieval | Top K 3; explicit cosine distance, lower is better |
| Context | 12,000 Unicode characters including source labels and separators |
| Generation | temperature 0.3, top_p 1, max_tokens 800, timeout 60 seconds |
| Prompt | `b1-v1`; deterministic explanation/comparison/guidance rules |
| Evaluation | `b1-context-v1`; optional legacy cosine diagnostics |

The embedding model loaded in B0 was not explicitly connected to Chroma. B1 makes that connection explicit and fixes the index distance to cosine. This can change rankings; it is not a quality claim. Chroma uses an isolated ephemeral collection per build. Index identity hashes the actual chunks, embedding revision and distance setting; it does not depend on the temporary collection name.

Word windows can span pages, and each chunk carries document name/hash, physical 1-based page range, word offsets and stable chunk ID. B1 inserts page boundaries explicitly to avoid concatenating the last word of one page with the first of the next. Cleaning retains the historical regex function but applies it per page so provenance survives. These documented boundary changes mean B1 is not a bitwise replay of B0 preprocessing.

The Context Builder includes **all retrieved chunks in full**. It does not use per-chunk `[:300]`, silently skip lower-ranked evidence, or partially slice a chunk. When the whole selection exceeds the configured budget, it records the required size and does not request an answer. Adjust the budget or Top K explicitly and create a new run. The character budget controls reference evidence, not the model's full token window; query/prompt/output tokens are additional. Provider context-limit errors remain visible as request errors. M1 has no automatic recovery.

`max_tokens` increases from the historical source's 300 to 800 as an initial development setting, not a measured optimum. Q8 is handled as a comparison. Definitions receive explanations, and defensive questions receive relevant steps without an arbitrary count of three.

## Trace and evaluation contract

New results are exclusively created under `experiments/b1/B1-<unique-id>/run.json`; existing run directories cannot be overwritten. Each record contains:

- run ID/time, baseline and execution mode;
- Git commit/dirty status, source-file hashes, Python and relevant package versions;
- full configuration, requested/returned model, embedding model and resolved revision, prompt version and exact messages;
- actual PDF hashes, dataset and index identity;
- full query IDs/text, ranked retrieval hits and distance meaning;
- actual context with source labels, evidence offsets and complete chunk text;
- answer, request/return status, finish reason, usage when provided, empty/truncated flags and errors;
- per-query evaluation, context hash, evidence IDs and aggregate completion/error counts.

Readback checks query alignment, context/message identity, evidence/source mapping, evaluation context hashes, response completion and summary counts. Git hashes plus dirty source hashes distinguish uncommitted implementations; untracked source files are included. API delivery on request errors is not confirmed; saved messages record the attempted request.

Evaluation only accesses the actual generation context. CR, AR and Faithfulness proxy remain **legacy diagnostic metrics**. Their historical window sizes (500 characters per evidence/answer, 1,000 joined context characters) are retained within that diagnostic calculation, and the exact diagnostic inputs are saved. Those windows do not truncate model context. Because B0 scored text the generator did not see, old and new values are not directly comparable under an identical evidence scope.

Empty, length-terminated, filtered, unknown-termination or failed responses are not scored as normal completed answers. Human claim-level grounding is explicitly `not_assessed` with a null score; cosine similarity does not supply a factual-support KPI. M1 does not add an LLM judge or implement the later M5 evaluation program.

## Scope and limitations

The original ten questions remain the B1 regression set. Historical failures and provenance gaps remain visible. No real Groq experiment or new effectiveness comparison was run for this implementation. Real source-corpus evaluation requires the local PDFs and a key. M2–M6 remain outside this change.

See [M1 acceptance record](docs/M1-acceptance.md) for the exact changes, checks and final status.
