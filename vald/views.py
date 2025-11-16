from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.conf import settings
from django.core.mail import send_mail
from django.views.decorators.http import require_http_methods
from pathlib import Path
import glob

from .models import UserPreferences, PersonalConfig, LineList
from .utils import (
    validate_user_email,
    spam_check,
    read_config_file,
    render_request_template,
)


def get_user_context(request):
    """Get common context data for templates"""
    context = {
        'sitename': settings.SITENAME,
        'user_email': request.session.get('email'),
        'user_name': request.session.get('name'),
    }

    # Add user preferences if logged in
    if context['user_email']:
        try:
            prefs = UserPreferences.objects.get(email=context['user_email'])
            context.update({
                'energyunit': prefs.energyunit,
                'medium': prefs.medium,
                'waveunit': prefs.waveunit,
                'vdwformat': prefs.vdwformat,
                'isotopic_scaling': prefs.isotopic_scaling,
            })
        except UserPreferences.DoesNotExist:
            # Use defaults
            context.update({
                'energyunit': 'eV',
                'medium': 'air',
                'waveunit': 'angstrom',
                'vdwformat': 'default',
                'isotopic_scaling': 'on',
            })

    return context


def index(request):
    """Main page - shows about_vald.html content"""
    context = get_user_context(request)

    # Handle page navigation from top form
    if request.method == 'POST':
        page = request.POST.get('page')

        if page == 'logout':
            request.session.flush()
            return redirect('vald:index')

        page_map = {
            'extractall': 'vald:extractall',
            'extractelement': 'vald:extractelement',
            'extractstellar': 'vald:extractstellar',
            'showline': 'vald:showline',
            'showline-online': 'vald:showline_online',
        }

        if page in page_map:
            return redirect(page_map[page])

    # Show about_vald.html content
    doc_file = settings.DOCUMENTATION_DIR / 'about_vald.html'
    if doc_file.exists():
        with open(doc_file, 'r') as f:
            content_html = f.read()
        context['content_html'] = content_html

    return render(request, 'vald/index.html', context)


def login(request):
    """Handle user login"""
    if request.method == 'POST':
        email = request.POST.get('user', '').strip()

        is_valid, user_name, is_local = validate_user_email(email)

        if is_valid:
            request.session['email'] = email
            request.session['name'] = user_name
            request.session['is_local'] = is_local

            # Get or create user preferences
            prefs, created = UserPreferences.objects.get_or_create(
                email=email,
                defaults={'name': user_name}
            )

            if created or not prefs.name:
                prefs.name = user_name
                prefs.save()

            return redirect('vald:index')
        else:
            context = get_user_context(request)
            return render(request, 'vald/notregistered.html', context)

    return redirect('vald:index')


def require_login(view_func):
    """Decorator to require login"""
    def wrapper(request, *args, **kwargs):
        if not request.session.get('email'):
            context = get_user_context(request)
            context['error'] = 'You are not logged in. Please log in and try again.'
            return render(request, 'vald/error.html', context)
        return view_func(request, *args, **kwargs)
    return wrapper


@require_login
def extractall(request):
    """Extract All form"""
    context = get_user_context(request)
    return render(request, 'vald/extractall.html', context)


@require_login
def extractelement(request):
    """Extract Element form"""
    context = get_user_context(request)
    return render(request, 'vald/extractelement.html', context)


@require_login
def extractstellar(request):
    """Extract Stellar form"""
    context = get_user_context(request)
    return render(request, 'vald/extractstellar.html', context)


@require_login
def showline(request):
    """Show Line form"""
    context = get_user_context(request)
    return render(request, 'vald/showline.html', context)


@require_login
def showline_online(request):
    """Show Line Online form"""
    context = get_user_context(request)
    return render(request, 'vald/showline-online.html', context)


@require_login
def showline_online_submit(request):
    """Execute Show Line Online and display results"""
    import subprocess
    from .persconfig import load_or_create_persconfig, compare_with_default

    context = get_user_context(request)

    if request.method != 'POST':
        return redirect('vald:showline_online')

    # Get form data
    wvl0 = request.POST.get('wvl0', '')
    win0 = request.POST.get('win0', '')
    el0 = request.POST.get('el0', '')
    pconf = request.POST.get('pconf', 'default')
    isotopic_scaling = request.POST.get('isotopic_scaling', 'on')

    # Determine config file to use
    email = request.session.get('email')
    if pconf == 'personal':
        # Try to get user's personal config from database
        try:
            persconf_obj = load_or_create_persconfig(
                email,
                settings.PERSCONFIG_DEFAULT,
                settings.PERSCONFIG_DIR / f"{email.replace('@', '_').replace('.', '_')}.cfg"
            )
            # For now, just use default - would need to write DB config to temp file
            configfile = str(settings.PERSCONFIG_DEFAULT)
            note = "NOTE: Custom configuration not yet fully supported for online extraction. Using default configuration instead."
        except:
            configfile = str(settings.PERSCONFIG_DEFAULT)
            note = "NOTE: Custom configuration file does not (yet) exist. Using default configuration instead."
    else:
        configfile = str(settings.PERSCONFIG_DEFAULT)
        note = None

    # Build request content (same format as showline-online-req.txt)
    request_content = f"{wvl0}, {win0}\n{el0}\n{configfile}\n"

    # Build command arguments
    args = [str(settings.VALD_BIN_PATH), '-html']
    if isotopic_scaling == 'off':
        args.append('-noisotopic')

    # Execute the showline binary
    try:
        if not settings.VALD_BIN_PATH.exists():
            context['error'] = f'Show Line binary not found at: {settings.VALD_BIN_PATH}'
            return render(request, 'vald/error.html', context)

        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        stdout, stderr = process.communicate(input=request_content, timeout=30)

        if process.returncode != 0:
            context['error'] = f'Show Line execution failed with return code {process.returncode}.\nError: {stderr}'
            return render(request, 'vald/error.html', context)

        context['output'] = stdout
        context['note'] = note
        return render(request, 'vald/showline-online-result.html', context)

    except subprocess.TimeoutExpired:
        context['error'] = 'Show Line execution timed out (30 seconds)'
        return render(request, 'vald/error.html', context)
    except Exception as e:
        context['error'] = f'Error executing Show Line: {e}'
        return render(request, 'vald/error.html', context)


def submit_request(request):
    """Handle form submissions"""
    if request.method != 'POST':
        return redirect('vald:index')

    reqtype = request.POST.get('reqtype')
    context = get_user_context(request)

    # Contact form is accessible to everyone
    if reqtype == 'contact':
        return handle_contact_request(request)

    # All other requests require login
    if not request.session.get('email'):
        context['error'] = 'You are not logged in. Please log in and try again.'
        return render(request, 'vald/error.html', context)

    # Handle different request types
    handlers = {
        'extractall': handle_extract_request,
        'extractelement': handle_extract_request,
        'extractstellar': handle_extract_request,
        'showline': handle_extract_request,
    }

    handler = handlers.get(reqtype)
    if handler:
        return handler(request)

    context['error'] = 'Invalid request type.'
    return render(request, 'vald/error.html', context)


def handle_contact_request(request):
    """Handle contact form submission"""
    context = get_user_context(request)

    # Get form data
    message = request.POST.get('message', '')
    contactemail = request.POST.get('contactemail', '')
    contactname = request.POST.get('contactname', '')
    affiliation = request.POST.get('affiliation', '')
    position = request.POST.get('position', '')
    manager = request.POST.get('manager', 'valdadministrator')
    subject = request.POST.get('subject', 'VALD contact request')
    permission = request.POST.get('permission', '')
    privacy_statement = request.POST.get('privacy_statement', '')

    # Spam check
    if not spam_check(message):
        context['error'] = 'Your message was rejected because the content was classed as spam.'
        return render(request, 'vald/error.html', context)

    # Prepare email content
    email_context = {
        'contactemail': contactemail,
        'contactname': contactname,
        'affiliation': affiliation,
        'position': position,
        'message': message,
        'permission': permission,
        'privacy_statement': privacy_statement,
    }

    mail_content = render_request_template('contact', email_context)

    # Determine recipient based on manager selection
    recipient_map = {
        'valdadministrator': settings.VALD_REQUEST_EMAIL,
        'valdwebmanager': settings.VALD_REQUEST_EMAIL,
    }
    recipient = recipient_map.get(manager, settings.VALD_REQUEST_EMAIL)

    try:
        send_mail(
            subject,
            mail_content,
            settings.DEFAULT_FROM_EMAIL,
            [recipient],
            fail_silently=False,
        )
        return render(request, 'vald/confirmcontact.html', context)
    except Exception as e:
        context['error'] = f'A problem occurred when processing your input: {e}'
        return render(request, 'vald/error.html', context)


def handle_extract_request(request):
    """Handle extract/showline form submissions"""
    context = get_user_context(request)

    reqtype = request.POST.get('reqtype')
    user_email = request.session.get('email')

    # Build email context from POST data
    email_context = {
        'reqtype': reqtype,
        'user_email': user_email,
    }

    # Get user preferences
    try:
        prefs = UserPreferences.objects.get(email=user_email)
        email_context.update({
            'energyunit': prefs.energyunit,
            'medium': prefs.medium,
            'waveunit': prefs.waveunit,
            'vdwformat': prefs.vdwformat,
            'isotopic_scaling': prefs.isotopic_scaling,
        })
    except UserPreferences.DoesNotExist:
        # Use defaults
        email_context.update({
            'energyunit': 'eV',
            'medium': 'air',
            'waveunit': 'angstrom',
            'vdwformat': 'default',
            'isotopic_scaling': 'on',
        })

    # Copy all POST data to context
    for key, value in request.POST.items():
        if key != 'csrfmiddlewaretoken':
            email_context[key] = value

    # Render request template
    mail_content = render_request_template(reqtype, email_context)
    subject = request.POST.get('subject', f'VALD {reqtype} request')

    try:
        send_mail(
            subject,
            mail_content,
            user_email,
            [settings.VALD_REQUEST_EMAIL],
            fail_silently=False,
        )
        return render(request, 'vald/confirmsubmitted.html', context)
    except Exception as e:
        context['error'] = f'A problem occurred when processing your input: {e}'
        return render(request, 'vald/error.html', context)


@require_login
def unitselection(request):
    """Unit selection page"""
    context = get_user_context(request)
    return render(request, 'vald/unitselection.html', context)


@require_login
def save_units(request):
    """Save unit preferences"""
    if request.method != 'POST':
        return redirect('vald:unitselection')

    email = request.session.get('email')
    prefs, created = UserPreferences.objects.get_or_create(email=email)

    # Update preferences from POST data
    prefs.energyunit = request.POST.get('energyunit', 'eV')
    prefs.medium = request.POST.get('medium', 'air')
    prefs.waveunit = request.POST.get('waveunit', 'angstrom')
    prefs.vdwformat = request.POST.get('vdwformat', 'default')
    prefs.isotopic_scaling = request.POST.get('isotopic_scaling', 'on')
    prefs.save()

    context = get_user_context(request)
    context['unitsupdated'] = True
    return render(request, 'vald/unitselection.html', context)


def documentation(request, docpage):
    """Display documentation pages"""
    context = get_user_context(request)

    # Special handling for contact.html
    if docpage == 'contact.html':
        return render(request, 'vald/contact.html', context)

    doc_file = settings.DOCUMENTATION_DIR / docpage
    if doc_file.exists() and doc_file.is_file():
        with open(doc_file, 'r') as f:
            content_html = f.read()
        context['content_html'] = content_html
        return render(request, 'vald/documentation.html', context)

    context['error'] = f'Documentation page "{docpage}" not found.'
    return render(request, 'vald/error.html', context)


def news(request, newsitem=0):
    """Display news items"""
    context = get_user_context(request)

    # Get list of news files
    news_files = sorted(glob.glob(str(settings.NEWS_DIR / '[0-9]*')), reverse=True)

    if not news_files:
        context['error'] = 'No news items found.'
        return render(request, 'vald/error.html', context)

    # Ensure newsitem is within range
    newsitem = int(newsitem)
    if newsitem < 0 or newsitem >= len(news_files):
        newsitem = 0

    # Read news content
    with open(news_files[newsitem], 'r') as f:
        news_content = f.read()

    # Build file list for navigation
    file_list = [Path(f).name for f in news_files]

    context.update({
        'news_content': news_content,
        'news_files': file_list,
        'current_index': newsitem,
    })

    return render(request, 'vald/news.html', context)


@require_login
def persconf(request):
    """Personal configuration page"""
    from .persconfig import (
        load_or_create_persconfig,
        compare_with_default,
        restore_linelist_to_default,
    )

    context = get_user_context(request)
    email = request.session.get('email')

    # Load or create config
    persconf_obj = load_or_create_persconfig(
        email,
        settings.PERSCONFIG_DEFAULT,
        settings.PERSCONFIG_DIR / f"{email.replace('@', '_').replace('.', '_')}.cfg"
    )

    # Compare with default to mark modifications
    persconf_obj = compare_with_default(persconf_obj, settings.PERSCONFIG_DEFAULT)

    # Handle actions (edit, save, restore, cancel)
    action = request.POST.get('action') if request.method == 'POST' else None
    editid = request.POST.get('editid')

    if action == 'save' and editid:
        # Save edited linelist
        try:
            linelist = persconf_obj.linelists.get(list_id=int(editid))

            # Update commented status
            linelist.commented = not request.POST.get('linelist-checked')

            # Update editable parameters (5-13)
            for j in range(5, 14):
                param_value = request.POST.get(f'edit-val-{j}', '')
                linelist.set_param(j, param_value)

            linelist.save()

            # Re-compare with default
            persconf_obj = compare_with_default(persconf_obj, settings.PERSCONFIG_DEFAULT)

        except (LineList.DoesNotExist, ValueError):
            pass

        # Clear edit mode
        editid = None
        action = None

    elif action == 'restore' and editid:
        # Restore linelist to default
        try:
            linelist = persconf_obj.linelists.get(list_id=int(editid))
            restore_linelist_to_default(linelist, settings.PERSCONFIG_DEFAULT)
        except (LineList.DoesNotExist, ValueError):
            pass

        editid = None
        action = None

    elif action == 'cancel':
        editid = None
        action = None

    # Build context for template
    linelists = persconf_obj.linelists.all().order_by('list_id')

    context.update({
        'linelists': linelists,
        'editid': int(editid) if editid and action == 'edit' else None,
        'action': action,
    })

    return render(request, 'vald/persconf.html', context)
