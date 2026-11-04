"""Versioned document comparison by representation and declared semantic role.

No connector IDs, operation names, field-name guesses or similarity thresholds.
"""
import hashlib
import json
import unicodedata
from html.parser import HTMLParser

from markdown_it import MarkdownIt

VERSION = 1


def lexical(text):
    """Keep words/numbers/punctuation and order, independent of CJK line wraps."""
    tokens, word = [], []
    def flush():
        if word:
            tokens.append(''.join(word)); word.clear()
    normalized = unicodedata.normalize('NFC', text)
    for index, char in enumerate(normalized):
        # Some plain-text projections remove a line break immediately before
        # an absolute URI. Its explicit scheme still defines a token boundary;
        # unrelated alphanumeric identifiers remain intact.
        if normalized.startswith(('https://', 'http://'), index):
            flush()
        code = ord(char)
        ideograph = 0x3400 <= code <= 0x9fff or 0x20000 <= code <= 0x323af
        if not ideograph and (char.isalnum() or char == '_' or unicodedata.category(char).startswith('M')):
            word.append(char)
        else:
            flush()
            if not char.isspace():
                tokens.append(char)
    flush()
    return tokens


def canonical(parts):
    result, text = [], []
    def flush():
        if text:
            result.extend(('text', value) for value in lexical(''.join(text))); text.clear()
    for part in parts:
        if isinstance(part, str):
            text.append(part)
        else:
            flush(); result.append(part)
    flush()
    return result


class VisibleDocument(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.head, self.anchor = [], [], []
        self.ignored = 0
        self.title_start = self.title_end = None
        self.literal, self.lists = None, []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'head', 'script', 'style'}:
            self.ignored += 1
        if self.ignored:
            return
        if self.literal:
            return
        if tag in {'pre', 'code'}:
            self.literal = (tag, [])
            return
        if tag in {'td', 'th', 'tr'}:
            self.parts.append(('row' if tag == 'tr' else 'cell', 'start'))
        if tag in {'ol', 'ul'}:
            self.parts.append(('list', tag))
            self.lists.append(int(attrs.get('start', '1')) if tag == 'ol' else None)
        if tag == 'li' and self.lists:
            number = self.lists[-1]
            if number is not None:
                number = int(attrs.get('value', number))
            self.parts.append(('item', str(number) if number is not None else 'bullet'))
            if number is not None:
                self.lists[-1] = number + 1
        if tag in {'p', 'div', 'article', 'section', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th', 'tr', 'br', 'pre'}:
            self.parts.append('\n')
        if tag == 'h1' and self.title_start is None and not canonical(self.parts):
            self.title_start = len(self.parts)
        if tag == 'a':
            self.anchor.append((attrs.get('href', ''), len(self.parts)))
        if tag == 'img':
            self.parts.extend([attrs.get('alt', ''), ' ', attrs.get('src', '')])
        if tag in {'del', 's', 'strike', 'input', 'audio', 'video', 'iframe', 'object'}:
            raise ValueError('该正文包含无法可靠跨格式核对的内容，请使用保留其结构的回读格式。')

    def handle_endtag(self, tag):
        if tag in {'head', 'script', 'style'} and self.ignored:
            self.ignored -= 1
            return
        if self.ignored:
            return
        if self.literal:
            if tag == self.literal[0]:
                self.parts.append(('literal', ''.join(self.literal[1]).replace('\r\n', '\n')))
                self.literal = None
            return
        if tag == 'h1' and self.title_start is not None and self.title_end is None:
            self.title_end = len(self.parts)
        if tag == 'a' and self.anchor:
            href, start = self.anchor.pop()
            label = ''.join(p if isinstance(p, str) else p[1] for p in self.parts[start:]).strip()
            if href and label != href:
                self.parts.extend([' ', href])
        if tag in {'p', 'div', 'article', 'section', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th', 'tr', 'pre'}:
            self.parts.append('\n')
        if tag in {'td', 'th', 'tr'}:
            self.parts.append(('row' if tag == 'tr' else 'cell', 'end'))
        if tag in {'ol', 'ul'}:
            self.parts.append(('list', 'end'))
            if self.lists:
                self.lists.pop()
        if tag == 'li':
            self.parts.append(('item', 'end'))

    def handle_data(self, value):
        if not self.ignored:
            (self.literal[1] if self.literal else self.parts).append(value)

    def document(self):
        if self.literal:
            raise ValueError('正文的代码结构不完整，无法可靠核对。')
        text = self.parts
        if self.title_start is not None and self.title_end is not None:
            title = ''.join(p if isinstance(p, str) else p[1] for p in self.parts[self.title_start:self.title_end]).strip()
            body = self.parts[:self.title_start] + self.parts[self.title_end:]
            return text, title, body
        return text, None, text


def parse_document(text, fmt):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('核对需要非空正文。')
    if len(text.encode()) > 10_000_000:
        raise ValueError('核对正文超过 10MB。')
    if fmt == 'text':
        return [text], None, [text]
    if fmt == 'markdown':
        text = MarkdownIt('commonmark', {'html': True}).enable('table').render(text)
    elif fmt != 'html':
        raise ValueError('正文格式需要明确为 text、markdown 或 html，不猜测返回格式。')
    parser = VisibleDocument(); parser.feed(text); parser.close()
    return parser.document()


def verify_document(expected, actual, *, expected_format='markdown', actual_format='text',
                    expected_title=None, actual_title=None, title_policy='preserve', version=VERSION):
    if version != VERSION or title_policy not in {'preserve', 'target'}:
        raise ValueError('文档校验版本或标题规则无效。')
    if title_policy == 'target' and (not isinstance(expected_title, str) or not expected_title.strip()):
        raise ValueError('允许使用目标文档标题时，必须提供用户要求的目标标题。')
    if actual_title is not None and expected_title is not None and (not isinstance(actual_title, str) or not isinstance(expected_title, str) or lexical(actual_title) != lexical(expected_title)):
        raise ValueError('回读的文档名称与交付目标不一致。')
    expected_text, original_title, expected_body = parse_document(expected, expected_format)
    actual_text, read_title, actual_body = parse_document(actual, actual_format)
    expected_tokens, actual_tokens = canonical(expected_text), canonical(actual_text)
    matched, applied = expected_tokens == actual_tokens, 'content'
    if not matched and isinstance(expected_title, str) and expected_title.strip():
        # A named target permits a document-level heading role, never arbitrary
        # first lines or body headings. Only an exact known target prefix is
        # removed from a plain-text projection.
        if actual_format == 'text' and actual.startswith(expected_title):
            read_title, actual_body = expected_title, [actual[len(expected_title):]]
        if read_title is not None and lexical(read_title) == lexical(expected_title):
            body_tokens = canonical(actual_body)
            # Metadata may be prefixed to an intact report, or may replace its
            # one explicitly parsed leading H1. Every body token must match.
            matched = body_tokens == expected_tokens or (title_policy == 'target' and original_title is not None and body_tokens == canonical(expected_body))
            applied = 'target-title' if matched else 'content'
    result = {'version': VERSION, 'matched': matched, 'comparison': applied, 'titlePolicy': title_policy,
              'expectedFormat': expected_format, 'actualFormat': actual_format,
              'expectedSha256': hashlib.sha256(expected.encode()).hexdigest(), 'actualSha256': hashlib.sha256(actual.encode()).hexdigest(),
              'expectedContentSha256': hashlib.sha256(json.dumps(expected_tokens, ensure_ascii=False).encode()).hexdigest(),
              'actualContentSha256': hashlib.sha256(json.dumps(actual_tokens, ensure_ascii=False).encode()).hexdigest()}
    return result


def pointer(value, path):
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('请提供回读字段的 JSON Pointer。')
    try:
        for part in path[1:].split('/'):
            part = part.replace('~1', '/').replace('~0', '~')
            value = value[int(part)] if isinstance(value, list) else value[part]
        return value
    except (KeyError, IndexError, ValueError, TypeError) as error:
        raise ValueError('回读结果中没有指定字段：' + path) from error
