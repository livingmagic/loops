"""LAN discovery and pinned TLS transport for shared workflows."""
import asyncio
import errno
import hashlib
import http.client
import json
import os
import socket
import ssl
import struct
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

GROUP, DISCOVERY_PORT = '239.255.76.80', 45871
MAX_BODY = 15 * 1024 * 1024


def local_interfaces():
    """Read address sources from local interfaces, without DNS or network round trips."""
    addresses = {'127.0.0.1': {'ip': '127.0.0.1', 'interface': '', 'source': '本机', 'active': True}}
    # SIOCGIFADDR from Darwin sys/sockio.h and Linux linux/sockios.h.
    operation = {'darwin': 0xc0206921, 'linux': 0x8915}.get(sys.platform)
    if operation is None:
        return list(addresses.values())
    import fcntl
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for _, name in socket.if_nameindex():
                query = struct.pack('256s', name.encode())
                try:
                    result = fcntl.ioctl(sock.fileno(), operation, query)
                    # ifreq starts with a 16-byte name, then sockaddr_in.sin_addr at offset 4.
                    address = socket.inet_ntoa(result[20:24])
                except OSError:  # Interfaces without an IPv4 address are expected.
                    continue
                if address == '0.0.0.0':
                    continue
                def read(operation, offset=16, fmt='I'):
                    try:
                        return struct.unpack_from(fmt, fcntl.ioctl(sock.fileno(), operation, query), offset)[0]
                    except OSError:
                        return None
                flags = read(0xc0206911 if sys.platform == 'darwin' else 0x8913, fmt='H')
                functional = read(0xc02069ad) if sys.platform == 'darwin' else None
                source = {2: '以太网', 3: 'Wi-Fi', 4: '点对点 Wi-Fi', 5: '蜂窝网络'}.get(functional, '网络接口')
                if flags is not None and flags & 0x8:
                    source = '本机'
                elif flags is not None and flags & 0x10:
                    source = '隧道接口'
                elif sys.platform == 'darwin' and name.startswith('bridge'):
                    source = '网桥'
                active = bool(flags & 1) if flags is not None else None
                if sys.platform == 'darwin':
                    # Darwin's packed ifmediareq is 44 bytes; IFM_AVALID / IFM_ACTIVE.
                    media = read(0xc02c6938, offset=24)
                    if media is not None and media & 1:
                        active = bool(media & 2)
                item = {'ip': address, 'interface': name, 'source': source, 'active': active}
                if flags is not None and flags & 0x2:  # IFF_BROADCAST; never probe an IP range.
                    broadcast = read(0xc0206923 if sys.platform == 'darwin' else 0x8919, offset=20, fmt='4s')
                    if broadcast and broadcast != bytes(4):
                        item['broadcast'] = socket.inet_ntoa(broadcast)
                addresses[address] = item
    except OSError:
        pass
    return sorted(addresses.values(), key=lambda item: (item['source'] != '本机', item['active'] is False, item['ip']))


def local_addresses():
    return {item['ip'] for item in local_interfaces()}


def certificate(directory, name):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    cert, key = directory / 'server.pem', directory / 'server.key'
    if not cert.exists() or not key.exists():
        private = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name[:64])])
        current = datetime.now(timezone.utc)
        signed = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
                  .public_key(private.public_key()).serial_number(x509.random_serial_number())
                  .not_valid_before(current - timedelta(minutes=1))
                  .not_valid_after(current + timedelta(days=3650)).sign(private, hashes.SHA256()))
        key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        key.chmod(0o600)
        cert.write_bytes(signed.public_bytes(serialization.Encoding.PEM))
    der = ssl.PEM_cert_to_DER_cert(cert.read_text())
    return cert, key, hashlib.sha256(der).hexdigest()


def pairing_code(fingerprint, token):
    value = hashlib.sha256((fingerprint + token).encode()).hexdigest()[:8].upper()
    return value[:4] + '-' + value[4:]


def request(address, method, params=None, *, token=None, fingerprint=None, download=None):
    url = urlsplit(address)
    if url.scheme != 'https' or not url.hostname or url.username or url.password or url.path not in {'', '/'} or url.query or url.fragment:
        raise ValueError('请输入 https://主机:端口 格式的 Loops 地址。')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE  # Trust comes from the locally approved pairing code and certificate pin.
    connection = http.client.HTTPSConnection(url.hostname, url.port or 443, context=context, timeout=15)
    try:
        connection.connect()
        actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
        if fingerprint and actual != fingerprint:
            raise ValueError('对方身份已变化，请重新申请连接并核对配对码。')
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        endpoint = '/v1/action'
        body = json.dumps({'method': method, 'params': params or {}}).encode()
        if download is not None:
            endpoint = f"/v1/files/{params['taskId']}/{params['index']}"
            body = None
        connection.request('GET' if download is not None else 'POST', endpoint, body, headers)
        response = connection.getresponse()
        if response.status == 200 and download is not None:
            target = Path(download)
            fd, temporary = tempfile.mkstemp(prefix='.loops-download-', dir=target.parent)
            partial = Path(temporary)
            try:
                with os.fdopen(fd, 'wb') as output:
                    while chunk := response.read(65536):
                        output.write(chunk)
                os.replace(partial, target)
            finally:
                partial.unlink(missing_ok=True)
            return {'path': str(target)}
        payload = response.read(MAX_BODY + 1)
        if len(payload) > MAX_BODY:
            raise ValueError('响应过大，请通过文件下载接口取回。')
        value = json.loads(payload)
        if response.status != 200:
            raise ValueError(value.get('error', '共享服务请求失败。'))
        return {**value, 'fingerprint': actual} if method == 'identity' else value
    except TimeoutError:
        raise ConnectionError('连接超时，请确认对方 Loops 已开启共享且网络可达。') from None
    except ssl.SSLError:
        raise ConnectionError('无法建立安全连接，请确认地址属于 Loops 共享服务。') from None
    except (ConnectionError, socket.gaierror):
        raise ConnectionError('无法连接对方 Loops，请确认对方已开启共享，并检查地址和网络。') from None
    except OSError as error:
        if error.errno in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN, errno.EHOSTDOWN}:
            raise ConnectionError('无法访问对方网络，请检查网络连接和地址。') from None
        raise
    finally:
        connection.close()


class Discovery(asyncio.DatagramProtocol):
    def __init__(self, identity, addresses):
        self.identity = identity
        self.addresses = addresses
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, address):
        if data == b'LOOPS_DISCOVER_V1':
            # Only advertise an address that the service actually accepts connections on.
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    probe.connect(address)
                    if probe.getsockname()[0] not in self.addresses():
                        return
            except OSError:
                return
            self.transport.sendto(json.dumps(self.identity()).encode(), address)

    async def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if sys.platform == 'darwin':
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.bind(('', DISCOVERY_PORT))
            joined = False
            allowed = self.addresses()
            for item in local_interfaces():
                if item['active'] is False or item['ip'] not in allowed:
                    continue
                try:
                    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                    socket.inet_aton(GROUP) + socket.inet_aton(item['ip']))
                    joined = True
                except OSError:
                    # A VPN or disappearing adapter must not disable discovery on Wi-Fi.
                    continue
            if not joined:
                raise OSError('没有可用于自动发现的共享地址。')
            sock.setblocking(False)
            await asyncio.get_running_loop().create_datagram_endpoint(lambda: self, sock=sock)
        except BaseException:
            sock.close()
            raise

    def close(self):
        if self.transport:
            self.transport.close()


async def _discover_on_interface(ip, timeout, broadcast=None):
    # Bind both the source address and multicast interface; a wildcard socket
    # otherwise searches only the OS-selected interface (which may be a VPN).
    loop = asyncio.get_running_loop()
    found = {}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setblocking(False)
        sock.bind((ip, 0))
        async def query():
            sent = False
            try:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(ip))
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
                await loop.sock_sendto(sock, b'LOOPS_DISCOVER_V1', (GROUP, DISCOVERY_PORT))
                sent = True
            except OSError:
                pass
            if broadcast:
                # Some Wi-Fi networks drop multicast while allowing subnet broadcasts.
                # Send one small query to the OS-provided broadcast address, not every host.
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    await loop.sock_sendto(sock, b'LOOPS_DISCOVER_V1', (broadcast, DISCOVERY_PORT))
                    sent = True
                except OSError:
                    pass
            if not sent:
                raise OSError('无法发送局域网发现请求。')

        started = loop.time()
        for attempt in range(2):
            await query()
            deadline = started + timeout * (attempt + 1) / 2
            while loop.time() < deadline:
                try:
                    data, address = await asyncio.wait_for(loop.sock_recvfrom(sock, 4096), deadline - loop.time())
                    value = json.loads(data)
                    if (isinstance(value, dict) and value.get('protocol') == 'loops-sharing-v1'
                            and isinstance(value.get('id'), str) and value['id']
                            and type(value.get('port')) is int and 0 < value['port'] < 65536):
                        found[value['id']] = {**value, 'address': f'https://{address[0]}:{value["port"]}'}
                except asyncio.TimeoutError:
                    break
                except ValueError:
                    continue
    return list(found.values())


async def discover(timeout=2):
    interfaces = [item for item in local_interfaces() if item['active'] is not False]
    results = await asyncio.gather(*(_discover_on_interface(item['ip'], timeout, item.get('broadcast')) for item in interfaces),
                                   return_exceptions=True)
    found = {}
    searched = False
    for result in results:
        if isinstance(result, OSError):
            continue
        if isinstance(result, BaseException):
            raise result
        searched = True
        for peer in result:
            # Stable interface order, rather than packet timing, chooses a duplicate's address.
            found.setdefault(peer['id'], peer)
    if not searched:
        raise ConnectionError('无法搜索局域网，请检查网络连接及 Loops 的本地网络权限。')
    return list(found.values())
