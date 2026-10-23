import asyncio
import os
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import main
from test_api_usage import asgi_request
from web_config import allowed_origins


class ProductionConnectionTests(unittest.TestCase):
    def test_development_defaults_only_allow_local_websites(self):
        with patch.dict(os.environ,{'APP_ENV':'development','CORS_ALLOWED_ORIGINS':''}):
            self.assertIn('http://localhost:5173',allowed_origins())
            self.assertNotIn('*',allowed_origins())

    def test_production_requires_exact_https_origins(self):
        with patch.dict(os.environ,{'APP_ENV':'production','CORS_ALLOWED_ORIGINS':''}):
            with self.assertRaises(ValueError): allowed_origins()
        for value in ('*','https://*.example.com','http://example.com','https://example.com/path','https://user:password@example.com'):
            with self.subTest(value=value),patch.dict(os.environ,{'APP_ENV':'production','CORS_ALLOWED_ORIGINS':value}):
                with self.assertRaises(ValueError): allowed_origins()

    def test_only_configured_origin_gets_browser_access(self):
        app=FastAPI()
        app.add_middleware(CORSMiddleware,allow_origins=['https://courtcast.example'],allow_methods=['GET','POST'],allow_headers=['Authorization','Content-Type'])
        @app.get('/test')
        def endpoint(): return {'ok':True}
        with patch.object(main,'app',app):
            for origin,allowed in [('https://courtcast.example',True),('https://untrusted.example',False)]:
                status,headers,_=asyncio.run(asgi_request('/test',headers=[(b'origin',origin.encode())]))
                self.assertEqual(status,200)
                self.assertEqual(b'access-control-allow-origin' in headers,allowed)
