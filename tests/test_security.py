"""Regression tests for the security fixes.

Each test here corresponds to a defect that was exploitable in production, so a
failure means a real regression rather than a style change.
"""
import datetime

import pytest
from django.test import Client
from django.utils import timezone

from vald.models import User, UserEmail, Linelist, Config, ConfigLinelist


def register(email='mallory@example.com'):
    """Submit the public registration form."""
    Client().post('/submit/', {
        'reqtype': 'registration', 'email': email, 'name': 'Mallory',
        'affiliation': 'Nowhere', 'privacy_accepted': 'on',
    })
    return User.objects.get(emails__email=email)


def mail_to(mailoutbox, address):
    """Messages addressed to one recipient.

    Registering now also notifies VALD_ADMIN_EMAIL, so a bare len(mailoutbox)
    check no longer expresses what these tests guard: that the *registrant*
    never receives a link they could use to bypass approval.
    """
    return [m for m in mailoutbox if address in m.to]


def use_token_to_set_password(email, token, password='correct-horse-batt-9'):
    """Follow an activation link and try to set a password. Returns True if logged in."""
    client = Client()
    client.get(f'/activate/{token}/')
    client.post('/set-password/', {'password': password, 'password_confirm': password})
    check = Client()
    check.post('/login/', {'user': email, 'password': password})
    return 'user_id' in check.session


# --- R1: admin approval must not be bypassable -----------------------------

@pytest.mark.django_db
def test_self_registration_is_inactive():
    assert register().is_active is False


@pytest.mark.django_db
def test_login_with_empty_password_does_not_issue_a_token(mailoutbox):
    """The route that let a self-registered user mail themselves an activation link."""
    user = register()
    Client().post('/login/', {'user': 'mallory@example.com', 'password': ''})
    user.refresh_from_db()
    assert user.activation_token is None
    assert mail_to(mailoutbox, 'mallory@example.com') == []


@pytest.mark.django_db
def test_password_reset_does_not_issue_a_token_for_unapproved_account(mailoutbox):
    """Second, independent bypass: skip login and use "forgot password"."""
    user = register()
    Client().post('/reset-password/', {'email': 'mallory@example.com'})
    user.refresh_from_db()
    assert user.activation_token is None
    assert mail_to(mailoutbox, 'mallory@example.com') == []


@pytest.mark.django_db
def test_unapproved_user_cannot_activate_even_with_a_token():
    """Belt and braces: a token that exists must still not activate an inactive user."""
    user = register()
    token = user.generate_activation_token()
    user.save()
    assert use_token_to_set_password('mallory@example.com', token) is False


@pytest.mark.django_db
def test_approved_user_can_activate_and_log_in():
    """The legitimate flow must still work - the fix is worthless if it blocks this."""
    user = register()
    user.is_active = True                      # what the admin action does
    token = user.generate_activation_token()
    user.save()
    assert use_token_to_set_password('mallory@example.com', token) is True


# --- R2: personal config edits must be scoped to their owner ---------------

@pytest.fixture
def system_config(db):
    """A system default config plus one linelist, mimicking the imported default."""
    linelist = Linelist.objects.create(path='/CVALD3/ATOMS/x1', name='List one',
                                       element_min=1, element_max=99)
    config = Config.objects.create(name='Default', user=None, is_default=True)
    entry = ConfigLinelist.objects.create(config=config, linelist=linelist, priority=10)
    return config, entry


@pytest.mark.django_db
def test_user_cannot_disable_a_linelist_in_the_system_default(logged_in_client, system_config):
    """The system default owns the lowest ConfigLinelist pks, so they are guessable."""
    _, entry = system_config
    logged_in_client.get('/persconf/')          # creates this user's own config
    logged_in_client.post('/persconf/', {'action': 'save', 'editid': str(entry.pk)})
    entry.refresh_from_db()
    assert entry.is_enabled is True, 'system default config was modified by a user'


@pytest.mark.django_db
def test_user_cannot_edit_another_users_config(logged_in_client, system_config, db):
    from vald.persconfig import get_user_config
    other = User.objects.create(name='Other', is_active=True)
    other_entry = get_user_config(other).configlinelist_set.first()

    logged_in_client.get('/persconf/')
    logged_in_client.post('/persconf/', {'action': 'save', 'editid': str(other_entry.pk)})
    other_entry.refresh_from_db()
    assert other_entry.is_enabled is True, "another user's config was modified"


@pytest.mark.django_db
def test_user_can_still_edit_their_own_config(logged_in_client, system_config):
    logged_in_client.get('/persconf/')
    mine = Config.objects.get(user__isnull=False).configlinelist_set.first()
    logged_in_client.post('/persconf/', {'action': 'save', 'editid': str(mine.pk)})
    mine.refresh_from_db()
    assert mine.is_enabled is False, 'owner can no longer edit their own config'


# --- R6: token expiry and password validators ------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('age_days,should_work', [(0, True), (6, True), (8, False), (30, False)])
def test_activation_token_expires(age_days, should_work, settings):
    user = User.objects.create(name='T', is_active=True)
    UserEmail.objects.create(user=user, email='t@example.com', is_primary=True)
    token = user.generate_activation_token()
    user.save()
    User.objects.filter(pk=user.pk).update(
        token_created_at=timezone.now() - datetime.timedelta(days=age_days))

    assert use_token_to_set_password('t@example.com', token) is should_work


@pytest.mark.django_db
def test_token_without_issue_time_is_treated_as_expired():
    user = User.objects.create(name='T', is_active=True)
    UserEmail.objects.create(user=user, email='t@example.com', is_primary=True)
    token = user.generate_activation_token()
    user.save()
    User.objects.filter(pk=user.pk).update(token_created_at=None)
    assert use_token_to_set_password('t@example.com', token) is False


@pytest.mark.django_db
@pytest.mark.parametrize('password,accepted', [
    ('abc123z', False),            # too short
    ('9382749382', False),         # entirely numeric
    ('password123', False),        # too common
    ('correct-horse-batt-9', True),
])
def test_password_validators_are_applied_at_activation(password, accepted):
    """AUTH_PASSWORD_VALIDATORS was configured but validate_password never called."""
    user = User.objects.create(name='T', is_active=True)
    UserEmail.objects.create(user=user, email='t@example.com', is_primary=True)
    token = user.generate_activation_token()
    user.save()
    assert use_token_to_set_password('t@example.com', token, password) is accepted


@pytest.mark.parametrize('password,accepted', [('abc12x', False), ('correct-horse-batt-9', True)])
def test_password_validators_are_applied_on_reset_form(password, accepted):
    from vald.forms import PasswordResetForm
    form = PasswordResetForm({'password': password, 'password_confirm': password})
    assert form.is_valid() is accepted


# --- R4: rate-limit key must not be client-controllable --------------------

@pytest.mark.parametrize('forwarded', [
    None,
    '203.0.113.9',
    '1.2.3.4, 203.0.113.9',
    '9.9.9.9, 203.0.113.9',
    '1.1.1.1, 2.2.2.2, 203.0.113.9',
    'not-an-ip',
    '::ffff:203.0.113.9',
])
def test_client_ip_cannot_be_varied_by_the_client(forwarded, settings):
    """A proxy appends to X-Forwarded-For, so only the rightmost entry is trustworthy."""
    from django.test import RequestFactory
    from vald.ratelimit import client_ip

    settings.RATELIMIT_CLIENT_IP_HEADER = 'HTTP_X_FORWARDED_FOR'
    request = RequestFactory().post('/submit/')
    request.META['REMOTE_ADDR'] = '203.0.113.9'
    if forwarded is not None:
        request.META['HTTP_X_FORWARDED_FOR'] = forwarded

    assert client_ip('group', request) == '203.0.113.9'


@pytest.mark.django_db
def test_session_user_key_separates_users(approved_user):
    from django.test import RequestFactory
    from vald.ratelimit import session_user

    request = RequestFactory().post('/submit/')
    request.META['REMOTE_ADDR'] = '203.0.113.9'
    request.session = {'user_id': approved_user.pk}
    assert session_user('g', request) == f'user:{approved_user.pk}'

    request.session = {}
    assert session_user('g', request) == 'ip:203.0.113.9'


# --- activation is reachable however the user fills the login form ---------

@pytest.mark.django_db
@pytest.mark.parametrize('password_field', ['', 'a-guessed-password'])
def test_activation_email_is_sent_whether_or_not_a_password_was_typed(
        password_field, mailoutbox, db):
    """Requiring an empty password field was a dead end.

    A returning user who typed something was told to check their email for a
    link that had never been sent, with no hint that blanking the field was the
    trigger. Both paths must now send the activation link.
    """
    user = User.objects.create(name='Returning User', is_active=True)
    UserEmail.objects.create(user=user, email='returning@example.com', is_primary=True)
    assert user.needs_activation()

    Client().post('/login/', {'user': 'returning@example.com',
                              'password': password_field})

    user.refresh_from_db()
    assert user.activation_token is not None, 'no activation token was issued'
    assert len(mailoutbox) == 1, 'no activation email was sent'
    assert 'returning@example.com' in mailoutbox[0].to


# --- inactive means two different things, and must be worded that way ------

def login_message(email, password='whatever-123'):
    """The message shown after a failed login attempt."""
    response = Client().post('/login/', {'user': email, 'password': password},
                             follow=True)
    return ' '.join(str(m) for m in response.context['messages'])


@pytest.mark.django_db
def test_pending_registration_is_told_it_awaits_approval():
    register()
    assert 'awaiting approval' in login_message('mallory@example.com')


@pytest.mark.django_db
def test_suspended_account_is_not_told_it_awaits_approval():
    """is_active=False on an activated account means suspended, not unapproved.

    The approval wording sent these users to wait for an email that never comes.
    """
    user = User.objects.create(name='Ex User', is_active=False)
    user.set_password('pw-for-testing-123')
    user.save()
    UserEmail.objects.create(user=user, email='ex@example.com', is_primary=True)

    message = login_message('ex@example.com', 'pw-for-testing-123')
    assert 'deactivated' in message
    assert 'awaiting approval' not in message
    assert user.is_suspended() and not user.is_pending_approval()


@pytest.mark.django_db
def test_reject_registration_spares_suspended_accounts():
    """Both states are is_active=False; only one of them is a registration."""
    from vald.admin import PENDING_APPROVAL

    pending = register()
    suspended = User.objects.create(name='Ex User', is_active=False)
    suspended.set_password('pw-for-testing-123')
    suspended.save()

    User.objects.filter(PENDING_APPROVAL).delete()

    assert not User.objects.filter(pk=pending.pk).exists()
    assert User.objects.filter(pk=suspended.pk).exists()


@pytest.mark.django_db
def test_blank_password_counts_as_no_password_in_the_pending_filter():
    """password='' and password=NULL are the same state to needs_activation()."""
    from vald.admin import PENDING_APPROVAL

    blank = User.objects.create(name='Blank', is_active=False, password='')
    assert blank.is_pending_approval()
    assert User.objects.filter(PENDING_APPROVAL).filter(pk=blank.pk).exists()

