import socket
import struct
import unittest
from unittest.mock import Mock, patch

from loops_runtime.sharing_network import Discovery, local_interfaces


class SharingNetworkTests(unittest.TestCase):
    def test_interface_sources_use_system_type_and_link_state(self):
        interfaces = {
            'lo0': ('127.0.0.1', 0x49, 1, None),
            'en7': ('192.168.10.8', 0x41, 3, 3),
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


if __name__ == '__main__':
    unittest.main()
