import itertools
import math
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db.models import (Count, DateField, DateTimeField, DurationField,
                              ExpressionWrapper, F, Min, Q)
from django.db.models.functions import TruncDay, TruncHour, TruncMonth, TruncYear
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.conf import settings
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import urlencode
from django import forms
from .models import Request, User, UserEmail, UserPreferences, Linelist, Config, ConfigLinelist


def get_queue_stats():
    """Get current job queue statistics from database."""
    from django.utils import timezone
    from datetime import timedelta
    from .models import Request
    
    cutoff = timezone.now() - timedelta(minutes=30)
    pending_count = Request.objects.filter(
        status__in=['pending', 'processing'],
        created_at__gte=cutoff
    ).count()
    max_queue_size = getattr(settings, 'VALD_MAX_QUEUE_SIZE', 10)
    max_threads = getattr(settings, 'VALD_MAX_THREADS', 2)
    return {
        'queue_size': pending_count,
        'max_queue_size': max_queue_size,
        'max_threads': max_threads,
    }


# A password of '' means the same as NULL here, so every query about
# activation state has to say so. Kept as one expression rather than repeated
# lookups, which had let the "pending approval" filter and column disagree.
NO_PASSWORD = Q(password__isnull=True) | Q(password='')
PENDING_APPROVAL = Q(is_active=False) & NO_PASSWORD


@staff_member_required
def admin_help(request):
    """Operator reference for the states these admin screens expose.

    Wired into vald_web/urls.py ahead of admin.site.urls rather than into a
    ModelAdmin, since it spans models. Every number is read from settings or the
    live queryset, so the page cannot drift the way a separate wiki page would.
    """
    users = User.objects.all()
    changelist = reverse('admin:vald_user_changelist')
    account_states = [
        {
            'state': 'Pending approval',
            'is_active': False,
            'has_password': False,
            'how': 'Self-registered through the public form; no admin has acted on it yet.',
            'sees': 'Cannot log in: "awaiting approval by the VALD administrator". '
                    'A password reset request is answered as if the address were unknown, '
                    'so approval cannot be routed around.',
            'do': 'Approve and send activation email, or Reject.',
            'count': users.filter(PENDING_APPROVAL).count(),
            'query': '?pending_approval=yes',
        },
        {
            'state': 'Approved, not activated',
            'is_active': True,
            'has_password': False,
            'how': 'Approved by an admin, or had its password cleared, but the holder '
                   'has not set one yet.',
            'sees': 'Any login attempt on this address mails a fresh activation link, '
                    'whether or not a password was typed. Clicking it sets the password.',
            'do': 'Nothing - waiting on the user. Resend by having them try to log in.',
            'count': users.filter(Q(is_active=True) & NO_PASSWORD).count(),
            'query': '?is_active__exact=1&has_password=no',
        },
        {
            'state': 'Active',
            'is_active': True,
            'has_password': True,
            'how': 'Normal working account.',
            'sees': 'Logs in and submits requests.',
            'do': 'Nothing. Untick is_active to suspend, or clear the password to '
                  'force re-activation.',
            'count': users.filter(is_active=True).exclude(NO_PASSWORD).count(),
            'query': '?is_active__exact=1&has_password=yes',
        },
        {
            'state': 'Suspended',
            'is_active': False,
            'has_password': True,
            'how': 'Was working, then is_active was unticked.',
            'sees': 'Cannot log in: "account has been deactivated, contact the administrator". '
                    'Any session they already had stops working on their next click - '
                    'sessions are revalidated against this flag on every request.',
            'do': 'Tick is_active to reinstate; the old password still works. '
                  'Reject does not touch these - delete deliberately if that is the intent.',
            'count': users.filter(is_active=False).exclude(NO_PASSWORD).count(),
            'query': '?is_active__exact=0&has_password=yes',
        },
    ]
    for row in account_states:
        row['url'] = changelist + row['query']

    limits = [
        ('VALD_TOKEN_MAX_AGE_DAYS', settings.VALD_TOKEN_MAX_AGE_DAYS,
         'Lifetime of activation and password-reset links. Expired links send the '
         'user back to the login form for a new one.'),
        ('VALD_RESULT_RETENTION_DAYS', settings.VALD_RESULT_RETENTION_DAYS,
         'How long result files survive before the cleanup timer removes them. '
         'The Request row stays, and reports the results as expired.'),
        ('VALD_MAX_THREADS', settings.VALD_MAX_THREADS,
         'Jobs run in parallel, as threads inside the single gunicorn process. '
         'Everything else queues.'),
        ('VALD_MAX_QUEUE_SIZE', settings.VALD_MAX_QUEUE_SIZE,
         'Queued jobs before new submissions are refused site-wide.'),
        ('VALD_MAX_REQUESTS_PER_USER', settings.VALD_MAX_REQUESTS_PER_USER,
         'Per-user cap on queued jobs, so one user cannot fill the queue alone.'),
        ('VALD_MAX_LINES_PER_REQUEST', settings.VALD_MAX_LINES_PER_REQUEST,
         'Upper bound on the line count a single extraction may ask for.'),
        ('VALD_JOB_TIMEOUT', settings.VALD_JOB_TIMEOUT,
         'Seconds before a running Fortran job is killed and marked failed.'),
        ('VALD_SUBMIT_RATE', settings.VALD_SUBMIT_RATE,
         'Rate limit on request submission, per logged-in user.'),
        ('VALD_ADMIN_LOGIN_RATE', getattr(settings, 'VALD_ADMIN_LOGIN_RATE', '10/h'),
         'Rate limit on this admin login form, per client IP. Django does not '
         'throttle it on its own; exceeding this returns 403.'),
        ('SESSION_COOKIE_AGE', settings.SESSION_COOKIE_AGE,
         'Seconds an idle login survives. Sessions are also revalidated against '
         'the database every request, so suspending an account or changing a '
         'password ends them at once regardless of this.'),
        ('VALD_MAX_EMAIL_ATTACH_BYTES', settings.VALD_MAX_EMAIL_ATTACH_BYTES,
         'Results larger than this are not attached to the completion email; '
         'the download links in the body are the fallback.'),
        ('VALD_QUEUE_FULL_COOLDOWN', settings.VALD_QUEUE_FULL_COOLDOWN,
         'Minimum gap between "job queue full" alerts to the webmaster.'),
        ('VALD_ADMIN_EMAIL', settings.VALD_ADMIN_EMAIL,
         'Recipient of new-registration and queue-full notifications.'),
        ('SITE_URL', settings.SITE_URL,
         'Base URL used to build activation and reset links in email. Wrong value '
         'here means links that go nowhere.'),
    ]

    # Deployment paths come from the running instance, and the unit list from
    # what is actually in the checkout, so adding a timer shows up here without
    # anyone remembering to edit this page.
    base_dir = Path(settings.BASE_DIR)
    unit_files = sorted(
        p.name for p in base_dir.iterdir()
        if p.suffix in ('.service', '.timer')
    )

    # Personal-configuration split. One GROUP BY over the junction table; the
    # alternative is 700-odd per-config queries, which is what an obvious
    # implementation would do.
    default_config = Config.objects.filter(user__isnull=True, is_default=True).first()
    default_ids = set()
    if default_config:
        default_ids = set(default_config.configlinelist_set.values_list(
            'linelist_id', flat=True))
    personal_total = Config.objects.filter(user__isnull=False).count()
    behind = 0
    if default_ids:
        rows = (ConfigLinelist.objects
                .filter(config__user__isnull=False, linelist_id__in=default_ids)
                .values('config_id').annotate(n=Count('linelist_id')))
        behind = sum(1 for row in rows if row['n'] < len(default_ids))

    # The system configs as the request forms' menu shows them. Live, so an
    # imported variant appears here without anyone editing the help page - and
    # the enabled counts are what tells an operator which .cfg went where.
    enabled_per_config = dict(
        ConfigLinelist.objects
        .filter(config__user__isnull=True, is_enabled=True)
        .values_list('config_id')
        .annotate(n=Count('id'))
    )
    system_configs = [
        {'name': c.name, 'slug': c.slug, 'is_default': c.is_default,
         'enabled': enabled_per_config.get(c.id, 0),
         'total': c.configlinelist_set.count()}
        for c in Config.objects.filter(user__isnull=True).order_by('-is_default', 'name')
    ]

    context = {
        **admin.site.each_context(request),
        'title': 'Admin help',
        'system_configs': system_configs,
        'personal_total': personal_total,
        'personal_behind': behind,
        'tracking_default': users.count() - personal_total,
        'default_linelist_count': len(default_ids),
        'account_states': account_states,
        'user_total': users.count(),
        'limits': limits,
        'token_max_age_days': settings.VALD_TOKEN_MAX_AGE_DAYS,
        'retention_days': settings.VALD_RESULT_RETENTION_DAYS,
        'queue_stats': get_queue_stats(),
        'user_changelist': changelist,
        'base_dir': base_dir,
        'unit_files': unit_files,
        'settings_module': os.environ.get('DJANGO_SETTINGS_MODULE', '(default)'),
    }
    return render(request, 'admin/vald/help.html', context)


STATS_PERIODS = ('hour', 'day', 'month', 'year')

# An hourly view over a year would be 8700 bars in 950px, i.e. sub-pixel rects
# and a template loop long enough to notice. Truncate instead, and say so.
STATS_MAX_BUCKETS = 400

_TRUNC = {'hour': TruncHour, 'day': TruncDay,
          'month': TruncMonth, 'year': TruncYear}

# strftime for the bar's hover text and for the axis tick under it. The axis one
# is short because only every Nth tick is drawn and they must not collide.
_BUCKET_FORMATS = {
    'hour': ('%Y-%m-%d %H:%M', '%H:%M'),
    'day': ('%Y-%m-%d', '%d %b'),
    'month': ('%B %Y', '%b %y'),
    'year': ('%Y', '%Y'),
}

# Hour buckets are naive local datetimes; the coarser ones are plain dates. Both
# are compared against what the database hands back, so the two have to agree -
# see _bucket_key.
_HOUR = 'hour'


def _as_datetime(value):
    return value if isinstance(value, datetime) else datetime.combine(value, time.min)


def _bucket_key(value):
    """Normalise a truncated value from the database into a bucket key.

    TruncHour yields an aware datetime, the coarser Trunc* a date. Converting
    the datetime to local time first matters: the truncation already happened in
    Europe/Stockholm, but the value comes back in UTC, so comparing it raw would
    file 01:00 local under 00:00.
    """
    if isinstance(value, datetime):
        return timezone.localtime(value).replace(tzinfo=None)
    return value


def _floor_to_period(d, period):
    if period == _HOUR:
        return _as_datetime(d).replace(minute=0, second=0, microsecond=0)
    if period == 'month':
        return d.replace(day=1)
    if period == 'year':
        return date(d.year, 1, 1)
    return d


def _advance(d, period):
    """Next bucket start. Calendar arithmetic, not 30- or 365-day steps."""
    if period == _HOUR:
        return d + timedelta(hours=1)
    if period == 'month':
        return date(d.year + d.month // 12, d.month % 12 + 1, 1)
    if period == 'year':
        return date(d.year + 1, 1, 1)
    return d + timedelta(days=1)


def _bucket_starts(frm, to, period):
    """Every bucket start covering [frm, to], including the ones with no rows.

    SQL only returns buckets that have rows. Plotting those alone would put
    March next to July at the same spacing and quietly misreport the shape.

    `to` is an inclusive date, so the hourly limit is the end of that day rather
    than its midnight.
    """
    cur = _floor_to_period(frm, period)
    limit = datetime.combine(to, time.max) if period == _HOUR else to
    out = []
    while cur <= limit and len(out) < STATS_MAX_BUCKETS:
        out.append(cur)
        cur = _advance(cur, period)
    return out, cur <= limit


def _parse_date(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _default_window(period, today):
    """The enclosing interval one level up: hours fill a day, days fill a month,
    months fill a year. Picking a grouping is nearly always asking "and over
    what", and this is the answer that needs no second click."""
    if period == _HOUR:
        return today, today
    if period == 'day':
        return today.replace(day=1), today
    if period == 'month':
        return date(today.year, 1, 1), today
    earliest = Request.objects.aggregate(first=Min('created_at'))['first']
    first_year = timezone.localtime(earliest).year if earliest else today.year - 4
    return date(first_year, 1, 1), today


def _parse_stats_period(request):
    """(period, from_date, to_date) from the querystring, never raising.

    A hand-edited or stale URL must render the default window rather than a 500,
    so every unparseable part falls back independently.
    """
    period = request.GET.get('period', 'month')
    if period not in STATS_PERIODS:
        period = 'month'

    today = timezone.localdate()
    frm = _parse_date(request.GET.get('from'))
    to = _parse_date(request.GET.get('to'))
    if frm is None or to is None:
        default_from, default_to = _default_window(period, today)
        frm = frm if frm is not None else default_from
        to = to if to is not None else default_to
    if frm > to:
        frm, to = to, frm
    return period, frm, to


def _y_ticks(vmax):
    """Gridline values 0..top in a 1/2/5 step, so every label is a whole number."""
    vmax = max(1, math.ceil(vmax))
    for mag in itertools.count(0):
        for m in (1, 2, 5):
            step = m * 10 ** mag
            if vmax <= step * 5:
                top = step * math.ceil(vmax / step)
                return top, [step * i for i in range(top // step + 1)]


# Turnaround is completed_at - created_at, so it includes queue wait. Plotting it
# in raw seconds gives axis labels like 0/2000/4000; picking one unit for the
# whole chart keeps the ticks readable.
_DURATION_UNITS = ((3600, 'hours'), (60, 'minutes'), (1, 'seconds'))


def _duration_unit(vmax):
    for divisor, name in _DURATION_UNITS:
        if vmax >= divisor * 2:
            return divisor, name
    return 1, 'seconds'


def _format_duration(seconds):
    sign, seconds = ('-', -seconds) if seconds < 0 else ('', seconds)
    return sign + _format_positive_duration(seconds)


def _format_positive_duration(seconds):
    if seconds < 1:
        return f'{seconds * 1000:.0f} ms'
    if seconds < 60:
        return f'{seconds:.1f} s'
    if seconds < 3600:
        return f'{int(seconds // 60)}m {int(seconds % 60):02d}s'
    return f'{int(seconds // 3600)}h {int(seconds % 3600 // 60):02d}m'


def _percentile(values, q):
    """Nearest-rank percentile of an already-sorted list.

    statistics.quantiles needs at least two points and interpolates; these
    samples are small enough that an interpolated p90 between two runs would be
    a number no request ever took.
    """
    if not values:
        return None
    rank = max(1, math.ceil(q / 100 * len(values)))
    return values[rank - 1]


# Canvas geometry. Everything is computed here rather than in the template:
# arithmetic in template tags is where this kind of page becomes unreadable.
_SVG_W, _SVG_H = 1000, 280
_PLOT_L, _PLOT_R, _PLOT_T, _PLOT_B = 46, 8, 12, 34
_MAX_X_LABELS = 15


def _chart(buckets, values, period, tooltips):
    """Bar geometry for one series.

    `values` may hold None for a bucket with nothing to say, which is not the
    same as zero: no completed request in a month is a gap in the turnaround
    chart, whereas no request at all is a genuine zero in the count chart.
    """
    tick_fmt = _BUCKET_FORMATS[period][1]
    if period == _HOUR and buckets[0].date() != buckets[-1].date():
        # Bare clock times repeat once the window is longer than a day, and only
        # every Nth tick is drawn, so which day it is stops being inferable.
        tick_fmt = '%d %b %H'
    plot_w = _SVG_W - _PLOT_L - _PLOT_R
    plot_h = _SVG_H - _PLOT_T - _PLOT_B
    baseline = _PLOT_T + plot_h

    present = [v for v in values if v is not None]
    top, ticks = _y_ticks(max(present) if present else 0)
    step = plot_w / len(buckets)
    bar_w = min(step * 0.8, 60)
    label_every = math.ceil(len(buckets) / _MAX_X_LABELS)

    bars, labels = [], []
    for i, (bucket, value, tooltip) in enumerate(zip(buckets, values, tooltips)):
        centre = _PLOT_L + i * step + step / 2
        height = plot_h * (value or 0) / top
        bars.append({
            'x': round(centre - bar_w / 2, 2),
            'y': round(baseline - height, 2),
            'w': round(bar_w, 2),
            'h': round(height, 2),
            'value': value,
            'title': tooltip,
            'empty': not value,
        })
        if i % label_every == 0:
            labels.append({'x': round(centre, 2), 'text': bucket.strftime(tick_fmt)})

    return {
        'width': _SVG_W,
        'height': _SVG_H,
        'baseline': baseline,
        'plot_left': _PLOT_L,
        'plot_right': _SVG_W - _PLOT_R,
        'label_y': baseline + 18,
        'bars': bars,
        'x_labels': labels,
        'y_ticks': [
            {'value': v, 'y': round(baseline - plot_h * v / top, 2)}
            for v in ticks
        ],
    }


def _turnaround(qs, buckets, period):
    """Turnaround stats for the completed requests in the window.

    Only status='complete' counts. A failed request either died immediately or
    sat until VALD_JOB_TIMEOUT and was killed, and a pile of identical
    timeout-length rows would drag the median towards the timeout and describe
    nothing anyone can act on. Failures are counted on the page separately.

    The rows are pulled into Python rather than aggregated in SQL: SQLite has no
    median, and two datetime columns over one window is cheap at VALD's volume.
    If the table ever gets big enough for that to hurt, cache the whole page
    before rewriting this.
    """
    title_fmt = _BUCKET_FORMATS[period][0]
    output_field = DateTimeField() if period == _HOUR else DateField()
    rows = (qs
            .filter(status='complete', completed_at__isnull=False)
            .annotate(bucket=_TRUNC[period]('created_at', output_field=output_field))
            .values_list('bucket', 'id', 'request_type', 'created_at', 'completed_at'))

    per_bucket, slowest = {}, []
    for bucket, pk, request_type, created, completed in rows:
        bucket = _bucket_key(bucket)
        seconds = (completed - created).total_seconds()
        if seconds < 0:
            # Only reachable via a clock step or a hand-edited row. Counting it
            # would pull the median down for a request that took real time.
            continue
        per_bucket.setdefault(bucket, []).append(seconds)
        slowest.append({'pk': pk, 'type': request_type, 'seconds': seconds,
                        'when': created})

    everything = sorted(s for values in per_bucket.values() for s in values)
    if not everything:
        return None

    medians = []
    for bucket in buckets:
        values = sorted(per_bucket.get(bucket, []))
        medians.append(_percentile(values, 50) if values else None)

    divisor, unit = _duration_unit(max((m for m in medians if m is not None), default=0))
    tooltips = [
        f'{b.strftime(title_fmt)}: median {_format_duration(m)} '
        f'over {len(per_bucket.get(b, []))}'
        if m is not None else f'{b.strftime(title_fmt)}: nothing completed'
        for b, m in zip(buckets, medians)
    ]

    slowest.sort(key=lambda row: row['seconds'], reverse=True)
    for row in slowest[:10]:
        row['duration'] = _format_duration(row['seconds'])
        row['url'] = reverse('admin:vald_request_change', args=[row['pk']])

    return {
        'count': len(everything),
        'median': _format_duration(_percentile(everything, 50)),
        'p90': _format_duration(_percentile(everything, 90)),
        'worst': _format_duration(everything[-1]),
        'unit': unit,
        'chart': _chart(buckets, [None if m is None else m / divisor for m in medians],
                        period, tooltips),
        'slowest': slowest[:10],
    }


@staff_member_required
def admin_stats(request):
    """Request activity over time, plus who is generating it.

    Wired into vald_web/urls.py next to admin_help for the same reason: it is a
    report over Request, User and time rather than a view of one model, and the
    period lives in the querystring so a particular window can be linked to.
    """
    period, frm, to = _parse_stats_period(request)
    buckets, truncated = _bucket_starts(frm, to, period)

    # The window comes from the buckets rather than the other way round, so that
    # a period flooring to the start of its month, or a range cut short by
    # STATS_MAX_BUCKETS, moves the totals and the leaderboard with the bars
    # instead of leaving them describing a wider span than the chart shows.
    start = timezone.make_aware(_as_datetime(buckets[0]))
    end = timezone.make_aware(_as_datetime(_advance(buckets[-1], period)))
    frm = start.date()
    to = (end - timedelta(seconds=1)).date()
    qs = Request.objects.filter(created_at__gte=start, created_at__lt=end)

    # Hour truncation has to stay a datetime; the coarser ones become dates,
    # which is what buckets holds. Either way the truncation happens in the
    # active timezone, so a request at 00:30 Stockholm counts as that Stockholm
    # hour and day rather than the previous UTC one.
    output_field = DateTimeField() if period == _HOUR else DateField()
    rows = (qs
            .annotate(bucket=_TRUNC[period]('created_at', output_field=output_field))
            .values('bucket')
            .annotate(n=Count('id')))
    by_bucket = {_bucket_key(row['bucket']): row['n'] for row in rows}
    counts = [by_bucket.get(b, 0) for b in buckets]

    # Carry the window into the changelist links. str() of an aware datetime is
    # the same form Django's own DateFieldListFilter emits, so the filter sidebar
    # shows the range as selected instead of ignoring it.
    request_changelist = reverse('admin:vald_request_changelist')
    period_filter = {'created_at__gte': str(start), 'created_at__lt': str(end)}

    def changelist(**extra):
        return f'{request_changelist}?{urlencode({**period_filter, **extra})}'

    leaders = []
    for row in (qs.values('user_id', 'user__name')
                  .annotate(n=Count('id'))
                  .order_by('-n', 'user__name')[:20]):
        leaders.append({
            'name': row['user__name'] or 'Unknown',
            'count': row['n'],
            # Request.user is nullable. An empty user__id__exact makes the
            # changelist bail out to ?e=1, so the orphan row gets no links.
            'user_url': (reverse('admin:vald_user_change', args=[row['user_id']])
                         if row['user_id'] else None),
            'requests_url': (changelist(user__id__exact=row['user_id'])
                             if row['user_id'] else None),
        })

    by_type = [
        {'type': row['request_type'],
         'count': row['n'],
         'url': changelist(request_type=row['request_type'])}
        for row in qs.values('request_type').annotate(n=Count('id')).order_by('-n')
    ]

    counts_by_status = {
        row['status']: row['n']
        for row in qs.values('status').annotate(n=Count('id'))
    }
    busiest = max(zip(counts, buckets), default=(0, None))

    turnaround = _turnaround(qs, buckets, period)

    context = {
        **admin.site.each_context(request),
        'title': 'Request statistics',
        'period': period,
        'periods': STATS_PERIODS,
        'date_from': frm.isoformat(),
        'date_to': to.isoformat(),
        'truncated': truncated,
        'max_buckets': STATS_MAX_BUCKETS,
        'chart': (_chart(buckets, counts, period,
                         [f'{b.strftime(_BUCKET_FORMATS[period][0])}: {n}'
                          for b, n in zip(buckets, counts)])
                  if buckets else None),
        'turnaround': turnaround,
        'leaders': leaders,
        'by_type': by_type,
        'total': sum(counts_by_status.values()),
        'user_count': qs.values('user_id').distinct().count(),
        'complete_count': counts_by_status.get('complete', 0),
        'failed_count': counts_by_status.get('failed', 0),
        'busiest_count': busiest[0],
        'busiest_label': (busiest[1].strftime(_BUCKET_FORMATS[period][0])
                          if busiest[1] and busiest[0] else None),
        'all_requests_url': changelist(),
        'help_url': reverse('admin_help'),
    }
    return render(request, 'admin/vald/stats.html', context)


class HasPasswordFilter(admin.SimpleListFilter):
    title = 'has password'
    parameter_name = 'has_password'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Yes'),
            ('no', 'No'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.exclude(NO_PASSWORD)
        if self.value() == 'no':
            return queryset.filter(NO_PASSWORD)


class PendingApprovalFilter(admin.SimpleListFilter):
    title = 'pending approval'
    parameter_name = 'pending_approval'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Yes'),
            ('no', 'No'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(PENDING_APPROVAL)
        if self.value() == 'no':
            return queryset.exclude(PENDING_APPROVAL)


class UserChangeForm(forms.ModelForm):
    """Custom form for User admin with proper password display"""
    password = ReadOnlyPasswordHashField(
        label="Password",
        help_text=(
            "Raw passwords are not stored, so there is no way to see this "
            "user's password, but you can change the password using "
            '<a href="../password/">this form</a>.'
        ),
    )

    class Meta:
        model = User
        fields = '__all__'


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'request_type', 'get_user_email', 'status', 'created_at',
                    'duration', 'has_output')
    list_filter = ('status', 'request_type', 'created_at')
    search_fields = ('uuid', 'user__name', 'user__emails__email')
    readonly_fields = ('uuid', 'created_at', 'updated_at', 'rerun_link')
    fieldsets = (
        ('Request Information', {
            'fields': ('uuid', 'request_type', 'user', 'rerun_link')
        }),
        ('Parameters', {
            'fields': ('parameters',),
            'classes': ('collapse',)
        }),
        ('Status', {
            'fields': ('status', 'queue_position', 'output_file', 'error_message')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'completed_at')
        }),
    )

    def changelist_view(self, request, extra_context=None):
        """Add queue stats to the changelist view."""
        extra_context = extra_context or {}
        extra_context['queue_stats'] = get_queue_stats()
        return super().changelist_view(request, extra_context=extra_context)

    def get_queryset(self, request):
        # Annotated rather than computed per row so the column can be sorted on:
        # admin_order_field needs something the database can ORDER BY. Unfinished
        # requests get NULL, which SQLite sorts first ascending, last descending.
        return super().get_queryset(request).annotate(
            _duration=ExpressionWrapper(F('completed_at') - F('created_at'),
                                        output_field=DurationField()))

    @admin.display(description='Duration', ordering='_duration')
    def duration(self, obj):
        """Submission to completion, so queue wait is in here too."""
        if obj._duration is None:
            return '—'
        return _format_duration(obj._duration.total_seconds())

    def get_user_email(self, obj):
        """Display user's primary email"""
        return obj.user_email
    get_user_email.short_description = 'User Email'

    def has_output(self, obj):
        """Show if output file exists"""
        return obj.output_exists()
    has_output.boolean = True
    has_output.short_description = 'Output File'

    @admin.display(description='Rerun')
    def rerun_link(self, obj):
        """Open the front-end form for this request type, pre-filled from it.

        Submitting it is a new request owned by whoever the browser is logged in
        as on the front end, not an edit of this one. request_type is free text in
        the database, so a row from an older or mistyped type must not take the
        whole change page down with a NoReverseMatch.
        """
        if obj is None or not obj.pk:
            return '—'
        try:
            url = reverse(f'vald:{obj.request_type}')
        except NoReverseMatch:
            return format_html('<span>No form for request type “{}”.</span>', obj.request_type)
        return format_html(
            '<a class="button" href="{}?{}" target="_blank" rel="noopener">Rerun this request</a>'
            '<p class="help">Opens the {} form pre-filled with these parameters, in a new tab. '
            'Requires a front-end login in this browser; the new request will be owned by that '
            'account.</p>',
            url, urlencode({'modify': str(obj.uuid)}), obj.request_type)


class UserEmailInline(admin.TabularInline):
    model = UserEmail
    extra = 1
    fields = ('email', 'is_primary')


class UserPreferencesInline(admin.StackedInline):
    model = UserPreferences
    can_delete = False
    verbose_name_plural = 'Preferences'


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    form = UserChangeForm
    list_display = ('name', 'get_emails', 'has_password', 'is_active', 'is_pending', 'is_suspended', 'config_link', 'created_at')
    list_filter = ('is_active', HasPasswordFilter, PendingApprovalFilter, 'created_at')
    search_fields = ('name', 'affiliation', 'emails__email')
    readonly_fields = ('created_at', 'updated_at', 'activation_token')
    inlines = [UserEmailInline, UserPreferencesInline]
    actions = ['approve_and_send_activation', 'approve_without_email', 'clear_password',
               'suspend_users', 'reject_registration']
    fieldsets = (
        ('User Information', {
            'fields': ('name', 'affiliation', 'is_active')
        }),
        ('Authentication', {
            'fields': ('password', 'activation_token')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    change_form_template = 'admin/vald/user/change_form.html'

    # Buttons added to the change form by that template. Each dispatches to the
    # changelist action of the same name with a one-row queryset, so the two
    # entry points cannot drift apart.
    CHANGE_FORM_ACTIONS = {
        '_approve_send': 'approve_and_send_activation',
        '_approve_quiet': 'approve_without_email',
        '_clear_password': 'clear_password',
    }

    def response_change(self, request, obj):
        for field, action_name in self.CHANGE_FORM_ACTIONS.items():
            if field in request.POST:
                action = getattr(self, action_name)
                action(request, self.model.objects.filter(pk=obj.pk))
                return HttpResponseRedirect(request.get_full_path())
        return super().response_change(request, obj)

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path(
                '<id>/password/',
                self.admin_site.admin_view(self.user_change_password),
                name='vald_user_password_change',
            ),
            path(
                '<id>/config/',
                self.admin_site.admin_view(self.user_config),
                name='vald_user_config',
            ),
        ]
        return custom_urls + urls

    def user_config(self, request, id):
        """Read-only view of the linelist configuration a user's jobs actually use.

        Deliberately not the ConfigLinelist inline: that omits the nine rank
        weights, renders ~377 editable rows, and says nothing about which of them
        the user changed - which is the only part a support question is ever
        about. Built from the same persconfig functions the user-facing page
        calls, so what an admin sees is what the user sees.

        No POST handling: this is a window, not an editor. The inline remains for
        the rare case where something genuinely has to be changed by hand.
        """
        from django.contrib.admin.utils import unquote
        from .persconfig import (
            get_default_config, get_effective_config, get_linelists_for_display,
            get_modification_flags, linelists_added_since,
        )

        user = self.get_object(request, unquote(id))
        if user is None:
            raise self.model.DoesNotExist

        config, is_personal = get_effective_config(user)
        default_config = get_default_config()

        linelists = []
        if config:
            modifications = get_modification_flags(config, default_config)
            for entry in get_linelists_for_display(config):
                mod = modifications.get(entry['id'], {})
                entry['mod_comment'] = mod.get('is_enabled', False)
                entry['mod_flags'] = mod.get('ranks', [False] * 9)
                entry['any_modification'] = mod.get('any', False)
                linelists.append(entry)

        # Differences only by default: a personal config is ~377 rows of which a
        # handful are the user's, and scrolling for them is the whole problem
        # this page exists to solve.
        # ...except when there is no personal config, where "differences" is
        # empty by definition and the toggle would just be a click to nowhere.
        show_all = request.GET.get('all') == '1' or not is_personal
        changed = [e for e in linelists if e['any_modification']]
        shown = linelists if show_all else changed

        context = {
            **self.admin_site.each_context(request),
            'title': f'Configuration: {user.name}',
            'opts': self.model._meta,
            'vald_user': user,
            'config': config,
            'is_personal': is_personal,
            'snapshot_date': config.snapshot_date if is_personal else None,
            # Distinguishes "created here on that date" from "carried over from a
            # legacy file with that mtime" - otherwise a 2019 date on a row
            # written today reads as a bug.
            'snapshot_from_file': bool(config.snapshot_at) if is_personal else False,
            'added_since': linelists_added_since(config) if is_personal else [],
            'linelists': shown,
            'total_count': len(linelists),
            'changed_count': len(changed),
            'show_all': show_all,
        }
        return render(request, 'admin/vald/user_config.html', context)

    def config_link(self, obj):
        """Column linking to the read-only configuration view."""
        url = reverse('admin:vald_user_config', args=[obj.pk])
        return format_html('<a href="{}">View</a>', url)
    config_link.short_description = 'Configuration'

    def user_change_password(self, request, id, form_url=''):
        from django.contrib import messages
        from django.shortcuts import redirect, render
        from django.contrib.admin.utils import unquote

        user = self.get_object(request, unquote(id))
        if user is None:
            raise self.model.DoesNotExist

        if request.method == 'POST':
            password = request.POST.get('password1')
            password2 = request.POST.get('password2')

            if not password:
                messages.error(request, 'Password cannot be empty.')
            elif password != password2:
                messages.error(request, 'Passwords do not match.')
            else:
                # AUTH_PASSWORD_VALIDATORS, same as the activation and reset
                # forms. This path used to accept anything six characters long,
                # so the one password an admin sets by hand was the weakest the
                # site allowed.
                try:
                    validate_password(password, user)
                except DjangoValidationError as e:
                    for message in e.messages:
                        messages.error(request, message)
                else:
                    user.set_password(password)
                    user.save()
                    messages.success(request, f'Password changed successfully for {user.name}.')
                    return redirect('admin:vald_user_change', user.id)

        context = {
            'user': user,
            'opts': self.model._meta,
            'title': f'Change password: {user.name}',
        }
        return render(request, 'admin/vald/user_password_change.html', context)

    def get_emails(self, obj):
        """Display all email addresses for the user"""
        return ', '.join(obj.emails.values_list('email', flat=True))
    get_emails.short_description = 'Email Addresses'

    def has_password(self, obj):
        """Show if user has set a password"""
        return bool(obj.password)
    has_password.boolean = True
    has_password.short_description = 'Has Password'
    has_password.admin_order_field = 'password'

    def is_pending(self, obj):
        """Show if user is pending approval (inactive with no password)"""
        return obj.is_pending_approval()
    is_pending.boolean = True
    is_pending.short_description = 'Pending Approval'

    def is_suspended(self, obj):
        """Inactive but already activated - switched off, not awaiting approval"""
        return obj.is_suspended()
    is_suspended.boolean = True
    is_suspended.short_description = 'Suspended'

    def approve_and_send_activation(self, request, queryset):
        """Approve selected users and send activation email"""
        count = 0
        for user in queryset:
            if not user.is_active:
                user.is_active = True
                token = user.generate_activation_token()
                user.save()
                self.log_change(request, user, 'Approved and activation email requested.')

                if user.primary_email:
                    activation_path = reverse('vald:activate_account', kwargs={'token': token})
                    activation_url = f"{settings.SITE_URL}{activation_path}"
                    try:
                        send_mail(
                            'VALD Account Activated',
                            render_to_string('vald/email/activation.txt', {
                                'user_name': user.name,
                                'activation_url': activation_url,
                                'token_max_age_days': settings.VALD_TOKEN_MAX_AGE_DAYS,
                                'approved': True,
                            }),
                            settings.DEFAULT_FROM_EMAIL,
                            [user.primary_email],
                            fail_silently=False,
                        )
                        count += 1
                    except Exception as e:
                        self.message_user(request, f'Error sending email to {user.name}: {e}', level='error')

        self.message_user(request, f'{count} user(s) approved and activation emails sent.')
    approve_and_send_activation.short_description = 'Approve and send activation email'

    def approve_without_email(self, request, queryset):
        """Approve selected users without sending email

        Equivalent to ticking Active and saving, which is why it also bumps
        updated_at and writes a history entry: a bare queryset.update() does
        neither, and an approval that leaves no trace of who granted it is worse
        than the extra query.
        """
        approved = [user for user in queryset if not user.is_active]
        queryset.update(is_active=True, updated_at=timezone.now())
        for user in approved:
            self.log_change(request, user, 'Approved without sending email.')
        self.message_user(request, f'{len(approved)} user(s) approved (no email sent).')
    approve_without_email.short_description = 'Approve without sending email'

    def clear_password(self, request, queryset):
        """Drop the password so the next login attempt re-triggers activation"""
        cleared = [user for user in queryset if user.password]
        queryset.update(password=None, activation_token=None, token_created_at=None,
                        updated_at=timezone.now())
        for user in cleared:
            self.log_change(request, user, 'Password cleared; re-activation required.')
        self.message_user(
            request,
            f'{len(cleared)} user(s) had their password removed; they will be sent an '
            f'activation link on their next login attempt.'
        )
    clear_password.short_description = 'Clear password (force re-activation)'

    def suspend_users(self, request, queryset):
        """Switch off activated accounts, leaving the password in place

        Restricted to users that have activated: clearing is_active on an
        already-pending registration would be a no-op, and on an approved but
        not-yet-activated one it would silently push the account back into the
        pending-approval bucket.
        """
        suspendable = [user for user in queryset if user.is_active and user.password]
        User.objects.filter(pk__in=[u.pk for u in suspendable]).update(
            is_active=False, updated_at=timezone.now())
        for user in suspendable:
            self.log_change(request, user, 'Suspended.')
        self.message_user(request, f'{len(suspendable)} user(s) suspended.')
    suspend_users.short_description = 'Suspend (deactivate) selected users'

    def reject_registration(self, request, queryset):
        """Delete/reject selected pending users"""
        pending = queryset.filter(PENDING_APPROVAL)
        count = pending.count()
        self.log_deletions(request, pending)   # needs the rows, so before delete()
        pending.delete()
        self.message_user(request, f'{count} pending registration(s) rejected and deleted.')
    reject_registration.short_description = 'Reject pending registrations'


@admin.register(UserEmail)
class UserEmailAdmin(admin.ModelAdmin):
    list_display = ('email', 'user', 'is_primary', 'created_at')
    list_filter = ('is_primary',)
    search_fields = ('email', 'user__name')
    readonly_fields = ('created_at',)


@admin.register(UserPreferences)
class UserPreferencesAdmin(admin.ModelAdmin):
    list_display = ('user', 'energyunit', 'waveunit', 'medium', 'vdwformat', 'isotopic_scaling')
    list_filter = ('energyunit', 'waveunit', 'medium')
    search_fields = ('user__name',)
    readonly_fields = ('created_at', 'updated_at')


# ============================================================================
# Linelist Configuration Admin
# ============================================================================

class ConfigLinelistInline(admin.TabularInline):
    model = ConfigLinelist
    extra = 0
    fields = ('linelist', 'priority', 'is_enabled', 'mergeable')
    autocomplete_fields = ['linelist']
    ordering = ['priority']


@admin.register(Linelist)
class LinelistAdmin(admin.ModelAdmin):
    list_display = ('name', 'path', 'element_range', 'default_priority', 'is_molecular', 'is_active')
    list_filter = ('is_active', 'is_molecular', 'source')
    search_fields = ('name', 'path', 'source')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ['default_priority', 'path']
    fieldsets = (
        ('Basic Information', {
            'fields': ('path', 'name', 'source', 'is_molecular', 'is_active')
        }),
        ('Element Range', {
            'fields': ('element_min', 'element_max')
        }),
        ('Default Settings', {
            'fields': ('default_priority',),
        }),
        ('Default Rank Weights', {
            'fields': (
                ('default_rank_wl', 'default_rank_gf', 'default_rank_rad'),
                ('default_rank_stark', 'default_rank_waals', 'default_rank_lande'),
                ('default_rank_term', 'default_rank_ext_vdw', 'default_rank_zeeman'),
            ),
            'classes': ('collapse',)
        }),
        ('Notes', {
            'fields': ('notes',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )
    
    def element_range(self, obj):
        return f"{obj.element_min} - {obj.element_max}"
    element_range.short_description = 'Element Range'


@admin.register(Config)
class ConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'user', 'is_default', 'linelist_count', 'updated_at')
    list_filter = ('is_default', 'user')
    search_fields = ('name', 'slug', 'user__name', 'description')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [ConfigLinelistInline]
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'slug', 'user', 'is_default', 'description'),
            'description': 'The name is what the request forms show in the '
                           'linelist configuration menu. The slug is what a '
                           'submitted request stores, so changing it orphans '
                           'requests that already chose this config.',
        }),
        ('Global Parameters', {
            'fields': (
                ('wl_window_ref', 'wl_ref'),
                ('max_ionization', 'max_excitation_eV'),
            )
        }),
        ('Timestamps', {
            'fields': ('snapshot_at', 'created_at', 'updated_at')
        }),
    )
    
    def linelist_count(self, obj):
        return obj.configlinelist_set.count()
    linelist_count.short_description = 'Linelists'


@admin.register(ConfigLinelist)
class ConfigLinelistAdmin(admin.ModelAdmin):
    list_display = ('config', 'linelist', 'priority', 'is_enabled', 'mergeable')
    list_filter = ('is_enabled', 'mergeable', 'config')
    search_fields = ('config__name', 'linelist__name', 'linelist__path')
    autocomplete_fields = ['config', 'linelist']
    ordering = ['config', 'priority']
    fieldsets = (
        ('Association', {
            'fields': ('config', 'linelist', 'priority', 'is_enabled')
        }),
        ('Merge Settings', {
            'fields': ('mergeable', 'replacement_window')
        }),
        ('Rank Weights', {
            'fields': (
                ('rank_wl', 'rank_gf', 'rank_rad'),
                ('rank_stark', 'rank_waals', 'rank_lande'),
                ('rank_term', 'rank_ext_vdw', 'rank_zeeman'),
            ),
            'classes': ('collapse',)
        }),
    )
