"""Private RPC to the desktop host. API keys never cross this channel."""
import asyncio
from uuid import uuid4


class ModelHost:
    def __init__(self, send):
        self.send, self.pending = send, {}

    async def call(self, method, params=None):
        request_id = uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        self.send({'hostRequest': request_id, 'method': method, 'params': params or {}})
        try:
            return await asyncio.wait_for(future, 185)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            self.send({'hostCancel': request_id})
            raise
        finally:
            self.pending.pop(request_id, None)

    def receive(self, response):
        future = self.pending.get(response['hostResponse'])
        if future and not future.done():
            if 'error' in response:
                future.set_exception(ValueError(response['error']))
            else:
                future.set_result(response.get('result'))
