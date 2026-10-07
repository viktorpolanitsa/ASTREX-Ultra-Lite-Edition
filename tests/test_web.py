#!/usr/bin/env python3
"""Тесты веб-API: токен, защита от DNS-rebinding, запрещённые пути, сканирование"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tests.helpers  # noqa: F401,E402

TOKEN = 'test-token-' + 'x' * 40

try:
    from fastapi.testclient import TestClient
    import web.api as web_api
    HAVE_FASTAPI = web_api.FASTAPI_AVAILABLE
except ImportError:
    HAVE_FASTAPI = False


@unittest.skipUnless(HAVE_FASTAPI, 'fastapi/httpx not installed')
class TestWebAPI(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls._saved_token = os.environ.get('ASTREX_API_TOKEN')
        os.environ['ASTREX_API_TOKEN'] = TOKEN
        # Host по умолчанию у TestClient — "testserver", его отклонит защита Host
        cls.client = TestClient(web_api.app, base_url='http://localhost')
        cls.auth = {'Authorization': f'Bearer {TOKEN}'}

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        if cls._saved_token is None:
            os.environ.pop('ASTREX_API_TOKEN', None)
        else:
            os.environ['ASTREX_API_TOKEN'] = cls._saved_token

    def test_health_is_public(self):
        r = self.client.get('/api/health')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['status'], 'healthy')

    def test_token_required(self):
        """Регрессия: API без аутентификации позволял читать любые файлы."""
        url = '/api/index/stats'
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.get(url, headers={'Authorization': 'Bearer wrong'}).status_code, 401)
        self.assertEqual(self.client.get(url, headers=self.auth).status_code, 200)
        self.assertEqual(self.client.get(url, headers={'X-API-Key': TOKEN}).status_code, 200)
        self.assertEqual(self.client.get(url, params={'token': TOKEN}).status_code, 200)

    def test_foreign_host_rejected(self):
        """DNS-rebinding: страница с чужого домена не должна достучаться до локального API."""
        self.assertEqual(self.client.get('/api/health', headers={'Host': 'evil.example.com'}).status_code, 400)
        self.assertEqual(self.client.get('/api/health', headers={'Host': 'localhost:8080'}).status_code, 200)
        self.assertEqual(self.client.get('/api/health', headers={'Host': '127.0.0.1:8080'}).status_code, 200)

    def test_websocket_requires_token(self):
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect) as ctx:
            with self.client.websocket_connect('/ws') as ws:
                ws.receive_text()
        self.assertEqual(ctx.exception.code, 1008)
        with self.client.websocket_connect(f'/ws?token={TOKEN}'):
            pass

    def test_blocked_paths(self):
        """Регрессия: '/home/user/.ssh/../.ssh', символьные ссылки и т.п. обходили запрет."""
        from core.config import ASTREX_HOME
        for folder in (str(ASTREX_HOME), str(ASTREX_HOME / 'logs' / '..')):
            with self.subTest(folder=folder):
                r = self.client.post('/api/scan', json={'query': 'x', 'folder': folder}, headers=self.auth)
                self.assertEqual(r.status_code, 403)
        with tempfile.TemporaryDirectory() as d:
            link = Path(d) / 'link'
            link.symlink_to(ASTREX_HOME, target_is_directory=True)
            r = self.client.post('/api/scan', json={'query': 'x', 'folder': str(link)}, headers=self.auth)
            self.assertEqual(r.status_code, 403)

    def test_scan_validation(self):
        r = self.client.post('/api/scan', json={'query': 'x', 'folder': '/nonexistent/astrex_dir'}, headers=self.auth)
        self.assertEqual(r.status_code, 400)
        r = self.client.post('/api/scan', json={'query': '', 'folder': '/tmp'}, headers=self.auth)
        self.assertEqual(r.status_code, 422)
        self.assertEqual(self.client.get('/api/scan/unknown', headers=self.auth).status_code, 404)
        self.assertEqual(self.client.delete('/api/scan/unknown', headers=self.auth).status_code, 404)

    def test_scan_then_search(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / 'a.txt').write_text('Отчёт о поставках по проекту Альбатрос, 15.03.2024', encoding='utf-8')
            (Path(d) / 'b.txt').write_text('Посторонний текст', encoding='utf-8')
            r = self.client.post('/api/scan', headers=self.auth,
                                 json={'query': 'Альбатрос', 'folder': d, 'use_nlp': False, 'workers': 1})
            self.assertEqual(r.status_code, 200, r.text)
            task_id = r.json()['task_id']

            deadline = time.monotonic() + 120
            while True:
                status = self.client.get(f'/api/scan/{task_id}', headers=self.auth).json()
                if status['status'] != 'running' or time.monotonic() > deadline:
                    break
                time.sleep(0.2)
            self.assertEqual(status['status'], 'completed', status)
            self.assertEqual(status['result_count'], 1)
            self.assertNotIn('results', status)          # статус не тянет за собой все результаты

            page = self.client.get(f'/api/scan/{task_id}/results', headers=self.auth).json()
            self.assertEqual(page['results'][0]['filename'], 'a.txt')

            found = self.client.post('/api/search', json={'query': 'альбатрос'}, headers=self.auth).json()
            self.assertIn('a.txt', [x['filename'] for x in found['results']])

    def test_nlp_endpoints(self):
        r = self.client.post('/api/nlp/entities', json={'text': 'Договор от 15.03.2024, тел. +7 (999) 123-45-67'},
                             headers=self.auth)
        self.assertEqual(r.status_code, 200)
        entities = r.json()['entities']
        self.assertIn('15.03.2024', entities['DATES'])
        r = self.client.post('/api/nlp/relevance', json={'text': 'договор поставки', 'query': 'договор'},
                             headers=self.auth)
        self.assertGreater(r.json()['score'], 0.5)

    def test_graph_endpoint(self):
        r = self.client.get('/api/graph', headers=self.auth)
        self.assertEqual(r.status_code, 200)
        self.assertIn('nodes', r.json())
        self.assertIn('edges', r.json())


if __name__ == '__main__':
    unittest.main()
