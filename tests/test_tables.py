"""Monospaced tables: alignment, truncation, HTML safety across chunks."""
from budget_bot.telegram.tables import PRE_CLOSE, PRE_OPEN, flat, plain, table, table_rows, to_html


def test_amounts_right_aligned_in_one_column():
    text = plain(table([['Food:', '₹1.00'], ['Travel:', '₹1200.50']], footer=[['Total:', '₹1201.50']]))
    lines = text.splitlines()
    assert len({len(line) for line in lines if '₹' in line}) == 1  # amounts end in one column
    assert set(lines[2]) == {'─'}
    assert lines[-1].startswith('Total:') and lines[-1].endswith('₹1201.50')
    assert 'Food:' in lines[0] and lines[0].endswith('₹1.00')


def test_long_labels_truncate_but_amounts_stay_whole():
    text = plain(table([['A very very long free-text label that goes on:', '₹123456.78']]))
    assert '…' in text and text.endswith('₹123456.78')
    label = text.split('₹')[0].rstrip()
    assert len(label) == 20  # labels keep at least LABEL_MIN characters before truncating
    short = plain(table([['Unallocated pool:', '₹1.01']]))
    assert short.startswith('Unallocated pool:')  # ordinary labels are never cut


def test_headers_and_rule():
    text = plain(table([['Lunch', 'Food', '₹99.00']], headers=['Item', 'Bucket', 'Amount'], numeric={2}))
    head, rule, row = text.splitlines()
    assert head.startswith('Item') and set(rule) == {'─'} and row.endswith('₹99.00')


def test_html_escapes_user_text_and_converts_markers():
    out = to_html('Note <b>&</b>\n' + table([['<script>:', '₹1.00']]))
    assert '<b>' not in out and '&lt;b&gt;&amp;&lt;/b&gt;' in out
    assert out.count('<pre>') == 1 and out.count('</pre>') == 1
    assert '&lt;script&gt;' in out


def test_unbalanced_markers_from_chunk_split_are_dropped():
    assert to_html('a' + PRE_OPEN + 'b') == 'ab'
    assert to_html('a' + PRE_CLOSE + 'b') == 'ab'
    assert to_html(PRE_OPEN + PRE_OPEN + 'x' + PRE_CLOSE + PRE_CLOSE) == 'x'


def test_user_supplied_markers_cannot_open_blocks():
    out = to_html(table([[PRE_CLOSE + '<i>' + PRE_OPEN + ':', '₹1.00']]))
    assert out.count('<pre>') == 1 and '<i>' not in out


def test_long_descriptions_wrap_fully_under_aligned_row():
    long = 'Dinner with the whole extended family at the riverside place'
    text = table([['Food', '₹1500.00'], [f'  {long}', '']], headers=['Bucket', 'Amount'], numeric={1}, wrap_first=True)
    lines = plain(text).splitlines()
    assert all(len(line) <= 30 for line in lines)
    assert ' '.join(line.strip() for line in lines[3:]) == long  # nothing truncated
    (entry,) = table_rows(text, 'Bucket')
    assert entry.split(maxsplit=2) == ['Food', '₹1500.00', long]


def test_short_description_stays_on_one_line():
    text = table([['Travel', '₹0.01'], ['  Fictional-00', '']], headers=['Bucket', 'Amount'], numeric={1}, wrap_first=True)
    assert plain(text).splitlines()[-1] == '  Fictional-00'


def test_flat_collapses_padding_for_value_checks():
    assert flat('Head\n' + table([['Pool:', '₹5.00']], footer=[['Total:', '₹5.00']])) == 'Head\nPool: ₹5.00\nTotal: ₹5.00'
