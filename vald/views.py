from django.shortcuts import render, redirect
from django.http import HttpResponse
from django.conf import settings
from django.core.mail import send_mail
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.urls import reverse
from pathlib import Path
import glob

from .models import Request, User, UserEmail
from .forms import (
    PasswordResetRequestForm,
    PasswordResetForm,
    RegistrationForm,
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


def get_current_user(request):
    """Get the User object from session. Returns None if not logged in."""
    user_id = request.session.get('user_id')
    if not user_id:
        return None
    try:
        return User.objects.get(id=user_id)
    except User.DoesNotExist:
        return None


def get_user_context(request):
    """Get common context data for templates"""
    context = {
        'sitename': settings.SITENAME,
        'user_email': request.session.get('email'),
        'user_name': request.session.get('name'),
    }

    # Add user preferences if logged in
    user = get_current_user(request) if request.session.get('user_id') else None
    if user:
        # Load preferences from database
        prefs = user.get_preferences()
        context.update(prefs.as_dict())

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
            is_valid, user_name = validate_user_email(email)
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
            activation_path = reverse('vald:activate_account', kwargs={'token': token})
            activation_url = request.build_absolute_uri(activation_path)

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
            reset_path = reverse('vald:request_password_reset')
            reset_url = request.build_absolute_uri(reset_path)
            messages.error(
                request,
                f'Invalid password. <a href="{reset_url}">Forgot your password?</a>',
                extra_tags='safe'
            )
            return redirect('vald:index')

        # Login successful
        request.session['email'] = email
        request.session['name'] = user.name
        request.session['user_id'] = user.id

        # Set the login email as primary (tracks actively used email)
        user.emails.update(is_primary=False)  # Clear all primary flags
        UserEmail.objects.filter(user=user, email=email).update(is_primary=True)

        # User preferences are now file-based, no DB object needed

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

        # Store in session for password setting
        request.session['activation_email'] = user.primary_email
        request.session['activation_name'] = user.name
        request.session['activation_token'] = token

        context = get_user_context(request)
        context.update({
            'email': user.primary_email,
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

        # User preferences are now file-based, no DB object needed

        messages.success(request, 'Password set successfully! You are now logged in.')
        return redirect('vald:index')

    except User.DoesNotExist:
        messages.error(request, 'Invalid or expired activation link. Please request a new one by logging in.')
        return redirect('vald:index')


def request_password_reset(request):
    """Handle password reset request form"""
    context = get_user_context(request)

    if request.method == 'POST':
        form = PasswordResetRequestForm(request.POST)

        if not form.is_valid():
            for field, errors in form.errors.items():
                for error in errors:
                    field_label = form.fields[field].label if field in form.fields else field
                    messages.error(request, f"{field_label}: {error}")
            context['form'] = form
            return render(request, 'vald/request_password_reset.html', context)

        email = form.cleaned_data['email']

        # Check if user exists
        try:
            user_email = UserEmail.objects.select_related('user').get(email=email)
            user = user_email.user

            # Generate reset token
            token = user.generate_activation_token()
            user.save()

            # Build reset URL
            reset_path = reverse('vald:reset_password', kwargs={'token': token})
            reset_url = request.build_absolute_uri(reset_path)

            # Send reset email
            email_subject = 'VALD Password Reset'
            email_body = f"""Hello {user.name},

You requested to reset your password for your VALD account.

To reset your password, please click the link below:

{reset_url}

This link will expire in 7 days.

If you did not request this password reset, please ignore this email.

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
                messages.success(request, f'Password reset email sent to {email}. Please check your inbox.')
            except Exception as e:
                messages.error(request, f'Failed to send reset email: {e}. Please contact the administrator.')

        except UserEmail.DoesNotExist:
            # Don't reveal if email exists or not (security best practice)
            messages.success(request, f'If {email} is registered, a password reset email has been sent.')

        return redirect('vald:index')

    # GET request - show form
    context['form'] = PasswordResetRequestForm()
    return render(request, 'vald/request_password_reset.html', context)


def reset_password(request, token):
    """Handle password reset with token"""
    context = get_user_context(request)

    # Verify token
    try:
        user = User.objects.get(activation_token=token)
    except User.DoesNotExist:
        messages.error(request, 'Invalid or expired password reset link.')
        return redirect('vald:index')

    if request.method == 'POST':
        form = PasswordResetForm(request.POST)

        if not form.is_valid():
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, error)
            context['form'] = form
            context['token'] = token
            return render(request, 'vald/reset_password.html', context)

        # Set new password
        user.set_password(form.cleaned_data['password'])
        user.save()

        messages.success(request, 'Password reset successfully! You can now log in with your new password.')
        return redirect('vald:index')

    # GET request - show form
    context['form'] = PasswordResetForm()
    context['token'] = token
    context['user_name'] = user.name
    return render(request, 'vald/reset_password.html', context)


def require_login(view_func):
    """Decorator to require login"""
    def wrapper(request, *args, **kwargs):
        if not request.session.get('user_id'):
            messages.error(request, 'You are not logged in. Please log in and try again.')
            return redirect('vald:index')
        return view_func(request, *args, **kwargs)
    return wrapper


@require_login
def extractall(request):
    """Extract All form"""
    context = get_user_context(request)
    user = get_current_user(request)

    # Check if modifying an existing request
    modify_uuid = request.GET.get('modify')
    initial_data = {}

    if modify_uuid:
        try:
            req_obj = Request.objects.get(uuid=modify_uuid)
            # Security: only allow user to modify their own requests
            if req_obj.user_id == user.id:
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
    user = get_current_user(request)

    # Check if modifying an existing request
    modify_uuid = request.GET.get('modify')
    initial_data = {}

    if modify_uuid:
        try:
            req_obj = Request.objects.get(uuid=modify_uuid)
            # Security: only allow user to modify their own requests
            if req_obj.user_id == user.id:
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
    user = get_current_user(request)

    # Check if modifying an existing request
    modify_uuid = request.GET.get('modify')
    initial_data = {}

    if modify_uuid:
        try:
            req_obj = Request.objects.get(uuid=modify_uuid)
            # Security: only allow user to modify their own requests
            if req_obj.user_id == user.id:
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
    """Show Line form - uses simplified ONLINE form"""
    context = get_user_context(request)
    user = get_current_user(request)

    # Check if modifying an existing request
    modify_uuid = request.GET.get('modify')
    initial_data = {}

    if modify_uuid:
        try:
            req_obj = Request.objects.get(uuid=modify_uuid)
            # Security: only allow user to modify their own requests
            if req_obj.user_id == user.id:
                initial_data = req_obj.parameters
                messages.info(request, 'Form pre-filled with previous request values.')
            else:
                messages.error(request, 'You do not have permission to modify this request.')
        except Request.DoesNotExist:
            messages.error(request, 'Request not found.')

    context['form'] = ShowLineOnlineForm(initial=initial_data)
    return render(request, 'vald/showline.html', context)


@require_login
def showline_online(request):
    """Show Line form - same as showline()"""
    return showline(request)


# Removed - showline now uses queue like other extracts


def submit_request(request):
    """Handle form submissions"""
    if request.method != 'POST':
        return redirect('vald:index')

    reqtype = request.POST.get('reqtype')
    context = get_user_context(request)

    # Contact and registration forms are accessible to everyone
    if reqtype == 'contact':
        return handle_contact_request(request)
    elif reqtype == 'registration':
        return handle_registration_request(request)

    # All other requests require login
    if not request.session.get('user_id'):
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
        context['registration_form'] = RegistrationForm()
        return render(request, 'vald/contact.html', context)

    # Spam check
    message = form.cleaned_data['message']
    if not spam_check(message):
        messages.error(request, 'Your message was rejected because the content was classed as spam.')
        context['form'] = form
        context['registration_form'] = RegistrationForm()
        return render(request, 'vald/contact.html', context)

    # Prepare email content
    email_context = {
        'contactemail': form.cleaned_data['contactemail'],
        'message': form.cleaned_data['message'],
        'permission': form.cleaned_data['permission'],
        'privacy_statement': form.cleaned_data['privacy_statement'],
    }

    mail_content = render_request_template('contact', email_context)

    # Determine recipient based on manager selection
    manager = form.cleaned_data['manager']
    recipient_map = {
        'valdadministrator': settings.VALD_ADMIN_EMAIL,
        'webmaster': settings.VALD_WEBMASTER_EMAIL,
    }
    recipient = recipient_map.get(manager, settings.VALD_ADMIN_EMAIL)

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
        context['registration_form'] = RegistrationForm()
        return render(request, 'vald/contact.html', context)


def handle_registration_request(request):
    """Handle registration form submission"""
    context = get_user_context(request)
    form = RegistrationForm(request.POST)

    if not form.is_valid():
        # Show form errors with field names
        for field, errors in form.errors.items():
            for error in errors:
                field_label = form.fields[field].label if field in form.fields else field
                messages.error(request, f"{field_label}: {error}")
        context['registration_form'] = form
        context['form'] = ContactForm()
        return render(request, 'vald/contact.html', context)

    # Create new user with is_active=False (pending admin approval)
    user = User.objects.create(
        name=form.cleaned_data['name'],
        affiliation=form.cleaned_data['affiliation'],
        password=None,  # No password yet - needs activation
        is_active=False  # Requires admin approval
    )

    # Create email record
    UserEmail.objects.create(
        user=user,
        email=form.cleaned_data['email'],
        is_primary=True
    )

    messages.success(
        request,
        f"Registration submitted successfully! Your account for {form.cleaned_data['email']} "
        "is pending approval. You will receive an email once your account is activated."
    )

    # Return clean forms
    context['registration_form'] = RegistrationForm()
    context['form'] = ContactForm()
    return render(request, 'vald/contact.html', context)


def handle_extract_request(request):
    """Handle extract/showline form submissions"""
    context = get_user_context(request)
    reqtype = request.POST.get('reqtype')
    user = get_current_user(request)

    if not user:
        messages.error(request, 'User not found. Please log in again.')
        return redirect('vald:index')

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
        'user_email': user.primary_email,
    }

    # Get user preferences from database
    prefs = user.get_preferences()
    email_context.update(prefs.as_dict())

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

    # Merge user preferences into parameters for backend processing
    request_params = form.cleaned_data.copy()
    request_params.update(prefs.as_dict())

    # Check queue capacity before creating request
    from .backend import check_queue_capacity, notify_queue_full
    has_capacity, current_count, max_size = check_queue_capacity()
    if not has_capacity:
        notify_queue_full()
        messages.error(
            request,
            f'Server is busy processing requests ({current_count}/{max_size} in queue). '
            'Please try again in a few minutes.'
        )
        context['form'] = form
        template_map = {
            'extractall': 'vald/extractall.html',
            'extractelement': 'vald/extractelement.html',
            'extractstellar': 'vald/extractstellar.html',
            'showline': 'vald/showline.html',
        }
        return render(request, template_map[reqtype], context)

    # Create Request record for tracking
    req_obj = Request.objects.create(
        user=user,
        request_type=reqtype,
        parameters=request_params,
        status='pending'
    )

    # Start background processing
    import threading

    def process_request_background():
        """Process request in background thread"""
        from django.core.mail import send_mail

        try:
            # Import here to avoid circular imports
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

                # Send email if user selected email delivery
                viaftp = req_obj.parameters.get('viaftp', 'email')
                if viaftp == 'email':
                    # Build URLs for email
                    request_path = reverse('vald:request_detail', kwargs={'uuid': req_obj.uuid})
                    download_path = reverse('vald:download_request', kwargs={'uuid': req_obj.uuid})
                    bib_download_path = reverse('vald:download_bib_request', kwargs={'uuid': req_obj.uuid})
                    my_requests_path = reverse('vald:my_requests')

                    # Use SITE_URL as base for email links (no request object in background)
                    base_url = getattr(settings, 'SITE_URL', settings.SITENAME)
                    # Add FORCE_SCRIPT_NAME prefix if configured (e.g., /new)
                    script_name = getattr(settings, 'FORCE_SCRIPT_NAME', '')
                    request_url = f"{base_url}{script_name}{request_path}"
                    download_url = f"{base_url}{script_name}{download_path}"
                    bib_download_url = f"{base_url}{script_name}{bib_download_path}"
                    my_requests_url = f"{base_url}{script_name}{my_requests_path}"

                    # Send email
                    from django.core.mail import EmailMessage
                    from pathlib import Path

                    subject = f"VALD {req_obj.request_type} results ready"

                    # Build download links section
                    download_links = f"Main results: {download_url}"
                    if req_obj.bib_output_exists():
                        download_links += f"\nBibliography: {bib_download_url}"

                    body = f"""Your VALD extraction request has completed successfully.

Request Type: {req_obj.request_type}
Submitted: {req_obj.created_at.strftime('%Y-%m-%d %H:%M:%S')}

Your results are attached to this email and are also available for download:

{download_links}
Request details: {request_url}

You can modify and resubmit this request with different parameters from:
{my_requests_url}

Files are available for download for 48 hours.

---
Vienna Atomic Line Database (VALD)
{settings.SITENAME}
"""

                    # Create email with attachments
                    email = EmailMessage(
                        subject=subject,
                        body=body,
                        from_email=settings.DEFAULT_FROM_EMAIL,
                        to=[req_obj.user_email]
                    )

                    # Attach main results file
                    output_path = Path(req_obj.output_file)
                    if output_path.exists():
                        email.attach_file(str(output_path))

                    # Attach bibliography file if exists (only for extract requests)
                    if req_obj.bib_output_exists():
                        bib_file = req_obj.get_bib_output_file()
                        email.attach_file(str(bib_file))

                    email.send(fail_silently=True)

            else:
                # Processing failed
                req_obj.status = 'failed'
                req_obj.error_message = result
                req_obj.save()

        except Exception as e:
            # Mark request as failed
            req_obj.status = 'failed'
            req_obj.error_message = str(e)
            req_obj.save()

    # Start background thread
    thread = threading.Thread(target=process_request_background, daemon=True)
    thread.start()

    # Immediately redirect to request detail page
    messages.success(request, 'Your request has been submitted and is being processed.')
    return redirect('vald:request_detail', uuid=req_obj.uuid)


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

    user = get_current_user(request)
    if not user:
        messages.error(request, 'Could not save preferences: user not found.')
        return redirect('vald:unitselection')

    # Get or create preferences and update from POST data
    prefs = user.get_preferences()
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
        context['registration_form'] = RegistrationForm()
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


def news(request, newsitem=None):
    """Display news items"""
    context = get_user_context(request)

    # Get list of news files
    news_files = sorted(glob.glob(str(settings.NEWS_DIR / '[0-9]*')), reverse=True)

    if not news_files:
        context['error'] = 'No news items found.'
        return render(request, 'vald/error.html', context)

    # Build file list for navigation
    file_list = [Path(f).name for f in news_files]

    # If newsitem is None, show all news items
    if newsitem is None:
        all_news = []
        for news_file in news_files:
            with open(news_file, 'r') as f:
                all_news.append({
                    'filename': Path(news_file).name,
                    'content': f.read()
                })

        context.update({
            'show_all': True,
            'all_news': all_news,
            'news_files': file_list,
        })
    else:
        # Show single news item
        newsitem = int(newsitem)
        if newsitem < 0 or newsitem >= len(news_files):
            newsitem = 0

        # Read news content
        with open(news_files[newsitem], 'r') as f:
            news_content = f.read()

        context.update({
            'show_all': False,
            'news_content': news_content,
            'news_files': file_list,
            'current_index': newsitem,
        })

    return render(request, 'vald/news.html', context)


@require_login
def persconf(request):
    """Personal configuration page - file-based implementation"""
    from .persconfig import read_persconfig_file, write_persconfig_file

    context = get_user_context(request)
    user = get_current_user(request)

    if not user or not user.client_name:
        messages.error(request, 'Could not determine user name.')
        return redirect('vald:index')

    user_config_path = settings.PERSCONFIG_DIR / f"{user.client_name}.cfg"
    default_config_path = settings.PERSCONFIG_DEFAULT

    # Read user config (or use default if doesn't exist)
    if user_config_path.exists():
        hidden_params, linelists = read_persconfig_file(user_config_path)
    else:
        hidden_params, linelists = read_persconfig_file(default_config_path)

    # Read default config for comparison
    default_hidden_params, default_linelists = read_persconfig_file(default_config_path)
    default_lookup = {ll['id']: ll for ll in default_linelists}

    # Mark modifications by comparing with default
    for ll in linelists:
        ll['mod_comment'] = False
        ll['mod_flags'] = [False] * 15

        default_ll = default_lookup.get(ll['id'])
        if default_ll:
            # Check if commented status differs
            if ll['commented'] != default_ll['commented']:
                ll['mod_comment'] = True

            # Check each parameter
            for j in range(1, 14):
                user_val = ll['params'][j] if j < len(ll['params']) else ''
                default_val = default_ll['params'][j] if j < len(default_ll['params']) else ''
                if user_val != default_val:
                    ll['mod_flags'][j] = True

    # Handle actions (edit, save, restore, cancel, reset_to_default)
    action = request.POST.get('action') if request.method == 'POST' else None
    editid = request.POST.get('editid')

    if action == 'reset_to_default':
        # Delete user's config file to reset to default
        if user_config_path.exists():
            user_config_path.unlink()
            messages.success(request, 'Personal configuration has been reset to VALD default.')
        else:
            messages.info(request, 'You are already using the VALD default configuration.')

        # Re-read config (will use default now)
        hidden_params, linelists = read_persconfig_file(default_config_path)

        # Re-mark modifications (should be all False now since we're using default)
        for ll in linelists:
            ll['mod_comment'] = False
            ll['mod_flags'] = [False] * 15

    elif action == 'save' and editid:
        # Save edited linelist
        try:
            editid_int = int(editid)
            linelist = None
            for ll in linelists:
                if ll['id'] == editid_int:
                    linelist = ll
                    break

            if linelist:
                # Update commented status
                linelist['commented'] = not request.POST.get('linelist-checked')

                # Update editable parameters (5-13)
                for j in range(5, 14):
                    param_value = request.POST.get(f'edit-val-{j}', '')
                    linelist['params'][j] = param_value

                # Write back to file
                write_persconfig_file(user_config_path, hidden_params, linelists)

                # Re-read to refresh modification flags
                hidden_params, linelists = read_persconfig_file(user_config_path)

                # Re-mark modifications
                for ll in linelists:
                    ll['mod_comment'] = False
                    ll['mod_flags'] = [False] * 15
                    default_ll = default_lookup.get(ll['id'])
                    if default_ll:
                        if ll['commented'] != default_ll['commented']:
                            ll['mod_comment'] = True
                        for j in range(1, 14):
                            user_val = ll['params'][j] if j < len(ll['params']) else ''
                            default_val = default_ll['params'][j] if j < len(default_ll['params']) else ''
                            if user_val != default_val:
                                ll['mod_flags'][j] = True

                messages.success(request, f'Linelist "{linelist["name"]}" has been saved successfully.')
            else:
                messages.error(request, 'Linelist not found.')
        except (ValueError, KeyError) as e:
            messages.error(request, f'Failed to save linelist: {e}')

        # Clear edit mode
        editid = None
        action = None

    elif action == 'restore' and editid:
        # Restore linelist to default
        try:
            editid_int = int(editid)
            linelist = None
            for ll in linelists:
                if ll['id'] == editid_int:
                    linelist = ll
                    break

            if linelist:
                # Find default values
                default_ll = default_lookup.get(editid_int)
                if default_ll:
                    # Restore from default
                    linelist['commented'] = default_ll['commented']
                    linelist['params'] = default_ll['params'][:]
                    linelist['name'] = default_ll['name']

                    # Write back to file
                    write_persconfig_file(user_config_path, hidden_params, linelists)

                    messages.success(request, f'Linelist "{linelist["name"]}" has been restored to default.')
                else:
                    messages.error(request, 'Default values not found.')
            else:
                messages.error(request, 'Linelist not found.')
        except (ValueError, KeyError) as e:
            messages.error(request, f'Failed to restore linelist: {e}')

        # Clear edit mode
        editid = None
        action = None

        # Re-read config
        if user_config_path.exists():
            hidden_params, linelists = read_persconfig_file(user_config_path)
        else:
            hidden_params, linelists = read_persconfig_file(default_config_path)

        # Re-mark modifications
        for ll in linelists:
            ll['mod_comment'] = False
            ll['mod_flags'] = [False] * 15
            default_ll = default_lookup.get(ll['id'])
            if default_ll:
                if ll['commented'] != default_ll['commented']:
                    ll['mod_comment'] = True
                for j in range(1, 14):
                    user_val = ll['params'][j] if j < len(ll['params']) else ''
                    default_val = default_ll['params'][j] if j < len(default_ll['params']) else ''
                    if user_val != default_val:
                        ll['mod_flags'][j] = True

    elif action == 'cancel':
        editid = None
        action = None

    # Build context for template
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
    user = get_current_user(request)

    # Get all requests for this user (regardless of which email they used)
    requests = Request.objects.filter(user=user).order_by('-created_at')

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
    from .backend import format_request_file, uuid_to_6digit

    context = get_user_context(request)
    user = get_current_user(request)

    try:
        req_obj = Request.objects.get(uuid=uuid)

        # Security: only allow user to view their own requests
        if req_obj.user_id != user.id:
            messages.error(request, 'You do not have permission to view this request.')
            return redirect('vald:my_requests')

        # Calculate backend ID (6-digit hash of UUID)
        backend_id = uuid_to_6digit(req_obj.uuid)

        # Format request file content for display
        request_file_content = format_request_file(req_obj)

        # Check if output file exists
        output_ready = req_obj.output_exists()
        output_size = req_obj.get_output_size() if output_ready else None

        # Check if bib output file exists
        bib_output_ready = req_obj.bib_output_exists()
        bib_output_size = req_obj.get_bib_output_size() if bib_output_ready else None

        # For showline requests, read and display output content inline
        output_content = None
        if req_obj.request_type == 'showline' and output_ready:
            try:
                with open(req_obj.output_file, 'r') as f:
                    output_content = f.read()
            except Exception:
                pass

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
            'backend_id': backend_id,
            'request_file_content': request_file_content,
            'output_ready': output_ready,
            'output_size': output_size,
            'bib_output_ready': bib_output_ready,
            'bib_output_size': bib_output_size,
            'queue_position': queue_position,
            'output_content': output_content,
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

    user = get_current_user(request)

    try:
        req_obj = Request.objects.get(uuid=uuid)

        # Security: only allow user to download their own requests
        if req_obj.user_id != user.id:
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

    user = get_current_user(request)

    try:
        req_obj = Request.objects.get(uuid=uuid)

        # Security: only allow user to download their own requests
        if req_obj.user_id != user.id:
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
