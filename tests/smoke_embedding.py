"""Opt-in real embedding smoke: downloads a public model, never calls Groq.

HF_HOME=.venv/huggingface .venv/bin/python tests/smoke_embedding.py
Synthetic PDF, real SentenceTransformer + Chroma, fixed Mock LLM responses.
Not a B0/B1 quality comparison. Not included in offline test discovery.
"""
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import fitz
from phishing_agent.config import load_config
from phishing_agent.pipeline import load_queries, run_queries
from phishing_agent.rag.ingest import ingest_pdfs
from phishing_agent.rag.retrieve import ChromaRetriever
from phishing_agent.trace import code_provenance, export_run, read_run


def main():
    root = Path(__file__).resolve().parents[1]
    config = load_config(root/'config.json')
    fixture = json.loads((root/'tests/fixtures/q8_historical.json').read_text())
    with tempfile.TemporaryDirectory() as d:
        with fitz.open() as pdf:
            page = pdf.new_page()
            room = page.insert_textbox(fitz.Rect(40, 40, 550, 800), fixture['text'], fontsize=11)
            assert room > 0
            pdf.save(Path(d)/'synthetic-q8-excerpt.pdf')
        chunks, manifest = ingest_pdfs(d, config)
        manifest['note'] = 'Synthetic PDF containing a historical spreadsheet excerpt; not the original PDF'
        retriever = ChromaRetriever(chunks, config)
        requests = []
        def completion(**kwargs):
            requests.append(kwargs)
            return {'model':'mock-llm', 'choices':[{'message':{'content':'Fixed smoke response [E1].'}, 'finish_reason':'stop'}]}
        try:
            run = run_queries(load_queries(root/'evaluation/queries.json'), config, retriever.retrieve, completion,
                              data_manifest=manifest, embedding=retriever.embedding, index_version=retriever.index_version,
                              provenance=code_provenance(root), encode=retriever.encode,
                              execution_mode='synthetic_pdf_real_embedding_chroma_mock_llm')
            assert len(requests) == 10 and run['summary']['complete'] == 10
            assert run['summary']['evaluation_errors'] == 0
            assert fixture['definition'] in requests[7]['messages'][-1]['content']
            path = export_run(run, root/'experiments/b1')
            assert read_run(path) == run
            print(json.dumps({'result':'PASS', 'path':str(path), 'embedding':retriever.embedding, 'summary':run['summary']}))
        finally:
            retriever.close()


if __name__ == '__main__':
    main()
