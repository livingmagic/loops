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


class ObservedDict(dict):
    pass


class ObservedList(list):
    pass


class ObservedText(str):
    def __str__(self):
        return self

    def strip(self, chars=None):
        value = super().strip(chars)
        # The view may trim whitespace; verification still reads the untouched
        # host snapshot at this origin, never the locally transformed string.
        return self if value == self else observed(value, self.evidence_id, self.evidence_path)


def observed(value, evidence_id, path=''):
    """Preserve evidence origin when scripts access a normalized payload view."""
    if isinstance(value, dict):
        result = ObservedDict({key: observed(item, evidence_id, path + '/' + str(key).replace('~', '~0').replace('/', '~1')) for key, item in value.items()})
    elif isinstance(value, list):
        result = ObservedList(observed(item, evidence_id, path + '/' + str(index)) for index, item in enumerate(value))
    elif isinstance(value, str):
        result = ObservedText(value)
    else:
        return value
    result.evidence_id, result.evidence_path = evidence_id, path
    return result


def evidence_field(response, pointer):
    """Resolve a selected field to its recorded origin, including projections."""
    if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
        raise ValueError('回读字段需要有效的 JSON Pointer。')
    value = response
    parent, suffix = response, pointer
    parts = pointer[1:].split('/') if pointer else []
    for index, part in enumerate(parts):
        if getattr(value, 'evidence_id', None):
            parent, suffix = value, '/' + '/'.join(parts[index:])
        key = part.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    if getattr(value, 'evidence_id', None):
        return value.evidence_id, value.evidence_path, value
    evidence_id = getattr(parent, 'evidence_id', None)
    if evidence_id:
        return evidence_id, parent.evidence_path + suffix, value
    if isinstance(response, dict) and response.get('_evidenceId'):
        return response['_evidenceId'], pointer, value
    return None, None, value


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

    def connector(self, connector_id, operation, arguments=None, *, call_key=None):
        """同步调用固定版本授权的操作，返回 result（原始）、payload（已解码业务数据）、links（实际返回链接的绝对 URL 候选）。取得已创建资源的链接时优先选择 links 中对应资源的 url，不因 payload 中协议相对链接缺少 https: 就另行搜索或重复创建。链接本身不代表业务成功。回读保留来源追踪；直接字段、原字段投影和首尾空白视图指向原始快照，平台始终核对未修改的远端值。正文改写、JSON 编解码会丢失来源。不含凭证。"""
        self._connector_sequence = getattr(self, "_connector_sequence", 0) + 1
        result = self._call("connector", connectorId=connector_id, operation=operation, arguments=arguments or {},
                            callKey=call_key if call_key is not None else str(self._connector_sequence))
        return observed(result, result.get('_evidenceId'))

    def verify_document(self, input_name, readback, *, content_path, expected_format=None, actual_format='text',
                        format_path=None, expected_title=None, title_path=None, title_policy=None):
        """同步核对固定输入和实际只读回读。失败抛 RuntimeError；成功返回 {ok: true, matched: true, ...摘要}，不是服务端业务响应。通常直接用 deliver_document 完成校验和回执，无需自行判断成功字段。content_path 是相对于 readback 的 JSON Pointer；支持原响应、payload 视图或保留原字段的投影。format_path 可选，否则 actual_format 必须是实际表达格式（text/markdown/html），不能仅凭字段名猜格式。title_path 若指定必须来自实际只读返回。原文格式、标题规则和期望名称取固定 verification，不得放宽。"""
        contract = getattr(self, 'verification', None) or {}
        evidence_id, content_pointer, _ = evidence_field(readback, content_path)
        declared = readback.get('_evidenceId') if isinstance(readback, dict) else None
        if declared and declared != evidence_id:
            raise ValueError('回读对象声明的来源与实际字段来源不一致。')
        format_id = title_id = None
        format_pointer = title_pointer = None
        if format_path is not None:
            format_id, format_pointer, value = evidence_field(readback, format_path)
            if not format_id:
                if value not in {'text', 'markdown', 'html'}:
                    raise ValueError('请明确指定可核对的实际回读格式。')
                actual_format = value
        if title_path is not None:
            title_id, title_pointer, _ = evidence_field(readback, title_path)
            if not title_id:
                raise ValueError('目标名称必须来自实际只读返回，不能用自行拼装的名称代替。')
        return self._call('verify_document', inputName=input_name,
                          evidenceId=evidence_id, contentPath=content_pointer,
                          expectedFormat=expected_format if expected_format is not None else contract.get('expectedFormat', 'markdown'),
                          actualFormat=actual_format, formatPath=format_pointer, formatEvidenceId=format_id,
                          expectedTitle=expected_title if expected_title is not None else contract.get('expectedTitle'),
                          titlePath=title_pointer, titleEvidenceId=title_id,
                          titlePolicy=title_policy if title_policy is not None else contract.get('titlePolicy', 'preserve'), version=1)

    def deliver_document(self, input_name, readback=None, *, target, content=None, actual_title=None, actual_format='text',
                         content_path=None, format_path=None, title_path=None, status='delivered', **details):
        """文档输出的终结操作：优先用 content=本次只读返回中的实际正文字符串、actual_title=只读回读的实际名称字段（指定了 expectedTitle 时必需）、actual_format=实际格式、target=已核对身份的实际URL。直接传字段值，框架自动追踪原始位置，不需要拼 JSON Pointer。actual_title 不是配置中的目标名称，不得用 ctx.config 或常量代替实际名称。不要清洗、改写或 JSON 编解码后再传回读字段。按固定 verification 验证后才确认回执，失败抛异常，成功返回 True，无需再判断 ok/matched 或调用 receipt。也支持各字段独立通过 readback 的 content_path/title_path/format_path 选择；直接字段值优先，路径相对于 readback，不拼接字段移动前的旧位置。不会创建、写入、重复调用插件或修改授权。"""
        # Compatibility for scripts saved during the earlier SDK revision.
        legacy_title = details.pop('title', None)
        title = actual_title if actual_title is not None else legacy_title
        def select(path):
            if readback is None:
                raise ValueError('按路径选择字段时需要提供实际 readback。')
            origin, original_path, value = evidence_field(readback, path)
            return observed(value, origin, original_path) if origin else value

        if content is None:
            if content_path is None:
                if not isinstance(readback, str):
                    raise ValueError('请直接传实际正文 content，或为 readback 指定 content_path。')
                content_path = ''
            content = select(content_path)
        elif readback is not None:
            origin = getattr(readback, 'evidence_id', None) or (readback.get('_evidenceId') if isinstance(readback, dict) else None)
            if origin and origin != getattr(content, 'evidence_id', None):
                raise ValueError('正文值和回读对象必须属于同一次实际只读返回。')
            if content_path is not None:
                selected = select(content_path)
                if (getattr(selected, 'evidence_id', None), str(selected)) != (getattr(content, 'evidence_id', None), str(content)):
                    raise ValueError('直接正文值与路径选中的字段不一致。')
        if title is None and title_path is not None:
            title = select(title_path)
        if format_path is not None:
            actual_format = select(format_path)
        if (getattr(self, 'verification', None) or {}).get('expectedTitle') and title is None:
            raise ValueError('目标名称需要真实回读：请读取实际名称字段并传 actual_title，不能用配置中的目标名称代替。')
        view = {'content': content, 'format': actual_format}
        if title is not None:
            view['title'] = title
        self.verify_document(input_name, view, content_path='/content', format_path='/format',
                             title_path='/title' if title is not None else None)
        return self.receipt(target=target, status=status, **details)

    def read_text(self, name, encoding="utf-8"):
        value = self.inputEvidence[name]
        return Path(self.inputs[name]).read_text(encoding=encoding) if value["kind"] == "file" else self.inputs[name]

    def read_bytes(self, name):
        value = self.inputEvidence[name]
        return Path(self.inputs[name]).read_bytes() if value["kind"] == "file" else self.inputs[name].encode("utf-8")


def sdk_contract():
    """Use real runtime signatures/docstrings as the builder's source of truth."""
    methods = {}
    for name in ('connector', 'read_text', 'read_bytes', 'verify_document', 'deliver_document', 'receipt', 'emit', 'log'):
        method = getattr(Context, name)
        signature = inspect.signature(method)
        signature = signature.replace(parameters=[p for p in signature.parameters.values() if p.name != 'self'])
        methods[name] = {'signature': f'ctx.{name}{signature}', 'description': inspect.getdoc(method) or ''}
    return {'methods': methods, 'synchronous': True,
            'documentDelivery': '文档优先以 ctx.deliver_document 终结输出；它校验后才确认回执，不需要额外判断返回字段。为 Markdown/HTML 报告选择回读操作时，优先查找服务提供的完整 HTML/Markdown 表示，保留超链接目标、标题和结构；有这种表示时不选纯文本提取或搜索摘要代替。格式以操作契约为准；只有纯文本回读且能完整表达所需语义时才使用 text。无文档验收的通知等输出用 ctx.receipt。',
            'example': 'ctx.deliver_document(bound_name, target=actual_url, content=body_field, actual_title=read_name_field, actual_format=read_format)'}


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
