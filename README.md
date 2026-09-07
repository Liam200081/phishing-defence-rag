# Phishing-Defence RAG System

A Retrieval-Augmented Generation (RAG) system for generating evidence-grounded phishing-defence guidance from authoritative cybersecurity sources.

The project combines semantic retrieval, an open-weight LLM, reference-free RAG evaluation, ablation experiments, and query-level failure analysis to investigate how preprocessing, retrieval quality, and context construction affect RAG reliability.

## Key Features

- RAG pipeline over authoritative cybersecurity documents
- Sentence-transformer-based semantic retrieval
- LLaMA 3.1 8B generation through Groq
- ChromaDB vector retrieval
- Context Relevance, Answer Relevance, and Faithfulness evaluation
- Per-query error and failure analysis
- PDF preprocessing ablation experiments
- Retrieval and prompt-construction diagnostics
- Reproducible evaluation over a 10-query benchmark

## System Workflow

```text
Cybersecurity Documents
        ↓
PDF Extraction & Preprocessing
        ↓
Text Chunking
        ↓
Sentence-Transformer Embeddings
        ↓
ChromaDB Vector Retrieval
        ↓
Retrieved Context
        ↓
LLaMA 3.1 8B via Groq
        ↓
Generated Answer
        ↓
RAG Evaluation + Error Analysis + Ablation Study
```

## Repository Structure

```text
phishing-defence-rag/
├── phishing_defence_rag.ipynb    # Main notebook: end-to-end RAG pipeline
├── README.md                      # Project documentation
├── requirements.txt               # Python dependencies
├── .gitignore                     # Excludes secrets, local files, and source PDFs
├── ablation_v1_comparison.xlsx    # Baseline vs cleaned-corpus comparison
└── data/                          # Local knowledge-base documents (PDFs not committed)
```

> Third-party source PDFs are intentionally excluded from version control. The notebook expects the local knowledge-base files to be placed in the `data/` directory before execution.

## Data Sources

The local knowledge base used for the experiments contains five phishing and cybersecurity documents:

- *Anti-Phishing Training Program Support* — CISA
- *Avoiding Social Engineering and Phishing Attacks* — CISA
- *Counter-Phishing Guidance for Non-Federal Organizations* — CISA
- *A Review of Phishing Attacks and Countermeasures for Internet of Things-Based Environments*
- *Phishing-Resistant Multi-Factor Authentication (MFA) Success Story: USDA FIDO Implementation* — CISA

The source documents are not redistributed in this repository.

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure the Groq API Key

Create a `.env` file in the project root directory:

```env
GROQ_API_KEY=your_groq_api_key_here
```

The `.env` file is excluded from version control through `.gitignore`. Do not commit API keys or other secrets to the repository.

A Groq API key can be obtained from:

```text
https://console.groq.com/
```

### 3. Add the Knowledge-Base Documents

Place the required PDF files inside:

```text
data/
```

The public repository does not include these third-party documents.

### 4. Run the Notebook

Open:

```text
phishing_defence_rag.ipynb
```

in Jupyter Notebook or Visual Studio Code, then run all cells from top to bottom.

The pipeline will:

1. Load and chunk the five PDF documents.
2. Build a ChromaDB vector index.
3. Run 10 benchmark queries through the RAG pipeline.
4. Compute Context Relevance, Answer Relevance, and Faithfulness metrics.
5. Export evaluation results.
6. Run the V1 preprocessing ablation experiment.
7. Produce diagnostic outputs for query-level failure analysis.

Expected runtime is approximately 35–45 seconds, depending on Groq API response time and local environment.

## System Requirements

- Python 3.10+
- Jupyter Notebook or Visual Studio Code
- Internet connection for Groq API calls
- Groq account and API key

The pipeline includes short delays between API calls to reduce the likelihood of free-tier rate-limit errors.

## Evaluation

The project evaluates the RAG pipeline using three reference-free metrics:

- **Context Relevance** — whether retrieved context is relevant to the query
- **Answer Relevance** — whether the generated response addresses the query
- **Faithfulness** — whether the answer is supported by the retrieved context

In addition to aggregate metrics, the notebook performs per-query analysis because average scores can hide important query-level regressions.

## Ablation Study

A preprocessing ablation compares the baseline corpus with a cleaned version of the source documents.

| Metric | Baseline Mean | V1 Cleaned Mean | Delta |
|---|---:|---:|---:|
| Context Relevance | 0.5424 | 0.5541 | +0.012 |
| Answer Relevance | 0.7573 | 0.7507 | -0.007 |
| Faithfulness | 0.6426 | 0.6549 | +0.012 |

The experiment shows that corpus cleaning can improve average retrieval quality and faithfulness while still degrading individual queries. This motivates query-level evaluation rather than relying only on aggregate scores.

## Failure Analysis

The notebook includes case-level diagnostics for retrieval and generation failures, including:

- metadata pollution in retrieved chunks
- query-dependent effects of preprocessing
- chunk-boundary changes after cleaning
- prompt/context construction issues
- cases where aggregate improvements hide individual regressions

These analyses are preserved alongside the implementation so that experimental conclusions can be traced back to retrieved evidence and generated outputs.

## Reproducibility Notes

- The ChromaDB collection is rebuilt on each full run; no persistent vector store is required.
- The language model is accessed through the Groq API with `temperature=0.3`.
- Small metric variations may occur across independent runs because generation is stochastic.
- Evaluation and diagnostic results can be exported to `.xlsx` files for inspection.
- Local secrets and third-party PDFs are excluded from version control.

## Project Scope

This is a small-scale RAG engineering project focused on retrieval quality, evaluation, error analysis, and preprocessing trade-offs in the phishing-defence domain.
