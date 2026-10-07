#!/usr/bin/env python3
"""Тесты сканера и пула процессов"""

import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.helpers  # noqa: F401,E402


def _engine(**kwargs):
    from core.engine import ScanEngine
    options = dict(use_nlp=False, fuzzy_search=False, build_graph=False, apply_limits=False,
                   callback=lambda *args: None)
    options.update(kwargs)
    return ScanEngine(**options)


class TestScanEngine(unittest.TestCase):

    def test_create_engine(self):
        self.assertIsNotNone(_engine())

    def test_scan_empty_folder(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(_engine().scan(d, 'test', max_workers=1), [])

    def test_scan_folder_with_match(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'match.txt').write_text('This contains the search_keyword_xyz here', encoding='utf-8')
            results = _engine().scan(d, 'search_keyword_xyz', max_workers=1)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].filename, 'match.txt')

    def test_scan_no_match(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'nomatch.txt').write_text('Nothing relevant here', encoding='utf-8')
            self.assertEqual(len(_engine().scan(d, 'zzz_unique_nonexistent_zzz', max_workers=1)), 0)

    def test_stop(self):
        engine = _engine()
        engine.stop()
        with tempfile.TemporaryDirectory() as d:
            self.assertIsInstance(engine.scan(d, 'test', max_workers=1), list)

    def test_snippet_contains_deep_match(self):
        """Регрессия: при совпадении дальше 600-го символа сниппет обрезался до [:500] без совпадения."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'deep.txt').write_text('слово ' * 2000 + 'ИСКОМОЕ' + ' слово' * 2000, encoding='utf-8')
            results = _engine().scan(d, 'искомое', max_workers=1)
            self.assertEqual(len(results), 1)
            self.assertIn('ИСКОМОЕ', results[0].snippet)
            self.assertIn('ИСКОМОЕ', results[0].to_dict()['snippet'])
            self.assertGreaterEqual(results[0].score, 0.9)

    def test_multiword_query_requires_all_words(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'both.txt').write_text('Петров встретил Иванова в Москве', encoding='utf-8')
            (Path(d) / 'one.txt').write_text('Только Иванов пришёл', encoding='utf-8')
            (Path(d) / 'sofa.txt').write_text('Купил диван и кресло', encoding='utf-8')
            names = {r.filename for r in _engine().scan(d, 'Иванов и Петров', max_workers=2)}
            self.assertEqual(names, {'both.txt'})

    def test_cached_files_get_entities_later(self):
        """Регрессия: файл, закешированный без сущностей, никогда их не получал."""
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'doc.txt').write_text('Договор с ООО «Вектор» подписал Петров И.И. 15.03.2024',
                                             encoding='utf-8')
            first = _engine(use_nlp=True).scan(d, 'несуществующее_слово_qwe', max_workers=1)
            self.assertEqual(first, [])
            second = _engine(use_nlp=True).scan(d, 'Вектор', max_workers=1)
            self.assertEqual(len(second), 1)
            self.assertIn('DATES', second[0].entities)
            stats_engine = _engine(use_nlp=True)
            third = stats_engine.scan(d, 'Вектор', max_workers=1)
            self.assertIn('DATES', third[0].entities)
            self.assertEqual(stats_engine.stats.cached, 1)

    def test_fuzzy_search_finds_typo(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'typo.txt').write_text('Проект Альбатрас утверждён руководством.', encoding='utf-8')
            results = _engine(fuzzy_search=True).scan(d, 'Альбатрос', max_workers=1)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].metadata.get('match_type'), 'fuzzy')

    def test_hidden_and_excluded_dirs(self):
        with tempfile.TemporaryDirectory() as d:
            for sub in ('.hidden', 'secret', 'normal'):
                (Path(d) / sub).mkdir()
                (Path(d) / sub / 'f.txt').write_text('ключевое_слово_abc', encoding='utf-8')
            engine = _engine(exclude_paths=[str(Path(d) / 'secret')])
            names = {Path(r.path).parent.name for r in engine.scan(d, 'ключевое_слово_abc', max_workers=1)}
            self.assertEqual(names, {'normal'})

    def test_search_python_api(self):
        from core.engine import SearchOptions
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'a.txt').write_text('договор поставки', encoding='utf-8')
            engine = _engine()
            matches = list(engine.search('договор', d, SearchOptions(extract_entities=False, max_workers=1)))
            self.assertEqual(len(matches), 1)

    def test_progress_and_stats_events(self):
        events = []
        with tempfile.TemporaryDirectory() as d:
            for i in range(30):
                (Path(d) / f'f{i}.txt').write_text(f'текст номер {i}', encoding='utf-8')
            engine = _engine(callback=lambda t, data: events.append((t, data)))
            engine.scan(d, 'текст', max_workers=2)
        types = [t for t, _ in events]
        self.assertIn('progress', types)
        self.assertIn('stats', types)
        stats = [data for t, data in events if t == 'stats'][-1]
        self.assertEqual(stats['processed_files'], 30)
        self.assertEqual(stats['matched_files'], 30)
        self.assertGreater(stats['bytes_processed'], 0)


class TestPythonAPIScript(unittest.TestCase):

    def test_unguarded_user_script_runs_once(self):
        """Регрессия: скрипт без `if __name__ == "__main__"` выполнялся заново в каждом воркере."""
        from tests.helpers import ROOT
        with tempfile.TemporaryDirectory() as d:
            data = Path(d) / 'data'
            data.mkdir()
            (data / 'a.txt').write_text('договор поставки', encoding='utf-8')
            marker = Path(d) / 'marker.txt'
            script = Path(d) / 'user_script.py'
            script.write_text(
                "import sys\n"
                f"sys.path.insert(0, {str(ROOT)!r})\n"
                f"with open({str(marker)!r}, 'a') as f:\n"
                "    f.write('x')\n"
                "from core import ScanEngine, SearchOptions\n"
                f"found = list(ScanEngine().search('договор', {str(data)!r}, "
                "SearchOptions(max_workers=2, extract_entities=False)))\n"
                "print(len(found))\n", encoding='utf-8')
            proc = subprocess.run([sys.executable, str(script)], capture_output=True, text=True,
                                  timeout=180, env=dict(os.environ))
            self.assertEqual(proc.stdout.strip(), '1', proc.stderr[-2000:])
            self.assertEqual(marker.read_text(), 'x')


class TestScanStats(unittest.TestCase):

    def test_stats_defaults(self):
        from core.engine import ScanStats
        stats = ScanStats()
        self.assertEqual(stats.total_files, 0)
        self.assertEqual(stats.processed_files, 0)
        self.assertEqual(stats.errors, 0)

    def test_stats_to_dict(self):
        from core.engine import ScanStats
        d = ScanStats().to_dict()
        self.assertIn('total_files', d)
        self.assertIn('processed_files', d)
        self.assertIn('timeouts', d)


class TestMessageType(unittest.TestCase):

    def test_message_types(self):
        from core.engine import MessageType
        for name in ('STATUS', 'PROGRESS', 'MATCH', 'ERROR', 'STATS'):
            self.assertIsNotNone(getattr(MessageType, name))


class TestWorkerPool(unittest.TestCase):

    def test_results(self):
        from core.workerpool import WorkerPool
        from tests.helpers import worker_echo
        with WorkerPool(worker_echo, 2) as pool:
            results = {r.key: r.value for r in pool.run((i, (i,)) for i in range(20))}
        self.assertEqual(results, {i: i * 2 for i in range(20)})

    def test_timeout_kills_only_that_task(self):
        from core.workerpool import WorkerPool
        from tests.helpers import worker_sleep
        start = time.monotonic()
        with WorkerPool(worker_sleep, 2, task_timeout=1.0) as pool:
            results = {r.key: r for r in pool.run([('slow', (30,)), ('fast', (0.1,)), ('fast2', (0.1,))])}
        self.assertLess(time.monotonic() - start, 15)
        self.assertEqual(results['slow'].kind, 'timeout')
        self.assertTrue(results['fast'].ok and results['fast2'].ok)

    def test_crash_is_isolated(self):
        from core.workerpool import WorkerPool
        from tests.helpers import worker_crash
        with WorkerPool(worker_crash, 1) as pool:
            results = list(pool.run([('a', (3,)), ('b', (4,))]))
        self.assertEqual(len(results), 2)
        self.assertTrue(all(r.kind == 'crash' and not r.ok for r in results))

    def test_exception_reported(self):
        from core.workerpool import WorkerPool
        from tests.helpers import worker_raise
        with WorkerPool(worker_raise, 1) as pool:
            result = next(iter(pool.run([('x', ('boom',))])))
        self.assertFalse(result.ok)
        self.assertIn('boom', result.error)

    def test_stop(self):
        from core.workerpool import WorkerPool
        from tests.helpers import worker_sleep
        state = {'n': 0}

        def should_stop():
            state['n'] += 1
            return state['n'] > 3

        pool = WorkerPool(worker_sleep, 2)
        start = time.monotonic()
        list(pool.run([(i, (5,)) for i in range(10)], should_stop=should_stop))
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(pool._workers, [])


class TestIncrementalIndexer(unittest.TestCase):

    def test_index_and_cleanup(self):
        from core.engine import IncrementalIndexer
        from core.index import file_index
        with tempfile.TemporaryDirectory() as d:
            for i in range(5):
                (Path(d) / f'f{i}.txt').write_text(f'индекс {i}', encoding='utf-8')
            idx = IncrementalIndexer(callback=lambda *a: None, apply_limits=False)
            stats = idx.index_folder(d, extract_entities=False, max_workers=2)
            self.assertEqual(stats.processed_files, 5)
            again = idx.index_folder(d, extract_entities=False, max_workers=2)
            self.assertEqual(again.cached, 5)
            (Path(d) / 'f0.txt').unlink()
            idx.index_folder(d, extract_entities=False, max_workers=1, cleanup=True)
            paths = {r['path'] for r in file_index.iter_files() if r['path'].startswith(str(Path(d).resolve()))}
            self.assertEqual(len(paths), 4)


if __name__ == '__main__':
    unittest.main()
