import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from loops_runtime.sharing import Sharing
from loops_runtime.sharing_client import discovery_addresses
from loops_runtime.store import Store


INTERFACES = [{'ip': ip, 'kind': kind, 'interface': name, 'source': label, 'active': True}
              for ip, kind, name, label in (
                  ('127.0.0.1', 'local', 'lo0', '本机'),
                  ('192.168.1.2', 'wifi', 'en0', 'Wi-Fi'),
                  ('192.168.2.2', 'ethernet', 'en1', '以太网'),
                  ('10.0.0.2', 'tunnel', 'utun0', '隧道接口'),
                  ('10.1.0.2', 'bridge', 'bridge0', '网桥'),
                  ('10.2.0.2', 'unknown', 'other0', '网络接口'))]
DEFAULTS = {'127.0.0.1', '192.168.1.2', '192.168.2.2'}


class SharingPolicyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.store = Store(self.root)
        self.addCleanup(self.store.db.close)
        self.sharing = Sharing(self.store, Mock(), Mock())
        self.interfaces = patch('loops_runtime.sharing.local_interfaces', return_value=INTERFACES)
        self.interfaces.start()
        self.addCleanup(self.interfaces.stop)

    async def asyncTearDown(self):
        await self.sharing.close()

    async def test_defaults_match_settings_and_actual_listener_selection(self):
        self.assertEqual(self.sharing.allowed_addresses(), DEFAULTS)
        options = self.sharing.state()['addressOptions']
        self.assertEqual({item['ip'] for item in options if item['enabled']}, DEFAULTS)
        with patch.object(self.sharing, 'listen', AsyncMock()) as listen:
            await self.sharing.handle('sharing_settings', {'enabled': True})
            listen.assert_awaited_once_with(DEFAULTS)

    async def test_manual_selection_persists_and_controls_discovery(self):
        overrides = {'192.168.1.2': False, '10.0.0.2': True}
        await self.sharing.handle('sharing_settings', {'addressOverrides': overrides})
        restored = Sharing(self.store, Mock(), Mock())
        selected = (DEFAULTS - {'192.168.1.2'}) | {'10.0.0.2'}
        self.assertEqual(restored.allowed_addresses(), selected)
        self.assertEqual(restored.config['addressOverrides'], overrides)
        with patch('loops_runtime.sharing.discover', AsyncMock(return_value=[])) as scan:
            await restored.handle('sharing_discover', {})
            scan.assert_awaited_once_with(addresses=selected)
        with patch('loops_runtime.sharing_client.local_interfaces', return_value=INTERFACES):
            self.assertEqual(discovery_addresses(self.root), selected)
        with patch.object(restored, 'listen', AsyncMock()) as listen:
            await restored.handle('sharing_settings', {'enabled': True})
            listen.assert_awaited_once_with(selected)
        await restored.close()

    async def test_new_interfaces_use_type_defaults_without_resetting_manual_choices(self):
        await self.sharing.handle('sharing_settings', {'addressOverrides': {'192.168.1.2': False, '10.0.0.2': True}})
        added = [{'ip': '192.168.3.2', 'kind': 'ethernet'}, {'ip': '10.3.0.2', 'kind': 'tunnel'}]
        with patch('loops_runtime.sharing.local_interfaces', return_value=INTERFACES + added):
            self.assertEqual(self.sharing.allowed_addresses(), {'127.0.0.1', '192.168.2.2', '192.168.3.2', '10.0.0.2'})

    async def test_old_config_preserves_explicit_denials_without_enabling_tunnels(self):
        old = {**self.sharing.config, 'disabledAddresses': ['192.168.1.2']}
        old.pop('addressOverrides')
        self.sharing.put('config', 'local', old)
        with patch('loops_runtime.sharing_client.local_interfaces', return_value=INTERFACES):
            self.assertEqual(discovery_addresses(self.root), DEFAULTS - {'192.168.1.2'})
        restored = Sharing(self.store, Mock(), Mock())
        self.assertEqual(restored.config['addressOverrides'], {'192.168.1.2': False})
        self.assertNotIn('disabledAddresses', restored.config)
        self.assertEqual(restored.allowed_addresses(), DEFAULTS - {'192.168.1.2'})

    async def test_only_manually_enabled_tunnel_can_remain_selected(self):
        overrides = {ip: False for ip in DEFAULTS}
        with self.assertRaisesRegex(ValueError, '至少启用一个'):
            await self.sharing.handle('sharing_settings', {'enabled': True, 'addressOverrides': overrides})
        self.assertEqual(self.sharing.config['addressOverrides'], {})
        overrides['10.0.0.2'] = True
        with patch.object(self.sharing, 'listen', AsyncMock()) as listen:
            await self.sharing.handle('sharing_settings', {'enabled': True, 'addressOverrides': overrides})
            listen.assert_awaited_once_with({'10.0.0.2'})

    async def test_cli_without_config_uses_defaults_without_creating_data(self):
        empty = self.root / 'no-client'
        with patch('loops_runtime.sharing_client.local_interfaces', return_value=INTERFACES):
            self.assertEqual(discovery_addresses(empty), DEFAULTS)
        self.assertFalse(empty.exists())


if __name__ == '__main__':
    unittest.main()
