import hashlib
import json
import os
import re
import socket
import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from phishing_agent.tools import analyze_iocs, parse_email, retrieve_knowledge
from phishing_agent.rag.retrieve import ChromaRetriever

MANIFEST = json.loads((ROOT / "data/m2_manifest.json").read_text())
CASES = json.loads((ROOT / "evaluation/cases/m2_cases.json").read_text())["cases"]


def shingles(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return set(zip(*(words[i:] for i in range(5)))) if len(words) >= 5 else set()


class M2DataTests(unittest.TestCase):
    def test_manifest_sources_hashes_and_labels(self):
        samples = MANIFEST["samples"]
        self.assertEqual(len(samples), 12)
        self.assertEqual(len({s["sha256"] for s in samples}), 12)
        self.assertEqual(Counter(s["split"] for s in samples), {"development": 6, "held_out": 6})
        for s in samples:
            source = MANIFEST["sources"][s["source_id"]]
            self.assertTrue(source["use_conditions"] and source["label_scope"])
            self.assertTrue(source.get("commit") or source.get("version"))
            self.assertEqual(hashlib.sha256((ROOT / s["local_path"]).read_bytes()).hexdigest(), s["sha256"])
            self.assertNotIn("confirmed", s["source_label"])

    def test_cases_complete_and_no_split_leakage(self):
        self.assertEqual(len(CASES), 24)
        self.assertEqual(Counter(c["split"] for c in CASES), {"development": 12, "held_out": 12})
        self.assertEqual(len({c["case_id"] for c in CASES}), 24)
        required = {"sample_ref", "sample_sha256", "user_initial_input", "fixed_clarification_facts",
                    "expected_facts", "expected_evidence", "allowed_tools", "success_conditions",
                    "forbidden_key_assertions", "expected_result_status", "provenance_statement"}
        by_id = {s["sample_id"]: s for s in MANIFEST["samples"]}
        for c in CASES:
            self.assertFalse(required - c.keys())
            self.assertEqual(c["sample_sha256"], by_id[c["sample_ref"]]["sha256"])
            self.assertEqual(c["split"], by_id[c["sample_ref"]]["split"])
            self.assertTrue(c["expected_evidence"] and c["success_conditions"] and c["forbidden_key_assertions"])
            self.assertTrue(set(c["allowed_tools"]) <= {"retrieve_knowledge", "parse_email", "analyze_iocs"})
            self.assertIn("scripted", c["provenance_statement"])
        self.assertEqual(len({c["scenario"] for c in CASES}), 10)
        groups = {split: {s["source_group"] for s in MANIFEST["samples"] if s["split"] == split}
                  for split in ("development", "held_out")}
        self.assertFalse(groups["development"] & groups["held_out"])
        bodies = [(s["split"], shingles(parse_email(path=ROOT / s["local_path"])["body"]))
                  for s in MANIFEST["samples"]]
        for i, (split, a) in enumerate(bodies):
            for other_split, b in bodies[i + 1:]:
                if split != other_split and a and b:
                    self.assertLess(len(a & b) / len(a | b), 0.8)

    def test_injection_is_explicitly_synthetic(self):
        injected = [c for c in CASES if c["scenario"] == "prompt_injection_overlay"]
        self.assertEqual(len(injected), 2)
        for c in injected:
            self.assertIn("synthetic", c["mail_input"]["body_overlay_origin"])
            raw = (ROOT / c["mail_input"]["path"]).read_bytes()
            self.assertNotIn(c["mail_input"]["body_overlay"].encode(), raw)


class M2ToolTests(unittest.TestCase):
    def test_real_mime_all_samples_offline(self):
        with patch.object(socket.socket, "connect", side_effect=AssertionError("network access")):
            for s in MANIFEST["samples"]:
                with self.subTest(sample=s["sample_id"]):
                    email = parse_email(path=ROOT / s["local_path"])
                    self.assertEqual(email["sha256"], s["sha256"])
                    self.assertEqual(email["subject"], s["subject_observed"])
                    self.assertTrue(email["body_parts"])
                    result = analyze_iocs(email["urls"])
                    self.assertEqual(result["analysis"], "offline_static_only; observations are not verdicts")

    def test_missing_malformed_and_attachment(self):
        missing = parse_email(text="Body without headers")
        self.assertIn("missing_from", missing["warnings"])
        self.assertIn("missing_subject", missing["warnings"])
        raw = ("From: a@example.org\nSubject: test\nMIME-Version: 1.0\n"
               "Content-Type: multipart/mixed; boundary=xx\n\n--xx\nContent-Type: text/plain\n\n"
               "hello https://EXAMPLE.org:443/a\n--xx\nContent-Type: application/octet-stream\n"
               "Content-Disposition: attachment; filename=report.bin\n\nDATA\n--xx--\n")
        parsed = parse_email(text=raw)
        self.assertEqual(parsed["attachments"][0]["filename"], "report.bin")
        self.assertIn("hello", parsed["body"])
        self.assertTrue(parsed["urls"])
        with self.assertRaises(ValueError):
            parse_email(text="x", path="x.eml")

    def test_html_links_and_duplicate_positions(self):
        raw = ("From: a@example.org\nSubject: HTML\nContent-Type: text/html\n\n"
               '<a href="https://EXAMPLE.org:443/a">open</a> '
               '<a href="https://example.org/a">again</a>')
        parsed = parse_email(text=raw)
        self.assertGreaterEqual(len(parsed["urls"]), 2)
        result = analyze_iocs(parsed["urls"])
        match = next(i for i in result["iocs"] if i["normalized"] == "https://example.org/a")
        self.assertGreaterEqual(len(match["occurrences"]), 2)
        self.assertTrue(all(o["location"]["start"] is not None for o in match["occurrences"]))

    def test_ioc_normalization_static_observations(self):
        result = analyze_iocs([{"raw": "hxxps://EXAMPLE[.]org:443/a#frag", "location": {"part": 1}},
                               {"raw": "https://example.org/a", "location": {"part": 2}},
                               {"raw": "http://127.0.0.1/login", "location": {"part": 3}},
                               {"raw": "EXAMPLE.ORG", "location": {"field": "domain"}}])
        self.assertEqual(len(result["iocs"]), 3)
        self.assertEqual(len(result["iocs"][0]["occurrences"]), 2)
        self.assertIn("ip_literal_host", result["iocs"][1]["observations"])
        self.assertEqual(result["iocs"][2]["normalized"], "example.org")
        self.assertNotIn("malicious", json.dumps(result).lower())

    def test_retrieve_knowledge_real_chroma_and_model_source_chain(self):
        os.environ["HF_HUB_OFFLINE"] = "1"
        from sentence_transformers import SentenceTransformer
        import chromadb
        model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", local_files_only=True)
        from phishing_agent.rag.ingest import chunk_pages
        config = json.loads((ROOT / "config.json").read_text())
        chunks = chunk_pages(["Spear phishing targets a selected person. Ordinary phishing can target many recipients. " * 5],
                             "offline-fixture.pdf", "a" * 64, config)
        for chunk in chunks:
            chunk["data_version"] = "m2-test-data-v1"
        with patch.object(socket.socket, "connect", side_effect=AssertionError("network access")):
            retriever = ChromaRetriever(chunks, config, encoder=model, client=chromadb.EphemeralClient(),
                                        resolved_revision="local-cache")
            try:
                rows = retrieve_knowledge("What is spear phishing?", retriever, top_k=1)
            finally:
                retriever.close()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        for key in ("text", "document", "document_sha256", "page_start", "page_end", "chunk_id",
                    "score", "score_kind", "index_version", "data_version"):
            self.assertIn(key, row)
        self.assertEqual(row["page_start"], 1)
        self.assertEqual(row["data_version"], "m2-test-data-v1")


if __name__ == "__main__":
    unittest.main()
