"""Small stdlib-only SDK for automation scripts. stdout is a framed RPC channel."""
import asyncio
import inspect
import json
import runpy
import sys
import os
import signal
import threading
from pathlib import Path


class Context:
    def __init__(self, data, channel, replies):
        self.__dict__.update(data)
        self._channel, self._replies = channel, replies

    def _call(self, op, **values):
        self._channel.write(json.dumps({"op": op, **values}, ensure_ascii=False) + "\n")
        self._channel.flush()
        line = self._replies.readline()
        if not line:
            raise RuntimeError("Loops 执行服务已断开。")
        response = json.loads(line)
        if "error" in response:
            raise RuntimeError(response["error"])
        return response.get("result")

    @staticmethod
    def file(path):
        return {"kind": "file", "path": str(Path(path).resolve())}

    @staticmethod
    def text(value):
        return {"kind": "text", "value": str(value)}

    def emit(self, event_key, inputs, checkpoint=None):
        return self._call("emit", eventKey=event_key, inputs=inputs, checkpoint=checkpoint)

    def log(self, message):
        return self._call("log", message=str(message))

    def receipt(self, target, status="delivered", **details):
        return self._call("receipt", receipt={"target": target, "status": status, **details})

    def request(self, url, *, connection=None, method="GET", body=None, headers=None):
        """Authentication is applied by the host; credentials never enter this process."""
        return self._call("request", url=url, connection=connection, method=method, body=body, headers=headers or {})

    def read_text(self, name, encoding="utf-8"):
        value = self.inputEvidence[name]
        return Path(self.inputs[name]).read_text(encoding=encoding) if value["kind"] == "file" else self.inputs[name]

    def read_bytes(self, name):
        value = self.inputEvidence[name]
        return Path(self.inputs[name]).read_bytes() if value["kind"] == "file" else self.inputs[name].encode("utf-8")


def main():
    context_file, script, entry = sys.argv[1:]
    channel, replies = sys.stdout, sys.stdin
    parent = os.getppid()
    def supervise_parent():
        while not threading.Event().wait(1):
            if os.getppid() != parent:
                os.killpg(os.getpgrp(), signal.SIGTERM)
                return
    threading.Thread(target=supervise_parent, daemon=True).start()
    sys.stdout = sys.stderr  # ordinary print is a log, never an event or a receipt
    ctx = Context(json.loads(Path(context_file).read_text()), channel, replies)
    functions = runpy.run_path(script, run_name="automation_script")
    function = functions.get(entry)
    if not callable(function):
        raise ValueError(f"脚本需要定义 {entry}(ctx) 函数。")
    result = function(ctx)
    if inspect.isawaitable(result):
        asyncio.run(result)


if __name__ == "__main__":
    main()
