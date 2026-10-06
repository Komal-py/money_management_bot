"""Explicit CLI and scoped application lifecycle; imports have no side effects."""
import argparse
import asyncio
import signal
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

from budget_bot.settings import load_migration_settings, load_settings


@dataclass
class Runtime:
    store: object
    onboarding: object
    reports: object
    access: object
    workflow: object
    controller: object
    transport: object


def _migration_config(project_dir, settings):
    from alembic.config import Config
    root = Path(project_dir).resolve()
    if not (root / 'alembic.ini').is_file() or not (root / 'alembic/versions').is_dir():
        raise ValueError('Migration files missing; select the project directory')
    config = Config(str(root / 'alembic.ini'))
    config.attributes.update(database_url=settings.database_url, schema=settings.schema_name)
    return config


def migrate(settings, project_dir):
    """Explicit app-schema migration; never provision a database or erase rows."""
    from alembic import command
    command.upgrade(_migration_config(project_dir, settings), 'head')


def _require_migrated(store, settings, project_dir):
    from alembic.script import ScriptDirectory
    from sqlalchemy import text
    required = ScriptDirectory.from_config(_migration_config(project_dir, settings)).get_current_head()
    try:
        with store.engine.connect() as connection:
            actual = connection.scalar(text('SELECT version_num FROM alembic_version'))
    except Exception:
        raise ValueError('Application schema needs explicit migration before startup') from None
    if actual != required:
        raise ValueError('Application schema needs explicit migration before startup')


@asynccontextmanager
async def build_runtime(settings, *, project_dir=None, bot=None):
    """Compose actual services with persistent checkpoints and owned cleanup."""
    from budget_bot.ai import AIInterpreter, render_result, render_review
    from budget_bot.controller import BudgetController
    from budget_bot.services.access import AccessService
    from budget_bot.services.onboarding import OnboardingService
    from budget_bot.services.reports import ReportService
    from budget_bot.storage import BudgetStore
    from budget_bot.telegram.transport import TelegramTransport, create_bot
    from budget_bot.workflows import BudgetWorkflow, postgres_checkpointer

    root = Path.cwd() if project_dir is None else Path(project_dir)
    async with AsyncExitStack() as stack:
        store = BudgetStore(settings.database_url, schema=settings.schema_name)
        stack.callback(store.close)
        await asyncio.to_thread(_require_migrated, store, settings, root)
        bot = create_bot(settings.telegram_bot_token) if bot is None else bot
        stack.push_async_callback(bot.shutdown)
        await bot.initialize()
        webhook = await bot.get_webhook_info()
        if webhook.url:
            raise ValueError('Existing webhook prevents polling; no queued updates were deleted')
        onboarding = OnboardingService(store)
        await asyncio.to_thread(onboarding.initialize)
        await asyncio.to_thread(store.ensure_admin, settings.telegram_admin_user_id)
        saver = await stack.enter_async_context(postgres_checkpointer(
            settings.database_url, schema=settings.workflow_schema))
        interpreter = None
        if settings.ai_enabled:
            interpreter = AIInterpreter(settings.ai_base_url, settings.ai_api_key,
                                        settings.ai_model, settings.ai_timeout)
            stack.push_async_callback(interpreter.close)
        workflow = BudgetWorkflow(store, interpreter, saver)
        workflow.render_review = render_review
        workflow.render_result = render_result
        reports, access = ReportService(store), AccessService(store)
        controller = BudgetController(store, workflow, onboarding, reports, access)
        yield Runtime(store, onboarding, reports, access, workflow, controller,
                      TelegramTransport(bot, store, controller, admin_id=settings.telegram_admin_user_id))


async def run(settings, project_dir=None):
    """Run one poller; Ctrl+C/SIGTERM requests orderly durable-work shutdown."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    restorers = []
    for number in (signal.SIGINT, signal.SIGTERM):
        try:
            previous = signal.getsignal(number)
            signal.signal(number, lambda *_: loop.call_soon_threadsafe(stop.set))
            restorers.append((number, previous))
        except (ValueError, OSError):
            pass
    try:
        async with build_runtime(settings, project_dir=project_dir) as runtime:
            await runtime.transport.run(stop)
    finally:
        for number, previous in restorers:
            signal.signal(number, previous)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Invite-only virtual INR Telegram budget bot')
    parser.add_argument('--env-file', default='.env', help='Explicit local configuration file')
    parser.add_argument('--project-dir', default=str(Path.cwd()), help='Directory containing Alembic files')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--check-config', action='store_true', help='Validate configuration offline')
    mode.add_argument('--migrate', action='store_true', help='Explicitly upgrade application schema')
    args = parser.parse_args(argv)
    try:
        if args.migrate:
            config = load_migration_settings(args.env_file)
            migrate(config, args.project_dir)
            print('Application schema migration complete')
        else:
            config = load_settings(args.env_file)
            if args.check_config:
                print('Configuration valid (offline; no services contacted)')
            else:
                asyncio.run(run(config, args.project_dir))
    except KeyboardInterrupt:
        return 0
    except Exception:
        # Never print SQLAlchemy/provider/HTTPX exception strings: may contain secrets.
        print('Operation failed. Check configuration, migration and service availability.')
        return 1
    return 0
