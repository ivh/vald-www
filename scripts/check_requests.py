#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'vald_web.settings')
django.setup()

from vald.models import Request
from django.db.models import Count
from django.utils import timezone
from datetime import timedelta

# Get requests from last 5 minutes
recent = Request.objects.filter(created_at__gte=timezone.now()-timedelta(minutes=5))
print(f"Total requests in last 5 min: {recent.count()}")

print("\nStatus breakdown:")
for status_dict in recent.values('status').annotate(count=Count('status')):
    print(f"  {status_dict['status']}: {status_dict['count']}")

print("\nRecent requests (last 10):")
for req in recent.order_by('-created_at')[:10]:
    print(f"  {req.created_at.strftime('%H:%M:%S')} - {req.request_type} - {req.status} - {req.uuid}")
