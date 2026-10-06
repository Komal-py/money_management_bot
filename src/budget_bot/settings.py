"""Explicit project-local configuration with secret-safe errors and representation."""
import os
import re
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.engine import make_url


class MigrationSettings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    database_url: str = Field(repr=False)
    schema_name: str = 'budget'


class Settings(MigrationSettings):
    telegram_bot_token: str = Field(repr=False)
    telegram_admin_user_id: int
    ai_api_key: str | None = Field(default=None, repr=False)
    ai_base_url: str | None = None
    ai_model: str | None = None
    timezone: str = 'Asia/Kolkata'
    workflow_schema: str = 'workflow'
    pending_minutes: int = 30
    ai_timeout: int = 30
    ai_enabled: bool = True


def _values(env_path, environ):
    # dotenv_values(None) searches for a parent .env: None must mean no file.
    values = {} if env_path is None else dict(dotenv_values(env_path, interpolate=False))
    values.update(os.environ if environ is None else environ)
    return values


def _schema(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z_][a-z0-9_]{0,62}', value):
        raise ValueError('Invalid schema setting')
    return value


def _database(values):
    first, alias = values.get('DATABASE_URL'), values.get('BUDGET_DATABASE_URL')
    if first and alias and first != alias:
        raise ValueError('Conflicting database settings')
    value = first or alias
    if not value:
        raise ValueError('Missing required settings: DATABASE_URL')
    try:
        parsed = make_url(value)
        if parsed.get_backend_name() != 'postgresql' or not parsed.database:
            raise ValueError
        # Accessing port validates malformed port syntax without exposing URL.
        _ = parsed.port
    except Exception:
        raise ValueError('Invalid PostgreSQL database setting') from None
    return value


def _positive(values, name, default=None, maximum=None):
    try:
        result = int(values.get(name, default))
        if result <= 0 or (maximum is not None and result > maximum):
            raise ValueError
    except (ValueError, TypeError, OverflowError):
        raise ValueError('Invalid positive integer setting: ' + name) from None
    return result


def load_migration_settings(env_path: Path | str | None = '.env',
                            environ: Mapping[str, str] | None = None) -> MigrationSettings:
    values = _values(env_path, environ)
    return MigrationSettings(database_url=_database(values),
                             schema_name=_schema(values.get('BUDGET_SCHEMA', 'budget')))


def load_settings(env_path: Path | str | None = '.env',
                  environ: Mapping[str, str] | None = None) -> Settings:
    values = _values(env_path, environ)
    required = ['DATABASE_URL', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_ADMIN_USER_ID']
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise ValueError('Missing required settings: ' + ', '.join(missing))
    enabled = values.get('AI_ENABLED', 'true')
    if not isinstance(enabled, str) or enabled.lower() not in {'true', 'false'}:
        raise ValueError('AI_ENABLED must be true or false')
    enabled = enabled.lower() == 'true'
    ai = {name: None for name in ('AI_API_KEY', 'AI_BASE_URL', 'AI_MODEL')}
    if enabled:
        missing = [name for name in ai if not values.get(name)]
        if missing:
            raise ValueError('Missing required settings: ' + ', '.join(missing))
        ai.update({name: values[name] for name in ai})
        try:
            url = urlsplit(ai['AI_BASE_URL'])
            valid = (url.scheme == 'https' and url.hostname and not url.username
                     and not url.password and not url.query and not url.fragment)
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('AI_BASE_URL must be a credential-free HTTPS endpoint') from None
    zone = values.get('BUDGET_TIMEZONE', 'Asia/Kolkata')
    try:
        ZoneInfo(zone)
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        raise ValueError('Invalid BUDGET_TIMEZONE setting') from None
    return Settings(database_url=_database(values), telegram_bot_token=values['TELEGRAM_BOT_TOKEN'],
                    telegram_admin_user_id=_positive(values, 'TELEGRAM_ADMIN_USER_ID', maximum=2**52 - 1),
                    ai_api_key=ai['AI_API_KEY'], ai_base_url=ai['AI_BASE_URL'], ai_model=ai['AI_MODEL'],
                    timezone=zone, schema_name=_schema(values.get('BUDGET_SCHEMA', 'budget')),
                    workflow_schema=_schema(values.get('BUDGET_WORKFLOW_SCHEMA', 'workflow')),
                    pending_minutes=_positive(values, 'BUDGET_PENDING_MINUTES', 30),
                    ai_timeout=_positive(values, 'AI_TIMEOUT', 30), ai_enabled=enabled)
