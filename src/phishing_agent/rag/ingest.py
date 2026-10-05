"""PDF extraction and word chunking with physical, 1-based page provenance."""
import hashlib
import json
import re
from pathlib import Path

def clean_pdf_text(text):
    """Remove common PDF header/footer/metadata noise from CISA documents."""
    # Remove URLs and email addresses
    text = re.sub(r'https?://\S+', ' ', text)
    text = re.sub(r'\S+@\S+\.\S+', ' ', text)
    # Remove CISA social handles (e.g. @CISAgov, @CISACyber)
    text = re.sub(r'@\w+', ' ', text)
    # Remove "RELATED TOPICS: ... /topics/..." metadata blocks
    text = re.sub(r'RELATED TOPICS:.*?(?=\n|$)', ' ', text, flags=re.IGNORECASE)
    # Remove cross-reference paths like "</topics/cyber-threats-and-response>"
    text = re.sub(r'</[^>]+>', ' ', text)
    # Remove "Return to top", "Subscribe", "FOIA Requests" boilerplate
    boilerplate = [
        'Return to top', 'Subscribe', 'FOIA Requests', 'No FEAR Act',
        'Privacy Policy', 'Office of Inspector General', 'Website Feedback',
        'An official website of', "Here's how you know",
        'Commercial Routing Assistance',
    ]
    for phrase in boilerplate:
        text = re.sub(re.escape(phrase), ' ', text, flags=re.IGNORECASE)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def chunk_pages(pages, document, document_sha256, config):
    """Retain the original word window and >50-character filter.

    Explicit page separators prevent accidental word fusion at PDF page breaks.
    Cleaning uses the historical rules, applied per page to retain provenance.
    """
    tokens = []
    for page_number, text in enumerate(pages, 1):
        if config["cleaning"]:
            text = clean_pdf_text(text)
        tokens.extend((word, page_number) for word in text.split())
    chunks = []
    step = config["chunk_size"] - config["chunk_overlap"]
    if step <= 0:
        raise ValueError("chunk overlap must be less than size")
    for offset in range(0, len(tokens), step):
        window = tokens[offset:offset + config["chunk_size"]]
        text = " ".join(word for word, _ in window)
        if len(text.strip()) <= config["min_chunk_chars"]:
            continue
        item = {"document": document, "document_sha256": document_sha256,
                "page_start": window[0][1], "page_end": window[-1][1],
                "word_start": offset, "word_end": offset + len(window), "text": text,
                "cleaning": config["cleaning"]}
        chunks.append({**item, "chunk_id": "chunk-" + digest(item)})
    return chunks


def ingest_pdfs(folder, config):
    paths = sorted(p for p in Path(folder).glob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    if not paths:
        raise FileNotFoundError(f"No PDF files in {folder}; original source PDFs are not distributed with B0.")
    import fitz
    chunks, documents = [], []
    for path in paths:
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        with fitz.open(path) as pdf:
            pages = [page.get_text() for page in pdf]
        documents.append({"document": path.name, "sha256": sha, "pages": len(pages)})
        chunks.extend(chunk_pages(pages, path.name, sha, config))
    if not chunks:
        raise ValueError("PDF extraction produced no usable chunks; OCR is not implemented in M1")
    data_version = digest(documents)
    for chunk in chunks:
        chunk["data_version"] = data_version
    return chunks, {"documents": documents, "data_version": data_version,
                    "extraction": "pymupdf-text/physical-pages-1-based", "chunking": "word-window-v1"}
