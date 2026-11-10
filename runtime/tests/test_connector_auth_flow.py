import asyncio
import copy
from urllib.parse import parse_qs, urlencode, urlparse
import unittest

import httpx
from fixtures.connector_auth_server import AuthServer
from loops_runtime.connectors import Connectors, CredentialRequired
import tempfile
from pathlib import Path
from test_connector_onboarding import Vault


class OnDemandAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.vault = Vault()
        self.broker = Connectors(self.root, lambda event: None, vault=self.vault)
        self.fixture = None

    async def asyncTearDown(self):
        await self.broker.close()
        self.temp.cleanup()
        if self.fixture: await asyncio.to_thread(self.fixture.close)

    def create(self, mode, auth='oauth'):
        self.fixture = AuthServer(mode)
        self.manifest = {'id': 'service', 'name': 'Local service', 'type': 'mcp', 'url': self.fixture.url + '/mcp', 'auth': {'mode': auth}}
        self.broker.save(self.manifest)

    async def connect(self):
        await self.broker.connect('service'); await self.broker.jobs['service']
        return self.broker.entry('service')

    async def test_bearer_only_service_requests_one_key_and_reuses_it_after_restart(self):
        self.create('token')
        entry = await self.connect()
        self.assertEqual(entry['status'], 'authorization_required', entry)
        self.assertEqual(entry['credentialRequest']['kind'], 'token')
        revision = entry['revision']; manifest = copy.deepcopy(entry['manifest'])
        with self.assertRaises(ValueError):
            await self.broker.provide_credentials('service', 'token', {'client_secret': 'not-requested'})
        await self.broker.provide_credentials('service', 'token', {'token': self.fixture.token})
        await self.broker.jobs['service']
        self.assertEqual(entry['status'], 'connected', entry)
        self.assertEqual(entry['authorizationInfo']['mode'], 'token')
        self.assertIsNone(entry['credentialRequest'])
        self.assertEqual(entry['revision'], revision); self.assertEqual(entry['manifest'], manifest)
        self.assertNotIn(self.fixture.token, str(self.broker.public('service')))
        self.assertNotIn(self.fixture.token, self.broker.file.read_text())
        registers = self.fixture.requests.count(('POST', '/register'))
        await self.broker.close()
        self.broker = Connectors(self.root, lambda e: None, vault=self.vault)
        await self.broker.check('service')
        self.assertEqual(self.fixture.requests.count(('POST', '/register')), registers)
        self.fixture.token = 'fixture-replaced-token'
        with self.assertRaises(CredentialRequired): await self.broker.check('service')
        self.assertEqual(self.broker.entry('service')['credentialRequest']['kind'], 'token')

    async def test_open_mcp_connects_without_asking_for_any_credentials(self):
        self.create('open')
        entry = await self.connect()
        self.assertEqual(entry['status'], 'connected', entry)
        self.assertEqual(entry['authorizationInfo']['mode'], 'none')
        self.assertIsNone(entry.get('credentialRequest'))
        self.assertNotIn(('POST', '/register'), self.fixture.requests)

    async def test_pre_registered_oauth_requests_only_client_id_then_resumes_pkce(self):
        self.create('registered')
        entry = await self.connect()
        self.assertEqual(entry['credentialRequest']['kind'], 'oauth_client', entry)
        self.assertFalse(entry['credentialRequest']['secretSupported'])
        with self.assertRaises(ValueError):
            await self.broker.provide_credentials('service', 'oauth_client', {'client_id': 'fixture-client', 'client_secret': 'not-needed'})
        await self.broker.provide_credentials('service', 'oauth_client', {'client_id': 'fixture-client'})
        async def authorization_url():
            while not (self.broker.auth_views.get('service') or {}).get('url'):
                if self.broker.jobs['service'].done(): self.fail(str(self.broker.entry('service')))
                await asyncio.sleep(.01)
            return self.broker.auth_views['service']['url']
        url = await asyncio.wait_for(authorization_url(), 3)
        query = parse_qs(urlparse(url).query)
        self.assertEqual(query['client_id'], ['fixture-client'])
        callback = query['redirect_uri'][0] + '?' + urlencode({'code': 'fixture-code', 'state': query['state'][0]})
        async with httpx.AsyncClient(trust_env=False) as client:
            response = await client.get(callback)
            self.assertEqual(response.status_code, 200)
        await self.broker.jobs['service']
        self.assertEqual(entry['status'], 'connected', entry)
        self.assertTrue(entry['authorizationInfo']['renewable'])
        self.assertEqual(entry['revision'], 1)

    async def test_auth_service_outage_does_not_request_manual_credentials(self):
        self.create('outage')
        entry = await self.connect()
        self.assertEqual(entry['status'], 'error', entry)
        self.assertIsNone(entry.get('credentialRequest'))

    async def test_authorization_endpoint_query_and_requested_scope_are_preserved(self):
        self.create('registered')
        self.fixture.authorization_path = '/authorize?authType=2&route=hello%3Fworld'
        self.fixture.scopes = ['docs:read', 'docs:write', 'docs:manage']
        self.manifest['auth']['scope'] = 'docs:read docs:write'
        self.broker.save(self.manifest)
        self.vault.set('service', {'config': {'client_id': 'fixture-client'}})
        await self.broker.connect('service')
        async def authorization_url():
            while not (self.broker.auth_views.get('service') or {}).get('url'):
                if self.broker.jobs['service'].done(): self.fail(str(self.broker.entry('service')))
                await asyncio.sleep(.01)
            return self.broker.auth_views['service']['url']
        query = parse_qs(urlparse(await asyncio.wait_for(authorization_url(), 3)).query)
        self.assertEqual(query['authType'], ['2'])
        self.assertEqual(query['route'], ['hello?world'])
        self.assertEqual(query['response_type'], ['code'])
        self.assertEqual(query['scope'], ['docs:read docs:write'])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertTrue(query['state'][0]); self.assertTrue(query['code_challenge'][0])
        async with httpx.AsyncClient(trust_env=False) as client:
            await client.get(query['redirect_uri'][0] + '?' + urlencode({'code': 'fixture-code', 'state': query['state'][0]}))
        await self.broker.jobs['service']
        self.assertEqual(self.broker.entry('service')['status'], 'connected')

    async def test_confidential_client_secret_is_only_requested_when_required(self):
        self.create('registered'); self.fixture.auth_methods = ['client_secret_post']
        entry = await self.connect()
        self.assertTrue(entry['credentialRequest']['secretSupported'])
        self.assertTrue(entry['credentialRequest']['secretRequired'])
        with self.assertRaises(ValueError):
            await self.broker.provide_credentials('service', 'oauth_client', {'client_id': 'fixture-client'})
        await self.broker.provide_credentials('service', 'oauth_client', {'client_id': 'fixture-client', 'client_secret': 'incorrect-test-secret'})
        async def authorize():
            while not (self.broker.auth_views.get('service') or {}).get('url'):
                if self.broker.jobs['service'].done(): self.fail(str(entry))
                await asyncio.sleep(.01)
            query = parse_qs(urlparse(self.broker.auth_views['service']['url']).query)
            callback = query['redirect_uri'][0] + '?' + urlencode({'code': 'fixture-code', 'state': query['state'][0]})
            async with httpx.AsyncClient(trust_env=False) as client: await client.get(callback)
        await asyncio.wait_for(authorize(), 3)
        await self.broker.jobs['service']
        self.assertEqual(entry['status'], 'authorization_required', entry)
        self.assertEqual(entry['credentialRequest']['kind'], 'oauth_client')
        await self.broker.provide_credentials('service', 'oauth_client', {'client_id': 'fixture-client', 'client_secret': 'fixture-client-secret'})
        await asyncio.wait_for(authorize(), 3)
        await self.broker.jobs['service']
        self.assertEqual(entry['status'], 'connected', entry)
        self.assertTrue(entry['authorizationInfo']['renewable'])
        self.assertEqual(entry['revision'], 1)
        self.assertNotIn('fixture-client-secret', str(self.broker.public('service')))
