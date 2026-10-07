#!/usr/bin/env python3
"""Тесты командной строки (запуск astrex.py отдельным процессом, как это делает GUI)"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.helpers  # noqa: F401,E402
from tests.helpers import ROOT  # noqa: E402

CONTRACT = 'Договор поставки с ООО «Газпром» подписал Петров И.И. 15.03.2024 в г. Москва.'


class TestParser(unittest.TestCase):
    """Аргументы, которые передаёт GUI, должны приниматься парсером."""

    def parse(self, *argv):
        from astrex import build_parser
        return build_parser().parse_args(list(argv))

    def test_gui_arguments(self):
        args = self.parse('scan', '--format', 'jsonl', '--workers', '2', '--min-score', '0.2',
                          '--limit', '50', '--no-morph', '--no-nlp', '--', '-5%', '/data')
        self.assertEqual((args.query, args.folder, args.format), ('-5%', '/data', 'jsonl'))
        self.assertTrue(args.no_morph and args.no_nlp)
        self.assertTrue(self.parse('dedup', '/data', '--json').json)          # раньше: unrecognized arguments
        self.assertEqual(self.parse('export', 'g.graphml', '--format', 'graphml').format, 'graphml')
        self.assertTrue(self.parse('status', '--json').json)
        self.assertTrue(self.parse('index', '/data', '--json', '--cleanup').cleanup)
        self.assertTrue(self.parse('search', 'q', '--any').any)

    def test_unknown_argument_rejected(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.parse('scan', 'q', '/data', '--format', 'xml')


class TestCLIProcess(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls._tmp.name)
        cls.data = cls.root / 'data'
        cls.data.mkdir()
        (cls.data / 'contract.txt').write_text(CONTRACT, encoding='utf-8')
        (cls.data / 'contract_copy.txt').write_text(CONTRACT, encoding='utf-8')
        (cls.data / 'memo.txt').write_text('Служебная записка: Газпром задерживает оплату.', encoding='utf-8')
        (cls.data / 'other.txt').write_text('Совершенно посторонний текст про погоду.', encoding='utf-8')
        cls.env = dict(os.environ, ASTREX_HOME=str(cls.root / 'home'), ASTREX_LOG_LEVEL='WARNING',
                       LC_ALL='C', LANG='C')   # C-локаль: вывод всё равно должен быть UTF-8

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_cli(self, *argv, timeout=180):
        # cwd — не каталог проекта: astrex.py не должен зависеть от текущего каталога
        return subprocess.run([sys.executable, str(ROOT / 'astrex.py'), *argv], cwd=str(self.root),
                              env=self.env, capture_output=True, timeout=timeout)

    @staticmethod
    def json_lines(stdout: bytes):
        return [json.loads(line) for line in stdout.decode('utf-8').splitlines() if line.strip()]

    def test_scan_jsonl_stream(self):
        proc = self.run_cli('scan', 'Газпром', str(self.data), '--format', 'jsonl', '--workers', '2')
        self.assertEqual(proc.returncode, 0, proc.stderr.decode('utf-8', 'replace'))
        events = self.json_lines(proc.stdout)                     # каждая строка — валидный JSON
        types = [e['type'] for e in events]
        self.assertIn('results_begin', types)
        self.assertEqual(types[-1], 'complete')
        begin = next(e for e in events if e['type'] == 'results_begin')
        results = [e for e in events if e['type'] == 'result']
        self.assertEqual(begin['count'], len(results))
        self.assertEqual({Path(r['path']).name for r in results},
                         {'contract.txt', 'contract_copy.txt', 'memo.txt'})
        for r in results:
            self.assertIn('Газпром', r['snippet'])
            self.assertIn('size', r['metadata'])
        contract = next(r for r in results if r['filename'] == 'contract.txt')
        self.assertIn('DATES', contract['entities'])

    def test_scan_json_document(self):
        proc = self.run_cli('scan', 'Петров', str(self.data), '--format', 'json', '--no-nlp', '--workers', '1')
        self.assertEqual(proc.returncode, 0, proc.stderr.decode('utf-8', 'replace'))
        doc = json.loads(proc.stdout.decode('utf-8'))
        self.assertEqual(doc['returned'], 2)
        self.assertEqual(doc['stats']['processed_files'], 4)

    def test_scan_missing_folder(self):
        proc = self.run_cli('scan', 'x', str(self.root / 'nope'), '--format', 'jsonl')
        self.assertEqual(proc.returncode, 1)
        self.assertEqual(self.json_lines(proc.stdout)[-1]['type'], 'error')

    def test_index_search_status_export(self):
        proc = self.run_cli('index', str(self.data), '--json')
        self.assertEqual(proc.returncode, 0, proc.stderr.decode('utf-8', 'replace'))
        summary = json.loads(proc.stdout.decode('utf-8'))          # прогресс не смешивается с JSON
        self.assertEqual(summary['indexing']['total_files'], 4)

        proc = self.run_cli('search', 'Газпром', '--format', 'json')
        self.assertEqual(proc.returncode, 0)
        found = json.loads(proc.stdout.decode('utf-8'))
        self.assertEqual(len(found), 3)
        self.assertTrue(all('<mark>' in r['snippet'] for r in found))

        proc = self.run_cli('status', '--json')
        self.assertEqual(proc.returncode, 0, proc.stderr.decode('utf-8', 'replace'))
        status = json.loads(proc.stdout.decode('utf-8'))
        self.assertGreaterEqual(status['total_files'], 4)
        self.assertGreater(status['db_size'], 0)

        out = self.root / 'graph.graphml'
        proc = self.run_cli('export', str(out), '--format', 'graphml')
        self.assertEqual(proc.returncode, 0, proc.stderr.decode('utf-8', 'replace'))
        ns = '{http://graphml.graphdrawing.org/xmlns}'
        self.assertTrue(ET.parse(out).getroot().findall(f'.//{ns}node'))

        out_json = self.root / 'graph.json'
        self.assertEqual(self.run_cli('export', str(out_json)).returncode, 0)
        self.assertTrue(json.loads(out_json.read_text(encoding='utf-8'))['nodes'])

    def test_dedup_json_lines(self):
        proc = self.run_cli('dedup', str(self.data), '--json')
        self.assertEqual(proc.returncode, 0, proc.stderr.decode('utf-8', 'replace'))
        events = self.json_lines(proc.stdout)
        exact = [e for e in events if e['type'] == 'exact']
        self.assertEqual(len(exact), 1)
        self.assertEqual({Path(p).name for p in exact[0]['group']}, {'contract.txt', 'contract_copy.txt'})
        self.assertEqual(exact[0]['size'], len(CONTRACT.encode('utf-8')))
        summary = events[-1]
        self.assertEqual(summary['type'], 'summary')
        self.assertEqual(summary['total'], 4)

    def test_crypto_and_timeline_json(self):
        proc = self.run_cli('crypto', str(self.data), '--format', 'json')
        self.assertEqual(proc.returncode, 0, proc.stderr.decode('utf-8', 'replace'))
        json.loads(proc.stdout.decode('utf-8'))
        proc = self.run_cli('timeline', str(self.data), '--format', 'json')
        self.assertEqual(proc.returncode, 0, proc.stderr.decode('utf-8', 'replace'))
        timeline = json.loads(proc.stdout.decode('utf-8'))
        self.assertIn('2024-03-15', json.dumps(timeline))

    def test_text_output_in_c_locale(self):
        proc = self.run_cli('scan', 'Газпром', str(self.data), '--no-nlp', '--workers', '1')
        self.assertEqual(proc.returncode, 0, proc.stderr.decode('utf-8', 'replace'))
        self.assertIn('РЕЗУЛЬТАТЫ ПОИСКА: Газпром', proc.stdout.decode('utf-8'))

    def test_no_command_prints_help(self):
        proc = self.run_cli()
        self.assertEqual(proc.returncode, 0)
        self.assertIn(b'usage', proc.stdout.lower())


if __name__ == '__main__':
    unittest.main()
