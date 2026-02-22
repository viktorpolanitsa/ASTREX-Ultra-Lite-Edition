#!/usr/bin/env python3
"""Тесты индекса"""

import sys
import sqlite3
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestFileIndex(unittest.TestCase):

    def test_index_singleton(self):
        from core.index import FileIndex
        a = FileIndex()
        b = FileIndex()
        self.assertIs(a, b)

    def test_index_has_tables(self):
        from core.index import file_index
        conn = file_index._conn
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}
        self.assertIn('files', tables)

    def test_upsert_and_search(self):
        from core.index import file_index
        # Upsert a test entry
        file_index.upsert_file(
            path='/tmp/test_astrex_doc.txt',
            filename='test_astrex_doc.txt',
            extension='.txt',
            size=100,
            mtime=1000000,
            hash_md5='abc123test',
            extracted_text='уникальный_тестовый_текст_для_поиска',
        )
        # Search
        results = file_index.search_fts('уникальный_тестовый_текст_для_поиска', limit=10)
        self.assertGreater(len(results), 0)
        self.assertIn('score', results[0])
        self.assertGreater(results[0]['score'], 0)

    def test_close(self):
        from core.index import file_index
        # Should not raise
        file_index.close()
        # Should reconnect transparently
        conn = file_index._conn
        self.assertIsNotNone(conn)

    def test_get_stats(self):
        from core.index import file_index
        stats = file_index.get_stats()
        self.assertIn('total_files', stats)
        self.assertIn('total_size_bytes', stats)


if __name__ == '__main__':
    unittest.main()
