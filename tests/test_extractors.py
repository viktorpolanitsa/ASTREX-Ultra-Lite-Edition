#!/usr/bin/env python3
"""Тесты экстракторов"""

import sys
import json
import tempfile
import zipfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


class TestPlainTextExtractor(unittest.TestCase):

    def test_extract_utf8(self):
        from extractors.text import PlainTextExtractor
        with tempfile.NamedTemporaryFile(suffix='.txt', mode='w',
                                         encoding='utf-8', delete=False) as f:
            f.write('Привет, мир! Hello, world!')
            path = Path(f.name)
        try:
            result = PlainTextExtractor.extract(path)
            self.assertTrue(result.success)
            self.assertIn('Привет', result.text)
            self.assertIn('Hello', result.text)
        finally:
            path.unlink()

    def test_extract_empty_file(self):
        from extractors.text import PlainTextExtractor
        with tempfile.NamedTemporaryFile(suffix='.txt', delete=False) as f:
            path = Path(f.name)
        try:
            result = PlainTextExtractor.extract(path)
            self.assertTrue(result.success)
            self.assertEqual(result.text, '')
        finally:
            path.unlink()


class TestJSONExtractor(unittest.TestCase):

    def test_extract_json(self):
        from extractors.text import JSONExtractor
        data = {"title": "Test Document Title Long Enough", "body": "Some important content here"}
        with tempfile.NamedTemporaryFile(suffix='.json', mode='w',
                                         encoding='utf-8', delete=False) as f:
            json.dump(data, f)
            path = Path(f.name)
        try:
            result = JSONExtractor.extract(path)
            self.assertTrue(result.success)
            self.assertIn('Test Document Title Long Enough', result.text)
            self.assertIn('important content', result.text)
        finally:
            path.unlink()


class TestXMLExtractor(unittest.TestCase):

    def test_extract_xml(self):
        from extractors.text import XMLExtractor
        xml = '<root><item>Тестовый текст</item><item>Ещё текст</item></root>'
        with tempfile.NamedTemporaryFile(suffix='.xml', mode='w',
                                         encoding='utf-8', delete=False) as f:
            f.write(xml)
            path = Path(f.name)
        try:
            result = XMLExtractor.extract(path)
            self.assertTrue(result.success)
            self.assertIn('Тестовый текст', result.text)
        finally:
            path.unlink()


class TestCSVExtractor(unittest.TestCase):

    def test_extract_csv(self):
        from extractors.text import CSVExtractor
        csv_data = "name,value\nAlice,100\nBob,200\n"
        with tempfile.NamedTemporaryFile(suffix='.csv', mode='w',
                                         encoding='utf-8', delete=False) as f:
            f.write(csv_data)
            path = Path(f.name)
        try:
            result = CSVExtractor.extract(path)
            self.assertTrue(result.success)
            self.assertIn('Alice', result.text)
            self.assertIn('Bob', result.text)
        finally:
            path.unlink()


class TestZIPExtractor(unittest.TestCase):

    def test_extract_zip_with_text(self):
        from extractors.archives import ZIPExtractor
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as f:
            path = Path(f.name)
        try:
            with zipfile.ZipFile(path, 'w') as zf:
                zf.writestr('readme.txt', 'Hello from archive')
                zf.writestr('data.json', '{"key": "value"}')
            result = ZIPExtractor.extract(path)
            self.assertTrue(result.success)
            self.assertIn('Hello from archive', result.text)
        finally:
            path.unlink()

    def test_zip_path_traversal_blocked(self):
        from extractors.archives import ZIPExtractor
        with tempfile.NamedTemporaryFile(suffix='.zip', delete=False) as f:
            path = Path(f.name)
        try:
            with zipfile.ZipFile(path, 'w') as zf:
                zf.writestr('../../etc/passwd', 'root:x:0:0')
                zf.writestr('safe.txt', 'safe content')
            result = ZIPExtractor.extract(path)
            self.assertTrue(result.success)
            self.assertIn('safe content', result.text)
            self.assertNotIn('root:x:0:0', result.text)
        finally:
            path.unlink()


class TestExtractorRegistry(unittest.TestCase):

    def test_registry_extract(self):
        from extractors import registry
        with tempfile.NamedTemporaryFile(suffix='.txt', mode='w',
                                         encoding='utf-8', delete=False) as f:
            f.write('Registry test content')
            path = Path(f.name)
        try:
            result = registry.extract(path)
            self.assertTrue(result.success)
            self.assertIn('Registry test', result.text)
        finally:
            path.unlink()

    def test_registry_no_extractor(self):
        from extractors import registry
        path = Path('/tmp/fake.xyz_unknown_ext')
        result = registry.extract(path)
        self.assertFalse(result.success)
        self.assertIn('No extractor', result.error)

    def test_list_extractors(self):
        from extractors import registry
        extractors = registry.list_extractors()
        self.assertIsInstance(extractors, list)
        self.assertGreater(len(extractors), 0)
        for ext in extractors:
            self.assertIn('name', ext)
            self.assertIn('extensions', ext)


if __name__ == '__main__':
    unittest.main()
