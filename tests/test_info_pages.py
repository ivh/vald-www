"""The About and Registration/Contact pages, and what replaced the doc/ tree.

doc/ was a file server over documentation/, a tree the valdwiki superseded: of
its 19 files only about_vald.html was still linked. It now lives in old/ and is
not read at runtime, so About is a template like every other page.
"""
import pytest
from django.test import Client, RequestFactory
from django.urls import get_script_prefix, resolve, set_script_prefix


def test_about_page_renders_without_the_doc_prefix():
    body = Client().get('/about/').content.decode()
    assert 'Vienna Atomic Line Database' in body
    assert 'href="/contact/"' in body


def test_contact_page_renders_without_the_doc_prefix():
    body = Client().get('/contact/').content.decode()
    assert 'Register as a new user' in body


def test_index_and_about_share_one_copy_of_the_text():
    """Both include _about.html; a second copy is how the two would drift."""
    for url in ('/', '/about/'):
        assert 'computing opacity tables' in Client().get(url).content.decode()


@pytest.mark.parametrize('old,new', [
    ('/doc/contact.html', '/contact/'),
    ('/doc/about_vald.html', '/about/'),
])
def test_the_old_doc_urls_still_resolve(old, new):
    """Both were linked from the sidebar for the whole life of the Django
    interface, and one is linked from a news item still on the front page."""
    response = Client().get(old)
    assert response.status_code == 301
    assert response['Location'] == new


def test_the_doc_redirects_survive_a_url_prefix():
    """Deployment sets FORCE_SCRIPT_NAME; a literal target would leave the app.

    Driven through the view directly, as the test client never calls
    set_script_prefix.
    """
    view = resolve('/doc/contact.html').func
    previous = get_script_prefix()
    try:
        set_script_prefix('/new/')
        response = view(RequestFactory().get('/doc/contact.html'))
    finally:
        set_script_prefix(previous)
    assert response['Location'] == '/new/contact/'


def test_the_rest_of_the_doc_tree_is_gone():
    """Unreachable before this change, so nothing is worth redirecting - but it
    was still fetchable by anyone who guessed a filename."""
    assert Client().get('/doc/usage.html').status_code == 404


def test_news_items_no_longer_link_at_dead_interfaces():
    """News files are kept verbatim as published, so they carry links written
    for the PHP interface ($thisscript?docpage=) and for doc/."""
    body = Client().get('/news/').content.decode()
    assert 'thisscript' not in body
    assert 'href="doc/' not in body
