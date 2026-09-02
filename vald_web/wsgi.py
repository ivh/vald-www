"""
WSGI config for vald_web project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "vald_web.settings_deploy")

application = get_wsgi_application()

# After the application is built, so the app registry and the database are
# ready. Here rather than in AppConfig.ready() because only the process that
# owns the job queue may reconcile requests, and this file is loaded by gunicorn
# alone - not by management commands or the test suite. It never raises; see
# vald.startup.run.
from vald import startup  # noqa: E402  (must follow get_wsgi_application)

startup.run()
