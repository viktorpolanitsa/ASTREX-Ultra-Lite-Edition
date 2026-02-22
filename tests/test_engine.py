#!/usr/bin/env python3
"""Тесты сканера"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestScanEngine(unittest.TestCase):

    def test_create_engine(self):
        from core.engine import ScanEngine
        engine = ScanEngine(use_nlp=False, fuzzy_search=False, build_graph=False)
        self.assertIsNotNone(engine)

    def test_scan_empty_folder(self):
        from core.engine import ScanEngine
        engine = ScanEngine(use_nlp=False, fuzzy_search=False, build_graph=False)
        with tempfile.TemporaryDirectory() as d:
            results = engine.scan(d, 'test', max_workers=1)
            self.assertEqual(results, [])

    def test_scan_folder_with_match(self):
        from core.engine import ScanEngine
        engine = ScanEngine(use_nlp=False, fuzzy_search=False, build_graph=False)
        with tempfile.TemporaryDirectory() as d:
            # Create a file with matching content
            p = Path(d) / 'match.txt'
            p.write_text('This contains the search_keyword_xyz here', encoding='utf-8')
            results = engine.scan(d, 'search_keyword_xyz', max_workers=1)
            self.assertGreater(len(results), 0)
            self.assertEqual(results[0].filename, 'match.txt')

    def test_scan_no_match(self):
        from core.engine import ScanEngine
        engine = ScanEngine(use_nlp=False, fuzzy_search=False, build_graph=False)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'nomatch.txt'
            p.write_text('Nothing relevant here', encoding='utf-8')
            results = engine.scan(d, 'zzz_unique_nonexistent_zzz', max_workers=1)
            self.assertEqual(len(results), 0)

    def test_stop(self):
        from core.engine import ScanEngine
        engine = ScanEngine(use_nlp=False, fuzzy_search=False, build_graph=False)
        engine.stop()
        # After stop, new scan should work
        with tempfile.TemporaryDirectory() as d:
            results = engine.scan(d, 'test', max_workers=1)
            self.assertIsInstance(results, list)


class TestScanStats(unittest.TestCase):

    def test_stats_defaults(self):
        from core.engine import ScanStats
        stats = ScanStats()
        self.assertEqual(stats.total_files, 0)
        self.assertEqual(stats.processed_files, 0)
        self.assertEqual(stats.errors, 0)

    def test_stats_to_dict(self):
        from core.engine import ScanStats
        stats = ScanStats()
        d = stats.to_dict()
        self.assertIn('total_files', d)
        self.assertIn('processed_files', d)


class TestMessageType(unittest.TestCase):

    def test_message_types(self):
        from core.engine import MessageType
        self.assertIsNotNone(MessageType.STATUS)
        self.assertIsNotNone(MessageType.PROGRESS)
        self.assertIsNotNone(MessageType.MATCH)
        self.assertIsNotNone(MessageType.ERROR)


if __name__ == '__main__':
    unittest.main()
