"""Tests for the admin screens.

Note the two unrelated User models: django.contrib.auth's, which is who logs
into /admin/, and vald.models.User, which is the VALD account being administered.
"""
import pytest
from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import User as StaffUser
from django.test import Client

from vald.models import User, UserEmail


@pytest.fixture
def staff_client(db):
    StaffUser.objects.create_superuser('admin', 'admin@example.com', 'pw-for-testing-123')
    client = Client()
    assert client.login(username='admin', password='pw-for-testing-123')
    return client


def make_user(name, is_active, password=None):
    user = User.objects.create(name=name, is_active=is_active)
    if password:
        user.set_password(password)
        user.save()
    UserEmail.objects.create(user=user, email=f'{name.lower()}@example.com', is_primary=True)
    return user


def sidebar_of(body):
    """The nav sidebar only. The page has an earlier <nav>, so the closing tag
    has to be searched for from the sidebar's own start."""
    start = body.index('id="nav-sidebar"')
    return body[start:body.index('</nav>', start)]


def content_of(body):
    """Everything inside #content, i.e. the page proper without the chrome. The
    sidebar links to the reports too, so a test about the body of a page has to
    say so or it passes on the sidebar's copy."""
    return body[body.index('<div id="content"'):]


def run_action(client, action, users):
    return client.post('/admin/vald/user/', {
        'action': action,
        '_selected_action': [str(u.pk) for u in users],
        'index': '0',
    }, follow=True)


@pytest.mark.django_db
def test_approving_without_email_is_recorded_like_a_manual_edit(staff_client):
    """It is the same edit as ticking Active and saving, so it must leave the
    same evidence: a bumped updated_at and an entry in the object history."""
    user = make_user('Pending', is_active=False)
    before = user.updated_at

    run_action(staff_client, 'approve_without_email', [user])

    user.refresh_from_db()
    assert user.is_active
    assert user.updated_at > before, 'updated_at left stale by queryset.update()'
    assert LogEntry.objects.count() == 1
    assert 'Approved' in LogEntry.objects.get().get_change_message()


@pytest.mark.django_db
def test_already_active_users_are_not_logged_again(staff_client):
    """Selecting a whole page must not spray no-op entries into every history."""
    active = make_user('Working', is_active=True, password='pw-for-testing-123')
    run_action(staff_client, 'approve_without_email', [active])
    assert LogEntry.objects.count() == 0


@pytest.mark.django_db
def test_clearing_a_password_is_recorded(staff_client):
    user = make_user('Working', is_active=True, password='pw-for-testing-123')

    run_action(staff_client, 'clear_password', [user])

    user.refresh_from_db()
    assert user.needs_activation() and user.is_active
    assert 'Password cleared' in LogEntry.objects.get().get_change_message()


@pytest.mark.django_db
def test_rejecting_a_registration_is_recorded(staff_client):
    """The row is gone afterwards, so the log is the only evidence it existed."""
    pending = make_user('Pending', is_active=False)

    run_action(staff_client, 'reject_registration', [pending])

    assert not User.objects.filter(pk=pending.pk).exists()
    entry = LogEntry.objects.get()
    assert entry.is_deletion() and 'Pending' in entry.object_repr


@pytest.mark.django_db
def test_help_page_requires_staff():
    response = Client().get('/admin/help/')
    assert response.status_code == 302
    assert '/admin/login/' in response['Location']


@pytest.mark.django_db
def test_help_page_renders(staff_client):
    response = staff_client.get('/admin/help/')
    assert response.status_code == 200
    body = response.content.decode()
    for state in ('Pending approval', 'Approved, not activated', 'Active', 'Suspended'):
        assert state in body


@pytest.mark.django_db
def test_help_page_counts_each_state(staff_client):
    make_user('Pending', is_active=False)
    make_user('Waiting', is_active=True)
    make_user('Working', is_active=True, password='pw-for-testing-123')
    make_user('Suspended', is_active=False, password='pw-for-testing-123')
    make_user('Working2', is_active=True, password='pw-for-testing-123')

    counts = {row['state']: row['count']
              for row in staff_client.get('/admin/help/').context['account_states']}
    assert counts == {
        'Pending approval': 1,
        'Approved, not activated': 1,
        'Active': 2,
        'Suspended': 1,
    }


@pytest.mark.django_db
def test_state_counts_agree_with_the_changelist_filters(staff_client):
    """Each row links to a filtered user list; the link must select what it counted."""
    make_user('Pending', is_active=False)
    make_user('Waiting', is_active=True)
    make_user('Working', is_active=True, password='pw-for-testing-123')
    make_user('Suspended', is_active=False, password='pw-for-testing-123')

    for row in staff_client.get('/admin/help/').context['account_states']:
        listed = staff_client.get(row['url']).context['cl'].result_count
        assert listed == row['count'], f"{row['state']}: page says {row['count']}, filter shows {listed}"


@pytest.mark.django_db
def test_admin_root_lands_on_the_user_list(staff_client):
    """The index was an app list the sidebar already shows, and the one admin
    page with no sidebar of its own."""
    response = staff_client.get('/admin/')
    assert response.status_code == 302
    assert response['Location'] == '/admin/vald/user/'
    assert not response.get('Cache-Control', '').startswith('max-age')
    assert staff_client.get('/admin/', follow=True).status_code == 200


@pytest.mark.django_db
def test_the_redirect_only_shadows_the_index(staff_client):
    """An exact "admin/" match, so everything under it still reaches the site."""
    for page in ('/admin/vald/user/', '/admin/stats/', '/admin/help/',
                 '/admin/password_change/'):
        assert staff_client.get(page).status_code == 200, page


@pytest.mark.django_db
def test_signing_in_arrives_at_the_user_list():
    """Django sends a fresh login to the index, which now forwards on rather
    than dead-ending on the page this redirect exists to skip."""
    StaffUser.objects.create_superuser('admin', 'admin@example.com', 'pw-for-testing-123')
    client = Client()
    response = client.post('/admin/login/', {
        'username': 'admin', 'password': 'pw-for-testing-123',
        'next': '/admin/',
    }, follow=True)
    assert response.status_code == 200
    assert response.redirect_chain[-1][0] == '/admin/vald/user/'


@pytest.mark.django_db
def test_user_changelist_and_change_form_link_to_help(staff_client):
    user = make_user('Suspended', is_active=False, password='pw-for-testing-123')

    changelist = content_of(staff_client.get('/admin/vald/user/').content.decode())
    assert '/admin/help/' in changelist
    # .object-tools is floated with margin-top:-48px, so the note has to come
    # after it or the "Add user" button is pulled down on top of the text.
    assert changelist.index('class="object-tools"') < changelist.index('/admin/help/')

    change_form = content_of(
        staff_client.get(f'/admin/vald/user/{user.pk}/change/').content.decode())
    assert '/admin/help/' in change_form
    assert 'suspended' in change_form


# --- deployment section ------------------------------------------------------

@pytest.mark.django_db
def test_help_page_documents_deployment(staff_client):
    body = staff_client.get('/admin/help/').content.decode()
    assert 'Deploying a new version' in body
    for command in ['git pull', 'uv sync', 'bin/vald-manage migrate',
                    'systemctl daemon-reload', 'systemctl restart vald']:
        assert command in body, f'deployment section does not mention {command!r}'


@pytest.mark.django_db
def test_deployment_section_lists_every_unit_file_in_the_checkout(staff_client):
    """Discovered from the checkout, so adding a timer cannot leave this stale."""
    from pathlib import Path
    from django.conf import settings

    units = {p.name for p in Path(settings.BASE_DIR).iterdir()
             if p.suffix in ('.service', '.timer')}
    assert units, 'no unit files found - the fixture assumption is wrong'

    listed = set(staff_client.get('/admin/help/').context['unit_files'])
    assert listed == units


@pytest.mark.django_db
def test_deployment_section_shows_the_real_install_path(staff_client):
    """Paths come from the running instance rather than being written down."""
    from django.conf import settings
    body = staff_client.get('/admin/help/').content.decode()
    assert str(settings.BASE_DIR) in body


@pytest.mark.django_db
def test_help_page_does_not_advertise_a_stale_install_command(staff_client):
    """There is no requirements.txt; README still tells you to use one."""
    from pathlib import Path
    from django.conf import settings

    body = staff_client.get('/admin/help/').content.decode()
    if not (Path(settings.BASE_DIR) / 'requirements.txt').exists():
        assert 'requirements.txt' not in body


# --- read-only view of a user's linelist configuration ----------------------

@pytest.fixture
def system_default(db):
    from vald.models import Config, ConfigLinelist, Linelist
    cfg = Config.objects.create(name='Default', user=None, is_default=True)
    for i in range(4):
        ll = Linelist.objects.create(path=f'/CVALD3/ATOMS/x{i}', name=f'List {i}',
                                     element_min=1, element_max=99)
        ConfigLinelist.objects.create(config=cfg, linelist=ll, priority=10 * i)
    return cfg


def config_url(user):
    return f'/admin/vald/user/{user.id}/config/'


@pytest.mark.django_db
def test_config_view_requires_staff(system_default, approved_user):
    response = Client().get(config_url(approved_user))
    assert response.status_code == 302
    assert 'login' in response['Location']


@pytest.mark.django_db
def test_config_view_is_read_only(staff_client, system_default, approved_user):
    """No POST handling: this is a window, not an editor."""
    from vald.models import Config
    staff_client.post(config_url(approved_user), {'action': 'save', 'editid': '1'})
    assert not Config.objects.filter(user=approved_user).exists()


@pytest.mark.django_db
def test_config_view_reports_a_user_who_tracks_the_default(staff_client, system_default,
                                                           approved_user):
    body = staff_client.get(config_url(approved_user)).content.decode()
    assert 'No personal configuration' in body
    # nothing to diff against, so the full list is shown rather than an empty one
    assert 'List 0' in body and 'List 3' in body


@pytest.mark.django_db
def test_config_view_defaults_to_differences_only(staff_client, system_default,
                                                  approved_user):
    from vald.persconfig import create_user_config
    mine = create_user_config(approved_user)
    entry = mine.configlinelist_set.order_by('priority').first()
    entry.rank_wl = 9
    entry.save()

    body = staff_client.get(config_url(approved_user)).content.decode()
    assert 'Personal configuration' in body
    assert 'List 0' in body, 'the changed linelist is missing'
    assert 'List 3' not in body, 'unchanged linelists should be hidden by default'

    everything = staff_client.get(config_url(approved_user) + '?all=1').content.decode()
    assert 'List 3' in everything


@pytest.mark.django_db
def test_config_view_highlights_what_changed(staff_client, system_default, approved_user):
    from vald.persconfig import create_user_config
    mine = create_user_config(approved_user)
    entry = mine.configlinelist_set.order_by('priority').first()
    entry.rank_wl, entry.is_enabled = 9, False
    entry.save()

    body = staff_client.get(config_url(approved_user)).content.decode()
    assert body.count('class="n changed"') >= 2


@pytest.mark.django_db
def test_config_view_warns_about_linelists_added_since_the_snapshot(
        staff_client, system_default, approved_user):
    from vald.models import ConfigLinelist, Linelist
    from vald.persconfig import create_user_config
    mine = create_user_config(approved_user)
    entry = mine.configlinelist_set.first()
    entry.rank_wl = 9
    entry.save()

    ll = Linelist.objects.create(path='/CVALD3/ATOMS/new', name='Added in 2027',
                                 element_min=1, element_max=99)
    ConfigLinelist.objects.create(config=system_default, linelist=ll, priority=99)

    body = staff_client.get(config_url(approved_user)).content.decode()
    assert 'added to the' in body and 'Added in 2027' in body


@pytest.mark.django_db
def test_user_changelist_and_change_form_link_to_the_config_view(staff_client,
                                                                 system_default,
                                                                 approved_user):
    changelist = staff_client.get('/admin/vald/user/').content.decode()
    assert config_url(approved_user) in changelist

    change_form = staff_client.get(f'/admin/vald/user/{approved_user.id}/change/').content.decode()
    assert config_url(approved_user) in change_form


@pytest.mark.django_db
def test_config_view_survives_an_empty_database(staff_client, approved_user):
    """No system default imported yet - must explain, not raise."""
    response = staff_client.get(config_url(approved_user))
    assert response.status_code == 200
    assert 'no system default configuration' in response.content.decode().lower()


@pytest.mark.django_db
def test_admin_pages_render_their_title_once(staff_client, system_default, approved_user):
    """admin/base.html already emits <h1>{{ title }}</h1> from the context, so a
    template that adds its own shows the heading twice."""
    import re
    for url in [config_url(approved_user), '/admin/help/']:
        body = staff_client.get(url).content.decode()
        headings = re.findall(r'<h1[^>]*>(.*?)</h1>', body, re.S)
        assert len(headings) == 1, f'{url} rendered {len(headings)} <h1>: {headings}'


@pytest.mark.django_db
def test_help_page_reports_the_personal_config_split(staff_client, system_default,
                                                     approved_user):
    """The counts an admin needs when someone asks why they lack a new linelist."""
    from vald.models import ConfigLinelist, Linelist, User
    from vald.persconfig import create_user_config

    User.objects.create(name='Tracks Default', is_active=True)
    create_user_config(approved_user)

    # a release adds a linelist the snapshot will not have
    ll = Linelist.objects.create(path='/CVALD3/ATOMS/new', name='Added later',
                                 element_min=1, element_max=99)
    ConfigLinelist.objects.create(config=system_default, linelist=ll, priority=99)

    context = staff_client.get('/admin/help/').context
    assert context['personal_total'] == 1
    assert context['personal_behind'] == 1, 'snapshot missing a new linelist not counted'
    assert context['tracking_default'] == User.objects.count() - 1

    body = staff_client.get('/admin/help/').content.decode()
    assert 'Personal linelist configurations' in body
    assert 'Retiring a linelist' in body


@pytest.mark.django_db
def test_help_page_lists_the_rate_limits_that_exist(staff_client):
    listed = {name for name, _, _ in staff_client.get('/admin/help/').context['limits']}
    for setting in ['VALD_SUBMIT_RATE', 'VALD_ADMIN_LOGIN_RATE', 'SESSION_COOKIE_AGE']:
        assert setting in listed, f'{setting} is configurable but undocumented'


# --- Request statistics page ----------------------------------------------
#
# The gap filling and the timezone handling are the two parts with no visible
# symptom when they break: the chart still renders, it just misreports.

def make_request(user, when, request_type='extractall', status='complete'):
    """A Request at a given local time. created_at is auto_now_add, so it has to
    be overwritten afterwards with a queryset update."""
    from vald.models import Request
    req = Request.objects.create(user=user, request_type=request_type,
                                 status=status, parameters={})
    Request.objects.filter(pk=req.pk).update(created_at=when)
    return req


def local(year, month, day, hour=12, minute=0):
    from django.utils import timezone
    from datetime import datetime
    return timezone.make_aware(datetime(year, month, day, hour, minute))


@pytest.mark.django_db
def test_stats_page_requires_staff():
    response = Client().get('/admin/stats/')
    assert response.status_code == 302
    assert '/admin/login/' in response['Location']


@pytest.mark.django_db
@pytest.mark.parametrize('query', [
    '',
    '?period=day',
    '?period=month',
    '?period=year',
    '?period=weekly',                       # not a granularity we offer
    '?from=notadate&to=2026-13-45',         # unparseable
    '?period=day&from=2026-06-01&to=2026-01-01',   # backwards
    '?period=day&from=1990-01-01',          # more buckets than we will draw
])
def test_stats_page_renders_whatever_the_querystring_says(staff_client, query):
    """A hand-edited or stale URL must fall back, not 500."""
    assert staff_client.get(f'/admin/stats/{query}').status_code == 200


@pytest.mark.django_db
def test_empty_months_are_plotted_as_gaps(staff_client):
    """SQL returns only non-empty buckets. Plotting those alone would put March
    next to May at the same spacing and misreport the shape."""
    user = make_user('Busy', is_active=True)
    make_request(user, local(2026, 3, 10))
    make_request(user, local(2026, 3, 11))
    make_request(user, local(2026, 5, 4))

    context = staff_client.get(
        '/admin/stats/?period=month&from=2026-03-01&to=2026-05-31').context
    bars = context['chart']['bars']
    assert [b['value'] for b in bars] == [2, 0, 1]
    assert bars[1]['empty'] and bars[1]['h'] == 0
    assert context['total'] == 3


@pytest.mark.django_db
def test_a_day_is_a_local_day_not_a_utc_one(staff_client):
    """00:30 in Europe/Stockholm is 23:30 UTC the day before. Truncating in UTC
    would file it under the wrong day and drop it out of a range starting then."""
    user = make_user('Nightowl', is_active=True)
    make_request(user, local(2026, 6, 15, hour=0, minute=30))

    context = staff_client.get(
        '/admin/stats/?period=day&from=2026-06-15&to=2026-06-15').context
    assert context['total'] == 1
    assert [b['value'] for b in context['chart']['bars']] == [1]


@pytest.mark.django_db
def test_the_range_bounds_are_both_inclusive(staff_client):
    """An exclusive `to` silently drops the last day, which looks like a quiet day."""
    user = make_user('Edge', is_active=True)
    make_request(user, local(2026, 6, 1, hour=0, minute=1))
    make_request(user, local(2026, 6, 3, hour=23, minute=59))
    make_request(user, local(2026, 6, 4))

    context = staff_client.get(
        '/admin/stats/?period=day&from=2026-06-01&to=2026-06-03').context
    assert context['total'] == 2


@pytest.mark.django_db
def test_leaderboard_ranks_by_count_and_links_carry_the_period(staff_client):
    quiet = make_user('Quiet', is_active=True)
    loud = make_user('Loud', is_active=True)
    make_request(quiet, local(2026, 6, 2))
    for day in (2, 3, 4):
        make_request(loud, local(2026, 6, day))
    make_request(loud, local(2026, 9, 1))   # outside the window

    context = staff_client.get(
        '/admin/stats/?period=day&from=2026-06-01&to=2026-06-30').context
    assert [(r['name'], r['count']) for r in context['leaders']] == [
        ('Loud', 3), ('Quiet', 1)]

    link = context['leaders'][0]['requests_url']
    assert f'user__id__exact={loud.pk}' in link
    assert 'created_at__gte' in link and 'created_at__lt' in link
    # the link must actually filter the changelist to those three
    assert staff_client.get(link).context['cl'].result_count == 3


@pytest.mark.django_db
def test_request_types_are_broken_down_for_the_period(staff_client):
    user = make_user('Mixed', is_active=True)
    make_request(user, local(2026, 6, 2), request_type='showline')
    make_request(user, local(2026, 6, 3), request_type='extractall')
    make_request(user, local(2026, 6, 4), request_type='extractall')

    context = staff_client.get(
        '/admin/stats/?period=day&from=2026-06-01&to=2026-06-30').context
    assert [(r['type'], r['count']) for r in context['by_type']] == [
        ('extractall', 2), ('showline', 1)]


@pytest.mark.django_db
def test_an_over_long_range_shrinks_everything_not_just_the_chart(staff_client):
    """If only the bars were capped, the totals would describe a wider period
    than the chart and the two would disagree on the page."""
    from vald.admin import STATS_MAX_BUCKETS
    user = make_user('Old', is_active=True)
    make_request(user, local(2020, 1, 5))

    context = staff_client.get(
        '/admin/stats/?period=day&from=2020-01-01&to=2026-12-31').context
    assert context['truncated']
    assert len(context['chart']['bars']) == STATS_MAX_BUCKETS
    assert context['date_to'] < '2026-12-31'
    assert context['total'] == 1


@pytest.mark.django_db
def test_an_empty_period_says_so_instead_of_drawing_an_empty_axis(staff_client):
    body = staff_client.get(
        '/admin/stats/?period=day&from=2026-06-01&to=2026-06-30').content.decode()
    assert 'No requests between 2026-06-01 and 2026-06-30' in body
    assert '<svg class="stats-chart"' not in body, 'drew an axis with nothing on it'


@pytest.mark.django_db
def test_a_request_with_no_user_does_not_get_a_broken_link(staff_client):
    """Request.user is nullable. An empty user__id__exact bounces the changelist
    to ?e=1, so the orphan row is listed but not linked."""
    make_request(None, local(2026, 6, 2))

    context = staff_client.get(
        '/admin/stats/?period=day&from=2026-06-01&to=2026-06-30').context
    row = context['leaders'][0]
    assert (row['name'], row['count']) == ('Unknown', 1)
    assert row['user_url'] is None and row['requests_url'] is None


# --- turnaround ---------------------------------------------------------------

def finish(req, seconds):
    """Give a request a completion time, the way the job thread does."""
    from datetime import timedelta
    from vald.models import Request
    req.refresh_from_db()
    Request.objects.filter(pk=req.pk).update(
        completed_at=req.created_at + timedelta(seconds=seconds))
    return req


@pytest.mark.django_db
def test_turnaround_reports_median_p90_and_worst(staff_client):
    user = make_user('Timed', is_active=True)
    for seconds in (10, 20, 30, 40, 900):
        finish(make_request(user, local(2026, 6, 2)), seconds)

    t = staff_client.get(
        '/admin/stats/?period=day&from=2026-06-01&to=2026-06-30').context['turnaround']
    assert t['count'] == 5
    assert t['median'] == '30.0 s'      # nearest-rank, not interpolated
    assert t['p90'] == '15m 00s'
    assert t['worst'] == '15m 00s'


@pytest.mark.django_db
def test_failed_requests_are_left_out_of_turnaround(staff_client):
    """A request killed at VALD_JOB_TIMEOUT would drag the median to the timeout
    and describe the timeout rather than the work."""
    user = make_user('Timed', is_active=True)
    finish(make_request(user, local(2026, 6, 2)), 10)
    finish(make_request(user, local(2026, 6, 2), status='failed'), 3600)

    context = staff_client.get(
        '/admin/stats/?period=day&from=2026-06-01&to=2026-06-30').context
    assert context['turnaround']['count'] == 1
    assert context['turnaround']['worst'] == '10.0 s'
    assert context['failed_count'] == 1, 'failure still counted on the page'


@pytest.mark.django_db
def test_requests_still_running_do_not_count_as_instant(staff_client):
    """completed_at is null until the job thread sets it. Treating that as zero
    would make a busy period look fast."""
    user = make_user('Timed', is_active=True)
    finish(make_request(user, local(2026, 6, 2)), 12)
    make_request(user, local(2026, 6, 2), status='pending')

    t = staff_client.get(
        '/admin/stats/?period=day&from=2026-06-01&to=2026-06-30').context['turnaround']
    assert t['count'] == 1


@pytest.mark.django_db
def test_a_month_with_nothing_completed_is_a_gap_not_a_zero(staff_client):
    """Zero turnaround would read as instant. There is simply no measurement."""
    user = make_user('Timed', is_active=True)
    finish(make_request(user, local(2026, 3, 5)), 60)
    make_request(user, local(2026, 4, 5), status='pending')
    finish(make_request(user, local(2026, 5, 5)), 120)

    context = staff_client.get(
        '/admin/stats/?period=month&from=2026-03-01&to=2026-05-31').context
    bars = context['turnaround']['chart']['bars']
    assert [b['value'] for b in bars] == [1.0, None, 2.0]   # minutes
    assert context['turnaround']['unit'] == 'minutes'
    assert bars[1]['empty'] and 'nothing completed' in bars[1]['title']
    # the count chart still shows a real request that month
    assert [b['value'] for b in context['chart']['bars']] == [1, 1, 1]


@pytest.mark.django_db
def test_turnaround_is_absent_when_nothing_has_ever_completed(staff_client):
    """The dev database is entirely pre-completed_at rows, so this is the state
    the page renders in until real traffic arrives."""
    user = make_user('Timed', is_active=True)
    make_request(user, local(2026, 6, 2), status='pending')

    context = staff_client.get(
        '/admin/stats/?period=day&from=2026-06-01&to=2026-06-30').context
    assert context['turnaround'] is None
    body = staff_client.get(
        '/admin/stats/?period=day&from=2026-06-01&to=2026-06-30').content.decode()
    assert '<h2>Turnaround</h2>' not in body


@pytest.mark.django_db
def test_slowest_requests_are_listed_worst_first_and_link_to_the_row(staff_client):
    user = make_user('Timed', is_active=True)
    quick = finish(make_request(user, local(2026, 6, 2)), 5)
    slow = finish(make_request(user, local(2026, 6, 3)), 500)

    t = staff_client.get(
        '/admin/stats/?period=day&from=2026-06-01&to=2026-06-30').context['turnaround']
    assert [r['pk'] for r in t['slowest']] == [slow.pk, quick.pk]
    assert t['slowest'][0]['duration'] == '8m 20s'
    assert staff_client.get(t['slowest'][0]['url']).status_code == 200


@pytest.mark.django_db
def test_a_backwards_completed_at_is_ignored_rather_than_counted(staff_client):
    """Only reachable via a clock step, but a negative would pull the median
    below anything that actually happened."""
    from datetime import timedelta
    from vald.models import Request
    user = make_user('Timed', is_active=True)
    finish(make_request(user, local(2026, 6, 2)), 100)
    bad = make_request(user, local(2026, 6, 3))
    Request.objects.filter(pk=bad.pk).update(
        completed_at=local(2026, 6, 3) - timedelta(seconds=90))

    t = staff_client.get(
        '/admin/stats/?period=day&from=2026-06-01&to=2026-06-30').context['turnaround']
    assert t['count'] == 1
    assert t['median'] == '1m 40s'


@pytest.mark.django_db
def test_request_list_shows_and_sorts_by_duration(staff_client):
    """admin_order_field needs something the database can ORDER BY, so the
    column is an annotation rather than a per-row calculation."""
    user = make_user('Timed', is_active=True)
    quick = finish(make_request(user, local(2026, 6, 2)), 5)
    slow = finish(make_request(user, local(2026, 6, 3)), 500)
    running = make_request(user, local(2026, 6, 4), status='pending')

    changelist = staff_client.get('/admin/vald/request/').context['cl']
    assert '8m 20s' in staff_client.get('/admin/vald/request/').content.decode()

    # The ?o= index counts action_checkbox, which the admin prepends to
    # list_display whenever actions are enabled, so it is not the list_display
    # position. Derive it rather than hard-coding an off-by-one.
    column = changelist.list_display.index('duration')
    ordered = staff_client.get(
        f'/admin/vald/request/?o=-{column}').context['cl'].result_list
    assert [r.pk for r in ordered] == [slow.pk, quick.pk, running.pk], \
        'slowest first, and the unfinished request last'

    ascending = staff_client.get(
        f'/admin/vald/request/?o={column}').context['cl'].result_list
    assert [r.pk for r in ascending] == [running.pk, quick.pk, slow.pk], \
        'SQLite sorts NULL below every duration, so unfinished leads ascending'


@pytest.mark.django_db
def test_an_unfinished_request_shows_no_duration(staff_client):
    user = make_user('Timed', is_active=True)
    make_request(user, local(2026, 6, 2), status='pending')

    row = staff_client.get('/admin/vald/request/').context['cl'].result_list[0]
    from vald.admin import RequestAdmin
    from django.contrib.admin.sites import site
    assert RequestAdmin(type(row), site).duration(row) == '—'


# --- sidebar -----------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('page', [
    '/admin/vald/request/',
    '/admin/vald/user/',
    '/admin/stats/',
    '/admin/help/',
])
def test_reports_are_reachable_from_any_admin_page(staff_client, page):
    body = staff_client.get(page).content.decode()
    sidebar = sidebar_of(body)
    assert 'href="/admin/stats/"' in sidebar
    assert 'href="/admin/help/"' in sidebar


@pytest.mark.django_db
def test_the_sidebar_copy_still_renders_the_app_list(staff_client):
    """admin/nav_sidebar.html is a copy of Django's, so an upgrade could leave
    it stale. The app list going missing is how that would show up."""
    body = staff_client.get('/admin/vald/request/').content.decode()
    sidebar = sidebar_of(body)
    assert 'id="nav-filter"' in sidebar
    for entry in ('Requests', 'User Emails', 'Linelists', 'Configurations'):
        assert f'>{entry}</a>' in sidebar, f'{entry} dropped out of the sidebar'


@pytest.mark.django_db
def test_report_links_are_shaped_so_the_quick_filter_finds_them(staff_client):
    """nav_sidebar.js collects 'th[scope=row] a' and hides the enclosing <tr>.
    Links outside that shape stay visible while everything else filters away."""
    body = staff_client.get('/admin/stats/').content.decode()
    sidebar = sidebar_of(body)
    for url in ('/admin/stats/', '/admin/help/'):
        anchor = sidebar.index(f'href="{url}"')
        row = sidebar.rindex('<th scope="row">', 0, anchor)
        assert '</th>' not in sidebar[row:anchor]


@pytest.mark.django_db
def test_the_current_report_is_marked_in_the_sidebar(staff_client):
    body = staff_client.get('/admin/stats/').content.decode()
    sidebar = sidebar_of(body)
    assert 'href="/admin/stats/" aria-current="page"' in sidebar
    assert 'href="/admin/help/" aria-current="page"' not in sidebar


@pytest.mark.django_db
def test_the_landing_page_carries_the_report_links(staff_client):
    """The index used to need its own copies of these, because Django blanks the
    sidebar block there. Landing on a page that has a sidebar is what retired
    them, so the sidebar had better be on the page we land on."""
    body = staff_client.get('/admin/', follow=True).content.decode()
    sidebar = sidebar_of(body)
    assert 'href="/admin/stats/"' in sidebar and 'href="/admin/help/"' in sidebar


# --- hourly grouping and default windows --------------------------------------

@pytest.mark.django_db
def test_hourly_buckets_follow_local_time(staff_client):
    """TruncHour hands back an aware UTC datetime. In June, Stockholm is UTC+2,
    so 00:30 local is 22:30 the previous day in UTC - comparing the raw value
    against a local bucket start would file it two buckets away, on the wrong
    day."""
    from datetime import datetime
    user = make_user('Nightowl', is_active=True)
    make_request(user, local(2026, 6, 15, hour=0, minute=30))
    make_request(user, local(2026, 6, 15, hour=23, minute=59))

    context = staff_client.get(
        '/admin/stats/?period=hour&from=2026-06-15&to=2026-06-15').context
    bars = context['chart']['bars']
    assert len(bars) == 24, 'an hourly day is 24 buckets'
    assert [i for i, b in enumerate(bars) if b['value']] == [0, 23]
    assert bars[0]['title'].startswith('2026-06-15 00:00')
    assert context['total'] == 2


@pytest.mark.django_db
def test_an_hourly_range_can_span_days(staff_client):
    user = make_user('Busy', is_active=True)
    make_request(user, local(2026, 6, 15, hour=9))
    make_request(user, local(2026, 6, 16, hour=9))

    context = staff_client.get(
        '/admin/stats/?period=hour&from=2026-06-15&to=2026-06-16').context
    bars = context['chart']['bars']
    assert len(bars) == 48
    assert [i for i, b in enumerate(bars) if b['value']] == [9, 33]
    # bare clock times would repeat across the two days
    assert any('Jun' in label['text'] for label in context['chart']['x_labels'])


@pytest.mark.django_db
@pytest.mark.parametrize('period,expected', [
    ('hour', ('2026-08-10', '2026-08-10')),     # the current day
    ('day', ('2026-08-01', '2026-08-10')),      # the current month, so far
    # The current year, so far - but the last bucket is the whole of August, and
    # the reported range describes the buckets rather than trailing off mid-bar.
    ('month', ('2026-01-01', '2026-08-31')),
])
def test_choosing_a_grouping_selects_the_interval_one_level_up(
        staff_client, period, expected, monkeypatch):
    from datetime import date as real_date
    import vald.admin
    monkeypatch.setattr(vald.admin.timezone, 'localdate',
                        lambda *a, **kw: real_date(2026, 8, 10))

    context = staff_client.get(f'/admin/stats/?period={period}').context
    assert (context['date_from'], context['date_to']) == expected


@pytest.mark.django_db
def test_grouping_by_year_covers_everything_recorded(staff_client):
    user = make_user('Old', is_active=True)
    make_request(user, local(2021, 3, 4))

    context = staff_client.get('/admin/stats/?period=year').context
    assert context['date_from'] == '2021-01-01'
    assert context['total'] == 1


@pytest.mark.django_db
def test_the_grouping_links_drop_the_dates(staff_client):
    """Picking a grouping is meant to reset the range, which a <select> inside
    the form could not do without JS - it would post the old dates back."""
    body = staff_client.get(
        '/admin/stats/?period=day&from=2026-01-01&to=2026-01-31').content.decode()
    for p in ('hour', 'month', 'year'):
        assert f'href="/admin/stats/?period={p}"' in body, f'{p} link carries state'
    assert '<select' not in body


@pytest.mark.django_db
def test_dates_are_entered_in_iso_not_a_browser_locale(staff_client):
    """<input type=date> renders in the browser's locale, which the page cannot
    override, so an en-US browser would show a leading month."""
    body = staff_client.get('/admin/stats/').content.decode()
    assert 'type="date"' not in body
    assert body.count('placeholder="YYYY-MM-DD"') == 2
    assert body.count(r'pattern="\d{4}-\d{2}-\d{2}"') == 2


@pytest.mark.django_db
def test_a_month_grouping_reports_the_whole_months_it_actually_covers(staff_client):
    """The window is derived from the buckets, so a mid-month start shows as the
    1st rather than the page claiming a range it did not chart."""
    user = make_user('Mid', is_active=True)
    make_request(user, local(2026, 3, 2))     # before the requested start
    make_request(user, local(2026, 3, 20))

    context = staff_client.get(
        '/admin/stats/?period=month&from=2026-03-15&to=2026-03-31').context
    assert context['date_from'] == '2026-03-01'
    assert context['total'] == 2, 'both requests are inside the charted bucket'


@pytest.mark.django_db
def test_an_over_long_hourly_range_stops_on_a_bucket_boundary(staff_client):
    from vald.admin import STATS_MAX_BUCKETS
    context = staff_client.get(
        '/admin/stats/?period=hour&from=2026-01-01&to=2026-12-31').context
    assert context['truncated']
    assert len(context['chart']['bars']) == STATS_MAX_BUCKETS
    # 400 hours from midnight on 1 Jan ends part-way through 17 January
    assert context['date_to'] == '2026-01-17'


@pytest.mark.django_db
def test_django_auth_models_are_not_in_the_admin(staff_client):
    """One shared staff login, so the auth box managed nothing - and its "Users"
    sat directly above VALD's own "Users" under the same label."""
    body = staff_client.get('/admin/vald/request/').content.decode()
    assert 'Authentication and Authorization' not in body
    assert '/admin/auth/user/' not in body and '/admin/auth/group/' not in body
    assert staff_client.get('/admin/auth/user/').status_code == 404
    assert staff_client.get('/admin/auth/group/').status_code == 404


@pytest.mark.django_db
def test_the_staff_password_can_still_be_changed(staff_client):
    """admin:password_change belongs to the AdminSite, not to the UserAdmin that
    was just unregistered, so removing the box must not take it with it."""
    assert staff_client.get('/admin/password_change/').status_code == 200
    assert '/admin/password_change/' in staff_client.get(
        '/admin/vald/user/').content.decode()

    response = staff_client.post('/admin/password_change/', {
        'old_password': 'pw-for-testing-123',
        'new_password1': 'a-different-pw-456',
        'new_password2': 'a-different-pw-456',
    })
    assert response.status_code == 302
    StaffUser.objects.get(username='admin').check_password('a-different-pw-456')


@pytest.mark.django_db
def test_vald_users_are_still_administrable(staff_client):
    """The two User models are unrelated; unregistering auth's must not touch
    the VALD one this admin exists to manage."""
    user = make_user('Working', is_active=True, password='pw-for-testing-123')
    assert staff_client.get('/admin/vald/user/').status_code == 200
    assert staff_client.get(f'/admin/vald/user/{user.pk}/change/').status_code == 200


@pytest.mark.django_db
def test_help_page_says_where_staff_logins_are_managed(staff_client):
    """Nothing in the admin manages them any more, so the only pointer to the
    shell commands is this page."""
    body = staff_client.get('/admin/help/').content.decode()
    assert 'Staff logins are not managed from here' in body
    for command in ('createsuperuser', 'changepassword'):
        assert command in body


def test_the_landing_redirect_survives_a_url_prefix():
    """Deployment sets FORCE_SCRIPT_NAME, so in production the admin is not at
    /admin/. A literal redirect target would send people outside the app; going
    through pattern_name means reverse() picks the prefix up per request.

    Driven directly rather than through the test client, which never calls
    set_script_prefix and so cannot see FORCE_SCRIPT_NAME at all.
    """
    from django.test import RequestFactory
    from django.urls import get_script_prefix, resolve, set_script_prefix

    view = resolve('/admin/').func
    previous = get_script_prefix()
    try:
        set_script_prefix('/new/')
        response = view(RequestFactory().get('/admin/'))
    finally:
        set_script_prefix(previous)
    assert response['Location'] == '/new/admin/vald/user/'
