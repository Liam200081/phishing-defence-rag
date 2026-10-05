"""W0 path and checkpoint acceptance using the M1/M2 tools on normal paths."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from phishing_agent.config import load_config
from phishing_agent.graph import open_workflow
from phishing_agent.rag.ingest import chunk_pages
from phishing_agent.rag.retrieve import ChromaRetriever
from phishing_agent.state import current_facts


def completion(**_kwargs):
    return {"id": "fixed-answer", "model": "fixed-test", "choices": [
        {"message": {"content": "Evidence supports cautious phishing guidance [E1]."},
         "finish_reason": "stop"}], "usage": {}}


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["HF_HUB_OFFLINE"] = "1"
        import chromadb
        from sentence_transformers import SentenceTransformer
        cls.config = load_config(ROOT / "config.json")
        cls.config["top_k"] = 1
        text = ("Spear phishing targets selected people. Suspicious email links require caution. "
                "Opening an email alone differs from clicking a link. After credentials are entered, "
                "change the password through a trusted site and report the incident. ") * 3
        chunks = chunk_pages([text], "w0-fixture.pdf", "a" * 64, cls.config)
        for chunk in chunks:
            chunk["data_version"] = "w0-test-v1"
        model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", local_files_only=True)
        cls.retriever = ChromaRetriever(chunks, cls.config, encoder=model,
                                        client=chromadb.EphemeralClient(), resolved_revision="local-cache")

    @classmethod
    def tearDownClass(cls):
        cls.retriever.close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "checkpoints.sqlite"
        self.messages = []
        def recorded_completion(**kwargs):
            self.messages.append(kwargs["messages"])
            return completion(**kwargs)
        self.recorded_completion = recorded_completion
        self.context = open_workflow(self.path, self.retriever, self.config, recorded_completion)
        self.workflow = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_knowledge_fixed_path_and_case_isolation(self):
        a = self.workflow.run("a", {"kind": "knowledge", "question": "What is spear phishing?",
                                    "facts": {"clicked_link": True}})
        b = self.workflow.run("b", {"kind": "knowledge", "question": "What is phishing?",
                                    "facts": {"clicked_link": False}})
        self.assertEqual([h["tool"] for h in a["tool_history"]], ["retrieve_knowledge"])
        self.assertEqual([h["tool"] for h in b["tool_history"]], ["retrieve_knowledge"])
        self.assertEqual(a["status"], "completed")
        self.assertFalse(self.workflow.graph.get_state(self.workflow._config("a")).next)
        self.assertEqual(a["evidence"][0]["kind"], "knowledge")
        self.assertIn("chunk_id", a["evidence"][0]["data"])
        self.assertTrue(current_facts(self.workflow.state("a")["facts"])["clicked_link"])
        self.assertFalse(current_facts(self.workflow.state("b")["facts"])["clicked_link"])

    def test_email_path_real_tools_no_unnecessary_question(self):
        mail = ("From: alerts@example.org\nSubject: Notice\nContent-Type: text/plain\n\n"
                "Read https://example.org/login")
        state = self.workflow.run("mail", {"kind": "email", "question": "Is this email suspicious?",
                                           "email_text": mail})
        self.assertEqual([h["tool"] for h in state["tool_history"]],
                         ["parse_email", "analyze_iocs", "retrieve_knowledge"])
        self.assertTrue(all(h["status"] == "ok" for h in state["tool_history"]))
        self.assertEqual([e["kind"] for e in state["evidence"]], ["email", "iocs", "knowledge"])
        self.assertEqual(state["evidence"][1]["data"]["iocs"][0]["normalized"],
                         "https://example.org/login")
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["clarification"]["asked"], [])

    def test_email_path_with_explicit_none_text_uses_path(self):
        source = Path(self.tmp.name) / "message.eml"
        source.write_text("From: alerts@example.org\nSubject: Path input\n\n"
                          "Read https://example.org/login", encoding="utf-8")
        state = self.workflow.run("path", {"kind": "email", "question": "Is this email suspicious?",
                                           "email_path": str(source), "email_text": None})
        self.assertEqual(state["tool_history"][0]["tool"], "parse_email")
        self.assertEqual(state["tool_history"][0]["status"], "ok")
        email = next(e["data"] for e in state["evidence"] if e["kind"] == "email")
        self.assertEqual(email["source"], str(source))
        self.assertEqual(email["subject"], "Path input")
        self.assertEqual(email["urls"][0]["raw"], "https://example.org/login")

    def test_account_impact_question_asks_only_for_decision_changing_facts(self):
        mail = "From: a@example.org\nSubject: Check\n\nVisit https://example.org"
        waiting = self.workflow.run("impact", {"kind": "email",
            "question": "我的账户是否受到影响？", "email_text": mail})
        self.assertEqual(waiting["status"], "waiting_clarification")
        self.assertEqual(waiting["clarification"]["pending"], "clicked_link")
        clicked = self.workflow.resume("impact", {"clicked_link": True})
        self.assertEqual(clicked["clarification"]["pending"], "credential_submitted")
        done = self.workflow.resume("impact", {"credential_submitted": False})
        self.assertEqual(done["status"], "completed")
        self.assertEqual(len(done["clarification"]["asked"]), 2)

        opened = self.workflow.run("impact-opened", {"kind": "email",
            "question": "Is my account compromised?", "email_text": mail,
            "facts": {"clicked_link": False}})
        self.assertEqual(opened["status"], "completed")
        self.assertEqual(opened["clarification"]["asked"], [])

    def test_account_takeover_and_attacker_access_phrasings(self):
        mail = "From: a@example.org\nSubject: Check\n\nVisit https://example.org"
        questions = ("Has my account been taken over?",
                     "Could the attacker access my account?",
                     "Has someone accessed my account?",
                     "Has my account been accessed?",
                     "Was my account accessed?",
                     "Could the attacker have accessed my account?")
        for index, question in enumerate(questions):
            with self.subTest(question=question):
                case_id = f"access-{index}"
                waiting = self.workflow.run(case_id, {"kind": "email", "question": question,
                                                      "email_text": mail})
                self.assertEqual(waiting["clarification"]["pending"], "clicked_link")
                clicked = self.workflow.resume(case_id, {"clicked_link": True})
                self.assertEqual(clicked["clarification"]["pending"], "credential_submitted")
                done = self.workflow.resume(case_id, {"credential_submitted": False})
                self.assertEqual(done["status"], "completed")
                self.assertEqual(len(done["clarification"]["asked"]), 2)

                known = self.workflow.run(f"known-{index}", {"kind": "email", "question": question,
                    "email_text": mail, "facts": {"clicked_link": False}})
                self.assertEqual(known["status"], "completed")
                self.assertEqual(known["clarification"]["asked"], [])

                fully_known = self.workflow.run(f"fully-known-{index}", {"kind": "email",
                    "question": question, "email_text": mail,
                    "facts": {"clicked_link": True, "credential_submitted": False}})
                self.assertEqual(fully_known["status"], "completed")
                self.assertEqual(fully_known["clarification"]["asked"], [])

    def test_explicit_event_description_avoids_unnecessary_clarification(self):
        mail = "From: a@example.org\nSubject: Check\n\nVisit https://example.org"
        state = self.workflow.run("opened", {"kind": "email",
            "question": "I only opened the email; I did not click. What should I do?",
            "email_text": mail})
        self.assertEqual(state["status"], "completed")
        self.assertFalse(current_facts(state["facts"])["clicked_link"])
        self.assertEqual(state["clarification"]["asked"], [])

    def test_conflicting_action_report_clarifies_when_advice_changes(self):
        mail = "From: a@example.org\nSubject: Check\n\nVisit https://example.org"
        state = self.workflow.run("conflict", {"kind": "email",
            "question": "I clicked, but I did not click. What should I do?", "email_text": mail})
        self.assertEqual(state["status"], "waiting_clarification")
        self.assertEqual(state["clarification"]["pending"], "clicked_link")
        self.assertNotIn("clicked_link", current_facts(state["facts"]))

    def test_clarification_resume_and_fact_correction_in_new_run(self):
        mail = "From: a@example.org\nSubject: Check\n\nVisit https://example.org"
        request = {"kind": "email", "question": "What should I do now?", "email_text": mail}
        waiting = self.workflow.run("incident", request, run_id="run-one")
        self.assertEqual(waiting["status"], "waiting_clarification")
        self.assertEqual(waiting["clarification"]["pending"], "clicked_link")
        self.assertIn("clicked_link", [f["name"] for f in waiting["facts"] if f["status"] == "unknown"])
        with self.assertRaises(ValueError):
            self.workflow.run("incident", request)
        second = self.workflow.resume("incident", {"clicked_link": True})
        self.assertEqual(second["status"], "waiting_clarification")
        self.assertEqual(second["clarification"]["pending"], "credential_submitted")
        done = self.workflow.resume("incident", {"credential_submitted": True})
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["run_id"], "run-one")
        self.assertEqual(len(done["clarification"]["asked"]), 2)
        self.assertEqual(len(done["tool_history"]), 3)
        self.assertTrue(current_facts(done["facts"])["credential_submitted"])
        corrected = self.workflow.run("incident", {**request, "facts": {
            "clicked_link": False, "credential_submitted": False}}, run_id="run-two")
        self.assertEqual(corrected["status"], "completed")
        self.assertFalse(current_facts(corrected["facts"])["credential_submitted"])
        credential_records = [f for f in corrected["facts"] if f["name"] == "credential_submitted"]
        self.assertEqual(credential_records[-1]["status"], "user_reported")
        self.assertTrue(all(f["status"] == "superseded" for f in credential_records[:-1]))
        prompt = self.messages[-1][-1]["content"]
        self.assertNotIn("'credential_submitted': True", prompt)
        self.assertIn("'credential_submitted': False", prompt)
        self.assertEqual(len(corrected["tool_history"]), 3)
        self.assertEqual({h["result_ref"].split(":")[0] for h in corrected["tool_history"]}, {"run-two"})
        next_run = self.workflow.run("incident", {"kind": "knowledge", "question": "What is phishing?"},
                                     run_id="run-three")
        self.assertFalse(current_facts(next_run["facts"])["credential_submitted"])
        self.assertEqual([h["tool"] for h in next_run["tool_history"]], ["retrieve_knowledge"])

    def test_sqlite_survives_process_restart_and_resumes_same_case(self):
        mail = "From: a@example.org\nSubject: Check\n\nVisit https://example.org"
        self.workflow.run("restart", {"kind": "email", "question": "What should I do?",
                                      "email_text": mail, "facts": {"clicked_link": True}}, run_id="persisted-run")
        self.context.__exit__(None, None, None)
        script = """import json, sys
from phishing_agent.graph import open_workflow
from phishing_agent.config import load_config
class UnusedRetriever:
    def retrieve(self, *args): raise AssertionError('tools must not rerun')
    chunks = []
    embedding = {}
def completion(**kw):
    return {'choices':[{'message':{'content':'Supported answer [E1].'},'finish_reason':'stop'}]}
with open_workflow(sys.argv[1], UnusedRetriever(), load_config(sys.argv[2]), completion) as w:
    before = w.state('restart')
    after = w.resume('restart', {'credential_submitted': False})
    print(json.dumps({'before':before['status'],'after':after['status'], 'run_id':after['run_id'],
                      'tools':len(after['tool_history'])}))
"""
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
        output = subprocess.check_output([sys.executable, "-c", script, str(self.path),
                                          str(ROOT / "config.json")], env=env, text=True)
        result = json.loads(output)
        self.assertEqual(result, {"before": "waiting_clarification", "after": "completed",
                                  "run_id": "persisted-run", "tools": 3})
        self.context = open_workflow(self.path, self.retriever, self.config, self.recorded_completion)
        self.workflow = self.context.__enter__()
        self.assertFalse(current_facts(self.workflow.state("restart")["facts"])["credential_submitted"])


if __name__ == "__main__":
    unittest.main()
