"""Project-local settings with secret-safe representation."""
import os
from pathlib import Path
from typing import Mapping
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    database_url: str = Field(repr=False)
    telegram_bot_token: str = Field(repr=False)
    telegram_admin_user_id: int
    ai_api_key: str = Field(repr=False)
    ai_base_url: str
    ai_model: str
    timezone: str = 'Asia/Kolkata'
    schema_name: str = 'budget'
    workflow_schema: str = 'workflow'
    pending_minutes: int = 30
    ai_timeout: int = 30
    ai_enabled: bool = True


def load_settings(env_path: Path | str = '.env', environ: Mapping[str, str] | None = None) -> Settings:
    values = dict(dotenv_values(env_path, interpolate=False))
    values.update(os.environ if environ is None else environ)
    required = ['DATABASE_URL', 'TELEGRAM_BOT_TOKEN', 'TELEGRAM_ADMIN_USER_ID',
                'AI_API_KEY', 'AI_BASE_URL', 'AI_MODEL']
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise ValueError('Missing required settings: ' + ', '.join(missing))
    url = urlsplit(values['AI_BASE_URL'])
    if url.scheme != 'https' or url.username or url.password or url.query or url.fragment:
        raise ValueError('AI_BASE_URL must be a credential-free HTTPS endpoint')
    timezone = values.get('BUDGET_TIMEZONE', 'Asia/Kolkata')
    ZoneInfo(timezone)
    return Settings(database_url=values['DATABASE_URL'], telegram_bot_token=values['TELEGRAM_BOT_TOKEN'],
                    telegram_admin_user_id=int(values['TELEGRAM_ADMIN_USER_ID']),
                    ai_api_key=values['AI_API_KEY'], ai_base_url=values['AI_BASE_URL'],
                    ai_model=values['AI_MODEL'], timezone=timezone,
                    ai_enabled=values.get('AI_ENABLED', 'true').lower() == 'true')
