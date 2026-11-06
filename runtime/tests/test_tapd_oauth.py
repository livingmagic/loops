import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import httpx
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata, OAuthMetadata
from loops_runtime.connector_oauth import DurableOAuth
from loops_runtime.connector_presets import presets
from loops_runtime.connector_tools import builtin
from loops_runtime.connectors import Connectors

class Vault:
    def get(self, key): return {'credentialMode': 'token', 'config': {'token': 'legacy-secret'}}
    def redact(self, value): return str(value).replace('legacy-secret', '[hidden]')

class TapdOAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_preset_uses_remote_oauth_without_download(self):
        manifest = next(p for p in presets() if p['id'] == 'tapd')
        self.assertIsNone(builtin(manifest))
        with tempfile.TemporaryDirectory() as root:
            b = Connectors(root, lambda e: None, vault=Vault())
            b.save(manifest)
            self.assertEqual(b.mcp_auth_mode('tapd'), 'oauth')
            self.assertIsNone(b.discovery('tapd'))

    async def test_legacy_migration_invalidates_saved_contract_and_waits_for_user(self):
        with tempfile.TemporaryDirectory() as root:
            old = {'id':'tapd','provider':'tapd','name':'TAPD','type':'cli','command':['tapd-cli'],
                   'package':'tapd-cli','auth':{'mode':'token'}}
            Path(root, 'connectors.json').write_text(json.dumps({'tapd':{'manifest':old,'revision':4,
                'status':'connected','enabled':True,'autoReconnect':True,'tools':[{'name':'old'}],
                'capabilities':{'old':True},'credentialRequest':{'kind':'token'}}}))
            b = Connectors(root, lambda e: None, vault=Vault())
            item = b.public('tapd')
            self.assertEqual(item['manifest']['type'], 'mcp')
            self.assertEqual(item['revision'], 5)
            self.assertFalse(item['enabled'])
            self.assertFalse(item['autoReconnect'])
            self.assertIsNone(item['credentialRequest'])
            self.assertEqual(item['tools'], [])
            self.assertNotIn('capabilities', item)
            self.assertEqual(Connectors(root, lambda e:None, vault=Vault()).entry('tapd')['revision'], 5)

    async def test_dynamic_registration_negotiates_advertised_auth_method(self):
        for supported, expected in [(['client_secret_post'],'client_secret_post'),(['none','client_secret_post'],'none'),(['client_secret_basic'],'client_secret_basic')]:
            with self.subTest(supported=supported):
                auth = DurableOAuth(server_url='https://example.com/mcp',client_metadata=OAuthClientMetadata(
                    client_name='Loops',redirect_uris=['http://127.0.0.1:49217/callback'],token_endpoint_auth_method='none'),
                    storage=None,redirect_handler=None,callback_handler=None)
                auth.context.oauth_metadata=OAuthMetadata(issuer='https://example.com',authorization_endpoint='https://example.com/authorize',
                    token_endpoint='https://example.com/token',registration_endpoint='https://example.com/register',
                    response_types_supported=['code'],token_endpoint_auth_methods_supported=supported)
                async def flow(self, request):
                    response = yield httpx.Request('POST','https://example.com/register',json=self.context.client_metadata.model_dump(mode='json'))
                    self.test_status=response.status_code
                with patch.object(OAuthClientProvider,'async_auth_flow',flow):
                    generator=auth.async_auth_flow(httpx.Request('GET','https://example.com/mcp'))
                    request=await anext(generator)
                    self.assertEqual(json.loads(request.content)['token_endpoint_auth_method'],expected)
                    with self.assertRaises(StopAsyncIteration):
                        await generator.asend(httpx.Response(201,json={}))
                    self.assertEqual(auth.test_status,201)
