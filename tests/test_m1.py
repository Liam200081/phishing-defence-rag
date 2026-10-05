"""Default suite is offline and needs only Python's standard library."""
import ast
from copy import deepcopy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from phishing_agent.config import load_config, validate_config
from phishing_agent.rag.context import build_context, ContextBudgetError
from phishing_agent.rag.ingest import chunk_pages, clean_pdf_text, ingest_pdfs
from phishing_agent.rag.retrieve import ChromaRetriever
from phishing_agent.prompts import build_messages, question_type
from phishing_agent.llm import generate
from phishing_agent.metrics import evaluate, align_by_query_id
from phishing_agent.pipeline import load_queries, run_queries, run_b1
from phishing_agent.trace import export_run, read_run, validate_run, code_provenance
from phishing_agent.cli import main

CONFIG = load_config(ROOT / 'config.json')
QUERIES = load_queries(ROOT / 'evaluation/queries.json')
Q8 = json.loads((ROOT / 'tests/fixtures/q8_historical.json').read_text())


def hit(text=Q8['text'], cid='fixture-q8', **extra):
    return dict(text=text, chunk_id=cid, document='synthetic-source.pdf', document_sha256='fixture-sha',
                page_start=1, page_end=2, score=0.2, score_kind='cosine_distance_lower_is_better',
                data_version='fixture-data', index_version='fixture-index', **extra)


def raw(answer='Fixed supported answer [E1].', finish='stop'):
    return {'id': 'mock-response', 'model': 'mock-llm', 'choices': [{'message': {'content': answer}, 'finish_reason': finish}],
            'usage': {'prompt_tokens': 123, 'completion_tokens': 12, 'total_tokens': 135}}


def fixture_run(queries=None, config=None, completion=None, retrieve=None, encode=None):
    return run_queries(queries or QUERIES, config or CONFIG, retrieve or (lambda q, k: [hit()]),
                       completion or (lambda **kw: raw()), data_manifest={'data_version': 'fixture-data', 'documents': [], 'note': 'Synthetic source mapping; Q8 text is an exact historical excerpt'},
                       embedding={'model': 'mock-embedding', 'resolved_revision': 'fixture-v1'}, index_version='fixture-index',
                       provenance={'git_commit': 'unknown', 'test_fixture': True}, encode=encode, execution_mode='offline_mock',
                       run_id='B1-fixed-regression', created_at='2000-01-01T00:00:00+00:00')


class ContextTests(unittest.TestCase):
    def test_empty_and_zero_budget(self):
        self.assertEqual(build_context([], 0)['text'], '')

    def test_exact_budget_and_one_less(self):
        c = build_context([hit()], 12000)
        self.assertEqual(build_context([hit()], len(c['text'])), {**c, 'budget_chars': len(c['text'])})
        with self.assertRaises(ContextBudgetError):
            build_context([hit()], len(c['text']) - 1)

    def test_multiple_unicode_and_offsets(self):
        c = build_context([hit('定义与证据。'), hit('Second complete chunk', 'second')], 12000)
        self.assertEqual(c['used_chars'], len(c['text']))
        for e in c['evidence']:
            self.assertEqual(c['text'][e['context_start']:e['context_end']], e['text'])
            self.assertIn(e['chunk_id'], c['text'])
        self.assertEqual(len(c['evidence']), 2)

    def test_invalid_budget_duplicate_and_empty_chunk(self):
        for budget in (-1, 1.5, True):
            with self.assertRaises(ValueError):
                build_context([], budget)
        with self.assertRaises(ValueError):
            build_context([hit(), hit()], 12000)
        with self.assertRaises(ValueError):
            build_context([hit('  ')], 12000)

    def test_q8_historical_definition_reaches_actual_request(self):
        self.assertGreater(Q8['text'].index(Q8['definition']), 300)
        self.assertNotIn(Q8['definition'], Q8['text'][:300])
        completion = Mock(return_value=raw())
        run = fixture_run([QUERIES[7]], completion=completion)
        row = run['results'][0]
        self.assertIn(Q8['definition'], completion.call_args.kwargs['messages'][-1]['content'])
        self.assertEqual(row['messages'], completion.call_args.kwargs['messages'])
        self.assertIn(Q8['definition'], row['context']['text'])
        self.assertEqual(row['question_type'], 'comparison')

    def test_budget_failure_never_silently_drops_evidence(self):
        c = deepcopy(CONFIG); c['context_budget_chars'] = 10
        completion = Mock()
        run = fixture_run([QUERIES[7]], config=c, completion=completion)
        row = run['results'][0]
        completion.assert_not_called()
        self.assertEqual(row['response']['status'], 'context_budget_exceeded')
        self.assertEqual(row['context']['text'], '')
        self.assertEqual(row['retrieved'][0]['text'], Q8['text'])
        validate_run(run)


class IngestAndRetrievalTests(unittest.TestCase):
    def test_original_chunk_function_equivalence_single_page(self):
        nb = json.loads((ROOT / 'phishing_defence_rag.ipynb').read_text())
        tree = ast.parse(''.join(nb['cells'][3]['source']))
        scope = {}; exec(compile(ast.Module(body=[tree.body[0]], type_ignores=[]), '<B0-function>', 'exec'), scope)
        text = ' '.join('word' + str(i) for i in range(451))
        self.assertEqual([c['text'] for c in chunk_pages([text], 'test.pdf', 'sha', CONFIG)], scope['chunk_text'](text))

    def test_cleaning_function_preserves_original_rules(self):
        nb = json.loads((ROOT / 'phishing_defence_rag.ipynb').read_text())
        node = next(n for n in ast.parse(''.join(nb['cells'][14]['source'])).body if isinstance(n, ast.FunctionDef) and n.name == 'clean_pdf_text')
        import re
        scope = {'re': re}; exec(compile(ast.Module(body=[node], type_ignores=[]), '<B0-cleaning>', 'exec'), scope)
        text = 'Title https://cisa.gov hello@example.org @CISAgov\nRELATED TOPICS: phishing\nReturn to top\nKeep this definition.'
        self.assertEqual(clean_pdf_text(text), scope['clean_pdf_text'](text))
        self.assertEqual(clean_pdf_text(text), 'Title Keep this definition.')

    def test_cross_page_source_mapping_and_stable_ids(self):
        c = deepcopy(CONFIG); c.update(chunk_size=4, chunk_overlap=1, min_chunk_chars=0)
        pages = ['one two three', 'four five six']
        chunks = chunk_pages(pages, 'doc.pdf', 'abc', c)
        self.assertEqual(chunks[0]['text'], 'one two three four')
        self.assertEqual((chunks[0]['page_start'], chunks[0]['page_end']), (1, 2))
        self.assertEqual((chunks[1]['page_start'], chunks[1]['page_end']), (2, 2))
        self.assertEqual(chunks, chunk_pages(pages, 'doc.pdf', 'abc', c))
        self.assertNotEqual(chunks[0]['chunk_id'], chunk_pages(pages, 'doc.pdf', 'changed', c)[0]['chunk_id'])

    def test_cleaning_preserves_page_numbers(self):
        c = deepcopy(CONFIG); c.update(cleaning=True, min_chunk_chars=0)
        chunks = chunk_pages(['https://example.org', 'Actual evidence remains'], 'doc.pdf', 'abc', c)
        self.assertEqual(chunks[0]['page_start'], 2)
        self.assertEqual(chunks[0]['page_end'], 2)

    def test_missing_pdfs_fail_clearly(self):
        with tempfile.TemporaryDirectory() as d, self.assertRaises(FileNotFoundError):
            ingest_pdfs(d, CONFIG)

    def test_explicit_embeddings_and_score_source_join(self):
        encoder = Mock(); encoder.encode.side_effect = [[[1.0, 0.0], [0.0, 1.0]], [[0.0, 1.0]]]
        client = Mock(); collection = client.create_collection.return_value
        collection.query.return_value = {'ids': [['b', 'a']], 'distances': [[0.1, 0.9]]}
        chunks = [hit('first text', 'a'), hit('second text', 'b')]
        retriever = ChromaRetriever(chunks, CONFIG, encoder, client, 'test-revision')
        self.assertIsNone(client.create_collection.call_args.kwargs['embedding_function'])
        self.assertIn('embeddings', collection.add.call_args.kwargs)
        rows = retriever.retrieve('query', 3)
        self.assertNotIn('query_texts', collection.query.call_args.kwargs)
        self.assertEqual(collection.query.call_args.kwargs['n_results'], 2)
        self.assertEqual(rows[0]['chunk_id'], 'b')
        self.assertEqual(rows[0]['score'], 0.1)
        self.assertEqual(rows[0]['page_end'], 2)
        self.assertEqual(rows[0]['index_version'], retriever.index_version)
        self.assertEqual(retriever.embedding['resolved_revision'], 'test-revision')
        retriever.close(); client.delete_collection.assert_called_once()


class PromptAndResponseTests(unittest.TestCase):
    def test_question_types(self):
        for q, expected in [('What is spear phishing?', 'explanation'), (QUERIES[7]['query'], 'comparison'),
                            (QUERIES[0]['query'], 'guidance'), ('解释钓鱼攻击', 'explanation'), ('对比两种攻击', 'comparison')]:
            self.assertEqual(question_type(q), expected)
            _, messages = build_messages(q, 'actual evidence', 'b1-v1')
            self.assertNotIn('exactly 3', str(messages))
            self.assertTrue(messages[-1]['content'].endswith('actual evidence'))

    def test_complete_response_and_generation_parameters(self):
        completion = Mock(return_value=raw())
        r = generate([], CONFIG, completion)
        self.assertTrue(r['complete']); self.assertTrue(r['returned_successfully'])
        self.assertEqual(r['usage']['total_tokens'], 135)
        self.assertEqual(r['finish_reason'], 'stop')
        self.assertEqual(completion.call_args.kwargs['max_tokens'], CONFIG['generation']['max_tokens'])

    def test_empty_answer(self):
        for answer in (None, '', '  '):
            r = generate([], CONFIG, lambda **kw: raw(answer))
            self.assertTrue(r['empty']); self.assertFalse(r['complete'])
            self.assertEqual(r['status'], 'empty_response')

    def test_length_termination_with_or_without_text(self):
        for answer in ('partial answer', None):
            r = generate([], CONFIG, lambda **kw: raw(answer, 'length'))
            self.assertTrue(r['truncated']); self.assertFalse(r['complete'])
            self.assertEqual(r['status'], 'length_terminated')

    def test_error_response_is_retained_without_secret(self):
        def fail(**kw):
            raise RuntimeError('secret-should-not-be-exported')
        r = generate([], CONFIG, fail)
        self.assertFalse(r['returned_successfully']); self.assertFalse(r['complete'])
        self.assertEqual(r['status'], 'request_error')
        self.assertNotIn('secret-should', json.dumps(r))

    def test_unknown_filtered_and_malformed_responses(self):
        for finish in (None, 'content_filter', 'tool_calls'):
            self.assertFalse(generate([], CONFIG, lambda **kw: raw(finish=finish))['complete'])
        for payload in ({'choices': []}, {'choices': [{'message': {'content': ['bad']}}]}):
            self.assertEqual(generate([], CONFIG, lambda **kw: payload)['status'], 'invalid_response')


class EvaluationAndRunTests(unittest.TestCase):
    def test_diagnostics_use_sent_text_and_save_exact_windows(self):
        context = build_context([hit()], 12000)
        encoder = Mock(return_value=[[1., 0.]] * 4)
        ev = evaluate(QUERIES[7]['query'], generate([], CONFIG, lambda **kw: raw()), context, CONFIG, encoder)
        legacy = ev['legacy_diagnostic_metrics']
        self.assertEqual(legacy['context_relevance'], 1.0)
        self.assertEqual(legacy['inputs']['evidence_windows'], [Q8['text'][:500]])
        self.assertEqual(encoder.call_args.args[0][2], legacy['inputs']['faithfulness_context_window'])
        self.assertEqual(ev['grounding']['status'], 'not_assessed')
        self.assertIsNone(ev['grounding']['score'])

    def test_incomplete_responses_not_scored_as_normal(self):
        for finish, answer in [('length', 'partial'), ('stop', '')]:
            run = fixture_run([QUERIES[0]], completion=lambda **kw: raw(answer, finish))
            self.assertEqual(run['summary']['incomplete'], 1)
            self.assertIsNone(run['results'][0]['evaluation']['legacy_diagnostic_metrics']['faithfulness_proxy'])

    def test_empty_retrieval_makes_no_llm_request(self):
        completion = Mock()
        run = fixture_run([QUERIES[0]], retrieve=lambda q, k: [], completion=completion)
        completion.assert_not_called()
        self.assertEqual(run['results'][0]['response']['status'], 'no_evidence')
        validate_run(run)

    def test_retrieval_failure_is_not_no_evidence(self):
        retrieve = Mock(side_effect=RuntimeError('db failure'))
        run = fixture_run([QUERIES[0]], retrieve=retrieve)
        self.assertEqual(run['results'][0]['response']['status'], 'retrieval_error')
        validate_run(run)

    def test_query_id_alignment_not_zip_order(self):
        rows = align_by_query_id(QUERIES, list(reversed(QUERIES)))
        self.assertTrue(all(a['query_id'] == b['query_id'] for a, b in rows))
        with self.assertRaises(ValueError):
            align_by_query_id(QUERIES, QUERIES[:-1])
        with self.assertRaises(ValueError):
            align_by_query_id(QUERIES, QUERIES + QUERIES[:1])
        changed = deepcopy(QUERIES); changed[0]['query'] = 'changed'
        with self.assertRaises(ValueError):
            align_by_query_id(QUERIES, changed)

    def test_duplicate_query_id_rejected_before_request(self):
        completion = Mock()
        with self.assertRaises(ValueError):
            fixture_run(QUERIES + QUERIES[:1], completion=completion)
        completion.assert_not_called()

    def test_fixed_ten_query_deterministic_regression(self):
        run = fixture_run()
        self.assertEqual(run, fixture_run())
        self.assertEqual(run['summary'], {'queries': 10, 'complete': 10, 'incomplete': 0, 'evaluation_errors': 0})
        self.assertEqual([r['query_id'] for r in run['results']], [f'Q{i}' for i in range(1, 11)])
        for row in run['results']:
            self.assertEqual(row['response']['answer'], 'Fixed supported answer [E1].')
            self.assertEqual(row['response']['finish_reason'], 'stop')
            self.assertEqual(row['context']['evidence'][0]['chunk_id'], 'fixture-q8')
        validate_run(run)

    def test_export_readback_and_no_overwrite(self):
        run = fixture_run()
        with tempfile.TemporaryDirectory() as d:
            path = export_run(run, d)
            before = path.read_bytes()
            self.assertEqual(read_run(path), run)
            with self.assertRaises(FileExistsError):
                export_run(run, d)
            self.assertEqual(path.read_bytes(), before)

    def test_readback_rejects_tampered_context_evidence_and_queries(self):
        for mutation in ('message', 'source', 'evaluation', 'query'):
            run = fixture_run([QUERIES[0]]); row = run['results'][0]
            if mutation == 'message': row['messages'][-1]['content'] += 'unseen text'
            if mutation == 'source': row['context']['evidence'][0]['page_start'] = 99
            if mutation == 'evaluation': row['evaluation']['context_sha256'] = 'wrong'
            if mutation == 'query': row['query_id'] = 'Q99'
            with self.assertRaises(ValueError): validate_run(run)

    def test_evaluation_failure_preserves_generated_answer(self):
        run = fixture_run([QUERIES[0]], encode=Mock(side_effect=RuntimeError('encoder failed')))
        row = run['results'][0]
        self.assertEqual(row['response']['answer'], 'Fixed supported answer [E1].')
        self.assertEqual(row['evaluation']['legacy_diagnostic_metrics']['status'], 'evaluation_error')
        validate_run(run)

    def test_malformed_readback_and_forged_success_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'bad.json'; path.write_text('{}')
            with self.assertRaises(ValueError): read_run(path)
        run = fixture_run([QUERIES[0]], completion=lambda **kw: raw('partial', 'length'))
        run['results'][0]['response']['complete'] = True
        with self.assertRaises(ValueError): validate_run(run)
        run = fixture_run(); run['summary']['complete'] = 100
        with self.assertRaises(ValueError): validate_run(run)

    def test_code_provenance_includes_dirty_source_hashes(self):
        info = code_provenance(ROOT)
        self.assertIn('src/phishing_agent/pipeline.py', info['source_files_sha256'])
        self.assertEqual(len(info['source_tree_sha256']), 64)
        self.assertNotEqual(info['git_commit'], 'unknown')

    def test_config_invalid_and_unknown_fields(self):
        for key, value in [('chunk_overlap', 200), ('top_k', 0), ('context_budget_chars', True), ('prompt_version', 'fake')]:
            c = deepcopy(CONFIG); c[key] = value
            with self.assertRaises(ValueError): validate_config(c)
        c = deepcopy(CONFIG); c['planner'] = True
        with self.assertRaises(ValueError): validate_config(c)

    def test_cli_read_run_and_incomplete_exit_code(self):
        with tempfile.TemporaryDirectory() as d:
            path = export_run(fixture_run(), d)
            with patch('sys.stdout', new_callable=io.StringIO) as out:
                self.assertEqual(main(['--read-run', str(path)]), 0)
            self.assertEqual(json.loads(out.getvalue())['queries'], 10)
            run = fixture_run([QUERIES[0]], completion=lambda **kw: raw('partial', 'length'))
            run['run_id'] = 'B1-incomplete'
            path = export_run(run, d)
            with patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main(['--read-run', str(path)]), 2)

    def test_notebook_and_cli_execute_shared_pipeline(self):
        class FakeRetriever:
            embedding = {'model': 'mock', 'resolved_revision': 'fixture'}
            index_version = 'fixture-index'
            def __init__(self, *args): pass
            def retrieve(self, query, top_k): return [hit()]
            def encode(self, texts): return [[1., 0.] for _ in texts]
            def close(self): pass
        with tempfile.TemporaryDirectory() as d, patch('phishing_agent.pipeline.groq_completion', return_value=lambda **kw: raw()), patch('phishing_agent.pipeline.ingest_pdfs', return_value=([hit()], {'data_version': 'fixture-data'})), patch('phishing_agent.pipeline.ChromaRetriever', FakeRetriever):
            with patch('sys.stdout', new_callable=io.StringIO):
                self.assertEqual(main(['--config', str(ROOT/'config.json'), '--queries', str(ROOT/'evaluation/queries.json'), '--output-dir', d]), 0)
            notebook = json.loads((ROOT / 'notebooks/B1_baseline.ipynb').read_text())
            def notebook_run(**kwargs):
                kwargs['output_dir'] = d
                return run_b1(**kwargs)
            scope = {}
            with patch('phishing_agent.pipeline.run_b1', side_effect=notebook_run), patch('sys.stdout', new_callable=io.StringIO):
                for cell in notebook['cells']:
                    if cell['cell_type'] == 'code': exec(''.join(cell['source']), scope)
            self.assertEqual(scope['run']['summary']['complete'], 10)
            self.assertEqual(len(list(Path(d).glob('B1-*/run.json'))), 2)


class HistoricalTests(unittest.TestCase):
    def test_all_frozen_snapshots_and_original_experiments_unchanged(self):
        manifest = json.loads((ROOT/'experiments/historical/manifest.json').read_text())
        for entry in manifest['files']:
            self.assertEqual(hashlib.sha256((ROOT/entry['snapshot_path']).read_bytes()).hexdigest(), entry['sha256'])
            if entry['original_path'].endswith(('.ipynb', '.xlsx')):
                self.assertEqual(hashlib.sha256((ROOT/entry['original_path']).read_bytes()).hexdigest(), entry['sha256'])

    def test_ten_queries_preserved_verbatim(self):
        notebook = json.loads((ROOT/'phishing_defence_rag.ipynb').read_text())
        original = ast.literal_eval(ast.parse(''.join(notebook['cells'][8]['source'])).body[0].value)
        self.assertEqual([q['query'] for q in QUERIES], original)

    def test_q8_fixture_is_exact_exported_historical_cell(self):
        path = ROOT/Q8['source_file']
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), Q8['source_sha256'])
        ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(path) as z:
            tree = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
            cell = tree.find('.//s:c[@r="E9"]', ns)
            self.assertEqual(''.join(cell.itertext()), Q8['text'])


if __name__ == '__main__':
    unittest.main()
