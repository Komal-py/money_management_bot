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
