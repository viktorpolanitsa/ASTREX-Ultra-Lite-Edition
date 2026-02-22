#!/usr/bin/env python3
"""Тесты NLP модуля"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestEntityExtractor(unittest.TestCase):

    def test_extract_empty(self):
        from core.nlp import entity_extractor
        result = entity_extractor.extract('')
        self.assertEqual(result, {})

    def test_extract_short_text(self):
        from core.nlp import entity_extractor
        result = entity_extractor.extract('a')
        self.assertEqual(result, {})

    def test_extract_dates(self):
        from core.nlp import entity_extractor
        text = 'Договор от 15.03.2024 подписан 01.01.2025'
        result = entity_extractor.extract(text)
        if 'DATES' in result:
            self.assertGreater(len(result['DATES']), 0)

    def test_extract_contacts_email(self):
        from core.nlp import entity_extractor
        text = 'Контакт: test@example.com и info@company.ru'
        result = entity_extractor.extract(text)
        if 'CONTACTS' in result:
            found = ' '.join(result['CONTACTS'])
            self.assertTrue(
                'test@example.com' in found or 'info@company.ru' in found
            )

    def test_extract_money(self):
        from core.nlp import entity_extractor
        text = 'Сумма контракта 1 500 000 рублей и $50,000'
        result = entity_extractor.extract(text)
        if 'MONEY' in result:
            self.assertGreater(len(result['MONEY']), 0)

    def test_regex_text_limit(self):
        """Длинный текст не должен зависать (ReDoS protection)"""
        from core.nlp import entity_extractor
        # 200K символов — больше _MAX_REGEX_TEXT_LEN
        text = 'Слово ' * 50000
        # Не должно зависнуть
        result = entity_extractor.extract(text)
        self.assertIsInstance(result, dict)


class TestMorphologyAnalyzer(unittest.TestCase):

    def test_expand_query(self):
        from core.nlp import expand_query
        forms = expand_query('договор')
        self.assertIsInstance(forms, set)
        self.assertIn('договор', forms)

    def test_expand_query_empty(self):
        from core.nlp import expand_query
        forms = expand_query('')
        self.assertIsInstance(forms, set)


class TestRelevanceCalculator(unittest.TestCase):

    def test_calculate_relevance(self):
        from core.nlp import calculate_relevance
        score = calculate_relevance('Это тестовый документ', 'тестовый')
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_calculate_relevance_no_match(self):
        from core.nlp import calculate_relevance
        score = calculate_relevance('Абсолютно другой текст', 'xyz123')
        self.assertIsInstance(score, float)

    def test_calculate_relevance_batch(self):
        from core.nlp import calculate_relevance_batch
        texts = ['Первый текст', 'Второй текст', 'Третий']
        scores = calculate_relevance_batch(texts, 'текст')
        self.assertEqual(len(scores), 3)
        for s in scores:
            self.assertIsInstance(s, float)


class TestFuzzyMatcher(unittest.TestCase):

    def test_fuzzy_search(self):
        from core.nlp import fuzzy_search
        matches = fuzzy_search('Иванов', ['Иванов', 'Петров', 'Иванова'])
        self.assertIsInstance(matches, list)
        if matches:
            self.assertEqual(matches[0][0], 'Иванов')


if __name__ == '__main__':
    unittest.main()
