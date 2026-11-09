"""Hostile sharing peers must not control local identity or filesystem paths."""
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from loops_runtime.sharing import Sharing
from loops_runtime.sharing_client import main, read_peer
from loops_runtime.sharing_network import pairing_code, pairing_result, request, save_peer
from loops_runtime.store import Store


class SharingSecurityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'client')
        self.addCleanup(self.store.db.close)
        self.sharing = Sharing(self.store, Mock(), Mock())
        self.peer = {'id': 'peer-test', 'hostId': 'host-test', 'name': 'Test',
                     'address': 'https://peer.invalid:443', 'fingerprint': 'a' * 64,
                     'token': 't' * 43, 'createdAt': '2026-01-01', 'status': 'pending'}
        self.identity = {'id': 'host-test', 'name': 'Test', 'fingerprint': self.peer['fingerprint']}

    def response(self, peer, **extra):
        return {'status': 'pending', 'code': pairing_code(peer['fingerprint'], peer['token']), **extra}

    def transport(self, address, method, params=None, **kwargs):
        if method == 'identity':
            return self.identity
        self.assertEqual(method, 'pair')
        return self.response({'fingerprint': kwargs['fingerprint'], 'token': params['token']},
                             id='../../escaped', token='attacker', fingerprint='b' * 64,
                             address='https://attacker.invalid', hostId='host-attacker')

    async def test_connect_and_reapply_ignore_remote_identity_fields(self):
        with patch('loops_runtime.sharing.request', side_effect=self.transport):
            state = await self.sharing.handle('sharing_connect', {'address': self.peer['address']})
            peer = self.sharing.peer(state['connectedPeerId'])
            original = dict(peer)
            await self.sharing.handle('sharing_reapply', {'peerId': peer['id']})
        self.assertEqual(self.sharing.peer(peer['id']), original)
        self.assertTrue(peer['id'].startswith('peer-'))
        self.assertNotEqual(peer['token'], 'attacker')
        for key in ('fingerprint', 'address', 'hostId'):
            self.assertEqual(peer[key], self.peer[key])
        self.assertEqual(peer['code'], pairing_code(peer['fingerprint'], peer['token']))
        self.assertFalse((self.store.root / 'escaped.json').exists())

    async def test_relayed_code_is_rejected_before_connection_is_saved(self):
        def relay(address, method, params=None, **kwargs):
            if method == 'identity':
                return self.identity  # TLS connection is pinned to the relay's certificate.
            return {'status': 'pending', 'code': pairing_code('b' * 64, params['token'])}
        with patch('loops_runtime.sharing.request', side_effect=relay):
            with self.assertRaisesRegex(ValueError, '配对身份'):
                await self.sharing.handle('sharing_connect', {'address': self.peer['address']})
        self.assertEqual(self.sharing.peers(), [])

    async def test_bad_reapply_keeps_existing_credentials(self):
        self.sharing.save_peer(self.peer)
        with patch.object(self.sharing, 'remote', AsyncMock(return_value={'status': 'approved', 'code': 'DEAD-BEEF'})):
            with self.assertRaisesRegex(ValueError, '配对身份'):
                await self.sharing.handle('sharing_reapply', {'peerId': self.peer['id']})
        self.assertEqual(self.sharing.peer(self.peer['id']), self.peer)

    def cli(self):
        return patch('sys.argv', ['sharing_client', '--data-dir', str(self.store.root),
                                 'connect', self.peer['address'], '--name', 'test-client'])

    def test_cli_keeps_local_identity_and_reuses_connection(self):
        with self.cli(), patch('sys.stdout', new_callable=io.StringIO), patch('loops_runtime.sharing_client.request', side_effect=self.transport):
            main()
            first = self.sharing.peers()[0]
            main()
        peer = read_peer(self.store.root, first['id'])
        self.assertEqual(len(self.sharing.peers()), 1)
        self.assertEqual(peer['fingerprint'], self.peer['fingerprint'])
        self.assertEqual(peer['address'], self.peer['address'])
        self.assertNotEqual(peer['token'], 'attacker')
        self.assertEqual(peer['code'], pairing_code(peer['fingerprint'], peer['token']))
        self.assertEqual((self.sharing.directory / 'peers' / f"{peer['id']}.json").stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.store.root / 'escaped.json').exists())

    def test_cli_rejects_relayed_pairing_code(self):
        def relay(address, method, params=None, **kwargs):
            return self.identity if method == 'identity' else {'status': 'pending', 'code': pairing_code('b' * 64, params['token'])}
        with self.cli(), patch('loops_runtime.sharing_client.request', side_effect=relay):
            with self.assertRaisesRegex(ValueError, '配对身份'):
                main()
        self.assertEqual(self.sharing.peers(), [])

    def test_pairing_response_validates_contract_and_allows_normal_states(self):
        for status in ('pending', 'approved', 'rejected', 'revoked', 'expired'):
            result = pairing_result(self.peer, self.response(self.peer, status=status))
            self.assertEqual(result['status'], status)
            self.assertEqual(result['code'], pairing_code(self.peer['fingerprint'], self.peer['token']))
        for response in (None, [], {}, {'status': []}, self.response(self.peer, status='unknown'),
                         self.response(self.peer, code='错误'), self.response(self.peer, code=None)):
            with self.subTest(response=response), self.assertRaises(ValueError):
                pairing_result(self.peer, response)

    def test_peer_writer_rejects_escaping_ids_and_symlinks(self):
        folder = self.sharing.directory / 'peers'
        marker = self.root / 'marker.json'
        marker.write_text('unchanged')
        for peer_id in ('../../marker', str(marker.with_suffix('')), 'peer-../marker', 'peer-', None):
            with self.subTest(peer_id=peer_id), self.assertRaises(ValueError):
                save_peer(folder, {**self.peer, 'id': peer_id})
        folder.mkdir(exist_ok=True)
        (folder / 'peer-test.json').symlink_to(marker)
        with self.assertRaises(ValueError):
            self.sharing.save_peer(self.peer)
        self.assertEqual(marker.read_text(), 'unchanged')

    def test_peer_reader_rejects_mismatched_stored_identity(self):
        self.sharing.save_peer(self.peer)
        path = self.sharing.directory / 'peers' / 'peer-test.json'
        path.write_text(json.dumps({**self.peer, 'id': '../../marker'}))
        for read in (lambda: self.sharing.peer('peer-test'), lambda: read_peer(self.store.root, 'peer-test')):
            with self.assertRaises(ValueError):
                read()

    async def test_download_rejects_bad_requested_and_remote_indices(self):
        self.sharing.save_peer(self.peer)
        invalid = ('../../marker', str(self.root), '0', -1, True, 0.0, None, 2**31)
        for index in invalid:
            with self.subTest(index=index):
                remote = AsyncMock(return_value={'id': 'shared-test', 'files': [{'index': index, 'name': 'marker'}]})
                with patch.object(self.sharing, 'remote', remote):
                    with self.assertRaisesRegex(ValueError, '索引'):
                        await self.sharing.handle('sharing_download', {'peerId': self.peer['id'], 'taskId': 'shared-test', 'index': index})
                    remote.assert_not_called()
                    with self.assertRaisesRegex(ValueError, '索引'):
                        await self.sharing.handle('sharing_download', {'peerId': self.peer['id'], 'taskId': 'shared-test', 'index': 0})
                    self.assertEqual(remote.await_count, 1)  # No download request or write.
        self.assertFalse((self.sharing.directory / 'downloads').exists())

    async def test_download_rejects_directory_and_file_symlink_escapes(self):
        self.sharing.save_peer(self.peer)
        root = self.sharing.directory / 'downloads'
        root.mkdir()
        outside = self.root / 'outside'
        outside.mkdir()
        peer_dir = root / self.peer['id']
        peer_dir.symlink_to(outside, target_is_directory=True)
        job = {'id': 'shared-test', 'files': [{'index': 0, 'name': 'marker'}]}
        params = {'peerId': self.peer['id'], 'taskId': job['id'], 'index': 0}
        remote = AsyncMock(return_value=job)
        with patch.object(self.sharing, 'remote', remote):
            with self.assertRaisesRegex(ValueError, '路径'):
                await self.sharing.handle('sharing_download', params)
            peer_dir.unlink()
            directory = peer_dir / hashlib.sha256(job['id'].encode()).hexdigest()[:20] / '0'
            directory.mkdir(parents=True)
            marker = outside / 'marker'
            marker.write_text('unchanged')
            (directory / 'marker').symlink_to(marker)
            with self.assertRaisesRegex(ValueError, '路径'):
                await self.sharing.handle('sharing_download', params)
        self.assertEqual(remote.await_count, 2)
        self.assertEqual(marker.read_text(), 'unchanged')

    def test_transport_rejects_invalid_download_index_before_connecting(self):
        with patch('loops_runtime.sharing_network.http.client.HTTPSConnection') as connection:
            with self.assertRaises(ValueError):
                request(self.peer['address'], 'file', {'taskId': 'shared-test', 'index': '../marker'}, download=self.root / 'marker')
            connection.assert_not_called()


if __name__ == '__main__':
    unittest.main()
