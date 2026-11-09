import asyncio
import json
import socket
import struct
import unittest
from unittest.mock import AsyncMock, Mock, patch

from loops_runtime.sharing_network import DISCOVERY_PORT, GROUP, Discovery, _discover_on_interface, discover, local_interfaces


class SharingNetworkTests(unittest.TestCase):
    def test_interface_sources_use_system_type_and_link_state(self):
        interfaces = {
            'lo0': ('127.0.0.1', 0x49, 1, None),
            'en7': ('192.168.10.8', 0x43, 3, 3),
            'bridge2': ('192.168.20.1', 0x41, 2, 1),
            'utun9': ('10.20.30.1', 0x51, 0, None),
            'en9': ('192.168.30.8', 0x41, 2, 3),
        }
        def ioctl(fd, operation, query):
            name = query[:16].split(b'\0')[0].decode()
            ip, flags, functional, media = interfaces[name]
            result = bytearray(query)
            if operation == 0xc0206921:
                result[20:24] = socket.inet_aton(ip)
            elif operation == 0xc0206923:
                result[20:24] = socket.inet_aton('192.168.10.255')
            elif operation == 0xc0206911:
                struct.pack_into('H', result, 16, flags)
            elif operation == 0xc02069ad:
                struct.pack_into('I', result, 16, functional)
            elif operation == 0xc02c6938:
                if media is None:
                    raise OSError('No media status')
                struct.pack_into('I', result, 24, media)
            else:
                raise AssertionError(operation)
            return bytes(result)
        with patch('sys.platform', 'darwin'), patch('socket.if_nameindex', return_value=list(enumerate(interfaces))), patch('fcntl.ioctl', side_effect=ioctl):
            items = {item['interface']: item for item in local_interfaces()}
        self.assertEqual(items['en7']['source'], 'Wi-Fi')
        self.assertEqual(items['en7']['broadcast'], '192.168.10.255')
        self.assertNotIn('broadcast', items['utun9'])
        self.assertNotIn('broadcast', items['lo0'])
        self.assertEqual(items['en9']['source'], '以太网')
        self.assertEqual(items['bridge2']['source'], '网桥')
        self.assertFalse(items['bridge2']['active'])
        self.assertEqual(items['utun9']['source'], '隧道接口')
        self.assertEqual(items['lo0']['source'], '本机')

    def test_discovery_does_not_advertise_a_disabled_return_address(self):
        allowed = {'127.0.0.1'}
        discovery = Discovery(lambda: {'id': 'host', 'port': 1234}, lambda: allowed)
        discovery.connection_made(Mock())
        caller = ('127.0.0.1', 4321)
        discovery.datagram_received(b'LOOPS_DISCOVER_V1', caller)
        discovery.transport.sendto.assert_called_once()
        discovery.transport.sendto.reset_mock()
        allowed.clear()
        discovery.datagram_received(b'LOOPS_DISCOVER_V1', caller)
        discovery.transport.sendto.assert_not_called()


class DiscoveryInterfaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_listener_joins_each_enabled_active_interface_despite_one_failure(self):
        interfaces = [
            {'ip': '127.0.0.1', 'active': True},
            {'ip': '198.18.0.1', 'active': True},
            {'ip': '192.168.1.8', 'active': True},
            {'ip': '192.168.2.8', 'active': True},
            {'ip': '192.168.3.8', 'active': False},
        ]
        allowed = {item['ip'] for item in interfaces} - {'192.168.2.8'}
        sock = Mock()
        memberships = []
        def setsockopt(level, option, value):
            if option == socket.IP_ADD_MEMBERSHIP:
                memberships.append(socket.inet_ntoa(value[4:]))
                if memberships[-1] == '198.18.0.1':
                    raise OSError('Multicast unsupported')
        sock.setsockopt.side_effect = setsockopt
        loop = Mock(create_datagram_endpoint=AsyncMock())
        discovery = Discovery(lambda: {}, lambda: allowed)
        with patch('sys.platform', 'darwin'), patch('loops_runtime.sharing_network.local_interfaces', return_value=interfaces), patch('socket.socket', return_value=sock), patch('asyncio.get_running_loop', return_value=loop):
            await discovery.start()
        sock.setsockopt.assert_any_call(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.assertEqual(memberships, ['127.0.0.1', '198.18.0.1', '192.168.1.8'])
        loop.create_datagram_endpoint.assert_awaited_once()
        sock.close.assert_not_called()

    async def test_listener_reports_failure_when_no_membership_succeeds(self):
        sock = Mock()
        def reject_membership(level, option, value):
            if level == socket.IPPROTO_IP and option == socket.IP_ADD_MEMBERSHIP:
                raise OSError('Permission denied')
        sock.setsockopt.side_effect = reject_membership
        with patch('socket.socket', return_value=sock), patch('loops_runtime.sharing_network.local_interfaces', return_value=[{'ip': '127.0.0.1', 'active': True}]):
            with self.assertRaises(OSError):
                await Discovery(lambda: {}, lambda: {'127.0.0.1'}).start()
        sock.close.assert_called_once()

    async def test_scan_binds_source_and_interface_and_ignores_malformed_replies(self):
        peer = {'protocol': 'loops-sharing-v1', 'id': 'host', 'port': 4567}
        packets = [b'[]', b'null', b'bad json', json.dumps({**peer, 'port': True}).encode(), json.dumps(peer).encode()]
        loop = Mock(time=lambda: 0, sock_sendto=AsyncMock(),
                    sock_recvfrom=AsyncMock(side_effect=[(data, ('192.168.1.9', 45871)) for data in packets] + [asyncio.TimeoutError(), asyncio.TimeoutError()]))
        sock = Mock()
        factory = Mock()
        factory.__enter__ = Mock(return_value=sock)
        factory.__exit__ = Mock()
        with patch('socket.socket', return_value=factory), patch('asyncio.get_running_loop', return_value=loop):
            found = await _discover_on_interface('192.168.1.8', 1)
        sock.bind.assert_called_once_with(('192.168.1.8', 0))
        sock.setsockopt.assert_any_call(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton('192.168.1.8'))
        sock.setsockopt.assert_any_call(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        self.assertEqual(loop.sock_sendto.await_count, 2)
        loop.sock_sendto.assert_awaited_with(sock, b'LOOPS_DISCOVER_V1', (GROUP, DISCOVERY_PORT))
        self.assertEqual(found, [{**peer, 'address': 'https://192.168.1.9:4567'}])
        factory.__exit__.assert_called_once()

    async def test_broadcast_discovers_peer_when_multicast_send_fails(self):
        peer = {'protocol': 'loops-sharing-v1', 'id': 'remote', 'port': 55897}
        loop = Mock(time=lambda: 0, sock_sendto=AsyncMock(side_effect=[OSError('Multicast unavailable'), None, OSError('Multicast unavailable'), None]),
                    sock_recvfrom=AsyncMock(side_effect=[(json.dumps(peer).encode(), ('192.168.10.9', 45871)), asyncio.TimeoutError(), asyncio.TimeoutError()]))
        sock = Mock()
        factory = Mock(__enter__=Mock(return_value=sock), __exit__=Mock())
        with patch('socket.socket', return_value=factory), patch('asyncio.get_running_loop', return_value=loop):
            found = await _discover_on_interface('192.168.10.8', 1, '192.168.10.255')
        sock.setsockopt.assert_any_call(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.assertEqual([call.args[2] for call in loop.sock_sendto.await_args_list],
                         [(GROUP, DISCOVERY_PORT), ('192.168.10.255', DISCOVERY_PORT)] * 2)
        self.assertEqual(found[0]['address'], 'https://192.168.10.9:55897')

    async def test_lost_first_probe_is_retried_within_the_same_search(self):
        peer = {'protocol': 'loops-sharing-v1', 'id': 'remote', 'port': 1234}
        loop = Mock(time=lambda: 0, sock_sendto=AsyncMock(),
                    sock_recvfrom=AsyncMock(side_effect=[asyncio.TimeoutError(),
                        (json.dumps(peer).encode(), ('192.168.1.9', 45871)), asyncio.TimeoutError()]))
        factory = Mock(__enter__=Mock(return_value=Mock()), __exit__=Mock())
        with patch('socket.socket', return_value=factory), patch('asyncio.get_running_loop', return_value=loop):
            found = await _discover_on_interface('192.168.1.8', 1)
        self.assertEqual(loop.sock_sendto.await_count, 2)
        self.assertEqual(found[0]['id'], 'remote')

    async def test_scan_survives_failed_adapter_deduplicates_and_skips_inactive(self):
        interfaces = [{'ip': ip, 'active': active} for ip, active in [
            ('127.0.0.1', True), ('198.18.0.1', True), ('192.168.1.8', True), ('192.168.2.8', False)]]
        async def scan(ip, timeout, broadcast):
            if ip == '198.18.0.1':
                raise OSError('No route')
            return [{'id': 'host', 'address': f'https://{ip}:4567'}]
        with patch('loops_runtime.sharing_network.local_interfaces', return_value=interfaces), patch('loops_runtime.sharing_network._discover_on_interface', side_effect=scan) as search:
            found = await discover(.1)
        self.assertEqual(search.await_count, 3)
        self.assertEqual(found, [{'id': 'host', 'address': 'https://127.0.0.1:4567'}])

    async def test_all_failed_adapters_report_error_instead_of_empty_results(self):
        with patch('loops_runtime.sharing_network.local_interfaces', return_value=[{'ip': '127.0.0.1', 'active': True}]), patch('loops_runtime.sharing_network._discover_on_interface', side_effect=OSError('Permission denied')):
            with self.assertRaisesRegex(ConnectionError, '本地网络权限'):
                await discover()


if __name__ == '__main__':
    unittest.main()
