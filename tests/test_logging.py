#!/usr/bin/env python3
"""Тесты модуля логирования"""

import io
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.helpers  # noqa: F401,E402


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

    def test_child_logger_has_no_own_handlers(self):
        """Дочерние логгеры не получают своих обработчиков (иначе — дубли)."""
        from core.logging_setup import get_logger
        child = get_logger('astrex.cli_test')
        self.assertEqual(child.handlers, [])
        self.assertTrue(child.propagate)

    def test_message_is_written_once(self):
        """Регрессия: сообщение дочернего логгера выводилось дважды."""
        from core.logging_setup import get_logger, log
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setLevel(logging.DEBUG)
        log.addHandler(handler)
        try:
            get_logger('astrex.cli').error('единственное сообщение')
        finally:
            log.removeHandler(handler)
        self.assertEqual(stream.getvalue().count('единственное сообщение'), 1)
        # и корневой логгер Python его не дублирует
        self.assertFalse(logging.getLogger('astrex').propagate)


if __name__ == '__main__':
    unittest.main()
