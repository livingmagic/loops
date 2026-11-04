"""Real stdio MCP service used only by integration tests; no production imports."""
import json
import sys
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

root = Path(sys.argv[1])
root.mkdir(parents=True, exist_ok=True)
mcp = FastMCP('Independent workflow test service')
read = ToolAnnotations(readOnlyHint=True)
write = ToolAnnotations(readOnlyHint=False, destructiveHint=False)

def record(name, value):
    with (root / 'calls.jsonl').open('a') as stream:
        stream.write(json.dumps({'operation': name, 'value': value}) + '\n')

@mcp.tool(name='original_measure' if (root / 'changed').exists() else 'measure', annotations=read)
def measure(text: str) -> dict:
    """Measure supplied text. Returns text, length, items, enabled and optional."""
    record('measure', text)
    return {'text': text, 'length': len(text), 'items': [text, len(text)], 'enabled': True, 'optional': None}

@mcp.tool(annotations=read)
def combine(data: dict, count: int, items: list, enabled: bool, optional: str | None) -> dict:
    """Accept typed data from an earlier operation and return it without conversion."""
    result = dict(data=data, count=count, items=items, enabled=enabled, optional=optional)
    record('combine', result)
    return result

@mcp.tool(annotations=write)
def deliver(data: dict, fail: bool = False) -> dict:
    """Write data to a local test receipt. A failure occurs AFTER the write when requested."""
    record('deliver', data)
    (root / 'receipt.json').write_text(json.dumps(data))
    if fail:
        raise ValueError('Receipt written but confirmation failed')
    return {'saved': True, 'data': data}

if (root / 'changed').exists():
    @mcp.tool(name='measure', annotations=read)
    def changed_measure(number: int) -> dict:
        """Changed contract used to exercise stale workflow rejection."""
        record('changed_measure', number)
        return {'number': number}

mcp.run(transport='stdio')
