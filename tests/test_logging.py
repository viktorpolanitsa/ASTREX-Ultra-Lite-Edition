#!/usr/bin/env python3
"""Тесты модуля логирования"""

import sys
import logging
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestLogging(unittest.TestCase):

    def test_get_logger(self):
        from core.logging_setup import get_logger
        logger = get_logger('test_module')
        self.assertIsInstance(logger, logging.Logger)
        self.assertEqual(logger.name, 'test_module')

    def test_setup_logger(self):
        from core.logging_setup import setup_logger
        logger = setup_logger('test_setup', log_file=False)
        self.assertIsInstance(logger, logging.Logger)
        self.assertTrue(len(logger.handlers) > 0)

    def test_log_instance(self):
        from core.logging_setup import log
        self.assertIsInstance(log, logging.Logger)
        self.assertEqual(log.name, 'astrex')

    def test_log_does_not_crash(self):
        from core.logging_setup import log
        log.info('Test message')
        log.warning('Test warning')
        log.error('Test error')
        log.debug('Test debug')


if __name__ == '__main__':
    unittest.main()
