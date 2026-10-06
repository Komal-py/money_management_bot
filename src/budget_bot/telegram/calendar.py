"""Read-only Monday-first calendar. Controller supplies the authenticated owner."""
import calendar
import re

from budget_bot.services.reports import _date, _money, _summary
from budget_bot.telegram.tables import table

_MAX_PAGE = 999999


class CalendarInputError(ValueError):
    """Invalid calendar input, not a store or rendering failure."""


def _page(page):
    if type(page) is not int or not 0 <= page <= _MAX_PAGE:
        raise CalendarInputError('Invalid calendar page.')
    return page


def parse_callback(payload):
    """Validate bounded callback data, never extract or accept an owner identity."""
    if not isinstance(payload, str) or len(payload.encode('utf-8')) > 64:
        raise ValueError('Invalid calendar button.')
    match = re.fullmatch(r'cal:([0-9]{4})-([0-9]{2})', payload)
    if match:
        year, month = map(int, match.groups())
        _date(f'{year:04d}-{month:02d}-01')
        return {'kind': 'month', 'year': year, 'month': month}
    match = re.fullmatch(r'day:([0-9]{4}-[0-9]{2}-[0-9]{2})(?::(0|[1-9][0-9]{0,5}))?', payload)
    if match:
        return {'kind': 'day', 'date': _date(match[1]), 'page': _page(int(match[2] or 0))}
    raise ValueError('Invalid calendar button.')


def month_view(year, month):
    if type(year) is not int or type(month) is not int:
        raise ValueError('Invalid calendar month.')
    _date(f'{year:04d}-{month:02d}-01')
    current = f'cal:{year:04d}-{month:02d}'
    keyboard = [[{'text': name, 'data': current}
                 for name in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']]]
    for week in calendar.Calendar(firstweekday=0).monthdayscalendar(year, month):
        keyboard.append([{'text': str(day) if day else ' ',
                          'data': f'day:{year:04d}-{month:02d}-{day:02d}' if day else current}
                         for day in week])
    index = (year - 1) * 12 + month - 1
    navigation = []
    for offset, label in [(-1, 'Previous month'), (1, 'Next month')]:
        adjacent = index + offset
        if 0 <= adjacent < 9999 * 12:
            adjacent_year, adjacent_month = divmod(adjacent, 12)
            navigation.append({'text': label,
                               'data': f'cal:{adjacent_year + 1:04d}-{adjacent_month + 1:02d}'})
    if navigation:
        keyboard.append(navigation)
    return {'text': f'{calendar.month_name[month]} {year:04d}', 'keyboard': keyboard}


def day_view(store, owner_id, date, page=0):
    day = _date(date)
    page = _page(page)
    result = store.spending(owner_id, day, day)
    # Spending owns current-active-revision selection and aggregate truth.
    rows = sorted(result['expenses'], key=lambda row: (row['date'], row['id']))
    pages = max(1, (len(rows) + 7) // 8)
    if page >= pages:
        raise CalendarInputError('Calendar page is out of range.')
    lines = [_summary(result), f'Page {page + 1} of {pages}']
    shown = rows[page * 8:(page + 1) * 8]
    if shown:
        # Bucket and amount align in columns; the full description sits on its
        # own indented line so long text is never truncated on a phone.
        cells = []
        for row in shown:
            cells.append([row['bucket'], _money(row['amount'])])
            cells.append([f'  {row["description"]}', ''])
        lines.append(table(cells, headers=['Bucket', 'Amount'], numeric={1}, wrap_first=True))
    navigation = []
    if page:
        navigation.append({'text': 'Previous', 'data': f'day:{day.isoformat()}:{page - 1}'})
    if page + 1 < pages:
        navigation.append({'text': 'Next', 'data': f'day:{day.isoformat()}:{page + 1}'})
    keyboard = [navigation] if navigation else []
    keyboard.append([{'text': 'Back to month', 'data': f'cal:{day.year:04d}-{day.month:02d}'}])
    return {'text': '\n'.join(lines), 'keyboard': keyboard}
