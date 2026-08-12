"""Units as a property of the request rather than of the user.

The backend always read them off Request.parameters (create_job_config), so what
these cover is the other half: getting the user's choice into that dict, and
choosing what a fresh form starts from.
"""
import pytest

from vald.forms import ExtractAllForm, ExtractStellarForm
from vald.models import Request, UNIT_KEYS

EXTRACT = {'reqtype': 'extractall', 'stwvl': '5000', 'endwvl': '5002',
           'format': 'short', 'viaftp': 'via ftp', 'pconf': 'default'}

NM_VACUUM = {'energyunit': '1/cm', 'medium': 'vacuum', 'waveunit': 'nm',
             'vdwformat': 'extended', 'isotopic_scaling': 'off'}


def set_prefs(user, **values):
    prefs = user.get_preferences()
    for key, value in values.items():
        setattr(prefs, key, value)
    prefs.save()
    return prefs


def submitted(client, **extra):
    """POST an extraction and return the stored parameters."""
    client.post('/submit/', {**EXTRACT, **extra})
    return Request.objects.latest('created_at').parameters


# --- the form's choice has to survive into the request ----------------------

@pytest.mark.django_db
def test_units_chosen_on_the_form_are_what_gets_stored(logged_in_client, approved_user):
    """Regression: parameters were assembled as cleaned_data then overwritten with
    the profile, so every per-request unit choice was silently discarded."""
    set_prefs(approved_user, energyunit='eV', medium='air', waveunit='angstrom',
              vdwformat='default', isotopic_scaling='on')

    params = submitted(logged_in_client, **NM_VACUUM)

    assert {key: params[key] for key in UNIT_KEYS} == NM_VACUUM


@pytest.mark.django_db
def test_show_line_isotopic_scaling_reaches_the_request(logged_in_client, approved_user):
    """The one per-request unit that already existed. It is offered on the Show
    Line form and documented as taking precedence, but the profile overwrote it -
    and job_runner turns it into showline4.1's -noisotopic, so the choice was
    visible, promised, and inert."""
    set_prefs(approved_user, isotopic_scaling='on')

    logged_in_client.post('/submit/', {
        'reqtype': 'showline', 'wvl0': '5000', 'win0': '0.5', 'el0': 'Fe 1',
        'viaftp': 'via ftp', 'pconf': 'default', 'isotopic_scaling': 'off'})

    assert Request.objects.latest('created_at').parameters['isotopic_scaling'] == 'off'


@pytest.mark.django_db
def test_a_submission_without_unit_fields_still_uses_the_profile(logged_in_client,
                                                                approved_user):
    """What a POST predating these fields meant, and must keep meaning."""
    set_prefs(approved_user, **NM_VACUUM)

    params = submitted(logged_in_client)

    assert {key: params[key] for key in UNIT_KEYS} == NM_VACUUM


# --- what a fresh form starts from ------------------------------------------

@pytest.mark.django_db
def test_a_first_form_starts_from_the_saved_defaults(logged_in_client, approved_user):
    set_prefs(approved_user, waveunit='nm', medium='vacuum')

    form = logged_in_client.get('/extractall/').context['form']

    assert form.initial['waveunit'] == 'nm'
    assert form.initial['medium'] == 'vacuum'


@pytest.mark.django_db
def test_the_next_form_starts_from_the_last_request_of_that_type(logged_in_client,
                                                                approved_user):
    """The stickiness that makes per-request units bearable: without it a user
    who works in nm would re-pick nm on every form."""
    set_prefs(approved_user, waveunit='angstrom', medium='air')
    submitted(logged_in_client, waveunit='nm', medium='vacuum')

    form = logged_in_client.get('/extractall/').context['form']

    assert form.initial['waveunit'] == 'nm', 'last request of this type ignored'
    assert form.initial['medium'] == 'vacuum'


@pytest.mark.django_db
def test_stickiness_does_not_cross_request_types(logged_in_client, approved_user):
    set_prefs(approved_user, waveunit='angstrom')
    submitted(logged_in_client, waveunit='nm')

    stellar = logged_in_client.get('/extractstellar/').context['form']

    assert stellar.initial['waveunit'] == 'angstrom'


@pytest.mark.django_db
def test_only_units_are_inherited_never_the_values(logged_in_client, approved_user):
    """Reusing the numbers is what ?modify= is for, and it is explicit."""
    submitted(logged_in_client, stwvl='4321', endwvl='4322')

    form = logged_in_client.get('/extractall/').context['form']

    assert 'stwvl' not in form.initial and 'endwvl' not in form.initial


@pytest.mark.django_db
def test_an_old_request_missing_unit_keys_falls_back_to_the_profile(logged_in_client,
                                                                   approved_user):
    """Rows predating isotopic_scaling exist, so a partial inheritance has to
    layer over the profile rather than leaving the key unset."""
    set_prefs(approved_user, waveunit='angstrom', isotopic_scaling='off')
    Request.objects.create(user=approved_user, request_type='extractall',
                           status='complete', parameters={'waveunit': 'nm'})

    form = logged_in_client.get('/extractall/').context['form']

    assert form.initial['waveunit'] == 'nm'
    assert form.initial['isotopic_scaling'] == 'off'


# --- ?modify= must reproduce the original reading ---------------------------

@pytest.mark.django_db
def test_modify_prefills_the_units_the_request_was_made_with(logged_in_client,
                                                            approved_user):
    """Otherwise 5000 A comes back as 5000 nm: same number, different question,
    and the backend accepts both."""
    set_prefs(approved_user, waveunit='angstrom', medium='air')
    req = Request.objects.create(
        user=approved_user, request_type='extractall', status='complete',
        parameters={'stwvl': 5000.0, 'endwvl': 5002.0, 'waveunit': 'nm',
                    'medium': 'vacuum'})

    form = logged_in_client.get(f'/extractall/?modify={req.uuid}').context['form']

    assert form.initial['waveunit'] == 'nm'
    assert form.initial['medium'] == 'vacuum'


@pytest.mark.django_db
def test_modify_beats_the_last_used_units(logged_in_client, approved_user):
    """The later request sets the habit; the one being modified must still win."""
    req = Request.objects.create(
        user=approved_user, request_type='extractall', status='complete',
        parameters={'stwvl': 5000.0, 'endwvl': 5002.0, 'waveunit': 'angstrom'})
    submitted(logged_in_client, waveunit='nm')

    form = logged_in_client.get(f'/extractall/?modify={req.uuid}').context['form']

    assert form.initial['waveunit'] == 'angstrom'


# --- the cm^-1 / medium coupling -------------------------------------------

@pytest.mark.django_db
def test_wavenumbers_normalise_the_medium_to_vacuum(approved_user):
    """preselect5 ignores the medium flag under cm^-1, so the control is disabled
    and submits nothing. The stored value must not be left empty or as air."""
    form = ExtractAllForm({**EXTRACT, 'waveunit': '1/cm'}, user=approved_user)

    assert form.is_valid(), form.errors
    assert form.cleaned_data['medium'] == 'vacuum'


@pytest.mark.django_db
def test_an_air_choice_under_wavenumbers_is_corrected_not_rejected(approved_user):
    form = ExtractAllForm({**EXTRACT, 'waveunit': '1/cm', 'medium': 'air'},
                          user=approved_user)

    assert form.is_valid(), form.errors
    assert form.cleaned_data['medium'] == 'vacuum'


@pytest.mark.django_db
def test_an_omitted_unit_falls_back_to_the_forms_initial(approved_user):
    form = ExtractAllForm(EXTRACT, initial={'waveunit': 'nm', 'medium': 'vacuum'},
                          user=approved_user)

    assert form.is_valid(), form.errors
    assert form.cleaned_data['waveunit'] == 'nm'
    assert form.cleaned_data['medium'] == 'vacuum'


@pytest.mark.django_db
def test_a_junk_unit_is_still_rejected(approved_user):
    """required=False must not turn into "anything goes": these feed pres_in."""
    form = ExtractAllForm({**EXTRACT, 'waveunit': 'furlongs'}, user=approved_user)

    assert not form.is_valid()
    assert 'waveunit' in form.errors


# --- rendering --------------------------------------------------------------

@pytest.mark.django_db
@pytest.mark.parametrize('page', ['/extractall/', '/extractelement/', '/extractstellar/'])
def test_the_extract_forms_render_the_unit_controls(logged_in_client, page):
    body = logged_in_client.get(page).content.decode()
    for key in UNIT_KEYS:
        assert f'name="{key}"' in body, f'{page} has no {key} control'
    assert 'Units for this request' in body


@pytest.mark.django_db
@pytest.mark.parametrize('page', ['/extractall/', '/extractelement/', '/extractstellar/'])
def test_the_wavelength_labels_are_updatable(logged_in_client, page):
    """document.write() baked the label in at parse time. With the units on the
    form a fixed label is a request that quietly means something else, so the
    label has to be an element the selector can rewrite."""
    body = logged_in_client.get(page).content.decode()
    assert 'class="wavelabel1"' in body and 'class="wavelabel2"' in body
    assert 'document.write' not in body, 'label still written at parse time'


@pytest.mark.django_db
def test_stellar_help_text_does_not_share_a_row_with_the_units(logged_in_client):
    """The units panel took the third column the chemcomp help used to sit in."""
    body = logged_in_client.get('/extractstellar/').content.decode()
    start = body.index('id="id_chemcomp"')
    cell = body[start:body.index('<tr', start)]
    assert 'For example' in cell, 'help text no longer sits under the chemcomp box'
