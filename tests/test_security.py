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


def save_linelist(client, linelist_id, **fields):
    """Post the persconf save action for one linelist.

    The form names a Linelist, not a ConfigLinelist: the view resolves it inside
    the posting user's own config, so no pk belonging to another config can be
    named at all (R47). These tests therefore probe the id space rather than a
    specific victim row.
    """
    payload = {'action': 'save', 'editid': str(linelist_id)}
    payload.update(fields)
    return client.post('/persconf/', payload)


@pytest.mark.django_db
def test_user_cannot_disable_a_linelist_in_the_system_default(logged_in_client, system_config):
    """The original R2: the system default owns low, guessable pks."""
    _, entry = system_config
    logged_in_client.get('/persconf/')
    # Sweep the low id space - under the old keying one of these named the
    # system default's junction row directly.
    for candidate in range(1, 6):
        save_linelist(logged_in_client, candidate)
    entry.refresh_from_db()
    assert entry.is_enabled is True, 'system default config was modified by a user'


@pytest.mark.django_db
def test_user_cannot_edit_another_users_config(logged_in_client, system_config, db):
    from vald.persconfig import create_user_config
    other = User.objects.create(name='Other', is_active=True)
    other_entry = create_user_config(other).configlinelist_set.first()

    logged_in_client.get('/persconf/')
    for candidate in range(1, 6):
        save_linelist(logged_in_client, candidate)
    other_entry.refresh_from_db()
    assert other_entry.is_enabled is True, "another user's config was modified"


@pytest.mark.django_db
def test_a_posted_id_only_ever_reaches_the_posting_users_own_config(
        logged_in_client, system_config, approved_user):
    """The property that replaces the ownership check: whatever id is posted,
    the row that changes belongs to the poster."""
    from vald.models import Linelist
    linelist = Linelist.objects.get(path='/CVALD3/ATOMS/x1')

    save_linelist(logged_in_client, linelist.pk)

    changed = ConfigLinelist.objects.filter(is_enabled=False)
    assert changed.count() == 1
    assert changed.first().config.user_id == approved_user.pk


@pytest.mark.django_db
def test_user_can_still_edit_their_own_config(logged_in_client, system_config, approved_user):
    from vald.models import Linelist
    linelist = Linelist.objects.get(path='/CVALD3/ATOMS/x1')

    save_linelist(logged_in_client, linelist.pk)

    mine = ConfigLinelist.objects.get(config__user=approved_user, linelist=linelist)
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



# --- R38: a session must stop working when the account does ----------------
#
# R1 checked is_active at the login gate. Nothing rechecked it afterwards, so
# every one of these was a session that outlived the account state it was
# granted under - by up to SESSION_COOKIE_AGE.

@pytest.mark.django_db
@pytest.mark.parametrize('page', ['/extractall/', '/my-requests/', '/persconf/',
                                  '/unitselection/'])
def test_suspending_an_account_ends_its_live_sessions(logged_in_client, approved_user, page):
    approved_user.is_active = False
    approved_user.save()
    assert logged_in_client.get(page).status_code == 302
    assert 'user_id' not in logged_in_client.session


@pytest.mark.django_db
def test_deleting_an_account_ends_its_live_sessions(logged_in_client, approved_user):
    approved_user.delete()
    assert logged_in_client.get('/my-requests/').status_code == 302
    assert 'user_id' not in logged_in_client.session


@pytest.mark.django_db
def test_password_reset_ends_other_sessions(logged_in_client, approved_user):
    """The stolen-session case: recovering the account must actually recover it."""
    token = approved_user.generate_activation_token()
    approved_user.save()
    Client().post(f'/reset-password/{token}/',
                  {'password': 'brand-new-passw-77', 'password_confirm': 'brand-new-passw-77'})

    assert logged_in_client.get('/my-requests/').status_code == 302
    assert 'user_id' not in logged_in_client.session


@pytest.mark.django_db
def test_clearing_a_password_from_the_admin_ends_live_sessions(logged_in_client, approved_user):
    """The 'force re-activation' action, which is also how an admin locks someone out."""
    User.objects.filter(pk=approved_user.pk).update(password=None)
    assert logged_in_client.get('/my-requests/').status_code == 302


@pytest.mark.django_db
def test_an_ordinary_session_survives_unrelated_saves(logged_in_client, approved_user):
    """The revalidation must not log people out for no reason."""
    approved_user.affiliation = 'Uppsala'
    approved_user.save()
    assert logged_in_client.get('/my-requests/').status_code == 200


@pytest.mark.django_db
def test_session_key_is_rotated_on_login(approved_user):
    """Session fixation: a key fixed before login must not become authenticated."""
    client = Client()
    client.get('/')
    client.session.save()
    before = client.session.session_key

    client.post('/login/', {'user': 'test@example.com', 'password': 'pw-for-testing-123'})

    assert 'user_id' in client.session
    assert client.session.session_key != before


@pytest.mark.django_db
def test_session_key_is_rotated_when_setting_a_password(db):
    user = User.objects.create(name='T', is_active=True)
    UserEmail.objects.create(user=user, email='t@example.com', is_primary=True)
    token = user.generate_activation_token()
    user.save()

    client = Client()
    client.get(f'/activate/{token}/')
    client.session.save()
    before = client.session.session_key
    client.post('/set-password/', {'password': 'correct-horse-batt-9',
                                   'password_confirm': 'correct-horse-batt-9'})

    assert 'user_id' in client.session
    assert client.session.session_key != before


# --- R39: ?modify= is raw query string, not a validated UUID ----------------

@pytest.mark.django_db
@pytest.mark.parametrize('page', ['/extractall/', '/extractelement/',
                                  '/extractstellar/', '/showline/'])
@pytest.mark.parametrize('value', ['not-a-uuid', '', '../../etc/passwd', '1 OR 1=1'])
def test_modify_with_an_unparseable_uuid_is_not_a_500(logged_in_client, page, value):
    """A UUIDField lookup raises ValidationError, which was never caught."""
    assert logged_in_client.get(f'{page}?modify={value}').status_code == 200


@pytest.mark.django_db
def test_modify_does_not_leak_another_users_request(logged_in_client):
    from vald.models import Request
    other = User.objects.create(name='Other', is_active=True)
    req = Request.objects.create(
        user=other, request_type='extractall', status='complete',
        parameters={'stwvl': 1234.5, 'endwvl': 1240.0, 'subject': 'SECRET-PROJECT'})

    body = logged_in_client.get(f'/extractall/?modify={req.uuid}').content.decode()
    assert 'SECRET-PROJECT' not in body
    assert '1234.5' not in body


# --- R30/R40: rank weights reach both sqlite and the Fortran config parser --

@pytest.mark.django_db
@pytest.mark.parametrize('posted,expected', [
    ('7', 7),            # in range, kept
    ('500', 9),          # above range, clamped
    ('-9', 1),           # below range, clamped
    ('9' * 25, 9),       # past 2**63: used to raise OverflowError from sqlite
    ('not-a-number', 3), # unparseable, falls back
    ('', 3),
])
def test_rank_weights_are_clamped(logged_in_client, system_config, approved_user,
                                 posted, expected):
    from vald.models import Linelist
    linelist = Linelist.objects.get(path='/CVALD3/ATOMS/x1')

    payload = {'linelist-checked': 'on', 'edit-val-0': posted}
    payload.update({f'edit-val-{j}': '3' for j in range(1, 9)})
    assert save_linelist(logged_in_client, linelist.pk, **payload).status_code == 200

    mine = ConfigLinelist.objects.get(config__user=approved_user, linelist=linelist)
    assert mine.rank_wl == expected


@pytest.mark.django_db
def test_generated_cfg_only_contains_legal_ranks(logged_in_client, system_config,
                                                approved_user):
    """What preselect5 actually parses."""
    from vald.models import Linelist
    linelist = Linelist.objects.get(path='/CVALD3/ATOMS/x1')

    payload = {'linelist-checked': 'on'}
    payload.update({f'edit-val-{j}': '99999' for j in range(9)})
    save_linelist(logged_in_client, linelist.pk, **payload)

    mine = Config.objects.get(user=approved_user)
    ranks = mine.generate_cfg_content().splitlines()[1].split(',')[5:14]
    assert [int(r) for r in ranks] == [9] * 9


# --- R41: the admin is a public login form too -----------------------------

@pytest.fixture
def staff_client(db):
    from django.contrib.auth.models import User as AuthUser
    AuthUser.objects.create_superuser('root', 'root@example.com', 'admin-pw-for-testing-1')
    client = Client()
    client.force_login(AuthUser.objects.get(username='root'))
    return client


@pytest.mark.django_db
def test_admin_login_is_rate_limited(settings):
    settings.VALD_ADMIN_LOGIN_RATE = '3/h'
    client = Client()
    codes = [client.post('/admin/login/',
                         {'username': 'root', 'password': f'guess{i}'}).status_code
             for i in range(5)]
    assert 403 in codes, f'admin login accepted unlimited guesses: {codes}'


@pytest.mark.django_db
def test_admin_set_password_applies_the_password_validators(staff_client, approved_user):
    """This path accepted anything six characters long."""
    url = f'/admin/vald/user/{approved_user.pk}/password/'
    staff_client.post(url, {'password1': '123456', 'password2': '123456'})
    approved_user.refresh_from_db()
    assert not approved_user.check_password('123456')

    staff_client.post(url, {'password1': 'correct-horse-batt-9',
                            'password2': 'correct-horse-batt-9'})
    approved_user.refresh_from_db()
    assert approved_user.check_password('correct-horse-batt-9')
