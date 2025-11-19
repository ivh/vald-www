from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.conf import settings
from django.core.mail import send_mail
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from pathlib import Path
import glob

from .models import Request, UserPreferences, PersonalConfig, LineList, User, UserEmail
from .forms import (
    ExtractAllForm,
    ExtractElementForm,
    ExtractStellarForm,
    ShowLineForm,
    ContactForm,
    ShowLineOnlineForm,
)
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
    """Handle user login with password authentication"""
    if request.method == 'POST':
        email = request.POST.get('user', '').strip().lower()
        password = request.POST.get('password', '').strip()

        # Check if user exists in database
        try:
            user_email = UserEmail.objects.select_related('user').get(email=email)
            user = user_email.user
        except UserEmail.DoesNotExist:
            # Fallback: check if email is in register files (for new imports)
            is_valid, user_name, is_local = validate_user_email(email)
            if is_valid:
                messages.error(request, 'Your account has not been imported yet. Please contact the administrator.')
            else:
                messages.error(request, 'Email address not registered. Please use the contact form to register.')
            context = get_user_context(request)
            return render(request, 'vald/notregistered.html', context)

        # Check if user needs to activate (set password)
        if user.needs_activation():
            if password:
                messages.error(request, 'Your account needs activation. Check your email for the activation link.')
                return redirect('vald:index')

            # Generate activation token and send email
            token = user.generate_activation_token()
            user.save()

            # Build activation URL
            activation_url = request.build_absolute_uri(
                f"/activate/{token}/"
            )

            # Send activation email
            email_subject = 'VALD Account Activation'
            email_body = f"""Hello {user.name},

Welcome to VALD! This is your first time logging in.

To activate your account and set your password, please click the link below:

{activation_url}

This link will expire after you use it to set your password.

If you did not request this, please ignore this email.

Best regards,
VALD Team
"""

            try:
                send_mail(
                    email_subject,
                    email_body,
                    settings.DEFAULT_FROM_EMAIL,
                    [email],
                    fail_silently=False,
                )
                messages.success(request, f'Activation email sent to {email}. Please check your inbox and click the link to set your password.')
            except Exception as e:
                messages.error(request, f'Failed to send activation email: {e}. Please contact the administrator.')

            return redirect('vald:index')

        # User has password - check it
        if not password:
            messages.error(request, 'Please enter your password.')
            return redirect('vald:index')

        if not user.check_password(password):
            messages.error(request, 'Invalid password. Please try again.')
            return redirect('vald:index')

        # Login successful
        request.session['email'] = email
        request.session['name'] = user.name
        request.session['user_id'] = user.id

        # Set the login email as primary (tracks actively used email)
        user.emails.update(is_primary=False)  # Clear all primary flags
        UserEmail.objects.filter(user=user, email=email).update(is_primary=True)

        # Get or create user preferences
        prefs, created = UserPreferences.objects.get_or_create(
            email=email,
            defaults={'name': user.name}
        )

        if created or not prefs.name:
            prefs.name = user.name
            prefs.save()

        messages.success(request, f'Welcome, {user.name}! You have successfully logged in.')
        return redirect('vald:index')

    return redirect('vald:index')


def activate_account(request, token):
    """Verify activation token from email and show password setup page"""
    try:
        # Find user with this activation token
        user = User.objects.get(activation_token=token)

        # Verify user needs activation
        if not user.needs_activation():
            messages.info(request, 'Your account is already activated. Please login with your password.')
            return redirect('vald:index')

        # Get primary email
        primary_email = user.emails.filter(is_primary=True).first()
        if not primary_email:
            primary_email = user.emails.first()

        # Store in session for password setting
        request.session['activation_email'] = primary_email.email
        request.session['activation_name'] = user.name
        request.session['activation_token'] = token

        context = get_user_context(request)
        context.update({
            'email': primary_email.email,
            'user_name': user.name,
        })

        return render(request, 'vald/activate_account.html', context)

    except User.DoesNotExist:
        messages.error(request, 'Invalid or expired activation link. Please request a new one by logging in.')
        return redirect('vald:index')


def set_password(request):
    """Handle password setting for first-time activation"""
    if request.method != 'POST':
        return redirect('vald:index')

    activation_email = request.session.get('activation_email')
    activation_token = request.session.get('activation_token')

    if not activation_email or not activation_token:
        messages.error(request, 'Session expired. Please use the activation link from your email again.')
        return redirect('vald:index')

    password = request.POST.get('password', '').strip()
    password_confirm = request.POST.get('password_confirm', '').strip()

    # Validate passwords
    if not password:
        messages.error(request, 'Password cannot be empty.')
        return redirect('vald:activate_account', token=activation_token)

    if len(password) < 8:
        messages.error(request, 'Password must be at least 8 characters long.')
        return redirect('vald:activate_account', token=activation_token)

    if password != password_confirm:
        messages.error(request, 'Passwords do not match.')
        return redirect('vald:activate_account', token=activation_token)

    # Get user and verify token
    try:
        user = User.objects.get(activation_token=activation_token)

        # Verify email matches
        user_emails = user.emails.values_list('email', flat=True)
        if activation_email not in user_emails:
            messages.error(request, 'Invalid session. Please use the activation link from your email again.')
            return redirect('vald:index')

        # Set password (this also clears the activation_token)
        user.set_password(password)
        user.save()

        # Set the activation email as primary (they just verified it)
        # Clear any existing primary flags first
        user.emails.update(is_primary=False)
        # Set this email as primary
        UserEmail.objects.filter(user=user, email=activation_email).update(is_primary=True)

        # Clear activation session data
        del request.session['activation_email']
        del request.session['activation_name']
        del request.session['activation_token']

        # Log user in
        request.session['email'] = activation_email
        request.session['name'] = user.name
        request.session['user_id'] = user.id

        # Get or create user preferences
        prefs, created = UserPreferences.objects.get_or_create(
            email=activation_email,
            defaults={'name': user.name}
        )

        if created or not prefs.name:
            prefs.name = user.name
            prefs.save()

        messages.success(request, 'Password set successfully! You are now logged in.')
        return redirect('vald:index')

    except User.DoesNotExist:
        messages.error(request, 'Invalid or expired activation link. Please request a new one by logging in.')
        return redirect('vald:index')


def require_login(view_func):
    """Decorator to require login"""
    def wrapper(request, *args, **kwargs):
        if not request.session.get('email'):
            messages.error(request, 'You are not logged in. Please log in and try again.')
            return redirect('vald:index')
        return view_func(request, *args, **kwargs)
    return wrapper


@require_login
def extractall(request):
    """Extract All form"""
    context = get_user_context(request)

    # Check if modifying an existing request
    modify_uuid = request.GET.get('modify')
    initial_data = {}

    if modify_uuid:
        try:
            req_obj = Request.objects.get(uuid=modify_uuid)
            # Security: only allow user to modify their own requests
            if req_obj.user_email == request.session.get('email'):
                initial_data = req_obj.parameters
                messages.info(request, 'Form pre-filled with previous request values.')
            else:
                messages.error(request, 'You do not have permission to modify this request.')
        except Request.DoesNotExist:
            messages.error(request, 'Request not found.')

    context['form'] = ExtractAllForm(initial=initial_data)
    return render(request, 'vald/extractall.html', context)


@require_login
def extractelement(request):
    """Extract Element form"""
    context = get_user_context(request)

    # Check if modifying an existing request
    modify_uuid = request.GET.get('modify')
    initial_data = {}

    if modify_uuid:
        try:
            req_obj = Request.objects.get(uuid=modify_uuid)
            # Security: only allow user to modify their own requests
            if req_obj.user_email == request.session.get('email'):
                initial_data = req_obj.parameters
                messages.info(request, 'Form pre-filled with previous request values.')
            else:
                messages.error(request, 'You do not have permission to modify this request.')
        except Request.DoesNotExist:
            messages.error(request, 'Request not found.')

    context['form'] = ExtractElementForm(initial=initial_data)
    return render(request, 'vald/extractelement.html', context)


@require_login
def extractstellar(request):
    """Extract Stellar form"""
    context = get_user_context(request)

    # Check if modifying an existing request
    modify_uuid = request.GET.get('modify')
    initial_data = {}

    if modify_uuid:
        try:
            req_obj = Request.objects.get(uuid=modify_uuid)
            # Security: only allow user to modify their own requests
            if req_obj.user_email == request.session.get('email'):
                initial_data = req_obj.parameters
                messages.info(request, 'Form pre-filled with previous request values.')
            else:
                messages.error(request, 'You do not have permission to modify this request.')
        except Request.DoesNotExist:
            messages.error(request, 'Request not found.')

    context['form'] = ExtractStellarForm(initial=initial_data)
    return render(request, 'vald/extractstellar.html', context)


@require_login
def showline(request):
    """Show Line form"""
    context = get_user_context(request)

    # Check if modifying an existing request
    modify_uuid = request.GET.get('modify')
    initial_data = {}

    if modify_uuid:
        try:
            req_obj = Request.objects.get(uuid=modify_uuid)
            # Security: only allow user to modify their own requests
            if req_obj.user_email == request.session.get('email'):
                initial_data = req_obj.parameters
                messages.info(request, 'Form pre-filled with previous request values.')
            else:
                messages.error(request, 'You do not have permission to modify this request.')
        except Request.DoesNotExist:
            messages.error(request, 'Request not found.')

    context['form'] = ShowLineForm(initial=initial_data)
    return render(request, 'vald/showline.html', context)


@require_login
def showline_online(request):
    """Show Line Online form"""
    context = get_user_context(request)
    context['form'] = ShowLineOnlineForm()
    return render(request, 'vald/showline-online.html', context)


@require_login
def showline_online_submit(request):
    """Execute Show Line Online and display results"""
    import subprocess
    from .persconfig import load_or_create_persconfig, compare_with_default

    context = get_user_context(request)

    if request.method != 'POST':
        return redirect('vald:showline_online')

    # Validate form
    form = ShowLineOnlineForm(request.POST)
    if not form.is_valid():
        # Show form errors with field names
        for field, errors in form.errors.items():
            for error in errors:
                field_label = form.fields[field].label if field in form.fields else field
                messages.error(request, f"{field_label}: {error}")
        context['form'] = form
        return render(request, 'vald/showline-online.html', context)

    # Get validated form data
    wvl0 = form.cleaned_data['wvl0']
    win0 = form.cleaned_data['win0']
    el0 = form.cleaned_data['el0']
    pconf = form.cleaned_data['pconf']
    isotopic_scaling = form.cleaned_data['isotopic_scaling']

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
    args = [str(settings.VALD_SHOWLINE_BIN), '-html']
    if isotopic_scaling == 'off':
        args.append('-noisotopic')

    # Execute the showline binary
    try:
        if not settings.VALD_SHOWLINE_BIN.exists():
            messages.error(request, f'Show Line binary not found at: {settings.VALD_SHOWLINE_BIN}')
            return redirect('vald:showline_online')

        process = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )

        stdout, stderr = process.communicate(input=request_content, timeout=30)

        if process.returncode != 0:
            messages.error(request, f'Show Line execution failed with return code {process.returncode}. Error: {stderr}')
            return redirect('vald:showline_online')

        context['output'] = stdout
        context['note'] = note
        if note:
            messages.warning(request, note)
        return render(request, 'vald/showline-online-result.html', context)

    except subprocess.TimeoutExpired:
        messages.error(request, 'Show Line execution timed out (30 seconds)')
        return redirect('vald:showline_online')
    except Exception as e:
        messages.error(request, f'Error executing Show Line: {e}')
        return redirect('vald:showline_online')


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
    form = ContactForm(request.POST)

    if not form.is_valid():
        # Show form errors with field names
        for field, errors in form.errors.items():
            for error in errors:
                field_label = form.fields[field].label if field in form.fields else field
                messages.error(request, f"{field_label}: {error}")
        context['form'] = form
        return render(request, 'vald/contact.html', context)

    # Spam check
    message = form.cleaned_data['message']
    if not spam_check(message):
        messages.error(request, 'Your message was rejected because the content was classed as spam.')
        context['form'] = form
        return render(request, 'vald/contact.html', context)

    # Prepare email content
    email_context = {
        'contactemail': form.cleaned_data['contactemail'],
        'contactname': form.cleaned_data['contactname'],
        'affiliation': form.cleaned_data['affiliation'],
        'position': form.cleaned_data['position'],
        'message': form.cleaned_data['message'],
        'permission': form.cleaned_data['permission'],
        'privacy_statement': form.cleaned_data['privacy_statement'],
    }

    mail_content = render_request_template('contact', email_context)

    # Determine recipient based on manager selection
    manager = form.cleaned_data['manager']
    recipient_map = {
        'valdadministrator': settings.VALD_REQUEST_EMAIL,
        'valdwebmanager': settings.VALD_REQUEST_EMAIL,
    }
    recipient = recipient_map.get(manager, settings.VALD_REQUEST_EMAIL)

    try:
        send_mail(
            'VALD contact request',
            mail_content,
            settings.DEFAULT_FROM_EMAIL,
            [recipient],
            fail_silently=False,
        )
        messages.success(request, 'Your message has been sent successfully.')
        return render(request, 'vald/confirmcontact.html', context)
    except Exception as e:
        messages.error(request, f'A problem occurred when processing your input: {e}')
        context['form'] = form
        return render(request, 'vald/contact.html', context)


def handle_extract_request(request):
    """Handle extract/showline form submissions"""
    context = get_user_context(request)
    reqtype = request.POST.get('reqtype')
    user_email = request.session.get('email')

    # Determine which form to use
    form_map = {
        'extractall': ExtractAllForm,
        'extractelement': ExtractElementForm,
        'extractstellar': ExtractStellarForm,
        'showline': ShowLineForm,
    }

    form_class = form_map.get(reqtype)
    if not form_class:
        messages.error(request, 'Invalid request type.')
        return redirect('vald:index')

    form = form_class(request.POST)

    if not form.is_valid():
        # Show form errors with field names
        for field, errors in form.errors.items():
            for error in errors:
                field_label = form.fields[field].label if field in form.fields else field
                messages.error(request, f"{field_label}: {error}")
        context['form'] = form
        # Redirect to the appropriate form page
        template_map = {
            'extractall': 'vald/extractall.html',
            'extractelement': 'vald/extractelement.html',
            'extractstellar': 'vald/extractstellar.html',
            'showline': 'vald/showline.html',
        }
        return render(request, template_map[reqtype], context)

    # Build email context from validated form data
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

    # Copy all cleaned data to context
    for key, value in form.cleaned_data.items():
        # Convert boolean fields to their expected values
        if isinstance(value, bool):
            if value and key.startswith('h'):  # hfssplit, hrad, etc.
                # These need their label values from the form
                field_values = {
                    'hfssplit': 'HFS splitting',
                    'hrad': 'have rad',
                    'hstark': 'have stark',
                    'hwaals': 'have waals',
                    'hlande': 'have lande',
                    'hterm': 'have term',
                }
                email_context[key] = field_values.get(key, str(value))
            else:
                email_context[key] = value
        else:
            email_context[key] = value

    # Create Request record for tracking
    req_obj = Request.objects.create(
        user_email=user_email,
        user_name=request.session.get('name', user_email),
        request_type=reqtype,
        parameters=form.cleaned_data,
        status='pending'
    )

    try:
        # Check if direct submission is enabled
        if getattr(settings, 'VALD_DIRECT_SUBMISSION', False):
            # Direct submission - bypass email system
            from .backend import submit_request_direct

            # Update status to processing
            req_obj.status = 'processing'
            req_obj.save()

            # Submit directly to backend
            success, result = submit_request_direct(req_obj)

            if success:
                # Update request with output file
                req_obj.status = 'complete'
                req_obj.output_file = result
                req_obj.save()
                messages.success(request, 'Your request has been processed successfully.')
            else:
                # Processing failed
                req_obj.status = 'failed'
                req_obj.error_message = result
                req_obj.save()
                messages.error(request, f'Request processing failed: {result}')

        else:
            # Email-based submission (legacy)
            mail_content = render_request_template(reqtype, email_context)
            subject = form.cleaned_data.get('subject', f'VALD {reqtype} request')

            send_mail(
                subject if subject else f'VALD {reqtype} request',
                mail_content,
                user_email,
                [settings.VALD_REQUEST_EMAIL],
                fail_silently=False,
            )
            messages.success(request, 'Your request has been submitted successfully.')

        # Redirect to request detail page
        return redirect('vald:request_detail', uuid=req_obj.uuid)

    except Exception as e:
        # Mark request as failed
        req_obj.status = 'failed'
        req_obj.save()

        messages.error(request, f'A problem occurred when processing your input: {e}')
        context['form'] = form
        template_map = {
            'extractall': 'vald/extractall.html',
            'extractelement': 'vald/extractelement.html',
            'extractstellar': 'vald/extractstellar.html',
            'showline': 'vald/showline.html',
        }
        return render(request, template_map[reqtype], context)


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

    messages.success(request, 'Your unit preferences have been saved successfully.')
    context = get_user_context(request)
    context['unitsupdated'] = True
    return render(request, 'vald/unitselection.html', context)


def documentation(request, docpage):
    """Display documentation pages"""
    context = get_user_context(request)

    # Special handling for contact.html
    if docpage == 'contact.html':
        context['form'] = ContactForm()
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

            messages.success(request, f'Linelist "{linelist.name}" has been saved successfully.')
        except (LineList.DoesNotExist, ValueError):
            messages.error(request, 'Failed to save linelist.')

        # Clear edit mode
        editid = None
        action = None

    elif action == 'restore' and editid:
        # Restore linelist to default
        try:
            linelist = persconf_obj.linelists.get(list_id=int(editid))
            restore_linelist_to_default(linelist, settings.PERSCONFIG_DEFAULT)
            messages.success(request, f'Linelist "{linelist.name}" has been restored to default.')
        except (LineList.DoesNotExist, ValueError):
            messages.error(request, 'Failed to restore linelist.')

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

@require_login
def my_requests(request):
    """Show all requests for the current user"""
    context = get_user_context(request)
    user_email = request.session.get('email')

    # Get all requests for this user
    requests = Request.objects.filter(user_email=user_email).order_by('-created_at')

    # Count by status
    pending_count = requests.filter(status__in=['pending', 'processing']).count()
    complete_count = requests.filter(status='complete').count()
    failed_count = requests.filter(status='failed').count()

    context.update({
        'requests': requests,
        'pending_count': pending_count,
        'complete_count': complete_count,
        'failed_count': failed_count,
    })

    return render(request, 'vald/my_requests.html', context)


@require_login
def request_detail(request, uuid):
    """Show details of a specific request"""
    context = get_user_context(request)
    user_email = request.session.get('email')

    try:
        req_obj = Request.objects.get(uuid=uuid)

        # Security: only allow user to view their own requests
        if req_obj.user_email != user_email:
            messages.error(request, 'You do not have permission to view this request.')
            return redirect('vald:my_requests')

        # Check if output file exists
        output_ready = req_obj.output_exists()
        output_size = req_obj.get_output_size() if output_ready else None

        # Check if bib output file exists
        bib_output_ready = req_obj.bib_output_exists()
        bib_output_size = req_obj.get_bib_output_size() if bib_output_ready else None

        # Calculate queue position (rough estimate)
        if req_obj.status == 'pending':
            queue_position = Request.objects.filter(
                status='pending',
                created_at__lt=req_obj.created_at
            ).count() + 1
        else:
            queue_position = None

        context.update({
            'req': req_obj,
            'output_ready': output_ready,
            'output_size': output_size,
            'bib_output_ready': bib_output_ready,
            'bib_output_size': bib_output_size,
            'queue_position': queue_position,
        })

        return render(request, 'vald/request_detail.html', context)

    except Request.DoesNotExist:
        messages.error(request, 'Request not found.')
        return redirect('vald:my_requests')


@require_login
def download_request(request, uuid):
    """Download the output file for a completed request"""
    from django.http import FileResponse, Http404
    import mimetypes

    user_email = request.session.get('email')

    try:
        req_obj = Request.objects.get(uuid=uuid)

        # Security: only allow user to download their own requests
        if req_obj.user_email != user_email:
            messages.error(request, 'You do not have permission to download this file.')
            return redirect('vald:my_requests')

        # Check that output file exists
        if not req_obj.output_exists():
            messages.error(request, 'Output file not found.')
            return redirect('vald:request_detail', uuid=uuid)

        # Serve the file
        file_path = Path(req_obj.output_file)

        # Determine content type
        content_type, _ = mimetypes.guess_type(file_path.name)
        if not content_type:
            content_type = 'application/octet-stream'

        # Open and serve the file
        response = FileResponse(
            open(file_path, 'rb'),
            content_type=content_type,
            as_attachment=True,
            filename=file_path.name
        )

        return response

    except Request.DoesNotExist:
        messages.error(request, 'Request not found.')
        return redirect('vald:my_requests')
    except Exception as e:
        messages.error(request, f'Error downloading file: {e}')
        return redirect('vald:request_detail', uuid=uuid)


@require_login
def download_bib_request(request, uuid):
    """Download the .bib.gz output file for a completed request"""
    from django.http import FileResponse, Http404
    import mimetypes

    user_email = request.session.get('email')

    try:
        req_obj = Request.objects.get(uuid=uuid)

        # Security: only allow user to download their own requests
        if req_obj.user_email != user_email:
            messages.error(request, 'You do not have permission to download this file.')
            return redirect('vald:my_requests')

        # Check that bib output file exists
        if not req_obj.bib_output_exists():
            messages.error(request, 'Bibliography file not found.')
            return redirect('vald:request_detail', uuid=uuid)

        # Serve the file
        file_path = Path(req_obj.get_bib_output_file())

        # Determine content type
        content_type, _ = mimetypes.guess_type(file_path.name)
        if not content_type:
            content_type = 'application/octet-stream'

        # Open and serve the file
        response = FileResponse(
            open(file_path, 'rb'),
            content_type=content_type,
            as_attachment=True,
            filename=file_path.name
        )

        return response

    except Request.DoesNotExist:
        messages.error(request, 'Request not found.')
        return redirect('vald:my_requests')
    except Exception as e:
        messages.error(request, f'Error downloading file: {e}')
        return redirect('vald:request_detail', uuid=uuid)
