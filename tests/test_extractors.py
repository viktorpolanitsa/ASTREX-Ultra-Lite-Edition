#!/usr/bin/env python3
"""Тесты экстракторов"""

import bz2
import gzip
import io
import json
import os
import sqlite3
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.helpers  # noqa: F401,E402
from tests.helpers import make_docx, docx_bytes  # noqa: E402

DOCX_SPLIT = ('<w:p><w:pPr><w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs></w:pPr>'
              '<w:r><w:t>Гражданин Ива</w:t></w:r><w:r><w:t>нов</w:t></w:r>'
              '<w:r><w:t xml:space="preserve"> из ООО &quot;Вектор&quot; &amp; Ко</w:t></w:r></w:p>'
              '<w:p><w:r><w:t>Второй абзац</w:t></w:r></w:p>')


class ExtractorTestCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def extract(self, path):
        from extractors import registry
        return registry.extract(Path(path))


class TestPlainTextExtractor(ExtractorTestCase):

    def test_extract_utf8(self):
        p = self.dir / 'a.txt'
        p.write_text('Привет, мир! Hello, world!', encoding='utf-8')
        result = self.extract(p)
        self.assertTrue(result.success)
        self.assertIn('Привет', result.text)

    def test_extract_empty_file(self):
        p = self.dir / 'empty.txt'
        p.touch()
        result = self.extract(p)
        self.assertTrue(result.success)
        self.assertEqual(result.text, '')

    def test_cyrillic_encodings(self):
        """Регрессия: KOI8-R/CP866 декодировались как cp1251 (кракозябры)."""
        text = 'Секретный проект Альбатрос. Ответственный: Сидоров Пётр.'
        for enc in ('utf-8', 'cp1251', 'koi8-r', 'cp866', 'utf-16'):
            with self.subTest(encoding=enc):
                p = self.dir / f'{enc}.txt'
                p.write_bytes(text.encode(enc))
                self.assertIn('Альбатрос', self.extract(p).text)

    def test_large_utf8_file_cut_mid_character(self):
        """Регрессия: обрезка большого файла посреди символа UTF-8 → весь файл в cp1251."""
        from core.config import ENGINE_CONFIG
        old = ENGINE_CONFIG.chunk_size, ENGINE_CONFIG.max_extracted_chars
        ENGINE_CONFIG.chunk_size, ENGINE_CONFIG.max_extracted_chars = 1001, 250
        try:
            p = self.dir / 'big.txt'
            p.write_text('Привет мир ' * 500, encoding='utf-8')
            result = self.extract(p)
            self.assertTrue(result.metadata['truncated'])
            self.assertEqual(result.metadata['encoding'], 'utf-8')
            self.assertIn('Привет мир', result.text)
        finally:
            ENGINE_CONFIG.chunk_size, ENGINE_CONFIG.max_extracted_chars = old

    def test_binary_content_is_skipped_without_error(self):
        p = self.dir / 'data.dat'
        p.write_bytes(bytes(range(256)) * 4)
        result = self.extract(p)
        self.assertIsNone(result.error)
        self.assertEqual(result.text, '')


class TestStructuredText(ExtractorTestCase):

    def test_extract_json_keeps_short_values_and_numbers(self):
        """Регрессия: строки ≤20 символов, числа и элементы массива после 100-го терялись."""
        data = [{"name": f"Иван{i}", "phone": 79001000000 + i} for i in range(200)]
        p = self.dir / 'people.json'
        p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
        text = self.extract(p).text
        self.assertIn('Иван199', text)
        self.assertIn('79001000199', text)
        self.assertIn('name: Иван0', text)

    def test_extract_xml_document_order(self):
        """Регрессия: текст смешанного содержимого XML перемешивался."""
        p = self.dir / 'mixed.xml'
        p.write_bytes('<?xml version="1.0" encoding="windows-1251"?><p>Директор <b>Иванов</b> подписал '
                      '<i>договор</i> поставки</p>'.encode('cp1251'))
        self.assertEqual(self.extract(p).text, 'Директор Иванов подписал договор поставки')

    def test_extract_html_removes_scripts(self):
        p = self.dir / 'page.html'
        p.write_text('<html><head><title>Заголовок</title><script>var x="скрипт";</script></head>'
                     '<body><p>Видимый &amp; текст</p></body></html>', encoding='utf-8')
        result = self.extract(p)
        self.assertIn('Видимый & текст', result.text)
        self.assertNotIn('скрипт', result.text)
        self.assertEqual(result.metadata['title'], 'Заголовок')

    def test_extract_csv(self):
        p = self.dir / 'data.csv'
        p.write_text("name,value\nAlice,100\nBob,200\n", encoding='utf-8')
        result = self.extract(p)
        self.assertIn('Alice', result.text)
        self.assertIn('Bob', result.text)

    def test_csv_semicolon_and_all_rows(self):
        """Регрессия: только первые 1000 строк; разделитель ';' не определялся."""
        rows = ['ФИО;Город'] + [f'Человек {i};Город{i}' for i in range(3000)] + ['Зайцев Борис;Псков']
        p = self.dir / 'big.csv'
        p.write_text('\n'.join(rows), encoding='cp1251')
        result = self.extract(p)
        self.assertEqual(result.metadata['delimiter'], ';')
        self.assertIn('Зайцев Борис | Псков', result.text)


class TestOfficeExtractors(ExtractorTestCase):

    def test_docx_runs_joined_and_entities_decoded(self):
        """Регрессия: "Ива нов", "&quot;", весь документ одной строкой."""
        p = make_docx(self.dir / 'r.docx', DOCX_SPLIT)
        text = self.extract(p).text
        self.assertIn('Гражданин Иванов из ООО "Вектор" & Ко', text)
        self.assertIn('\nВторой абзац', text)
        self.assertNotIn('\t', text.split('\n')[0])  # позиции табуляции из свойств абзаца — не текст

    def test_xlsx_with_numbers(self):
        try:
            from openpyxl import Workbook
        except ImportError:
            self.skipTest('openpyxl not installed')
        wb = Workbook()
        ws = wb.active
        ws.title = 'Клиенты'
        ws.append(['ФИО', 'ИНН'])
        ws.append(['Петров Пётр', 7707083893])
        p = self.dir / 't.xlsx'
        wb.save(p)
        text = self.extract(p).text
        self.assertIn('Лист: Клиенты', text)
        self.assertIn('Петров Пётр\t7707083893', text)

    def test_odt_spans(self):
        p = self.dir / 'd.odt'
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('content.xml', '<?xml version="1.0"?><office:document-content '
                       'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
                       'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body>'
                       '<office:text><text:p>Мелен<text:span>тьев</text:span><text:s text:c="2"/>отсутствовал'
                       '</text:p></office:text></office:body></office:document-content>')
        self.assertEqual(self.extract(p).text, 'Мелентьев  отсутствовал')

    def test_rtf_codepage_without_striprtf(self):
        from extractors.documents import rtf_to_text_basic
        rtf = (r"{\rtf1\ansi\ansicpg1251{\fonttbl{\f0 Times;}}{\*\generator X;}"
               r"\'cf\'f0\'e8\'e2\'e5\'f2 {\b \'ec\'e8\'f0}\par " + chr(92) + r"u1046?\'e8\'e2\'ee\'e9}")
        self.assertEqual(rtf_to_text_basic(rtf), 'Привет мир\nЖивой')

    def test_xls_biff8_fallback(self):
        try:
            import xlwt
            import olefile
        except ImportError:
            self.skipTest('xlwt/olefile not installed')
        book = xlwt.Workbook(encoding='utf-8')
        sheet = book.add_sheet('Лист1')
        sheet.write(0, 0, 'Кузнецов Алексей')
        sheet.write(0, 1, 4500)
        p = self.dir / 'l.xls'
        book.save(str(p))
        from extractors.documents import _xls_biff8
        from extractors.base import TextCollector
        with olefile.OleFileIO(str(p)) as ole:
            collector = TextCollector()
            sheets = _xls_biff8(ole.openstream('Workbook').read(), collector)
        self.assertEqual(sheets, ['Лист1'])
        self.assertIn('Кузнецов Алексей\t4500', collector.text())
        self.assertIn('Кузнецов Алексей', self.extract(p).text)

    def test_fb2_cp1251_skips_binary(self):
        fb2 = ('<?xml version="1.0" encoding="windows-1251"?><FictionBook '
               'xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"><description><title-info>'
               '<book-title>Хаджи-Мурат</book-title></title-info></description><body><section>'
               '<p>Я возвращался домой полями.</p></section></body>'
               '<binary id="i">iVBORw0KGgoAAAANSUhEUg</binary></FictionBook>')
        p = self.dir / 'b.fb2'
        p.write_bytes(fb2.encode('cp1251'))
        text = self.extract(p).text
        self.assertIn('Хаджи-Мурат', text)
        self.assertIn('Я возвращался домой полями.', text)
        self.assertNotIn('iVBOR', text)

    def test_epub_spine_order(self):
        p = self.dir / 'b.epub'
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('META-INF/container.xml', '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:'
                       'opendocument:xmlns:container"><rootfiles><rootfile full-path="o/c.opf"/></rootfiles>'
                       '</container>')
            z.writestr('o/c.opf', '<package xmlns="http://www.idpf.org/2007/opf"><manifest>'
                       '<item id="a" href="1.xhtml"/><item id="b" href="2.xhtml"/></manifest>'
                       '<spine><itemref idref="a"/><itemref idref="b"/></spine></package>')
            z.writestr('o/2.xhtml', '<html><body><p>Вторая</p></body></html>')
            z.writestr('o/1.xhtml', '<html><body><p>Первая &mdash; глава</p></body></html>')
        text = self.extract(p).text
        self.assertLess(text.index('Первая — глава'), text.index('Вторая'))


class TestEmailExtractors(ExtractorTestCase):

    def test_eml_unknown_charset_and_attachment(self):
        """Регрессия: неизвестная кодировка роняла письмо; вложения не извлекались."""
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from email.mime.application import MIMEApplication
        from email.header import Header
        m = MIMEMultipart()
        m['Subject'] = Header('Секретная сделка', 'utf-8')
        part = MIMEText('Операция Феникс', 'plain', 'utf-8')
        part.replace_header('Content-Type', 'text/plain; charset="x-unknown-8bit"')
        m.attach(part)
        att = MIMEApplication(docx_bytes(DOCX_SPLIT))
        att['Content-Disposition'] = 'attachment; filename="report.docx"'
        m.attach(att)
        p = self.dir / 'm.eml'
        p.write_bytes(m.as_bytes())
        result = self.extract(p)
        self.assertIsNone(result.error)
        self.assertIn('Тема: Секретная сделка', result.full_text)
        self.assertIn('Операция Феникс', result.full_text)
        self.assertIn('Гражданин Иванов', result.full_text)   # текст вложения DOCX

    def test_mbox_decodes_headers_and_survives_bad_charset(self):
        """Регрессия: заголовки RFC 2047 не декодировались; один битый charset терял весь ящик."""
        from email.header import Header
        msgs = []
        for i, (subject, charset) in enumerate([('Встреча', 'utf-8'), ('Битое', 'bogus'), ('Третье', 'utf-8')]):
            msgs.append(b'From x@y Mon Jan  1 00:00:00 2024\n' +
                        f'Subject: {Header(subject, "utf-8").encode()}\n'
                        f'Content-Type: text/plain; charset={charset}\n\n'.encode() +
                        f'Письмо {i + 1} про Кукушкина'.encode('utf-8') + b'\n\n')
        p = self.dir / 'box.mbox'
        p.write_bytes(b''.join(msgs))
        result = self.extract(p)
        self.assertEqual(result.metadata['message_count'], 3)
        self.assertIn('Тема: Встреча', result.text)
        self.assertIn('Письмо 3 про Кукушкина', result.text)


class TestArchiveExtractors(ExtractorTestCase):

    def test_extract_zip_with_text(self):
        p = self.dir / 'a.zip'
        with zipfile.ZipFile(p, 'w') as zf:
            zf.writestr('readme.txt', 'Hello from archive')
            zf.writestr('data.json', '{"key": "value"}')
        result = self.extract(p)
        self.assertTrue(result.success)
        self.assertIn('Hello from archive', result.text)
        self.assertIn('key: value', result.text)

    def test_zip_documents_nested_and_no_duplicates(self):
        """Регрессия: документы внутри ZIP не извлекались; текст вложенного архива дублировался."""
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, 'w') as z:
            z.writestr('deep.txt', 'Глубоко вложенная Аврора')
        p = self.dir / 'pack.zip'
        with zipfile.ZipFile(p, 'w') as z:
            z.writestr('docs/report.docx', docx_bytes(DOCX_SPLIT))
            z.writestr('nested.zip', inner.getvalue())
        result = self.extract(p)
        self.assertIn('Гражданин Иванов', result.full_text)
        self.assertEqual(result.full_text.count('Глубоко вложенная Аврора'), 1)

    def test_zip_path_traversal_is_flagged_not_written(self):
        p = self.dir / 'evil.zip'
        with zipfile.ZipFile(p, 'w') as zf:
            zf.writestr('../../etc/passwd_astrex_test', 'root:x:0:0')
            zf.writestr('safe.txt', 'safe content')
            zf.writestr('report..final.txt', 'двойные точки в имени — это не обход пути')
        result = self.extract(p)
        self.assertTrue(result.success)
        self.assertIn('safe content', result.text)
        self.assertIn('двойные точки', result.text)
        self.assertEqual(result.metadata['suspicious_paths'], ['../../etc/passwd_astrex_test'])
        self.assertIn('../../etc/passwd_astrex_test  [подозрительный путь]', result.text)
        self.assertIn('root:x:0:0', result.text)          # содержимое читается из памяти, не на диск
        self.assertFalse(Path('/etc/passwd_astrex_test').exists())

    def test_tar_gz_and_compressed_files(self):
        data = 'Текст в tar: Титан'.encode('utf-8')
        p = self.dir / 'a.tar.gz'
        with tarfile.open(p, 'w:gz') as t:
            info = tarfile.TarInfo('note.txt')
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
        self.assertIn('Титан', self.extract(p).text)
        gz = self.dir / 'notes.txt.gz'
        gz.write_bytes(gzip.compress('Андромеда'.encode('cp1251')))
        self.assertIn('Андромеда', self.extract(gz).text)
        bz = self.dir / 'log.bz2'
        bz.write_bytes(bz2.compress('Плутон'.encode('utf-8')))
        self.assertIn('Плутон', self.extract(bz).text)
        docgz = self.dir / 'report.docx.gz'
        docgz.write_bytes(gzip.compress(docx_bytes(DOCX_SPLIT)))
        self.assertIn('Гражданин Иванов', self.extract(docgz).text)

    def test_7z(self):
        try:
            import py7zr
        except ImportError:
            self.skipTest('py7zr not installed')
        p = self.dir / 'a.7z'
        with py7zr.SevenZipFile(p, 'w') as z:
            z.writestr('Кассиопея'.encode('utf-8'), 'note.txt')
            z.writestr(docx_bytes(DOCX_SPLIT), 'r.docx')
        text = self.extract(p).text
        self.assertIn('Кассиопея', text)
        self.assertIn('Гражданин Иванов', text)

    def test_gzip_bomb_is_limited(self):
        from core.config import ENGINE_CONFIG
        old = ENGINE_CONFIG.archive_max_size
        ENGINE_CONFIG.archive_max_size = 1024 * 1024
        try:
            p = self.dir / 'bomb.txt.gz'
            p.write_bytes(gzip.compress(b'A' * (20 * 1024 * 1024)))
            result = self.extract(p)
            self.assertTrue(result.metadata.get('truncated'))
            self.assertLessEqual(len(result.text), ENGINE_CONFIG.max_extracted_chars)
        finally:
            ENGINE_CONFIG.archive_max_size = old


class TestDatabaseExtractors(ExtractorTestCase):

    def test_sqlite_hash_in_name_and_odd_identifiers(self):
        """Регрессия: '#' в имени отключал mode=ro и создавал файл рядом; странные имена таблиц."""
        db = self.dir / 'case#1.db'
        con = sqlite3.connect(db)
        con.execute('CREATE TABLE "my table" ("e-mail" TEXT, phone INTEGER, note BLOB)')
        for i in range(300):
            con.execute('INSERT INTO "my table" VALUES (?, ?, ?)',
                        (f'user{i}@mail.ru', 79990000000 + i, f'Заметка {i}'.encode('utf-8')))
        con.commit()
        con.close()
        before = sorted(os.listdir(self.dir))
        text = self.extract(db).text
        self.assertEqual(sorted(os.listdir(self.dir)), before)       # ничего не создано
        self.assertIn('user299@mail.ru', text)                       # все строки, не первые 100
        self.assertIn('phone: 79990000299', text)                    # числа тоже
        self.assertIn('note: Заметка 5', text)                       # BLOB-текст, а не b'...'

    def test_sql_dump(self):
        p = self.dir / 'dump.sql'
        p.write_text("CREATE TABLE people (id int, name text);\n"
                     "INSERT INTO people VALUES\n(1, 'O''Brien'),\n(2, 'Агент \\'Смит\\'');\n"
                     "COPY public.cities (id, name) FROM stdin;\n1\tНовосибирск\n\\.\n", encoding='utf-8')
        text = self.extract(p).text
        self.assertIn("O'Brien", text)
        self.assertIn("Агент 'Смит'", text)
        self.assertIn('Новосибирск', text)

    def test_mysql_utf8_strings(self):
        p = self.dir / 't.MYD'
        p.write_bytes(b'\x00\x01' + 'Иванов Иван'.encode('utf-8') + b'\x00\xff\x00' + 'Петров'.encode('cp1251') + b'\x00')
        text = self.extract(p).text
        self.assertIn('Иванов Иван', text)
        self.assertIn('Петров', text)


class TestImagesAndRegistry(ExtractorTestCase):

    def test_screenshot_extractor_only_for_images(self):
        """Регрессия: capture_log.txt / screen.json уходили в OCR и не извлекались."""
        from extractors.images import ScreenshotExtractor
        self.assertFalse(ScreenshotExtractor.can_handle(Path('capture_log.txt')))
        self.assertFalse(ScreenshotExtractor.can_handle(Path('screen_settings.json')))
        self.assertTrue(ScreenshotExtractor.can_handle(Path('Screenshot_2024.png')))
        p = self.dir / 'capture_log.txt'
        p.write_text('позывной Беркут', encoding='utf-8')
        self.assertIn('Беркут', self.extract(p).text)

    def test_registry_extract(self):
        p = self.dir / 'r.txt'
        p.write_text('Registry test content', encoding='utf-8')
        result = self.extract(p)
        self.assertTrue(result.success)
        self.assertIn('Registry test', result.text)

    def test_registry_no_extractor(self):
        result = self.extract(self.dir / 'fake.xyz_unknown_ext')
        self.assertFalse(result.success)
        self.assertIn('No extractor', result.error)

    def test_list_extractors(self):
        from extractors import registry
        extractors = registry.list_extractors()
        self.assertGreater(len(extractors), 0)
        for ext in extractors:
            self.assertIn('name', ext)
            self.assertIn('extensions', ext)

    def test_full_text_includes_nested_attachments(self):
        from extractors.base import ExtractionResult
        inner = ExtractionResult(text='вложенный', metadata={'filename': 'b.txt'})
        middle = ExtractionResult(text=None, metadata={'filename': 'a.zip'}, attachments=[inner])
        top = ExtractionResult(text='письмо', attachments=[middle])
        self.assertIn('вложенный', top.full_text)


if __name__ == '__main__':
    unittest.main()
