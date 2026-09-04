"""My Requests list: per-request summary and pagination (R20)."""
import pytest

from vald.models import Request


def make(user, request_type, **parameters):
    return Request.objects.create(user=user, request_type=request_type,
                                  parameters=parameters, status='complete')


# --- describe(): the summary shown in the Details column --------------------

@pytest.mark.django_db
def test_extract_all_shows_wavelength_range(approved_user):
    req = make(approved_user, 'extractall', stwvl=5000.0, endwvl=5010.0,
               waveunit='angstrom')
    assert req.describe() == '5000–5010 Å'


@pytest.mark.django_db
def test_extract_element_shows_element_and_range(approved_user):
    req = make(approved_user, 'extractelement', elmion='Fe 1',
               stwvl=5000.0, endwvl=5010.0, waveunit='angstrom')
    assert req.describe() == 'Fe 1, 5000–5010 Å'


@pytest.mark.django_db
def test_stellar_shows_atmosphere_parameters(approved_user):
    req = make(approved_user, 'extractstellar', stwvl=5700.0, endwvl=5703.0,
               teff=8000.0, logg=4.5, waveunit='angstrom')
    assert req.describe() == '5700–5703 Å, Teff 8000 / log g 4.5'


@pytest.mark.django_db
def test_waveunit_symbol_follows_the_parameter(approved_user):
    nm = make(approved_user, 'extractall', stwvl=500.0, endwvl=501.0, waveunit='nm')
    cm = make(approved_user, 'extractall', stwvl=20000.0, endwvl=20001.0, waveunit='1/cm')
    assert nm.describe() == '500–501 nm'
    assert cm.describe() == '20000–20001 cm⁻¹'


@pytest.mark.django_db
def test_waveunit_defaults_to_angstrom_when_absent(approved_user):
    req = make(approved_user, 'extractall', stwvl=5000.0, endwvl=5010.0)
    assert req.describe() == '5000–5010 Å'


@pytest.mark.django_db
def test_showline_summarises_query_sets(approved_user):
    req = make(approved_user, 'showline',
               wvl0=5000.0, el0='Fe 1', wvl1=6000.0, el1='Ca 2')
    summary = req.describe()
    assert 'Fe 1 5000' in summary
    assert 'Ca 2 6000' in summary


@pytest.mark.django_db
def test_showline_caps_the_number_of_sets_shown(approved_user):
    req = make(approved_user, 'showline',
               wvl0=5000.0, el0='Fe 1', wvl1=5100.0, el1='Fe 1',
               wvl2=5200.0, el2='Fe 1', wvl3=5300.0, el3='Fe 1',
               wvl4=5400.0, el4='Fe 1')
    summary = req.describe()
    assert '+2 more' in summary


@pytest.mark.django_db
def test_integer_wavelengths_have_no_trailing_zero(approved_user):
    req = make(approved_user, 'extractall', stwvl=5000, endwvl=5010, waveunit='angstrom')
    assert '5000.0' not in req.describe()
    assert '5000–5010' in req.describe()


@pytest.mark.django_db
@pytest.mark.parametrize('params', [
    {},                                          # no parameters at all
    {'stwvl': 'not-a-number', 'endwvl': 5010.0}, # malformed
    {'stwvl': None, 'endwvl': None},
    {'elmion': 'Fe 1'},                          # element but no range
])
def test_describe_never_raises_on_bad_parameters(approved_user, params):
    """Old or malformed rows must not break the list page."""
    req = make(approved_user, 'extractall', **params)
    result = req.describe()               # must not raise
    assert isinstance(result, str)


# --- pagination -------------------------------------------------------------

@pytest.mark.django_db
def test_requests_are_paginated(logged_in_client, approved_user):
    for i in range(30):
        make(approved_user, 'extractall', stwvl=5000.0 + i, endwvl=5010.0 + i)

    first = logged_in_client.get('/my-requests/')
    assert first.context['page_obj'].paginator.num_pages == 2
    assert len(first.context['requests']) == 25
    assert b'Older' in first.content

    second = logged_in_client.get('/my-requests/?page=2')
    assert len(second.context['requests']) == 5
    assert b'Newer' in second.content


@pytest.mark.django_db
def test_status_counts_cover_all_pages_not_just_the_visible_one(logged_in_client, approved_user):
    for i in range(30):
        make(approved_user, 'extractall', stwvl=5000.0 + i, endwvl=5010.0 + i)

    response = logged_in_client.get('/my-requests/')
    # 30 complete in total, even though only 25 are on this page
    assert response.context['complete_count'] == 30


@pytest.mark.django_db
def test_no_pagination_nav_with_few_requests(logged_in_client, approved_user):
    make(approved_user, 'extractall', stwvl=5000.0, endwvl=5010.0)
    response = logged_in_client.get('/my-requests/')
    assert response.context['page_obj'].paginator.num_pages == 1
    assert b'Older' not in response.content


@pytest.mark.django_db
def test_out_of_range_page_does_not_500(logged_in_client, approved_user):
    make(approved_user, 'extractall', stwvl=5000.0, endwvl=5010.0)
    assert logged_in_client.get('/my-requests/?page=999').status_code == 200
    assert logged_in_client.get('/my-requests/?page=abc').status_code == 200


@pytest.mark.django_db
def test_details_column_appears_in_the_rendered_page(logged_in_client, approved_user):
    make(approved_user, 'extractelement', elmion='Fe 1',
         stwvl=5000.0, endwvl=5010.0, waveunit='angstrom')
    response = logged_in_client.get('/my-requests/')
    assert 'Fe 1, 5000–5010 Å' in response.content.decode()


# --- comment(): the user's own label for a request --------------------------

@pytest.mark.django_db
def test_the_comment_shows_on_both_the_list_and_the_request(
        logged_in_client, approved_user):
    """It is the one line on either page the user wrote themselves.

    It reached the list from the start but not the detail page, where the
    subtitle showed only the derived wavelength summary.
    """
    req = make(approved_user, 'extractelement', subject='Ca triplet, run 3',
               elmion='Ca 1', stwvl=8490, endwvl=8680)

    listing = logged_in_client.get('/my-requests/').content.decode()
    assert 'Ca triplet, run 3' in listing

    detail = logged_in_client.get(f'/request/{req.uuid}/').content.decode()
    assert 'Ca triplet, run 3' in detail
    # Still shown alongside what was actually asked for, not instead of it.
    assert 'Ca 1' in detail


@pytest.mark.django_db
def test_a_request_without_a_comment_renders_cleanly(logged_in_client,
                                                     approved_user):
    req = make(approved_user, 'extractall', stwvl=5000, endwvl=5010)
    detail = logged_in_client.get(f'/request/{req.uuid}/')
    assert detail.status_code == 200
    assert 'pagecomment' not in detail.content.decode()


@pytest.mark.django_db
def test_a_comment_cannot_inject_markup(logged_in_client, approved_user):
    req = make(approved_user, 'extractall', subject='<script>alert(1)</script>')
    detail = logged_in_client.get(f'/request/{req.uuid}/').content.decode()
    assert '<script>alert(1)</script>' not in detail
    assert '&lt;script&gt;' in detail
