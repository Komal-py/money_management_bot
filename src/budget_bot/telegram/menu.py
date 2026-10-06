"""Static Telegram UI surface: command registration and the persistent main menu.

Pure data and pure functions. No network, no store access and no financial
decisions: the transport registers commands, and the controller maps a menu
button press onto the existing command text that ingress already supports.
"""

# (name, description) pairs offered to every private chat. Each name must be a
# real command that budget_bot.telegram.commands.parse_command accepts.
_COMMANDS = (
    ('start', 'Open setup or the main menu'),
    ('balance', 'Show the pool and bucket balances'),
    ('spending', 'Spending report: today, week, month or a date range'),
    ('calendar', 'Browse recorded expenses day by day'),
    ('income', 'Propose income: amount description [date]'),
    ('expense', 'Propose an expense: amount bucket description [date]'),
    ('bucket', 'Create a bucket: name'),
    ('allocate', 'Move money from the pool into a bucket: amount bucket'),
    ('transfer', 'Move money between buckets: amount source destination'),
    ('target', 'Set or clear a bucket target: bucket amount|off'),
    ('undo', 'Propose undoing a transaction: [transaction-id|last]'),
    ('correct', 'Propose a correction: transaction-id field=value'),
    ('timezone', 'Set your timezone: zone'),
    ('cancel', 'Cancel the current review or question'),
    ('help', 'Show the full command syntax'),
)

# Administration is not offered in the default command list; it is registered
# only for the administrator chat so ordinary members are not invited to try it.
_ADMIN_COMMANDS = (
    ('invite', 'Create a single-use invite'),
    ('users', 'List members'),
    ('revoke', 'Revoke access: telegram-id'),
)


# Persistent main menu: (button label, command text sent through ingress).
# Labels are distinctive (emoji prefixed) and matched exactly, so typing an
# ordinary word such as a bucket name is never mistaken for a button press.
MAIN_MENU = (
    (('💰 Balance', '/balance'), ('📊 Spending', '/spending')),
    (('📅 Calendar', '/calendar'), ('🏠 Menu', '/start')),
    (('❓ Help', '/help'), ('✖️ Cancel', '/cancel')),
)
_BUTTON_COMMANDS = {label: command for row in MAIN_MENU for label, command in row}


def main_menu_keyboard():
    """JSON-safe ReplyKeyboardMarkup dict; durable outbox stores it verbatim."""
    return {'keyboard': [[{'text': label} for label, _ in row] for row in MAIN_MENU],
            'is_persistent': True, 'resize_keyboard': True,
            'input_field_placeholder': 'Type an expense or pick an option'}


def button_command(text):
    """Return the command for an exact main-menu button label, else None."""
    if not isinstance(text, str):
        return None
    return _BUTTON_COMMANDS.get(text.strip())


# Spending period picker: inline callbacks carry only a closed period word,
# never an owner, amount, bucket or date. The controller rebuilds the query.
SPENDING_PERIODS = (('today', 'Today'), ('week', 'This week'), ('month', 'This month'))
_PERIODS = {f'sp:{period}': period for period, _ in SPENDING_PERIODS}


def spending_period_keyboard():
    return [[{'text': label, 'data': f'sp:{period}'} for period, label in SPENDING_PERIODS]]


def spending_period_prompt():
    return {'text': 'Spending report: choose a period.\n'
                    'For a range or one bucket use /spending start end [bucket] '
                    'or /spending today|week|month bucket.',
            'keyboard': spending_period_keyboard()}


def parse_spending_callback(data):
    """Exact 'sp:<period>' match, else None; nothing else is accepted."""
    return _PERIODS.get(data) if isinstance(data, str) else None


def spending_query(period):
    return {'report': 'spending', 'period': period, 'start': None, 'end': None, 'bucket_name': None}


def registered_commands():
    """Commands advertised to every private chat when typing '/'."""
    return list(_COMMANDS)


def admin_commands():
    """Default commands plus administration, for the administrator chat only."""
    return list(_COMMANDS) + list(_ADMIN_COMMANDS)
