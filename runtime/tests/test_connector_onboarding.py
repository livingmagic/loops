import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch
import httpx
from loops_runtime.connector_oauth import provider
from loops_runtime.connector_tools import ManagedTools
from loops_runtime.connectors import Connectors, CredentialRequired

class Vault:
    def __init__(self): self.data = {}
    def get(self, key): return copy.deepcopy(self.data.get(key, {}))
    def set(self, key, value): self.data[key] = copy.deepcopy(value)
    def redact(self, value): return str(value).replace('secret-value', '[hidden]')

class OnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def test_parallel_oauth_callbacks_are_distinct_and_released(self):
        with tempfile.TemporaryDirectory() as root:
            b = Connectors(root, lambda e:None, vault=Vault())
            for key in ('one','two'):
                b.save({'id':key,'name':key,'type':'mcp','url':f'https://{key}.invalid/mcp','auth':{'mode':'oauth'}})
            a = await provider(b, 'one', True)
            c = await provider(b, 'two', True)
            uris = [str(x.context.client_metadata.redirect_uris[0]) for x in (a,c)]
            try:
                self.assertNotEqual(*uris)
                from urllib.parse import urlparse
                ports = [urlparse(uri).port for uri in uris]
                for port in ports:
                    with self.assertRaises(OSError):
                        await asyncio.start_server(lambda r,w:None,'127.0.0.1',port)
            finally:
                await a.close(); await c.close()
            for port in ports:
                server = await asyncio.start_server(lambda r,w:None,'127.0.0.1',port)
                server.close(); await server.wait_closed()

    async def test_stored_callback_is_preserved_without_background_listener(self):
        with tempfile.TemporaryDirectory() as root:
            v=Vault();b=Connectors(root,lambda e:None,vault=v)
            b.save({'id':'remote','name':'Remote','type':'mcp','url':'https://example.invalid/mcp','auth':{'mode':'oauth'}})
            v.data['remote']={'client':{'redirect_uris':['http://127.0.0.1:49199/callback']}}
            a=await provider(b,'remote',False)
            try:self.assertEqual(str(a.context.client_metadata.redirect_uris[0]),'http://127.0.0.1:49199/callback')
            finally:await a.close()

    async def test_credential_reason_survives_check_and_connect_without_secret(self):
        with tempfile.TemporaryDirectory() as root:
            b=Connectors(root,lambda e:None,vault=Vault())
            b.save({'id':'remote','name':'Remote','type':'mcp','url':'https://example.invalid/mcp','auth':{'mode':'oauth'}})
            with patch.object(b,'discover',AsyncMock(side_effect=CredentialRequired('oauth_client','应用信息无效 secret-value',secret_required=True))):
                with self.assertRaisesRegex(CredentialRequired,'应用信息无效'):
                    await b.check('remote',interactive=True)
                await b.connect('remote');await b.jobs['remote']
            self.assertEqual(b.public('remote')['message'],'应用信息无效 [hidden]')
            self.assertTrue(b.public('remote')['credentialRequest']['secretRequired'])
            await b.close()

    async def test_download_reports_progress_with_and_without_content_length(self):
        original=httpx.AsyncClient
        for headers in ({'content-length':'12'},{}):
            with self.subTest(headers=headers),tempfile.TemporaryDirectory() as root:
                transport=httpx.MockTransport(lambda req:httpx.Response(200,headers=headers,content=b'hello world!'))
                updates=[]
                with patch('loops_runtime.connector_tools.httpx.AsyncClient',lambda **kwargs:original(**kwargs,transport=transport)):
                    await ManagedTools(root).download('https://example.invalid/tool',Path(root)/'tool',updates.append)
                self.assertTrue(updates)
                self.assertEqual((Path(root)/'tool').read_bytes(),b'hello world!')
                if headers:self.assertEqual(updates[-1],'100%')
