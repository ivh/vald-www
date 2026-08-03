"""
Pytest configuration for VALD tests.
"""
import os
import django
from pathlib import Path

import pytest

# Force the correct settings module
os.environ['DJANGO_SETTINGS_MODULE'] = 'vald_web.settings'


def pytest_configure(config):
    """Configure Django before running tests."""
    django.setup()
    config.addinivalue_line(
        'markers',
        'vald_binaries: needs a populated VALD_HOME (Fortran binaries and data)',
    )


@pytest.fixture(autouse=True)
def clear_rate_limit_buckets():
    """Rate-limit state is shared (filebased cache), so it leaks between tests.

    Without this, a test that logs in several times trips the 5/m login limit and
    later tests fail for reasons unrelated to what they assert.
    """
    from django.core.cache import cache
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def vald_home():
    """Path to a usable VALD installation, or skip."""
    from django.conf import settings
    home = Path(settings.VALD_HOME)
    if not (home / 'bin' / 'preselect5').exists():
        pytest.skip(f'no VALD binaries under {home}')
    return home


@pytest.fixture
def approved_user(db):
    """An approved user with a known password."""
    from vald.models import User, UserEmail
    user = User.objects.create(name='Test User', is_active=True)
    user.set_password('pw-for-testing-123')
    user.save()
    UserEmail.objects.create(user=user, email='test@example.com', is_primary=True)
    return user


@pytest.fixture
def logged_in_client(approved_user):
    """A Client with an authenticated session."""
    from django.test import Client
    client = Client()
    client.post('/login/', {'user': 'test@example.com',
                            'password': 'pw-for-testing-123'})
    assert 'user_id' in client.session, 'fixture failed to log in'
    return client
