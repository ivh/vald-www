"""
Pytest configuration for VALD tests.
"""
import os
import django
from pathlib import Path

import pytest

# Force the correct settings module
os.environ['DJANGO_SETTINGS_MODULE'] = 'vald_web.settings'


def pytest_report_header(config):
    """Say out loud when the binary tests are not being run.

    They are deselected by default (addopts in pyproject) because they carry the
    whole runtime, and they are also the only tests that catch the Fortran
    drifting away from what the app writes and parses. A count of "deselected"
    at the end of a green run is far too quiet for that.
    """
    if 'not vald_binaries' in (config.option.markexpr or ''):
        return ('vald: Fortran binary tests are DESELECTED. '
                'Run them with: pytest -m vald_binaries')
    return None


def pytest_configure(config):
    """Configure Django before running tests."""
    django.setup()
    from django.test.utils import override_settings

    # PBKDF2 is deliberately slow, ~100 ms per hash. The suite creates and logs
    # in hundreds of users, which made password hashing three quarters of its
    # runtime; nothing here tests the hasher itself. override_settings rather
    # than assignment so the cached hasher list is rebuilt.
    override_settings(
        PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
    ).enable()

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
def wait_for_worker(monkeypatch):
    """Run the request worker, and wait for the thread rather than poll its row.

    Polling Request.status while the worker writes to that row is what made these
    tests fail one run in four: the test database locks per table (see
    no_background_worker), so a read colliding with the worker's save raises
    SQLITE_LOCKED at the worker and it records the job as failed. Joining the
    thread keeps the test's reads off the table until the work is finished.
    """
    import threading

    workers = []

    def start(target):
        worker = threading.Thread(target=target, daemon=True)
        workers.append(worker)
        worker.start()

    monkeypatch.setattr('vald.views.start_background_worker', start)

    def wait(timeout=10):
        assert workers, 'no background worker was started'
        for worker in workers:
            worker.join(timeout)
            assert not worker.is_alive(), f'worker still running after {timeout}s'

    return wait


@pytest.fixture
def no_background_worker(monkeypatch):
    """Submit requests without starting the worker thread.

    For tests that assert what a submission stored, not what running it produced.
    The worker writes from another thread, which inside a transaction-wrapped
    test cannot get the lock - Django's sqlite test database is shared-cache
    in-memory, and a table locked by the test's transaction raises SQLITE_LOCKED,
    which no busy timeout retries. That filled the suite's output with
    'database table is locked' tracebacks from work the test never wanted.
    """
    monkeypatch.setattr('vald.views.start_background_worker', lambda target: None)


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
