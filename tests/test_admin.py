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
