#!/usr/bin/env python3
"""Тесты конфигурации"""

import sys
import os
import unittest
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).parent.parent))


class TestConfig(unittest.TestCase):
    """Тест загрузки конфигурации"""

    def test_paths_exist(self):
        from core.config import ASTREX_HOME, LOGS_PATH, CACHE_PATH
        self.assertTrue(ASTREX_HOME.exists())
        self.assertTrue(LOGS_PATH.exists())
        self.assertTrue(CACHE_PATH.exists())

    def test_file_types(self):
        from core.config import FILE_TYPES
        self.assertIn('.pdf', FILE_TYPES.PDF)
        self.assertIn('.docx', FILE_TYPES.OFFICE)
        self.assertIn('.eml', FILE_TYPES.EMAIL)
        self.assertIn('.txt', FILE_TYPES.TEXT)
        self.assertIn('.py', FILE_TYPES.CODE)
        self.assertIn('.zip', FILE_TYPES.ARCHIVE)
        self.assertIn('.png', FILE_TYPES.IMAGE)
        self.assertIn('.db', FILE_TYPES.DATABASE)
        # all_supported should include everything
        all_ext = FILE_TYPES.all_supported
        self.assertIn('.pdf', all_ext)
        self.assertIn('.py', all_ext)

    def test_engine_config_defaults(self):
        from core.config import ENGINE_CONFIG
        self.assertEqual(ENGINE_CONFIG.min_score, 0.1)
        self.assertGreater(ENGINE_CONFIG.max_file_size, 0)
        self.assertEqual(ENGINE_CONFIG.archive_max_depth, 3)

    def test_nlp_config_defaults(self):
        from core.config import NLP_CONFIG
        self.assertGreater(NLP_CONFIG.max_text_length, 0)
        self.assertTrue(len(NLP_CONFIG.spacy_models) > 0)

    def test_yaml_loader_no_crash(self):
        """load_config не должна падать даже без файла"""
        from core.config import load_config
        load_config()  # не должно вызвать исключение

    def test_apply_yaml_overrides(self):
        from core.config import _apply_yaml_overrides, EngineConfig
        cfg = EngineConfig()
        _apply_yaml_overrides(cfg, {'min_score': 0.5, 'ocr_enabled': False})
        self.assertEqual(cfg.min_score, 0.5)
        self.assertFalse(cfg.ocr_enabled)

    def test_apply_yaml_overrides_tuple(self):
        from core.config import _apply_yaml_overrides, EngineConfig
        cfg = EngineConfig()
        _apply_yaml_overrides(cfg, {'encodings': ['utf-8', 'cp1251']})
        self.assertEqual(cfg.encodings, ('utf-8', 'cp1251'))


if __name__ == '__main__':
    unittest.main()
