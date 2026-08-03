"""Rate-limit key helpers."""
import ipaddress

from django.conf import settings


def _remote_addr(request):
    return request.META.get('REMOTE_ADDR', '') or 'unknown'


def client_ip(group, request):
    """Return the requesting client's IP for use as a rate-limit bucket key.

    django-ratelimit's RATELIMIT_IP_META_KEY reads a header verbatim, which is
    unsafe for X-Forwarded-For: a reverse proxy *appends* to that header, so a
    client sending its own value gets "<spoofed>, <real peer>" and can mint a
    fresh bucket per request simply by varying the prefix.

    The trustworthy element is the rightmost one, which the nearest proxy wrote
    itself - true both for nginx's $proxy_add_x_forwarded_for (append) and for a
    proxy that overwrites the header with $remote_addr.
    """
    header = getattr(settings, 'RATELIMIT_CLIENT_IP_HEADER', 'HTTP_X_FORWARDED_FOR')
    forwarded = request.META.get(header, '')

    if not forwarded:
        return _remote_addr(request)

    candidate = forwarded.split(',')[-1].strip()
    try:
        parsed = ipaddress.ip_address(candidate)
    except ValueError:
        # Malformed header - fall back rather than key the cache on junk
        return _remote_addr(request)

    # Normalise so that "::ffff:1.2.3.4" and "1.2.3.4" share a bucket
    mapped = getattr(parsed, 'ipv4_mapped', None)
    return str(mapped or parsed)


def session_user(group, request):
    """Bucket by logged-in user, falling back to client IP.

    This app authenticates via session['user_id'] rather than django.contrib.auth,
    so django-ratelimit's built-in 'user' key (which reads request.user) would
    lump every visitor into the same anonymous bucket.
    """
    user_id = request.session.get('user_id')
    if user_id:
        return f'user:{user_id}'
    return f'ip:{client_ip(group, request)}'


def submit_rate(group, request):
    """Submission rate, read from settings so it can be tuned without a deploy."""
    return getattr(settings, 'VALD_SUBMIT_RATE', '120/h')
