"""Account details page: self-service editing of the stored affiliation."""
import pytest
from django.test import Client

from vald.forms import AFFILIATION_MAX_LENGTH
from vald.models import User


@pytest.mark.django_db
def test_page_requires_login():
    response = Client().get('/account/')
    assert response.status_code == 302


@pytest.mark.django_db
def test_page_shows_the_stored_affiliation(logged_in_client, approved_user):
    """The point of the page: what the importer stored is finally visible."""
    approved_user.affiliation = 'Uppsala University, Dept. of Physics'
    approved_user.save()

    content = logged_in_client.get('/account/').content.decode()
    assert 'Uppsala University, Dept. of Physics' in content


@pytest.mark.django_db
def test_saving_updates_the_affiliation(logged_in_client, approved_user):
    response = logged_in_client.post('/account/', {'affiliation': 'ESO, Garching'})

    assert response.status_code == 302
    approved_user.refresh_from_db()
    assert approved_user.affiliation == 'ESO, Garching'


@pytest.mark.django_db
def test_multiline_affiliation_survives_a_round_trip(logged_in_client, approved_user):
    """Register-imported values are multi-line, and folding in a position keeps them so."""
    value = 'Uppsala University\nDept. of Physics and Astronomy\nPostdoc'
    logged_in_client.post('/account/', {'affiliation': value})

    approved_user.refresh_from_db()
    assert approved_user.affiliation == value


@pytest.mark.django_db
def test_longest_imported_values_are_still_saveable(logged_in_client, approved_user):
    """The old 200-char form cap would have rejected real imported affiliations."""
    response = logged_in_client.post('/account/', {'affiliation': 'x' * 300})

    assert response.status_code == 302
    approved_user.refresh_from_db()
    assert len(approved_user.affiliation) == 300


@pytest.mark.django_db
def test_overlong_affiliation_is_rejected(logged_in_client, approved_user):
    approved_user.affiliation = 'Uppsala'
    approved_user.save()

    response = logged_in_client.post(
        '/account/', {'affiliation': 'x' * (AFFILIATION_MAX_LENGTH + 1)})

    assert response.status_code == 200
    approved_user.refresh_from_db()
    assert approved_user.affiliation == 'Uppsala'


@pytest.mark.django_db
def test_empty_affiliation_is_rejected(logged_in_client, approved_user):
    """An accidental empty submit must not wipe the stored value."""
    approved_user.affiliation = 'Uppsala'
    approved_user.save()

    logged_in_client.post('/account/', {'affiliation': ''})

    approved_user.refresh_from_db()
    assert approved_user.affiliation == 'Uppsala'


@pytest.mark.django_db
def test_one_user_cannot_edit_another(logged_in_client, approved_user, db):
    """The form is bound to the session's user, so there is no id to tamper with."""
    from vald.models import UserEmail
    other = User.objects.create(name='Other', affiliation='Elsewhere', is_active=True)
    UserEmail.objects.create(user=other, email='other@example.com', is_primary=True)

    logged_in_client.post('/account/', {'affiliation': 'Hijacked', 'id': other.id})

    other.refresh_from_db()
    approved_user.refresh_from_db()
    assert other.affiliation == 'Elsewhere'
    assert approved_user.affiliation == 'Hijacked'


@pytest.mark.django_db
def test_saving_does_not_end_the_session(logged_in_client):
    """set_password rotates auth_hash; an ordinary field save must not."""
    logged_in_client.post('/account/', {'affiliation': 'Uppsala'})
    assert logged_in_client.get('/my-requests/').status_code == 200


# --- registration: position is folded into affiliation ----------------------

@pytest.mark.django_db
def test_registration_no_longer_offers_a_position_field():
    content = Client().get('/contact/').content.decode()
    assert 'name="position"' not in content
    assert 'name="affiliation"' in content


@pytest.mark.django_db
def test_registration_ignores_a_submitted_position(mailoutbox):
    """The field is gone; a stray POST value must not become part of the account."""
    Client().post('/submit/', {
        'reqtype': 'registration', 'email': 'new@example.com', 'name': 'New',
        'affiliation': 'Uppsala University\nPostdoc', 'position': 'Professor',
        'privacy_accepted': 'on',
    })

    user = User.objects.get(emails__email='new@example.com')
    assert user.affiliation == 'Uppsala University\nPostdoc'
    assert 'Professor' not in user.affiliation
