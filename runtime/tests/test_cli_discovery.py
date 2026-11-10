import json
import sys
import tempfile
import unittest
from pathlib import Path

from loops_runtime.connectors import Connectors, ConnectorFailure, AuthorizationRequired
from loops_runtime.cli_discovery import CliDiscovery, service_contract
from test_connector_onboarding import Vault as MemoryVault


def install_notebook(directory):
    binary = directory / 'independent-notebook'
    source = (Path(__file__).parent / 'fixtures/notebook_cli.py').read_text()
    binary.write_text('#!' + sys.executable + '\n' + source.split('\n', 1)[1]); binary.chmod(0o700)
    home = directory / 'notebook'
    (home / 'records').mkdir(parents=True)
    (home / 'records/alpha.txt').write_text('Independent local input')
    return binary, home


class CustomCliTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        self.binary, self.home = install_notebook(self.root)
        self.vault = MemoryVault()
        self.b = Connectors(self.root / 'loops', lambda e: None, vault=self.vault)
        self.manifest = {'id': 'team-notes', 'name': 'Notebook', 'type': 'cli', 'command': [str(self.binary)], 'auth': {'mode': 'auto'}}
        self.b.save(self.manifest, {'env': {'NOTEBOOK_CLI_HOME': str(self.home)}})
        await self.b.connect('team-notes'); await self.b.jobs['team-notes']
        self.assertEqual(self.b.entry('team-notes')['status'], 'connected', self.b.entry('team-notes'))

    async def asyncTearDown(self):
        await self.b.close(); self.temp.cleanup()

    def commands(self):
        return [json.loads(line) for line in (self.home / 'commands.jsonl').read_text().splitlines()]

    async def test_direct_program_discovery_and_real_local_io_without_configuration_mapping(self):
        self.assertNotIn('operations', self.b.entry('team-notes')['manifest'])
        self.assertNotIn('provider', self.manifest)
        self.assertEqual(self.b.entry('team-notes')['capabilities']['discovery'], 'json-schema')
        d = self.b.discovery('team-notes')
        read = await d.operation('records.read'); send = await d.operation('notifications.append')
        result = await self.b.call('team-notes', read['name'], {'name': 'alpha'}, role='input', expected_revision=1, expected_contract=read)
        self.assertEqual(result['result']['content'], 'Independent local input')
        before = len(self.commands())
        first = await self.b.call('team-notes', send['name'], {'text': 'Real local delivery'}, role='output', expected_revision=1, expected_contract=send, delivery_key='same-delivery')
        second = await self.b.call('team-notes', send['name'], {'text': 'Real local delivery'}, role='output', expected_revision=1, expected_contract=send, delivery_key='same-delivery')
        self.assertEqual(first['result']['messageId'], second['result']['messageId'])
        self.assertEqual(len(list((self.home / 'outbox').glob('*.json'))), 1)
        self.assertFalse(any(cmd == ['schema'] or '--help' in cmd for cmd in self.commands()[before:]))
        with self.assertRaisesRegex(ConnectorFailure, '只读'):
            await self.b.call('team-notes', send['name'], {'text': 'blocked'}, role='sample', expected_contract=send)

    async def test_expired_access_after_reopen_refreshes_without_login_or_contract_change(self):
        d = self.b.discovery('team-notes'); op = await d.operation('records.read')
        credential_file = self.home / 'credentials.json'
        credentials = json.loads(credential_file.read_text()); credentials['accessUntil'] = 0
        credential_file.write_text(json.dumps(credentials))
        await self.b.close(); before = len(self.commands())
        self.b = Connectors(self.root / 'loops', lambda e: None, vault=self.vault)
        self.b.recover_connections(initial=True)
        result = await self.b.call('team-notes', op['name'], {'name': 'alpha'}, role='input', expected_revision=1, expected_contract=op)
        self.assertTrue(result['result']['ok'])
        commands = self.commands()[before:]
        self.assertEqual(commands.count(['auth', 'refresh']), 1)
        self.assertNotIn(['auth', 'login'], commands)
        self.assertEqual(self.b.entry('team-notes')['revision'], 1)
        self.assertEqual((await self.b.discovery('team-notes').operation(op['name']))['contractHash'], op['contractHash'])
        self.assertGreater(json.loads(credential_file.read_text())['rotation'], credentials['rotation'])

    async def test_upgrade_rejects_before_business_and_expired_refresh_requires_manual_connection(self):
        op = await self.b.discovery('team-notes').operation('records.read')
        self.binary.write_text(self.binary.read_text().replace('notebook-cli 1.0.0', 'notebook-cli 2.0.0'))
        before = len(self.commands())
        with self.assertRaisesRegex(ConnectorFailure, '版本'):
            await self.b.call('team-notes', op['name'], {'name': 'alpha'}, role='input', expected_revision=1, expected_contract=op)
        self.assertEqual(before, len(self.commands()))
        credential_file = self.home / 'credentials.json'
        credential_file.write_text(json.dumps({'accessUntil': 0, 'refreshUntil': 0}))
        with self.assertRaises(AuthorizationRequired):
            await self.b.check('team-notes')
        self.assertNotIn(['auth', 'login'], self.commands()[before:])

    async def test_temporary_refresh_failure_preserves_credentials_and_recovers_without_login(self):
        credential_file = self.home / 'credentials.json'
        value = json.loads(credential_file.read_text()); value['accessUntil'] = 0
        credential_file.write_text(json.dumps(value))
        marker = self.home / 'refresh-unavailable'; marker.touch()
        before = len(self.commands())
        with self.assertRaisesRegex(ConnectorFailure, '续期暂未完成'):
            await self.b.check('team-notes')
        self.assertEqual(self.b.entry('team-notes')['status'], 'error')
        self.assertEqual(json.loads(credential_file.read_text()), value)
        marker.unlink()
        self.b.recover_connections(initial=True); await self.b.jobs['team-notes']
        self.assertEqual(self.b.entry('team-notes')['status'], 'connected')
        self.assertNotIn(['auth', 'login'], self.commands()[before:])

    async def test_command_prefix_file_is_part_of_the_frozen_version(self):
        # A launcher can stay unchanged while its explicitly configured script changes.
        await self.b.disconnect('team-notes')
        manifest = {**self.manifest, 'command': [sys.executable, str(self.binary)]}
        self.b.save(manifest); await self.b.check('team-notes')
        op = await self.b.discovery('team-notes').operation('records.read')
        self.binary.write_text(self.binary.read_text() + '\n# changed entry script\n')
        with self.assertRaisesRegex(ConnectorFailure, '版本'):
            await self.b.call('team-notes', op['name'], {'name': 'alpha'}, role='input', expected_revision=2, expected_contract=op)


class ServiceSchemaTests(unittest.IsolatedAsyncioTestCase):
    async def test_refs_review_and_remote_schema_drift_are_generic(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binary = root / 'unrelated-cli'; binary.write_text('metadata launcher')
            source = {'method': 'archive.items.inspect', 'request': {'$ref': 'Input'}, 'response': {'$ref': 'Output'},
                'schemas': {'Input': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']},
                            'Output': {'type': 'object', 'properties': {'value': {'type': 'string'}}}}}
            method_names = ['archive.items.inspect']
            class Broker:
                def __init__(self): self.root, self.commands = root, []
                def entry(self, key): return {'manifest': {'command': [str(binary)]}}
                def executable(self, command, key): return command
                async def process(self, key, args):
                    self.commands.append(args)
                    outputs = {('--version',): 'unrelated-cli 3', ('--help',): 'Commands:\n  archive  Archives',
                        ('schema', '--help'): 'Commands:\n  list  Catalog\n  get   Method',
                        ('schema', 'list'): json.dumps([{'name': 'archive', 'methods': [{'name': name, 'description': 'Read an archive item'} for name in method_names]}]),
                        ('archive', 'items', 'inspect', '--help'): 'Options:\n  --json <JSON>  Request body',
                        ('archive', 'items', 'inspect', '--doc'): 'Read-only archive item lookup. Does not modify remote state.',
                        ('schema', 'get', 'archive.items.inspect'): json.dumps(source)}
                    return 0, outputs[tuple(args)], ''
            broker = Broker(); d = CliDiscovery(broker, 'arbitrary')
            op = await d.operation('archive.items.inspect')
            self.assertEqual(op['risk'], 'unknown')
            with self.assertRaisesRegex(ValueError, '未知'):
                d.frozen(op['name'])
            op = await d.review(op['name'], 'read', 'The complete method documentation states this reads one archive item and does not modify state.')
            from jsonschema import validate, ValidationError
            validate({'id': 'one'}, op['inputSchema'])
            with self.assertRaises(ValidationError): validate({}, op['inputSchema'])
            await d.verify_frozen(d.frozen(op['name']))
            with self.assertRaisesRegex(ValueError, '覆盖'):
                await d.review(op['name'], 'write', 'Cannot override an existing risk declaration or review.')
            method_names.append('archive.items.new_method')
            refreshed = await d.load(refresh=True)
            self.assertIn('archive.items.new_method', refreshed['operations'])
            same_method = await d.operation(op['name'])
            self.assertEqual(same_method['riskAssessment'], op['riskAssessment'])
            self.assertEqual(same_method['sourceHash'], op['sourceHash'])
            await d.verify_frozen(op)  # A new unrelated method does not invalidate this task.
            source['schemas']['Input']['properties']['id']['type'] = 'integer'
            with self.assertRaisesRegex(ValueError, '服务端'):
                await d.verify_frozen(op)
            updated = await d.operation(op['name'])
            self.assertEqual(updated['risk'], 'unknown')
            self.assertNotEqual(updated['sourceHash'], op['sourceHash'])

    async def test_unknown_schema_reference_is_not_resolved_over_network(self):
        with self.assertRaisesRegex(ValueError, '未知类型'):
            service_contract({'name': 'a.b', 'command': ['a', 'b'], 'description': ''},
                {'request': {'$ref': 'Input'}, 'response': {}, 'schemas': {'Input': {'type': 'object', 'properties': {'value': {'$ref': 'https://invalid.test/schema'}}}}}, '--json <JSON>')

    async def test_flat_schema_external_reference_is_rejected(self):
        from loops_runtime.cli_discovery import json_contract
        with self.assertRaisesRegex(ValueError, '外部'):
            json_contract({'name': 'items.read', 'command': ['items', 'read'], 'description': ''},
                          {'inputSchema': {'type': 'object', 'properties': {'value': {'$ref': 'file:///private/secret'}}}}, '--json <JSON>')

    async def test_help_only_command_groups_and_explicit_read_risk(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); binary = root / 'help-only'; binary.write_text('help-only launcher')
            class Broker:
                def __init__(self): self.root = root
                def entry(self, key): return {'manifest': {'command': [str(binary)]}}
                def executable(self, command, key): return command
                async def process(self, key, args):
                    outputs = {('--version',): 'help-only 1', ('--help',): 'Commands:\n  notes  Local notes',
                        ('notes', '--help'): 'Commands:\n  fetch  Read a local note',
                        ('notes', 'fetch', '--help'): 'Risk: read\nUsage: help-only notes fetch [OPTIONS]\nOptions:\n  --name <str>  (required) Note name'}
                    return (0, outputs[tuple(args)], '') if tuple(args) in outputs else (2, '', 'unsupported')
            d = CliDiscovery(Broker(), 'help-connector')
            group = await d.operation('notes')
            self.assertEqual(group['children'][0]['name'], 'notes.fetch')
            op = await d.operation('notes.fetch')
            self.assertEqual(op['risk'], 'read')
            self.assertEqual(op['inputSchema']['required'], ['name'])
            self.assertEqual(d.frozen(op['name'])['contractHash'], op['contractHash'])


from test_automation import AutomationTests as AutomationFixture


class CustomCliAutomationTests(unittest.IsolatedAsyncioTestCase):
    trial_and_save = AutomationFixture.trial_and_save

    async def asyncSetUp(self):
        from test_automation import AutomationTests
        await AutomationTests.asyncSetUp(self)
        self.binary, self.home = install_notebook(self.root)
        broker = self.service.connectors; broker.vault = MemoryVault()
        broker.save({'id': 'team', 'name': 'Notebook', 'type': 'cli', 'command': [str(self.binary)], 'auth': {'mode': 'auto'}},
                    {'env': {'NOTEBOOK_CLI_HOME': str(self.home)}})
        await broker.connect('team'); await broker.jobs['team']

    async def asyncTearDown(self):
        from test_automation import AutomationTests
        await AutomationTests.asyncTearDown(self)

    async def test_direct_cli_source_two_saved_loops_output_receipt(self):
        self.spec['connectors'] = [{'id': 'team', 'operations': ['records.read', 'notifications.append']}]
        self.spec['source']['script'] = '''from pathlib import Path
def sample(ctx):
    value = ctx.connector('team', 'records.read', {'name': 'alpha'})['result']
    path = Path(ctx.workspace) / 'source.txt'; path.write_text(value['content'])
    ctx.emit('notebook-read', {'source': ctx.file(path)})
def run(ctx): sample(ctx)
'''
        self.spec['outputs'][0]['script'] = '''def run(ctx):
    result = ctx.connector('team', 'notifications.append', {'text': ctx.read_text('report')}, call_key='report')['result']
    assert result['ok']
    ctx.receipt(target='local-notebook-outbox', status='accepted', messageId=result['messageId'])
'''
        task = await self.service.handle('automation_save', {'spec': self.spec})
        contracts = self.a.version(task['id'], task['revision'])['connectorContracts']['team']
        self.assertEqual(set(contracts), {'records.read', 'notifications.append'})
        await self.a.enable(task['id'], True)
        from test_automation import AutomationTests
        run = (await AutomationTests.settle(self, task['id']))[-1]
        self.assertEqual(run['status'], 'success', run)
        delivered = list((self.home / 'outbox').glob('*.json'))
        self.assertEqual(len(delivered), 1)
        self.assertEqual(json.loads(delivered[0].read_text())['text'], 'INDEPENDENT LOCAL INPUT')
        self.assertEqual(run['nodes']['delivery']['receipt']['messageId'], json.loads(delivered[0].read_text())['messageId'])
        self.assertEqual(self.a.version(task['id'], task['revision'])['connectorContracts']['team'], contracts)

# Imported solely as a setup helper, not a second suite to discover.
del AutomationFixture
