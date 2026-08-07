"""Template hygiene checks that a rendering test would only catch by accident."""
from pathlib import Path

import pytest
from django.conf import settings


def template_files():
    root = Path(settings.BASE_DIR) / 'vald' / 'templates'
    return sorted(root.rglob('*.html')) + sorted(root.rglob('*.txt'))


@pytest.mark.parametrize('path', template_files(), ids=lambda p: p.name)
def test_no_multiline_django_comments(path):
    """{# #} does not span lines - Django renders the rest as visible text.

    Both instances of this were invisible in review and only showed up when the
    page was actually looked at. {% comment %} is the multi-line form.
    """
    offenders = [
        (n, line.strip())
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if '{#' in line and '#}' not in line
    ]
    assert not offenders, (
        f'{path.name} has an unterminated {{# comment, which renders as text: '
        f'{offenders}')
