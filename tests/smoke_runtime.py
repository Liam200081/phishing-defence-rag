"""Opt-in dependency integration checks; no API keys, no network calls.

Run: .venv/bin/python tests/smoke_runtime.py -v
Uses actual PDF extraction, Chroma and Groq SDK with fixed vectors/HTTP responses.
The generated PDF is synthetic and is never represented as historical corpus data.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import fitz
import httpx
from groq import Groq
from phishing_agent.config import load_config
from phishing_agent.rag.ingest import ingest_pdfs
from phishing_agent.rag.retrieve import ChromaRetriever
from phishing_agent.llm import generate
from phishing_agent.pipeline import load_queries, run_queries
from phishing_agent.trace import code_provenance, export_run, read_run

ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_config(ROOT / 'config.json')


class FixedEncoder:
    def encode(self, texts, **kwargs):
        return [[1.0, 0.0] if 'spear' in t.lower() else [0.0, 1.0] for t in texts]


class RuntimeSmoke(unittest.TestCase):
    def test_real_pdf_chroma_ten_queries_and_export(self):
        with tempfile.TemporaryDirectory() as d:
            pdf_path = Path(d)/'synthetic.pdf'
            with fitz.open() as pdf:
                for content in ['Phishing uses deceptive messages. ' * 15,
                                'Spear phishing targets specific people. ' * 15]:
                    page = pdf.new_page()
                    remaining = page.insert_textbox(fitz.Rect(40, 40, 550, 800), content, fontsize=11)
                    self.assertGreater(remaining, 0)
                pdf.save(pdf_path)
            chunks, manifest = ingest_pdfs(d, CONFIG)
            self.assertEqual(manifest['documents'][0]['sha256'], hashlib.sha256(pdf_path.read_bytes()).hexdigest())
            self.assertEqual(manifest['documents'][0]['pages'], 2)
            self.assertEqual((chunks[0]['page_start'], chunks[0]['page_end']), (1, 2))
            retriever = ChromaRetriever(chunks, CONFIG, encoder=FixedEncoder(), resolved_revision='synthetic-fixed-vectors')
            try:
                hits = retriever.retrieve('What is spear phishing?', 3)
                self.assertEqual(hits[0]['document'], 'synthetic.pdf')
                self.assertAlmostEqual(hits[0]['score'], 0.0)
                run = run_queries(load_queries(ROOT/'evaluation/queries.json'), CONFIG, retriever.retrieve,
                                  lambda **kw: {'choices': [{'message': {'content': 'Fixed answer [E1].'}, 'finish_reason': 'stop'}]},
                                  data_manifest=manifest, embedding={**retriever.embedding, 'implementation': 'FixedEncoder, not SentenceTransformer'},
                                  index_version=retriever.index_version, provenance=code_provenance(ROOT),
                                  encode=retriever.encode, execution_mode='synthetic_pdf_real_chroma_mock_vectors_and_llm')
                self.assertEqual(run['summary']['complete'], 10)
                self.assertEqual(read_run(export_run(run, Path(d)/'results')), run)
            finally:
                retriever.close()

    def test_groq_sdk_request_and_response_with_mock_http(self):
        seen = []
        def respond(request):
            seen.append(json.loads(request.content))
            return httpx.Response(200, json={'id':'test-response', 'object':'chat.completion', 'created':0,
                'model': CONFIG['model'], 'choices':[{'index':0,'message':{'role':'assistant','content':'Partial'},'finish_reason':'length'}],
                'usage':{'prompt_tokens':10,'completion_tokens':8,'total_tokens':18}})
        with Groq(api_key='synthetic-test-key', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
            messages = [{'role':'user','content':'Exact context in test'}]
            result = generate(messages, CONFIG, client.chat.completions.create)
        self.assertEqual(seen[0]['messages'], messages)
        self.assertEqual(result['usage']['total_tokens'], 18)
        self.assertEqual(result['status'], 'length_terminated')
        self.assertEqual(result['response_model'], CONFIG['model'])

    def test_groq_sdk_error_is_not_a_normal_answer(self):
        attempts = []
        def respond(request):
            attempts.append(request)
            return httpx.Response(429, json={'error':{'message':'synthetic rate limit','type':'rate_limit_error'}})
        with Groq(api_key='synthetic-test-key', max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(respond))) as client:
            result = generate([{'role':'user','content':'test'}], CONFIG, client.chat.completions.create)
        self.assertEqual(result['status'], 'request_error')
        self.assertEqual(result['error']['type'], 'RateLimitError')
        self.assertEqual(len(attempts), 1)


if __name__ == '__main__':
    unittest.main()
