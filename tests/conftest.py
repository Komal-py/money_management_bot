"""Shared test database settings; owner credentials never appear in test output."""
import os
from pathlib import Path

from dotenv import dotenv_values


def pytest_configure(config):
    if not os.environ.get('BUDGET_TEST_DATABASE_URL'):
        values = dotenv_values(Path(__file__).resolve().parents[1] / '.env', interpolate=False)
        url = values.get('BUDGET_TEST_DATABASE_URL')
        if url:
            os.environ['BUDGET_TEST_DATABASE_URL'] = url
    os.environ.setdefault('BUDGET_TEST_SCHEMA', 'test_coordinator')
