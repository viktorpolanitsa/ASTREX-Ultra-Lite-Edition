#!/usr/bin/env python3
"""Тесты NLP модуля"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.helpers  # noqa: F401,E402


class TestEntityExtractor(unittest.TestCase):

    def extract(self, text):
        from core.nlp import entity_extractor
        return entity_extractor.extract(text)

    def test_extract_empty(self):
        self.assertEqual(self.extract(''), {})

    def test_extract_short_text(self):
        self.assertEqual(self.extract('a'), {})

    def test_extract_dates(self):
        result = self.extract('Договор от 15.03.2024 подписан 01.01.2025')
        self.assertIn('15.03.2024', result['DATES'])
        self.assertIn('01.01.2025', result['DATES'])

    def test_extract_contacts_email(self):
        result = self.extract('Контакт: test@example.com и info@company.ru')
        self.assertIn('test@example.com', result['CONTACTS'])
        self.assertIn('info@company.ru', result['CONTACTS'])

    def test_extract_money(self):
        result = self.extract('Сумма контракта 1 500 000 рублей и $50,000, цена 500 ₽ и 300 р. итого')
        money = result['MONEY']
        for value in ('1 500 000 рублей', '$50,000', '500 ₽', '300 р.'):
            self.assertIn(value, money)

    def test_persons_and_orgs(self):
        result = self.extract('Директор Петров И.И. и Иванов Иван Иванович из ООО «Вектор Плюс» и АО Север.')
        self.assertIn('Петров И.И.', result['PERSONS'])
        self.assertIn('Иванов Иван Иванович', result['PERSONS'])
        self.assertIn('ООО Вектор Плюс', result['ORGANIZATIONS'])

    def test_no_false_positives(self):
        """Регрессия: "актуальный" как документ, "ИПОТЕКА" как организация и т.п."""
        result = self.extract('Актуальный активный счетчик. ИПОТЕКА ОДОБРЕНА. Российская Федерация. '
                              'Screen Print и Annual Report. Этот магнит притягивает.')
        self.assertNotIn('DOCUMENTS', result)
        self.assertNotIn('ORGANIZATIONS', result)
        self.assertNotIn('PERSONS', result)

    def test_nested_matches_deduplicated(self):
        result = self.extract('Тел. +7 (999) 123-45-67, г. Санкт-Петербург')
        self.assertEqual(result['CONTACTS'], ['+7 (999) 123-45-67'])
        self.assertEqual(result['LOCATIONS'], ['г. Санкт-Петербург'])

    def test_regex_text_limit(self):
        """Длинный текст не должен зависать (ReDoS protection)"""
        result = self.extract('Слово ' * 50000)
        self.assertIsInstance(result, dict)


class TestStemmer(unittest.TestCase):

    def test_snowball_examples(self):
        from core.nlp import _stem_russian
        expected = {
            'договора': 'договор', 'договорами': 'договор', 'красивыми': 'красив',
            'одевавшись': 'одева', 'улыбались': 'улыба', 'наибольший': 'наибольш',
            'лёгкость': 'легкост',
        }
        for word, stem in expected.items():
            with self.subTest(word=word):
                self.assertEqual(_stem_russian(word), stem)


class TestQueryMatcher(unittest.TestCase):

    def matcher(self, query, morph=True):
        from core.nlp import build_query_matcher
        return build_query_matcher(query, use_morphology=morph)

    def test_whole_words_only(self):
        """Регрессия: "Иван" находил "диван" (поиск подстроки)."""
        self.assertIsNone(self.matcher('Иван').find('Купил диван и кресло.'))
        self.assertIsNotNone(self.matcher('Иван').find('Позвонил Иван.'))

    def test_stopwords_ignored_and_all_terms_required(self):
        """Регрессия: "Иванов и Петров" совпадал с любым текстом, где есть буква "и"."""
        m = self.matcher('Иванов и Петров')
        self.assertIsNone(m.find('Протокол заседания, без упоминания нужных лиц.'))
        self.assertIsNone(m.find('Только Иванов присутствовал.'))
        self.assertIsNotNone(m.find('Петров встретил Иванова.'))

    def test_morphology_and_yo(self):
        m = self.matcher('договор')
        self.assertIsNotNone(m.find('согласно договору поставки'))
        self.assertIsNotNone(self.matcher('ёлка').find('новогодняя елка'))

    def test_without_morphology_exact_only(self):
        m = self.matcher('договор', morph=False)
        self.assertIsNotNone(m.find('договор'))
        self.assertIsNone(m.find('договору'))

    def test_phrase_and_position(self):
        text = 'начало ' * 100 + 'Иван Петров' + ' конец' * 100
        info = self.matcher('Иван Петров').find(text)
        self.assertEqual(info.kind, 'phrase')
        self.assertEqual(text[info.pos:info.pos + info.length], 'Иван Петров')

    def test_digits_match_inside_requisites(self):
        self.assertIsNotNone(self.matcher('40817810').find('р/с40817810 в банке'))

    def test_serializable(self):
        from core.nlp import QueryMatcher
        m = self.matcher('Иванов договор')
        m2 = QueryMatcher.from_dict(m.to_dict())
        text = 'договор с Ивановым'
        self.assertEqual(m.find(text).pos, m2.find(text).pos)


class TestMorphologyAnalyzer(unittest.TestCase):

    def test_expand_query(self):
        from core.nlp import expand_query
        forms = expand_query('договор')
        self.assertIsInstance(forms, set)
        self.assertIn('договор', forms)

    def test_expand_query_empty(self):
        from core.nlp import expand_query
        self.assertIsInstance(expand_query(''), set)

    def test_expand_query_skips_stopwords(self):
        from core.nlp import expand_query
        self.assertNotIn('и', expand_query('Иванов и Петров'))

    def test_broken_pymorphy_does_not_crash(self):
        """Регрессия: pymorphy2 на Python ≥ 3.11 падал с AttributeError при импорте."""
        from core.nlp import MorphologyAnalyzer
        analyzer = MorphologyAnalyzer()
        self.assertIsInstance(analyzer.available, bool)


class TestRelevanceCalculator(unittest.TestCase):

    def test_calculate_relevance(self):
        from core.nlp import calculate_relevance
        score = calculate_relevance('Это тестовый документ', 'тестовый')
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_calculate_relevance_no_match(self):
        from core.nlp import calculate_relevance
        self.assertEqual(calculate_relevance('Абсолютно другой текст', 'xyz123'), 0.0)

    def test_inflected_match_scores_well(self):
        """Регрессия: совпадение по словоформе получало ~0.08 и отсекалось min-score."""
        from core.nlp import relevance_calculator
        self.assertGreater(relevance_calculator.keyword_score('Подписан договор с Ивановым', 'Иванов'), 0.5)

    def test_ordering(self):
        from core.nlp import relevance_calculator as rc
        exact = rc.keyword_score('контракт на поставку угля', 'поставку угля')
        partial = rc.keyword_score('поставку леса', 'поставку угля')
        none = rc.keyword_score('совсем другое', 'поставку угля')
        self.assertGreater(exact, partial)
        self.assertGreater(partial, none)

    def test_calculate_relevance_batch(self):
        from core.nlp import calculate_relevance_batch
        scores = calculate_relevance_batch(['Первый текст', 'Второй текст', 'Третий'], 'текст')
        self.assertEqual(len(scores), 3)
        self.assertGreater(scores[0], scores[2])


class TestFuzzyMatcher(unittest.TestCase):

    def test_fuzzy_search(self):
        from core.nlp import fuzzy_search
        matches = fuzzy_search('Иванов', ['Иванов', 'Петров', 'Иванова'])
        self.assertEqual(matches[0][0], 'Иванов')

    def test_fuzzy_find_short_text_and_tail(self):
        """Регрессия: тексты короче окна (200 символов) и хвост не сканировались."""
        from core.nlp import FuzzyMatcher
        self.assertTrue(FuzzyMatcher.fuzzy_find_in_text('Альбатрос', 'проект Альбатрас')[0])
        long_text = 'x ' * 5000 + 'Альбатрас'
        found, pos, _ = FuzzyMatcher.fuzzy_find_in_text('Альбатрос', long_text)
        self.assertTrue(found)
        self.assertEqual(long_text[pos:pos + 9], 'Альбатрас')

    def test_fuzzy_whole_words(self):
        """"Иван" не должен находить "диван" и через нечёткий поиск."""
        from core.nlp import FuzzyMatcher
        self.assertFalse(FuzzyMatcher.fuzzy_find_in_text('Иван', 'Купил диван')[0])

    def test_fuzzy_unrelated_text(self):
        """Регрессия (fallback без rapidfuzz): Jaccard*100 давал совпадение почти с любым текстом."""
        from core.nlp import FuzzyMatcher
        text = 'Отчёт о поставках продукции на склад за третий квартал, подписан директором.'
        self.assertFalse(FuzzyMatcher.fuzzy_find_in_text('газпром', text)[0])

    def test_fallback_without_rapidfuzz(self):
        from core.nlp import FuzzyMatcher
        saved = FuzzyMatcher._available
        FuzzyMatcher._available = False
        try:
            self.assertTrue(FuzzyMatcher.fuzzy_find_in_text('Альбатрос', 'проект Альбатрас утверждён')[0])
            self.assertFalse(FuzzyMatcher.fuzzy_find_in_text('газпром', 'склад и поставки')[0])
        finally:
            FuzzyMatcher._available = saved


if __name__ == '__main__':
    unittest.main()
