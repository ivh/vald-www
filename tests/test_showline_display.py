"""How the Show Line result is rendered on the detail page.

The PHP interface ran showline4.1 with -html and put the fragment straight on
the page - two tables with the reference keys linked to the wiki. That mode is
back, so the page shows tables where it can and the plain text where it cannot.
"""
import pytest

from vald.models import Request
from vald.utils import polish_showline_html

FRAGMENT = (
    '<DIV><PRE>Basic extraction parameters used:</PRE></DIV>\n'
    '<table border=1 cellspacing=0 cellpadding=3>'
    '<tr><td>   5000.2109<br><sup>(3)</sup></td>'
    '<td>   1 <a href="http://www.astro.uu.se/valdwiki/linelistRefs#K14">K14</a></td>'
    '</tr></table>\n'
)


@pytest.fixture
def showline_request(approved_user, tmp_path, settings):
    settings.VALD_FTP_DIR = tmp_path
    req = Request.objects.create(
        user=approved_user, request_type='showline', status='complete',
        output_file='Tester.000001.txt',
        parameters={'wl1': 5000.0, 'wlwin1': 1.0, 'el1': 'Fe 1'})

    def write(text=True, html=None):
        if text:
            (tmp_path / 'Tester.000001.txt').write_text(
                'Wl[A]      El/Ion      log(gf)\n   5000.2109  Fe 1  -1.045\n')
        if html is not None:
            (tmp_path / 'Tester.000001.html').write_text(html)
        return req

    return write


@pytest.mark.django_db
def test_tables_are_rendered_when_the_companion_exists(logged_in_client,
                                                       showline_request):
    req = showline_request(html=FRAGMENT)
    page = logged_in_client.get(f'/request/{req.uuid}/').content.decode()
    assert '<table border=1' in page, 'fragment was escaped or dropped'
    assert 'valdwiki/linelistRefs#K14' in page
    # and not also the raw text, which would double the listing
    assert 'Wl[A]' not in page


@pytest.mark.django_db
def test_plain_text_is_shown_when_there_is_no_companion(logged_in_client,
                                                        showline_request):
    """Results from before the companion existed, and from a binary whose
    -html mode fails, still have only the .txt."""
    req = showline_request()
    page = logged_in_client.get(f'/request/{req.uuid}/').content.decode()
    assert 'Wl[A]' in page
    assert '<table border=1' not in page


@pytest.mark.django_db
def test_companion_alone_is_not_a_result(logged_in_client, showline_request):
    """The .txt is what the request points at; the sweep deletes both, but if
    only the fragment survived the page must still say the results are gone."""
    req = showline_request(text=False, html=FRAGMENT)
    page = logged_in_client.get(f'/request/{req.uuid}/').content.decode()
    assert '<table border=1' not in page


def test_reference_links_open_the_wiki_in_a_new_tab():
    polished = polish_showline_html(FRAGMENT)
    assert 'href="https://www.astro.uu.se/valdwiki/linelistRefs#K14"' in polished
    assert 'target="_blank" rel="noopener"' in polished
    assert 'http://' not in polished


def test_unclosed_divs_are_balanced():
    """showline opens a div it never closes; unbalanced, the browser parses the
    rest of the page into it - .showline's font size and scroll container then
    swallow everything below the results."""
    polished = polish_showline_html('<div><table><tr><td>Fe 1</td></tr></table>')
    assert polished.count('</div>') == polished.lower().count('<div')


def test_balanced_fragments_are_left_alone():
    polished = polish_showline_html('<div>done</div>')
    assert polished == '<div>done</div>'
