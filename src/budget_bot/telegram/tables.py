"""Monospaced aligned tables for Telegram, rendered as HTML <pre> blocks.

Renderers keep producing plain text and wrap table sections in private marker
characters. The transport converts each message to Telegram HTML right before
sending: everything is escaped first, then balanced markers become <pre> tags.
Unbalanced markers (e.g. a block split across message chunks) are dropped, so a
message can never fail Telegram's HTML parser and block the outbox.
"""
import html
import re

WIDTH = 30
LABEL_MIN = 20
PRE_OPEN = '\x02'
PRE_CLOSE = '\x03'


def _clean(value):
    text = str(value).replace(PRE_OPEN, ' ').replace(PRE_CLOSE, ' ')
    indent = '  ' if text.startswith('  ') else ''
    return indent + ' '.join(text.split())


def _cut(value, width):
    value = _clean(value)
    return value if len(value) <= width else value[:max(width - 1, 0)] + '…'


def table(rows, headers=None, *, numeric=None, footer=None, wrap_first=False):
    """Align rows into fixed-width columns and wrap them as a <pre> block.

    The first column (labels) shrinks and truncates to keep the table within
    WIDTH characters; other columns (amounts) are never truncated. Columns in
    ``numeric`` are right-aligned (default: every column except the first).
    ``footer`` rows are separated by a rule (used for totals).
    ``wrap_first``: first-column cells of rows with no other content are
    word-wrapped instead of truncated (used for full descriptions).
    """
    body = [[_clean(cell) for cell in row] for row in rows]
    head = [_clean(cell) for cell in headers] if headers else None
    tail = [[_clean(cell) for cell in row] for row in (footer or [])]
    everything = ([head] if head else []) + body + tail
    if not everything:
        return ''
    columns = max(len(row) for row in everything)
    pad = lambda row: row + [''] * (columns - len(row))  # noqa: E731
    everything = [pad(row) for row in everything]
    numeric = set(range(1, columns)) if numeric is None else set(numeric)
    widths = [max(len(row[i]) for row in everything) for i in range(columns)]
    if columns > 1:
        # Shrink only free-text first columns (e.g. long descriptions). Labels
        # up to LABEL_MIN chars are never cut; a block wider than a phone
        # scrolls horizontally in Telegram rather than losing meaning.
        room = WIDTH - (columns - 1) - sum(widths[1:])
        widths[0] = max(min(widths[0], LABEL_MIN), min(widths[0], room))

    if wrap_first:
        # Description rows must not widen the column layout.
        sized = [row for row in everything if any(row[1:])] or everything
        widths = [max(len(row[i]) for row in sized) for i in range(columns)]
    total_width = max(sum(widths) + columns - 1, 1)

    def wrap(text, width):
        indent = '  ' if text.startswith('  ') else ''
        words, out, current = text.split(), [], ''
        for word in words:
            while len(word) > width - len(indent):
                if current:
                    out.append(current)
                    current = ''
                out.append(indent + word[:width - len(indent)])
                word = word[width - len(indent):]
            candidate = (current + ' ' + word) if current else indent + word
            if len(candidate) > width and current:
                out.append(current)
                candidate = indent + word
            current = candidate
        return out + ([current] if current else [])

    def line(row):
        row = pad(row)
        if wrap_first and not any(row[1:]):
            # Wrap to the phone width, not the (often narrow) column layout.
            return '\n'.join(wrap(row[0], max(total_width, WIDTH)))
        cells = []
        for i, cell in enumerate(pad(row)):
            if i == 0:
                cell = _cut(cell, widths[0])
            cells.append(cell.rjust(widths[i]) if i in numeric else cell.ljust(widths[i]))
        return ' '.join(cells).rstrip()

    rule = '─' * total_width
    lines = []
    if head:
        lines += [line(head), rule]
    lines += [line(row) for row in body]
    if tail:
        lines += [rule] + [line(row) for row in tail]
    return PRE_OPEN + '\n'.join(lines) + PRE_CLOSE


def plain(text):
    """Text with table markers removed (for logs and plain-text checks)."""
    return text.replace(PRE_OPEN, '').replace(PRE_CLOSE, '')


def flat(text):
    """Plain text with alignment padding and rules collapsed: 'Food:   ₹1' -> 'Food: ₹1'.

    For readers/tests that care about values, not column layout.
    """
    lines = [re.sub(r' {2,}', ' ', line).strip() for line in plain(text).splitlines()]
    return '\n'.join(line for line in lines if line and set(line) != {'─'})


def _balanced(text):
    depth = 0
    for character in text:
        if character == PRE_OPEN:
            depth += 1
        elif character == PRE_CLOSE:
            depth -= 1
        if depth not in (0, 1):
            return False
    return depth == 0


def to_html(text):
    """Escape a chunk for parse_mode=HTML and turn balanced markers into <pre>."""
    if not _balanced(text):
        text = plain(text)
    escaped = html.escape(text, quote=False)
    return escaped.replace(PRE_OPEN, '<pre>').replace(PRE_CLOSE, '</pre>')


def table_rows(text, header):
    """Entries of the <pre> table whose header starts with ``header``.

    Returns one string per entry: an aligned row plus any indented
    continuation lines (e.g. a wrapped description) joined with a space.
    """
    for block in re.findall(PRE_OPEN + '(.*?)' + PRE_CLOSE, text, flags=re.S):
        lines = block.splitlines()
        if not (lines and lines[0].startswith(header) and len(lines) > 1 and set(lines[1]) == {'─'}):
            continue
        entries = []
        for raw in lines[2:]:
            if not raw.strip() or set(raw.strip()) == {'─'}:
                continue
            if raw.startswith('  ') and entries:
                entries[-1] += ' ' + raw.strip()
            else:
                entries.append(raw.rstrip())
        return entries
    return []
