"""
Utility functions for VALD web interface
"""
import re
from django.conf import settings
from django.contrib import messages


def spam_check(message):
    """
    Check if message appears to be spam.
    Returns True if message is OK, False if it's spam.

    A plain URL is NOT spam - astronomers legitimately link papers, DOIs and
    screenshots, and the old filter rejected any message containing http(s):// ,
    silently blocking real bug reports. Reject instead on markup that is almost
    always spam (HTML/BBCode links) or an implausible number of links.
    """
    if not message or len(message.strip()) < 10:
        return False

    lowered = message.lower()
    compact = lowered.replace(" ", "")

    # Link markup - genuine spam/injection signal, not something a scientist types
    markup_patterns = ["ahref=", "[url", "[/url", "</a>"]
    if any(p in compact for p in markup_patterns):
        return False

    # A handful of links is fine; a wall of them is not
    if lowered.count("http://") + lowered.count("https://") > 5:
        return False

    return True


def add_form_errors(request, form):
    """
    Surface form validation errors as user messages.

    Errors raised in clean() are keyed by NON_FIELD_ERRORS ('__all__'), which
    is Django internals and means nothing to the person reading the message, so
    those are shown unprefixed.
    """
    for field, errors in form.errors.items():
        field_obj = form.fields.get(field)
        label = field_obj.label if field_obj is not None else None
        for error in errors:
            messages.error(request, f"{label}: {error}" if label else error)


def get_request_template_path(reqtype):
    """Get the path to a request template file"""
    template_map = {
        'contact': 'contact-req.txt',
        'extractall': 'extractall-req.txt',
        'extractelement': 'extractelement-req.txt',
        'extractstellar': 'extractstellar-req.txt',
        'showline': 'showline-req.txt',
    }

    filename = template_map.get(reqtype)
    if not filename:
        return None

    return settings.BASE_DIR / 'requests' / filename


def render_request_template(reqtype, context):
    """
    Render a request template with the given context.
    Similar to the PHP EditLine function.
    """
    template_path = get_request_template_path(reqtype)
    if not template_path or not template_path.exists():
        return ""

    with open(template_path, 'r') as f:
        content = f.read()

    # Replace template variables.
    # The replacement is passed as a callable because re.sub interprets escapes
    # in a string replacement: a user message containing "\2" (a Fortran format
    # spec, a LaTeX macro, a Windows path) otherwise raised
    # "invalid group reference" and crashed the contact form.
    for key, value in context.items():
        pattern_key = re.escape(str(key))
        if value:
            # Replace $key with value
            content = re.sub(rf'\${pattern_key}\b', lambda m, v=value: str(v), content)
        else:
            # If no value, remove the key (and optional trailing comma)
            content = re.sub(rf'\${pattern_key},?', '', content)

    # Remove any remaining unmatched $-strings
    content = re.sub(r'\$\w+', '', content)

    return content


# showline writes its reference links to the wiki over plain http, which the
# site redirects; asking for https directly saves the round trip and keeps the
# results page from mixing schemes.
_SHOWLINE_WIKI_LINK = re.compile(r'<a href="http://www\.astro\.uu\.se/')


def polish_showline_html(fragment):
    """Tidy the HTML fragment showline -html produces, for inline display.

    The markup is showline's, from 2005: border/cellpadding attributes on the
    tables and align="center" on the rows. Those are left alone - style/style.css
    overrides them under .showline - and only the links are touched, so that a
    reference key opens the wiki in a new tab rather than replacing the results
    the reader is comparing it against.
    """
    fragment = _SHOWLINE_WIKI_LINK.sub('<a target="_blank" rel="noopener" '
                                       'href="https://www.astro.uu.se/', fragment)
    # Fortran pads its header with WRITE(*,'(/)') either side, which inside the
    # <PRE> is a hand's width of empty box above and below the parameters.
    fragment = re.sub(r'\n{3,}', '\n\n', fragment)
    fragment = re.sub(r'(<PRE>)\s+', r'\1', fragment, flags=re.IGNORECASE)
    fragment = re.sub(r'\s+(</PRE>)', r'\1', fragment, flags=re.IGNORECASE)

    # showline opens one <div> more than it closes - it wraps the first table in
    # a div it never ends. Left as it comes, the browser keeps that div open and
    # parses the whole rest of the page into it, so everything below the results
    # (the request details box, the Modify request button) becomes a descendant
    # of .showline and picks up its font size and scroll container.
    unclosed = (len(re.findall(r'<div\b', fragment, re.IGNORECASE))
                - len(re.findall(r'</div\s*>', fragment, re.IGNORECASE)))
    fragment += '</div>' * max(unclosed, 0)

    return fragment.strip()
