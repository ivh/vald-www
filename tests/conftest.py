"""
Pytest configuration for VALD tests.
"""
import os
import django
from pathlib import Path

# Force the correct settings module
os.environ['DJANGO_SETTINGS_MODULE'] = 'vald_web.settings'


def pytest_configure():
    """Configure Django before running tests."""
    django.setup()
