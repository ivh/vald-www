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
def test_admin_index_links_to_help(staff_client):
    """The index template self-extends; a loader mistake would strip the app list."""
    response = staff_client.get('/admin/')
    assert response.status_code == 200
    body = response.content.decode()
    assert '/admin/help/' in body
    assert 'VALD administration' in body, 'stock index content was lost'


@pytest.mark.django_db
def test_user_changelist_and_change_form_link_to_help(staff_client):
    user = make_user('Suspended', is_active=False, password='pw-for-testing-123')

    changelist = staff_client.get('/admin/vald/user/').content.decode()
    assert '/admin/help/' in changelist
    # .object-tools is floated with margin-top:-48px, so the note has to come
    # after it or the "Add user" button is pulled down on top of the text.
    assert changelist.index('class="object-tools"') < changelist.index('/admin/help/')

    change_form = staff_client.get(f'/admin/vald/user/{user.pk}/change/').content.decode()
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
