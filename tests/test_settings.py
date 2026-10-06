from pathlib import Path

import pytest


def test_settings_requires_credentials_without_printing_secrets():
    from budget_bot.settings import load_settings
    with pytest.raises(ValueError, match='Missing required'):
        load_settings(Path('nonexistent.env'), {})


def test_settings_load_exact_project_credentials(tmp_path):
    from budget_bot.settings import load_settings
    path = tmp_path / '.env'
    path.write_text('DATABASE_URL=postgresql+psycopg://u:secret@localhost/db\n'
                    'TELEGRAM_BOT_TOKEN=123:token\nTELEGRAM_ADMIN_USER_ID=12345\n'
                    'AI_API_KEY=secret-key\nAI_BASE_URL=https://example.test/openai/v1\n'
                    'AI_MODEL=test-model\n')
    settings = load_settings(path, {})
    assert settings.telegram_admin_user_id == 12345
    assert settings.ai_model == 'test-model'
    assert 'secret' not in repr(settings)
    assert '123:token' not in repr(settings)


def runtime_env(**overrides):
    return {'DATABASE_URL': 'postgresql+psycopg://u:secret@localhost/db',
            'TELEGRAM_BOT_TOKEN': '123:TEST', 'TELEGRAM_ADMIN_USER_ID': '12345',
            'AI_ENABLED': 'false', **overrides}


def test_disabled_ai_needs_no_provider_settings():
    from budget_bot.settings import load_settings
    settings = load_settings(None, runtime_env())
    assert settings.ai_enabled is False
    assert settings.ai_api_key is None
    assert settings.ai_base_url is None
    assert settings.ai_model is None


def test_enabled_ai_still_requires_explicit_provider_settings():
    from budget_bot.settings import load_settings
    with pytest.raises(ValueError, match='AI_API_KEY, AI_BASE_URL, AI_MODEL'):
        load_settings(None, runtime_env(AI_ENABLED='true'))


@pytest.mark.parametrize('value', ['flase', 'yes', '', '0'])
def test_ai_boolean_is_not_silently_disabled(value):
    from budget_bot.settings import load_settings
    with pytest.raises(ValueError, match='AI_ENABLED'):
        load_settings(None, runtime_env(AI_ENABLED=value))


def test_schemas_timezone_and_timeout_are_loaded_explicitly():
    from budget_bot.settings import load_settings
    settings = load_settings(None, runtime_env(BUDGET_SCHEMA='test_w1',
                                             BUDGET_WORKFLOW_SCHEMA='test_w1',
                                             BUDGET_TIMEZONE='Europe/London', AI_TIMEOUT='12'))
    assert settings.schema_name == settings.workflow_schema == 'test_w1'
    assert settings.timezone == 'Europe/London'
    assert settings.ai_timeout == 12


@pytest.mark.parametrize('overrides', [
    {'BUDGET_SCHEMA': 'budget;public'}, {'BUDGET_WORKFLOW_SCHEMA': 'PUBLIC'},
    {'DATABASE_URL': 'sqlite:///secret'}, {'TELEGRAM_ADMIN_USER_ID': '-1'},
    {'BUDGET_TIMEZONE': 'not/a/timezone'}, {'AI_TIMEOUT': '0'},
])
def test_invalid_runtime_settings_are_rejected_without_values(overrides):
    from budget_bot.settings import load_settings
    with pytest.raises(ValueError) as error:
        load_settings(None, runtime_env(**overrides))
    assert 'secret' not in str(error.value)


def test_migrations_need_database_and_schema_not_bot_or_ai_credentials():
    from budget_bot.settings import load_migration_settings
    settings = load_migration_settings(None, {
        'BUDGET_DATABASE_URL': 'postgresql+psycopg://u:secret@localhost/db',
        'BUDGET_SCHEMA': 'test_w1',
    })
    assert settings.schema_name == 'test_w1'
    assert 'secret' not in repr(settings)


def test_migration_database_alias_conflict_is_rejected():
    from budget_bot.settings import load_migration_settings
    with pytest.raises(ValueError, match='Conflicting database settings'):
        load_migration_settings(None, {
            'DATABASE_URL': 'postgresql+psycopg://u:secret@localhost/db',
            'BUDGET_DATABASE_URL': 'postgresql+psycopg://u:other@localhost/db',
        })
