#!/usr/bin/env python3
"""Тесты аналитических модулей: кодировки, хронология, криптодетектор,
дубликаты, классификатор, плагины"""

import io
import os
import sys
import tempfile
import unittest
import zipfile
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.helpers  # noqa: F401,E402

RUSSIAN = ('Настоящим сообщаем, что поставка оборудования по договору задерживается. '
           'Просим согласовать новый график работ и подтвердить готовность площадки.')


class TestEncoding(unittest.TestCase):

    def test_cyrillic_single_byte_encodings(self):
        from core.encoding import detect_encoding
        for enc in ('cp1251', 'koi8-r', 'cp866'):
            with self.subTest(encoding=enc):
                self.assertEqual(detect_encoding(RUSSIAN.encode(enc))[0], enc)

    def test_upper_case_and_short_texts(self):
        """Регрессия: текст ЗАГЛАВНЫМИ в cp1251 определялся как KOI8-R, "Привет, мир" — как MacCyrillic."""
        from core.encoding import detect_encoding
        for text in ('СЕКРЕТНО. ТОЛЬКО ДЛЯ СЛУЖЕБНОГО ПОЛЬЗОВАНИЯ.', 'Привет, мир, как дела у всех?',
                     'ДОГОВОР № 15', 'УТВЕРЖДАЮ', "Україна, Київ, вулиця Хрещатик, будинок 1."):
            for enc in ('cp1251', 'koi8-r', 'cp866'):
                try:
                    data = text.encode(enc)
                except UnicodeEncodeError:
                    continue
                with self.subTest(text=text, encoding=enc):
                    self.assertEqual(data.decode(detect_encoding(data)[0]), text)

    def test_short_utf8_cyrillic_is_not_utf16(self):
        """16 байт кириллицы в UTF-8 (D0/D1 через байт) не должны приниматься за UTF-16BE."""
        from core.encoding import detect_encoding
        self.assertEqual(detect_encoding('Заметка 5'.encode('utf-8'))[0], 'utf-8')
        self.assertEqual(detect_encoding('ПриветМир'.encode('utf-16-le'))[0], 'utf-16-le')

    def test_utf8_utf16_and_bom(self):
        from core.encoding import decode_bytes
        english = 'The quick brown fox jumps over the lazy dog, 42 times.'
        for data, expected in ((RUSSIAN.encode('utf-8'), RUSSIAN), (RUSSIAN.encode('utf-16'), RUSSIAN),
                               (RUSSIAN.encode('utf-16-le'), RUSSIAN), (RUSSIAN.encode('utf-16-be'), RUSSIAN),
                               (english.encode('utf-16-le'), english),
                               (b'\xef\xbb\xbf' + RUSSIAN.encode('utf-8'), RUSSIAN)):
            with self.subTest(prefix=data[:4]):
                text, _ = decode_bytes(data)
                self.assertEqual(text.lstrip('\ufeff'), expected)

    def test_latin_text_not_detected_as_cyrillic(self):
        """Регрессия: западноевропейский текст декодировался как cp1251."""
        from core.encoding import decode_bytes
        text = 'Le café de la rue Saint-Honoré était fermé. Über die Brücke gehen.'
        decoded, enc = decode_bytes(text.encode('cp1252'))
        self.assertEqual(decoded, text, enc)

    def test_declared_charset_is_respected(self):
        from core.encoding import decode_bytes
        self.assertEqual(decode_bytes('Ёлка'.encode('koi8-r'), declared='koi8-r')[0], 'Ёлка')
        # неизвестная заявленная кодировка не роняет декодирование
        self.assertIn('Ёлка', decode_bytes('Ёлка ёж'.encode('utf-8'), declared='x-unknown')[0])

    def test_binary_detection(self):
        from core.encoding import is_probably_binary
        self.assertTrue(is_probably_binary(bytes(range(256))))
        self.assertFalse(is_probably_binary(RUSSIAN.encode('cp1251')))
        self.assertFalse(is_probably_binary(RUSSIAN.encode('utf-16')))


class TestTimeline(unittest.TestCase):

    def test_nested_patterns_do_not_create_false_dates(self):
        """Регрессия: "15 марта 2024" давало ещё и событие "1 марта 2024"."""
        from core.timeline import extract_dates_from_text
        dates = [d for d, _ctx, _pos in extract_dates_from_text('Совещание 15 марта 2024 года прошло успешно.')]
        self.assertEqual(dates, [date(2024, 3, 15)])

    def test_repeated_dates(self):
        from core.timeline import extract_dates_from_text
        text = 'Договор от 15.03.2024 (15 марта 2024). ' + 'текст ' * 100 + 'Оплата 15.03.2024.'
        found = extract_dates_from_text(text)
        self.assertEqual([d for d, _, _ in found], [date(2024, 3, 15)] * 2)   # рядом — одно, далеко — второе

    def test_invalid_dates_ignored(self):
        from core.timeline import extract_dates_from_text
        self.assertEqual(extract_dates_from_text('Версия 31.02.2024 и 99.99.9999, адрес 10.1.20.24'), [])

    def test_event_types(self):
        from core.timeline import TimelineBuilder
        builder = TimelineBuilder()
        builder.add_document('Платеж 01.02.2023 прошёл. ' + 'x ' * 100 + 'Общество учреждено 05.05.2010.',
                             source='a.txt')
        types = {e.date: e.event_type for e in builder.build()}
        self.assertEqual(types, {'2010-05-05': 'created', '2023-02-01': 'transaction'})


class TestCryptoDetector(unittest.TestCase):

    def setUp(self):
        from core.crypto import CryptoDetector
        self.detector = CryptoDetector()
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def detect(self, name, data, deep=False):
        p = self.dir / name
        p.write_bytes(data)
        return self.detector.detect_file(str(p), deep=deep)

    def test_encrypted_zip(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_STORED) as z:
            z.writestr('secret.bin', os.urandom(4000))
        data = bytearray(buf.getvalue())
        for sig, offset in ((b'PK\x03\x04', 6), (b'PK\x01\x02', 8)):   # флаг "зашифровано"
            i = data.find(sig)
            while i != -1:
                data[i + offset] |= 1
                i = data.find(sig, i + 4)
        result = self.detect('enc.zip', bytes(data))
        self.assertTrue(result.is_encrypted)
        self.assertEqual(result.encryption_type, 'zip_encrypted')

    def test_plain_zip_is_not_encrypted(self):
        """Регрессия: сжатые файлы (высокая энтропия) считались зашифрованными."""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
            z.writestr('random.bin', os.urandom(200_000))
        result = self.detect('plain.zip', buf.getvalue())
        self.assertFalse(result.is_encrypted)
        self.assertEqual(result.file_format, 'zip')

    def test_openssl_and_random_container(self):
        self.assertEqual(self.detect('f.enc', b'Salted__' + os.urandom(5000)).encryption_type, 'openssl')
        container = self.detect('volume.bin', os.urandom(300_000))
        self.assertEqual(container.encryption_type, 'possible_container')
        self.assertFalse(self.detect('small.bin', os.urandom(1000)).is_encrypted)

    def test_text_file_is_not_encrypted(self):
        self.assertFalse(self.detect('note.txt', (RUSSIAN * 50).encode('utf-8')).is_encrypted)

    def test_wallet_checksums(self):
        """Регрессия: любая base58-строка (в т.ч. в base64) считалась адресом Bitcoin."""
        wallets = self.detector.scan_for_crypto_wallets(
            'valid 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa, broken 1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNb, '
            'segwit bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4, bad bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5')
        self.assertEqual(sorted(w['address'] for w in wallets),
                         ['1A1zP1eP5QGefi2DMPTfTL5SLmv7DivfNa', 'bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4'])

    def test_private_key_deep(self):
        pem = b'-----BEGIN RSA PRIVATE KEY-----\nMIIEow' + b'A' * 200 + b'\n-----END RSA PRIVATE KEY-----\n'
        result = self.detect('id_rsa.pem', pem, deep=True)
        self.assertEqual(result.encryption_type, 'private_key')


class TestFingerprint(unittest.TestCase):

    def test_files_differing_after_first_megabyte_are_not_duplicates(self):
        """Регрессия: хешировался только первый мегабайт файла."""
        from core.fingerprint import FingerprintEngine, deduplicate
        with tempfile.TemporaryDirectory() as d:
            head = b'A' * (2 * 1024 * 1024)
            (Path(d) / 'a.bin').write_bytes(head + b'tail-1')
            (Path(d) / 'b.bin').write_bytes(head + b'tail-2')
            (Path(d) / 'c.bin').write_bytes(head + b'tail-1')
            engine = FingerprintEngine()
            fps = [engine.fingerprint_file(str(Path(d) / n)) for n in ('a.bin', 'b.bin', 'c.bin')]
            report = deduplicate(fps)
            self.assertEqual(len(report.exact_duplicate_groups), 1)
            self.assertEqual({Path(p).name for p in report.exact_duplicate_groups[0]}, {'a.bin', 'c.bin'})
            self.assertEqual(report.wasted_bytes, len(head) + 6)

    def test_near_duplicates(self):
        from core.fingerprint import FingerprintEngine, deduplicate
        engine = FingerprintEngine()
        words = [f'слово{i}' for i in range(400)]
        base = ' '.join(words)
        edited = ' '.join(words[:200] + ['вставка'] + words[200:])
        other = ' '.join(reversed(words))
        fps = [engine.fingerprint_text(t, n) for t, n in ((base, 'base'), (edited, 'edited'), (other, 'other'))]
        pairs = deduplicate(fps, near_threshold=0.8).near_duplicate_pairs
        self.assertEqual([(a, b) for a, b, _ in pairs], [('base', 'edited')])

    def test_empty_documents_are_not_similar(self):
        """Регрессия: два пустых документа давали схожесть 1.0."""
        from core.fingerprint import FingerprintEngine
        engine = FingerprintEngine()
        self.assertEqual(engine.jaccard_similarity(set(), set()), 0.0)
        self.assertIsNone(engine.fingerprint_text('коротко', 'x').shingles)


class TestClassifier(unittest.TestCase):

    def classify(self, text):
        from core.classifier import DocumentClassifier
        return DocumentClassifier().classify(text)

    def test_word_prefixes_not_substrings(self):
        """Регрессия: "иск" находился в "риск", "код" — в "кодекс"."""
        result = self.classify('Оценка рисков и рисковых активов, риски снижаются')
        self.assertNotEqual(result.category, 'юридический')

    def test_categories(self):
        self.assertEqual(self.classify('Истец подал иск в суд по договору поставки').category, 'юридический')
        self.assertEqual(self.classify('Оплата счёта и перевод суммы в банк').category, 'финансы')
        self.assertEqual(self.classify('Сервер на Python в Docker, репозиторий git').category, 'техническая')

    def test_empty(self):
        result = self.classify('')
        self.assertEqual(result.category, 'общий')
        self.assertEqual(result.word_count, 0)


class TestPlugins(unittest.TestCase):

    PLUGIN = '''
plugin_info = {"name": "test_xyz", "version": "1.0", "author": "t", "description": "d"}

from pathlib import Path
from extractors.base import BaseExtractor, ExtractionResult


class XyzExtractor(BaseExtractor):
    extensions = [".xyz_astrex_test"]
    priority = 5

    @classmethod
    def is_available(cls):
        return True

    @classmethod
    def extract(cls, path: Path):
        return ExtractionResult(text="XYZ:" + path.read_text(encoding="utf-8"))


def register(registry):
    registry.register(XyzExtractor)
'''

    BROKEN = '''
plugin_info = {"name": "broken_xyz"}
from extractors.base import BaseExtractor, ExtractionResult


class HalfExtractor(BaseExtractor):
    extensions = [".half_astrex_test"]

    @classmethod
    def extract(cls, path):
        return ExtractionResult(text="")


def register(registry):
    registry.register(HalfExtractor)
    raise RuntimeError("registration failed")
'''

    def setUp(self):
        from core.plugins import PluginManager
        self.manager = PluginManager()
        self.files = []
        for name, code in (('test_xyz.py', self.PLUGIN), ('broken_xyz.py', self.BROKEN)):
            path = self.manager.PLUGIN_DIR / name
            path.write_text(code, encoding='utf-8')
            self.files.append(path)

    def tearDown(self):
        for name in ('test_xyz', 'broken_xyz'):
            if self.manager.get_plugin(name):
                self.manager.unload(name)
        for path in self.files:
            path.unlink()

    def test_load_extract_unload(self):
        from extractors import registry
        from core.engine import default_extensions
        self.manager.discover()
        self.assertTrue(self.manager.load('test_xyz'))
        self.assertIn('.xyz_astrex_test', default_extensions())    # сканер подхватывает расширение
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'a.xyz_astrex_test'
            p.write_text('данные', encoding='utf-8')
            self.assertEqual(registry.extract(p).text, 'XYZ:данные')
        self.assertTrue(self.manager.unload('test_xyz'))
        self.assertNotIn('.xyz_astrex_test', default_extensions())

    def test_failed_plugin_rolls_back(self):
        from extractors import registry
        self.manager.discover()
        with self.assertLogs('astrex.plugins', level='ERROR'):
            self.assertFalse(self.manager.load('broken_xyz'))
        self.assertIn('registration failed', self.manager.get_plugin('broken_xyz').error)
        self.assertNotIn('.half_astrex_test', registry.supported_extensions())

    def test_plugin_info_is_read_without_executing(self):
        evil = self.manager.PLUGIN_DIR / 'evil_info.py'
        evil.write_text('plugin_info = {"name": __import__("os").getcwd()}\n', encoding='utf-8')
        self.files.append(evil)
        names = [p.name for p in self.manager.discover()]
        self.assertNotIn(os.getcwd(), names)


if __name__ == '__main__':
    unittest.main()
