#!/usr/bin/env python3
"""Тесты индекса"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.helpers  # noqa: F401,E402


class TestFileIndex(unittest.TestCase):

    def setUp(self):
        from core.index import FileIndex
        self.tmp = tempfile.TemporaryDirectory()
        self.index = FileIndex(Path(self.tmp.name) / "test.db")

    def tearDown(self):
        self.index.close_all()
        self.tmp.cleanup()

    def add(self, path, text, entities=None, filename=None):
        return self.index.upsert_file(path=path, filename=filename or Path(path).name, extension='.txt',
                                      size=len(text), mtime=1.0, extracted_text=text, entities=entities)

    def test_index_singleton(self):
        from core.index import FileIndex, file_index
        self.assertIs(FileIndex(), FileIndex())
        self.assertIs(FileIndex(), file_index)
        self.assertIsNot(self.index, file_index)  # FileIndex(path) — отдельный индекс

    def test_index_has_tables(self):
        tables = {row[0] for row in self.index._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn('files', tables)
        self.assertIn('entity_links', tables)

    def test_upsert_returns_correct_id_after_update(self):
        """Регрессия: после UPDATE возвращался id предыдущей вставки."""
        id_a = self.add('/d/a.txt', 'первый')
        id_b = self.add('/d/b.txt', 'второй')
        self.assertNotEqual(id_a, id_b)
        self.assertEqual(self.add('/d/a.txt', 'обновлённый'), id_a)

    def test_upsert_and_search(self):
        self.add('/tmp/test_astrex_doc.txt', 'уникальный_тестовый_текст_для_поиска')
        results = self.index.search_fts('уникальный_тестовый_текст_для_поиска', limit=10)
        self.assertGreater(len(results), 0)
        self.assertGreater(results[0]['score'], 0)

    def test_special_characters_in_query(self):
        """Регрессия: '-', '.', '@', AND и т.п. давали ошибку FTS5 и пустой результат."""
        self.add('/d/c.txt', 'ООО-Ромашка, сайт example.com, дата 15.03.2024, почта test@mail.ru, AND')
        for q in ('ООО-Ромашка', 'example.com', '15.03.2024', 'test@mail.ru', 'AND', '"Ромашка"', 'OR NOT'):
            with self.subTest(query=q):
                if q == 'OR NOT':
                    self.assertEqual(self.index.search_fts(q), [])
                else:
                    self.assertEqual(len(self.index.search_fts(q)), 1, q)
        self.assertEqual(self.index.search_fts('*'), [])

    def test_best_match_gets_highest_score(self):
        """Регрессия: score = 1/(1+|bm25|) ставил лучшие совпадения ниже всех."""
        self.add('/d/strong.txt', 'Иванов Иванов Иванов досье Иванов', filename='ivanov.txt')
        self.add('/d/weak.txt', ('обычный длинный текст ' * 300) + 'Иванов')
        for i in range(50):
            self.add(f'/d/filler{i}.txt', 'другой документ про склад')
        results = self.index.search_fts('Иванов')
        self.assertEqual(results[0]['path'], '/d/strong.txt')
        self.assertGreater(results[0]['score'], results[1]['score'])
        self.assertEqual(results[0]['score'], 1.0)
        self.assertTrue(all(r['score'] >= 0.2 for r in results))

    def test_snippet_is_fragment_with_highlight(self):
        """Регрессия: highlight() возвращал весь текст документа вместо фрагмента."""
        text = ('слово ' * 5000) + 'ИСКОМОЕ' + (' слово' * 5000)
        self.add('/d/long.txt', text)
        r = self.index.search_fts('искомое')[0]
        self.assertIn('<mark>', r['snippet'])
        self.assertLess(len(r['snippet']), 1000)
        self.assertNotIn('extracted_text', r)

    def test_and_semantics(self):
        self.add('/d/1.txt', 'красный автомобиль')
        self.add('/d/2.txt', 'красный дом')
        self.assertEqual([r['path'] for r in self.index.search_fts('красный автомобиль')], ['/d/1.txt'])
        self.assertEqual(len(self.index.search_fts('красный автомобиль', use_or=True)), 2)

    def test_iter_files_replaces_star_query(self):
        """export/graph/report перебирают все файлы через iter_files (а не search_fts('*'))."""
        for i in range(5):
            self.add(f'/d/{i}.txt', f'текст {i}', entities={'PERSONS': [f'Человек {i}']})
        self.add('/d/empty.txt', 'без сущностей')
        self.assertEqual(len(list(self.index.iter_files())), 6)
        rows = list(self.index.iter_files(include_text=True, with_entities_only=True))
        self.assertEqual(len(rows), 5)
        self.assertIn('extracted_text', rows[0])
        self.assertEqual(len(list(self.index.iter_files(limit=2))), 2)

    def test_delete_missing_files_scoped(self):
        """Регрессия: --cleanup удалял из индекса файлы ВСЕХ других папок."""
        self.add('/data/a/1.txt', 'x')
        self.add('/data/a/2.txt', 'x')
        self.add('/data/ab/3.txt', 'x')      # соседняя папка с похожим префиксом
        self.add('/data/b/4.txt', 'x')
        deleted = self.index.delete_missing_files({'/data/a/1.txt'}, scope='/data/a')
        self.assertEqual(deleted, 1)
        remaining = {r['path'] for r in self.index.iter_files()}
        self.assertEqual(remaining, {'/data/a/1.txt', '/data/ab/3.txt', '/data/b/4.txt'})

    def test_entities_none_vs_empty(self):
        """NULL = сущности не извлекались, '{}' = извлекались, но пусто."""
        self.add('/d/none.txt', 'текст')
        self.add('/d/empty.txt', 'текст', entities={})
        self.assertFalse(self.index.get_file('/d/none.txt').entities_computed)
        self.assertTrue(self.index.get_file('/d/empty.txt').entities_computed)
        self.assertTrue(self.index.update_entities('/d/none.txt', {'PERSONS': ['Пётр']}))
        self.assertEqual(self.index.get_file('/d/none.txt').entities, {'PERSONS': ['Пётр']})

    def test_entity_links_populated(self):
        """Регрессия: entity_links никогда не заполнялась (граф API всегда пуст)."""
        self.add('/d/e.txt', 'текст', entities={'PERSONS': ['Иванов', 'Петров'], 'ORGANIZATIONS': ['ООО Вектор']})
        graph = self.index.get_graph_data()
        self.assertEqual(len(graph['edges']), 3)
        labels = {n['entity'] for n in graph['nodes']}
        self.assertEqual(labels, {'Иванов', 'Петров', 'ООО Вектор'})
        self.assertEqual(len(self.index.get_entity_connections('иванов')), 2)
        # удаление файла удаляет и его связи
        self.index.delete_file('/d/e.txt')
        self.assertEqual(self.index.get_graph_data()['edges'], [])

    def test_search_by_entity_exact_and_typed(self):
        self.add('/d/1.txt', 't', entities={'PERSONS': ['Иванов'], 'LOCATIONS': ['Москва']})
        self.add('/d/2.txt', 't', entities={'LOCATIONS': ['Иванов']})          # то же значение, другой тип
        self.add('/d/3.txt', 't', entities={'PERSONS': ['Иванова_100%']})
        self.assertEqual(len(self.index.search_by_entity('иванов')), 2)
        typed = self.index.search_by_entity('Иванов', 'PERSONS')
        self.assertEqual([r['path'] for r in typed], ['/d/1.txt'])
        self.assertEqual(len(self.index.search_by_entity('Иванова_100%')), 1)
        self.assertEqual(len(self.index.search_by_entity('%')), 0)

    def test_file_needs_update(self):
        self.add('/d/x.txt', 'текст')
        self.assertFalse(self.index.file_needs_update('/d/x.txt', 1.0, len('текст')))
        self.assertTrue(self.index.file_needs_update('/d/x.txt', 2.0, len('текст')))
        self.assertTrue(self.index.file_needs_update('/d/new.txt', 1.0, 1))

    def test_close(self):
        self.index.close()
        self.assertIsNotNone(self.index._conn)

    def test_get_stats(self):
        self.add('/d/s.txt', 'x')
        stats = self.index.get_stats()
        self.assertEqual(stats['total_files'], 1)
        self.assertIn('total_size_bytes', stats)
        self.assertGreater(stats['db_size'], 0)

    def test_connection_reset_after_fork(self):
        """Соединение SQLite не используется в дочернем процессе после fork()."""
        import os
        conn_before = self.index._conn
        self.index._pid = -1          # имитируем "другой процесс"
        conn_after = self.index._conn
        self.assertIsNot(conn_before, conn_after)
        self.assertEqual(self.index._pid, os.getpid())


if __name__ == '__main__':
    unittest.main()
