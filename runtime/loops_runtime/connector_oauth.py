"""MCP SDK OAuth with durable expiry, token rotation and noninteractive refresh."""
import asyncio
import time
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.exceptions import OAuthTokenError, OAuthRegistrationError
from mcp.shared.auth import OAuthClientInformationFull, OAuthClientMetadata, OAuthMetadata, OAuthToken, ProtectedResourceMetadata

from .connectors import AuthorizationRequired, ConnectorFailure, CredentialRequired


class TokenStorage:
    def __init__(self, broker, key):
        self.broker, self.key = broker, key

    async def get_tokens(self):
        data = self.broker.vault.get(self.key).get("tokens")
        return OAuthToken.model_validate(data) if data else None

    async def set_tokens(self, tokens):
        data = self.broker.vault.get(self.key)
        value = tokens.model_dump(mode="json", exclude_none=True)
        # Some providers return a new access token without rotating the refresh token.
        if not value.get("refresh_token") and data.get("tokens", {}).get("refresh_token"):
            value["refresh_token"] = data["tokens"]["refresh_token"]
            tokens.refresh_token = value["refresh_token"]
        data.update(tokens=value, expiresAt=time.time() + tokens.expires_in - 60 if tokens.expires_in is not None else None)
        self.broker.vault.set(self.key, data)

    async def get_client_info(self):
        data = self.broker.vault.get(self.key)
        if data.get("client"):
            return OAuthClientInformationFull.model_validate(data["client"])
        config = data.get("config", {})
        if config.get("client_id"):
            manifest = self.broker.entry(self.key)["manifest"]
            metadata = data.get('metadata', {})
            methods = metadata.get('token_endpoint_auth_methods_supported') or ['client_secret_basic']
            method = 'none'
            if config.get('client_secret'):
                method = next((item for item in ('client_secret_post', 'client_secret_basic') if item in methods), None)
                if not method:
                    raise ConnectorFailure('此服务不支持使用应用密钥进行当前授权。')
            return OAuthClientInformationFull(client_id=config["client_id"], client_secret=config.get("client_secret"),
                redirect_uris=[f"http://127.0.0.1:{manifest['auth'].get('callbackPort', 49217)}/callback"],
                issuer=manifest["auth"].get("issuer") or metadata.get('issuer'), token_endpoint_auth_method=method)
        return None

    async def set_client_info(self, client_info):
        data = self.broker.vault.get(self.key)
        data["client"] = client_info.model_dump(mode="json", exclude_none=True)
        self.broker.vault.set(self.key, data)


class DurableOAuth(OAuthClientProvider):
    async def _perform_authorization_code_grant(self):
        # Some authorization endpoints already have routing parameters. The
        # pinned SDK appends '?' unconditionally; retain those parameters and
        # let the SDK continue owning PKCE generation and state verification.
        original_redirect = self.context.redirect_handler
        endpoint = str(self.context.oauth_metadata.authorization_endpoint) if self.context.oauth_metadata else ''
        requested_scope = self.context.storage.broker.entry(self.context.storage.key)['manifest']['auth'].get('scope')
        if requested_scope:
            # Resource metadata describes supported scopes, not the user's grant.
            self.context.client_metadata.scope = requested_scope
        async def redirect(url):
            if urlparse(endpoint).query and url.startswith(endpoint + '?'):
                parameters = dict(parse_qsl(urlparse(endpoint).query, keep_blank_values=True))
                parameters.update(parse_qsl(url[len(endpoint) + 1:], keep_blank_values=True))
                url = urlparse(endpoint)._replace(query=urlencode(parameters)).geturl()
            await original_redirect(url)
        self.context.redirect_handler = redirect
        try:
            return await super()._perform_authorization_code_grant()
        finally:
            self.context.redirect_handler = original_redirect

    def client_information_request(self):
        methods = getattr(self.context.oauth_metadata, 'token_endpoint_auth_methods_supported', None) or ['client_secret_basic']
        secret_supported = bool({'client_secret_post', 'client_secret_basic'} & set(methods))
        if 'none' not in methods and not secret_supported:
            return ConnectorFailure('此服务要求当前不支持的应用认证方式，请联系服务管理员。')
        return CredentialRequired('oauth_client', '请提供服务方分配的有效应用信息。',
            secret_required='none' not in methods, secret_supported=secret_supported)

    async def invalid_manual_client(self, response):
        if response.status_code not in {400, 401} or not self.context.storage.broker.vault.get(self.context.storage.key).get('config', {}).get('client_id'):
            return False
        try:
            await response.aread()
            return response.json().get('error') == 'invalid_client'
        except (ValueError, AttributeError):
            return False

    async def _handle_token_response(self, response):
        if await self.invalid_manual_client(response):
            raise self.client_information_request()
        return await super()._handle_token_response(response)

    async def async_auth_flow(self, request):
        # A provider may omit expires_in or revoke an access token early. Try the
        # durable refresh credential once on an actual resource 401 before asking
        # the user to log in again. SDK discovery/PKCE still owns full authorization.
        flow = super().async_auth_flow(request)
        retried = False
        bearer_challenge, registration_status = False, None
        try:
            outgoing = await flow.__anext__()
            while True:
                response = yield outgoing
                if response.status_code == 401 and outgoing.url == request.url:
                    bearer_challenge = response.headers.get('www-authenticate', '').lower().startswith('bearer')
                endpoint = getattr(self.context.oauth_metadata, 'registration_endpoint', None)
                if outgoing.method == 'POST' and (str(outgoing.url) == endpoint or outgoing.url.path.endswith('/register')):
                    registration_status = response.status_code
                if response.status_code == 401 and outgoing.url == request.url and not retried and self.context.can_refresh_token():
                    await flow.aclose()
                    self.context.token_expiry_time = time.time() - 1
                    retried = True
                    flow = super().async_auth_flow(request)
                    outgoing = await flow.__anext__()
                    continue
                try:
                    outgoing = await flow.asend(response)
                except StopAsyncIteration:
                    return
        except OAuthRegistrationError as error:
            if registration_status in {404, 405, 501}:
                metadata = self.context.oauth_metadata
                if metadata is None and bearer_challenge:
                    raise CredentialRequired('token', '此服务要求访问密钥，且没有提供可用的网页授权入口。') from error
                if metadata is not None:
                    raise self.client_information_request() from error
            if registration_status == 429 or registration_status is not None and registration_status >= 500:
                raise ConnectorFailure('授权服务暂时不可用，请稍后重试。') from error
            raise
        finally:
            await flow.aclose()

    async def _initialize(self):
        await super()._initialize()
        data = self.context.storage.broker.vault.get(self.context.storage.key)
        self.context.token_expiry_time = data.get("expiresAt")
        if data.get("metadata"):
            self.context.oauth_metadata = OAuthMetadata.model_validate(data["metadata"])
        if data.get("resourceMetadata"):
            self.context.protected_resource_metadata = ProtectedResourceMetadata.model_validate(data["resourceMetadata"])
        self.context.auth_server_url = data.get("authServer")

    async def _handle_refresh_response(self, response):
        if await self.invalid_manual_client(response):
            raise self.client_information_request()
        if response.status_code >= 500 or response.status_code == 429:
            raise ConnectorFailure("授权服务暂时不可用，保留长期凭据，请稍后重试。")
        if response.status_code != 200:
            if self.interactive:
                # An explicit reconnect may replace revoked credentials. Background
                # automation must stop instead of initiating a browser login.
                data = self.context.storage.broker.vault.get(self.context.storage.key)
                data.pop("tokens", None)
                data.pop("expiresAt", None)
                self.context.storage.broker.vault.set(self.context.storage.key, data)
                self.context.clear_tokens()
                return False
            raise AuthorizationRequired("长期授权已被撤销或刷新令牌过期，请重新连接。")
        return await super()._handle_refresh_response(response)

    async def persist_metadata(self):
        storage = self.context.storage
        data = storage.broker.vault.get(storage.key)
        if self.context.oauth_metadata:
            data["metadata"] = self.context.oauth_metadata.model_dump(mode="json", exclude_none=True)
        if self.context.protected_resource_metadata:
            data["resourceMetadata"] = self.context.protected_resource_metadata.model_dump(mode="json", exclude_none=True)
        data["authServer"] = self.context.auth_server_url
        if data:
            storage.broker.vault.set(storage.key, data)


async def provider(broker, key, interactive):
    manifest = broker.entry(key)["manifest"]
    port = manifest["auth"].get("callbackPort", 49217)
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    loop = asyncio.get_running_loop()
    callback = loop.create_future()
    server = None

    async def redirect(url):
        nonlocal server
        if not interactive:
            raise AuthorizationRequired("连接器需要用户重新授权；后台任务不会打开登录页面。")
        async def receive(reader, writer):
            try:
                header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
                target = header.split(b" ", 2)[1].decode()
                parsed = urlparse(target)
                query = parse_qs(parsed.query)
                if parsed.path == "/callback" and not callback.done():
                    if "code" in query:
                        callback.set_result((query["code"][0], query.get("state", [None])[0]))
                    elif "error" in query:
                        callback.set_exception(AuthorizationRequired("用户取消了授权。"))
                body = "授权请求已收到，可以返回 Loops 查看连接结果。".encode()
                writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\nConnection: close\r\n\r\n" + body)
                await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()
                await writer.wait_closed()
        server = await asyncio.start_server(receive, "127.0.0.1", port, limit=8192)
        broker.auth_views[key] = {"url": url}
        broker.notify({"event": "connector_state"})
        broker.notify({'event': 'connector_authorization', 'connectorId': key, 'url': url})

    async def wait_callback():
        try:
            return await asyncio.wait_for(callback, 900)
        except asyncio.TimeoutError as error:
            raise AuthorizationRequired('等待网页授权超时，请重新连接后完成登录和授权。') from error
        finally:
            if server:
                server.close()
                await server.wait_closed()

    auth = DurableOAuth(server_url=manifest["url"],
        client_metadata=OAuthClientMetadata(client_name="Loops", redirect_uris=[redirect_uri],
            grant_types=["authorization_code", "refresh_token"], response_types=["code"],
            token_endpoint_auth_method="none", scope=manifest["auth"].get("scope")),
        storage=TokenStorage(broker, key), redirect_handler=redirect, callback_handler=wait_callback)
    auth.interactive = interactive
    async def close():
        if server:
            server.close()
            await server.wait_closed()
        await auth.persist_metadata()
    auth.close = close
    return auth
