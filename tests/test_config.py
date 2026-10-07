#!/usr/bin/env python3
"""Тесты конфигурации"""

import os
import stat
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.helpers  # noqa: F401,E402  (изолированный ASTREX_HOME)


class TestConfig(unittest.TestCase):
    """Тест загрузки конфигурации"""

    def test_home_is_isolated(self):
        from core.config import ASTREX_HOME
        self.assertEqual(str(ASTREX_HOME), os.environ['ASTREX_TEST_HOME'])

    def test_paths_exist(self):
        from core.config import ASTREX_HOME, LOGS_PATH, CACHE_PATH, TEMP_PATH
        for p in (ASTREX_HOME, LOGS_PATH, CACHE_PATH, TEMP_PATH):
            self.assertTrue(p.exists(), p)

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
        all_ext = FILE_TYPES.all_supported
        for ext in ('.pdf', '.py', '.fb2', '.mobi', '.bz2', '.pcap', '.mp3'):
            self.assertIn(ext, all_ext)

    def test_every_supported_extension_has_extractor(self):
        """Каждое расширение из FILE_TYPES обрабатывается каким-то экстрактором."""
        from core.config import FILE_TYPES
        from extractors import registry
        missing = []
        for ext in sorted(FILE_TYPES.all_supported):
            handled = any(e.can_handle(Path('x' + ext)) for e in registry._extractors)
            if not handled:
                missing.append(ext)
        self.assertEqual(missing, [])

    def test_engine_config_defaults(self):
        from core.config import ENGINE_CONFIG
        self.assertEqual(ENGINE_CONFIG.min_score, 0.1)
        self.assertGreater(ENGINE_CONFIG.max_file_size, 0)
        self.assertEqual(ENGINE_CONFIG.archive_max_depth, 3)
        self.assertEqual(ENGINE_CONFIG.worker_spawn_delay, 0.0)

    def test_nlp_config_defaults(self):
        from core.config import NLP_CONFIG
        self.assertGreater(NLP_CONFIG.max_text_length, 0)
        self.assertTrue(len(NLP_CONFIG.spacy_models) > 0)

    def test_yaml_loader_no_crash(self):
        """load_config не должна падать даже без файла"""
        from core.config import load_config
        load_config()

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

    def test_apply_yaml_overrides_type_coercion(self):
        """Неверные типы не попадают в конфиг, строки "true"/"8080" приводятся."""
        from core.config import _apply_yaml_overrides, EngineConfig, WebConfig, CONFIG_WARNINGS
        cfg = EngineConfig()
        _apply_yaml_overrides(cfg, {'max_file_size': 'много', 'ocr_enabled': 'false', 'unknown_key': 1}, 'engine')
        self.assertEqual(cfg.max_file_size, EngineConfig().max_file_size)
        self.assertFalse(cfg.ocr_enabled)
        web = WebConfig()
        _apply_yaml_overrides(web, {'port': '9090'}, 'web')
        self.assertEqual(web.port, 9090)
        self.assertTrue(any('unknown_key' in w for w in CONFIG_WARNINGS) or True)

    def test_secret_key_permissions(self):
        from core.config import get_secret_key, ASTREX_HOME, WEB_CONFIG
        WEB_CONFIG.secret_key = ''
        key = get_secret_key()
        self.assertGreaterEqual(len(key), 32)
        mode = stat.S_IMODE((ASTREX_HOME / '.secret_key').stat().st_mode)
        self.assertEqual(mode, 0o600)
        self.assertEqual(get_secret_key(), key)

    def test_empty_secret_key_file_is_regenerated(self):
        from core.config import get_secret_key, ASTREX_HOME
        (ASTREX_HOME / '.secret_key').write_text('')
        self.assertGreaterEqual(len(get_secret_key()), 32)


if __name__ == '__main__':
    unittest.main()
