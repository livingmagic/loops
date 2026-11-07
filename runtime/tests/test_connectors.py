import copy
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager

from mcp.types import CallToolResult, ListToolsResult, Tool, ToolAnnotations
from loops_runtime.connectors import Connectors, ConnectorFailure


class MemoryVault:
    def get(self, key):
        return {}

    def redact(self, value):
        return str(value)


class MetadataConnectors(Connectors):
    """Inject metadata only; these tests never call a remote service."""
    def __init__(self, root, tools):
        super().__init__(root, lambda event: None, vault=MemoryVault())
        self.metadata = tools
        self.calls = []

    @asynccontextmanager
    async def session(self, key, interactive=False):
        owner = self
        class Session:
            async def list_tools(self, cursor=None):
                return ListToolsResult(tools=owner.metadata)
            async def call_tool(self, operation, arguments):
                owner.calls.append((operation, arguments))
                return CallToolResult(content=[], structuredContent={'items': [42]})
        yield Session()


class ConnectorTests(unittest.IsolatedAsyncioTestCase):
    async def test_incomplete_mcp_hints_require_versioned_review_before_reading(self):
        for hints in ({'destructiveHint': True}, {'destructiveHint': False}, {}, None):
            with self.subTest(hints=hints), tempfile.TemporaryDirectory() as directory:
                metadata = [Tool(name='catalog', description='Returns the visible catalog without changing it.',
                                 inputSchema={'type': 'object'},
                                 annotations=ToolAnnotations(**hints) if hints is not None else None)]
                broker = MetadataConnectors(directory, metadata)
                broker.save({'id': 'remote', 'name': 'Remote', 'type': 'mcp',
                             'url': 'https://example.invalid/mcp', 'auth': {'mode': 'none'}})
                await broker.check('remote')
                params = {'connectorId': 'remote', 'operation': 'catalog'}
                detail = await broker.tool_operation('remote', 'connector_describe', params)
                self.assertTrue(detail['riskReviewable'])
                with self.assertRaisesRegex(ConnectorFailure, '只读'):
                    await broker.preview('remote', 'catalog', {})
                with self.assertRaises(ConnectorFailure):
                    broker.frozen_mcp(detail)
                review = {**params, 'risk': 'read', 'sourceHash': detail['sourceHash'],
                          'evidence': 'The full description promises a catalog read; the schema has no mutation input.'}
                for source_hash in (None, 'stale'):
                    with self.assertRaisesRegex(ValueError, '重新检查'):
                        await broker.tool_operation('remote', 'connector_review_operation', {**review, 'sourceHash': source_hash})
                with self.assertRaises(ValueError):
                    await broker.tool_operation('remote', 'connector_review_operation', {**review, 'evidence': ''})
                assessed = await broker.tool_operation('remote', 'connector_review_operation', review)
                self.assertEqual(broker.calls, [])
                self.assertEqual(assessed['risk'], 'read')
                self.assertFalse(assessed['riskReviewable'])
                self.assertEqual(assessed.get('annotations'), detail.get('annotations'))
                frozen = broker.frozen_mcp(assessed)
                self.assertEqual((await broker.preview('remote', 'catalog', {}))['response']['payload'], {'items': [42]})
                # A new broker must reuse the review, but a metadata change must invalidate it.
                broker = MetadataConnectors(directory, metadata)
                self.assertEqual((await broker.preview('remote', 'catalog', {}))['response']['payload'], {'items': [42]})
                metadata[0].description = 'Removes the catalog.'
                changed = await broker.tool_operation('remote', 'connector_describe', params)
                self.assertTrue(changed['riskReviewable'])
                self.assertNotIn('riskAssessment', changed)
                with self.assertRaisesRegex(ConnectorFailure, '只读'):
                    await broker.preview('remote', 'catalog', {})
                with self.assertRaisesRegex(ConnectorFailure, '读写评估已改变'):
                    await broker.call('remote', 'catalog', {}, role='output', expected_contract=frozen)
                with self.assertRaisesRegex(ValueError, '重新检查'):
                    await broker.tool_operation('remote', 'connector_review_operation', review)
                metadata[0].annotations = ToolAnnotations(readOnlyHint=False, destructiveHint=True)
                with self.assertRaisesRegex(ValueError, '不能覆盖'):
                    await broker.tool_operation('remote', 'connector_review_operation', review)

    async def test_mcp_review_does_not_make_reviewed_writes_readable_or_destructive_tools_freezable(self):
        for risk in ('write', 'high-risk-write'):
            with self.subTest(risk=risk), tempfile.TemporaryDirectory() as directory:
                broker = MetadataConnectors(directory, [Tool(name='get_records',
                    description='Replaces existing records.', inputSchema={'type': 'object'},
                    annotations=ToolAnnotations(destructiveHint=True))])
                broker.save({'id': 'remote', 'name': 'Remote', 'type': 'mcp',
                             'url': 'https://example.invalid/mcp', 'auth': {'mode': 'none'}})
                await broker.check('remote')
                params = {'connectorId': 'remote', 'operation': 'get_records'}
                detail = await broker.tool_operation('remote', 'connector_describe', params)
                reviewed = await broker.tool_operation('remote', 'connector_review_operation', {**params, 'risk': risk,
                    'sourceHash': detail['sourceHash'], 'evidence': 'The description specifies replacement, despite the query-like operation name.'})
                with self.assertRaisesRegex(ConnectorFailure, '只读'):
                    await broker.preview('remote', 'get_records', {})
                if risk == 'high-risk-write':
                    with self.assertRaises(ConnectorFailure):
                        broker.frozen_mcp(reviewed)
                self.assertEqual(broker.calls, [])

    async def test_manual_cli_metadata_and_calls_share_identity_and_read_write_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            broker = Connectors(directory, lambda event: None, vault=MemoryVault())
            schema = {'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value']}
            broker.save({'id': 'manual', 'name': 'Manual', 'type': 'cli', 'identity': 'user',
                         'command': [sys.executable, '-c', 'import sys; print(sys.argv[1])'],
                         'auth': {'mode': 'none'}, 'operations': [
                             {'name': 'read', 'description': 'Read data', 'effect': 'read',
                              'inputSchema': schema, 'args': ['${arguments}'], 'format': 'json'},
                             {'name': 'write', 'description': 'Write data', 'effect': 'write',
                              'inputSchema': schema, 'args': ['${arguments}'], 'format': 'json'},
                             {'name': 'bot_only', 'effect': 'read', 'identity': 'bot', 'args': ['{}']}]})
            await broker.check('manual')
            try:
                index = await broker.handle('connector_operations', {'connectorId': 'manual', 'limit': 1})
                self.assertEqual(index['total'], 2)
                self.assertEqual(index['operations'][0]['name'], 'read')
                self.assertEqual(index['nextOffset'], 1)
                page = await broker.handle('connector_operations', {'connectorId': 'manual', 'offset': 1, 'limit': 1})
                self.assertEqual(page['operations'][0]['name'], 'write')
                self.assertIsNone(page['nextOffset'])
                filtered = await broker.handle('connector_operations', {'connectorId': 'manual', 'query': 'WRITE DATA'})
                self.assertEqual(filtered['total'], 1)
                detail = await broker.handle('connector_describe', {'connectorId': 'manual', 'operation': 'read'})
                self.assertEqual(detail['inputSchema'], schema)
                self.assertEqual(detail['risk'], 'read')
                self.assertIn('payload', detail['responseContract'])
                self.assertNotIn('args', detail)
                result = await broker.call('manual', 'read', {'value': 42}, role='input')
                self.assertEqual(result['payload'], {'value': 42})
                for name in ('bot_only', 'missing'):
                    with self.assertRaisesRegex(ValueError, '没有这项操作'):
                        await broker.handle('connector_describe', {'connectorId': 'manual', 'operation': name})
                with self.assertRaisesRegex(ConnectorFailure, '只读'):
                    await broker.call('manual', 'write', {'value': 42}, role='input')
                with self.assertRaisesRegex(ValueError, '不能覆盖'):
                    await broker.handle('connector_review_operation', {'connectorId': 'manual', 'operation': 'write',
                                        'risk': 'read', 'evidence': 'Cannot override a declared write operation'})
                with self.assertRaisesRegex(ValueError, 'Skill'):
                    await broker.handle('connector_skill', {'connectorId': 'manual', 'path': 'manual'})
            finally:
                await broker.close()

    async def test_mcp_risk_preserves_destructive_annotations(self):
        cases = [
            ('read', {'readOnlyHint': True}, 'read'),
            ('read_with_ignored_write_hint', {'readOnlyHint': True, 'destructiveHint': True}, 'read'),
            ('write', {'readOnlyHint': False, 'destructiveHint': False}, 'write'),
            ('destructive', {'readOnlyHint': False, 'destructiveHint': True}, 'high-risk-write'),
            ('destructive_only', {'destructiveHint': True}, 'high-risk-write'),
            ('unknown', None, 'unknown'),
        ]
        metadata = [Tool(name=name, inputSchema={'type': 'object'},
                         annotations=ToolAnnotations(**hints) if hints is not None else None)
                    for name, hints, risk in cases]
        with tempfile.TemporaryDirectory() as directory:
            broker = MetadataConnectors(directory, metadata)
            broker.save({'id': 'remote', 'name': 'Remote', 'type': 'mcp',
                         'url': 'https://example.invalid/mcp', 'auth': {'mode': 'none'}})
            tools, _ = await broker.discover('remote')
            for tool, (name, hints, risk) in zip(tools, cases):
                with self.subTest(name=name):
                    self.assertEqual(tool['risk'], risk)
                    if risk in {'read', 'write'}:
                        self.assertEqual(broker.frozen_mcp(tool)['risk'], risk)
                    else:
                        with self.assertRaises(ConnectorFailure):
                            broker.frozen_mcp(tool)
            # An old saved write contract must not bypass the corrected risk.
            old_tool = copy.deepcopy(tools[3])
            old_tool['risk'] = 'write'
            old_contract = broker.frozen_mcp(old_tool)
            broker.entry('remote').update(enabled=True, status='connected')
            with self.assertRaisesRegex(ConnectorFailure, '读写评估已改变'):
                await broker.call('remote', 'destructive', {}, role='output',
                                  expected_revision=1, expected_contract=old_contract)
