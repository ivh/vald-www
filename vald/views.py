from django.shortcuts import render, redirect
from django.http import HttpResponse, JsonResponse
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.contrib import messages
from django.views.decorators.http import require_http_methods
from django.urls import reverse
from django.core.paginator import Paginator
from django.utils import timezone
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.crypto import constant_time_compare
from django.utils.text import Truncator
from datetime import timedelta
from functools import wraps
from django_ratelimit.decorators import ratelimit
from pathlib import Path
import glob
import logging
import threading

from .models import UNIT_KEYS, Request, User, UserEmail
from .forms import (
    AccountDetailsForm,
    UserPreferencesForm,
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
    add_form_errors,
    spam_check,
    render_request_template,
)

logger = logging.getLogger(__name__)


# Session keys that together constitute "logged in". Cleared as a unit when a
# session stops being valid; the rest of the session (messages, activation
# state) is deliberately left alone.
SESSION_AUTH_KEYS = ('user_id', 'email', 'name', 'auth_hash')


def start_session(request, user, email):
    """Authenticate this session as `user`.

    cycle_key() issues a fresh session id, so a session id fixed by an attacker
    before login does not become an authenticated one. The auth hash lets every
    later request notice a password change - see User.session_auth_hash.
    """
    request.session.cycle_key()
    request.session['email'] = email
    request.session['name'] = user.name
    request.session['user_id'] = user.id
    request.session['auth_hash'] = user.session_auth_hash()


def end_session(request):
    """Drop the authentication keys, leaving the session itself intact."""
    for key in SESSION_AUTH_KEYS:
        request.session.pop(key, None)


def get_current_user(request):
    """Get the User object from session. Returns None if not logged in.

    The session is revalidated against the database on every request rather than
    trusted on the strength of session['user_id'] alone. Two states have to end a
    session immediately rather than whenever the cookie happens to expire: an
    admin unticking is_active, and a password reset (the stolen-session case).
    Both are checked here because this is the single place every logged-in view
    resolves its user.
    """
    # One resolution per request: the decorator, get_user_context() and the view
    # body all ask, and this is a database round trip.
    cached = getattr(request, '_vald_user', Ellipsis)
    if cached is not Ellipsis:
        return cached

    def resolve():
        user_id = request.session.get('user_id')
        if not user_id:
            return None
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            end_session(request)
            return None
        if not user.is_active:
            end_session(request)
            return None
        if not constant_time_compare(request.session.get('auth_hash', ''),
                                     user.session_auth_hash()):
            end_session(request)
            return None
        return user

    request._vald_user = resolve()
    return request._vald_user


def inactive_account_message(user):
    """Explain an is_active=False account in terms the account holder can act on.

    The flag covers two unrelated situations - never approved, and approved then
    switched off - and the approval wording is actively misleading for the second.
    """
    if user.is_suspended():
        return ('Your account has been deactivated. Please contact the VALD '
                'administrator if you need it reinstated.')
    return ('Your account is awaiting approval by the VALD administrator. '
            'You will receive an email once it has been activated.')


def get_user_context(request):
    """Get common context data for templates"""
    # Resolved first: it clears the session keys read below if the session has
    # stopped being valid, so a suspended user is not greeted by name.
    user = get_current_user(request)

    context = {
        'sitename': settings.SITENAME,
        'user_email': request.session.get('email'),
        'user_name': request.session.get('name'),
    }

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

    return render(request, 'vald/index.html', context)


@ratelimit(key='vald.ratelimit.client_ip', rate='5/m', method='POST', block=False)
def login(request):
    """Handle user login with password authentication"""
    if request.method == 'POST':
        # Check if rate limited
        if getattr(request, 'limited', False):
            messages.error(request, 'Too many login attempts. Please try again in 1 minute.')
            return redirect('vald:index')

        email = request.POST.get('user', '').strip().lower()
        password = request.POST.get('password', '').strip()

        # Check if user exists in database
        try:
            user_email = UserEmail.objects.select_related('user').get(email=email)
            user = user_email.user
        except UserEmail.DoesNotExist:
            messages.error(request, 'Email address not registered. Please use the contact form to register.')
            context = get_user_context(request)
            return render(request, 'vald/notregistered.html', context)

        # Self-registrations are created inactive and must be approved by an
        # admin before they can log in or trigger an activation email.
        if not user.is_active:
            messages.error(request, inactive_account_message(user))
            return redirect('vald:index')

        # Check if user needs to activate (set password)
        if user.needs_activation():
            # Send the activation email whether or not they typed something in
            # the password field. Requiring an empty field was a dead end: a
            # returning user who typed a guessed password was told to check
            # their email for a link that had never been sent, and had to work
            # out that leaving the field blank was the trigger.
            token = user.generate_activation_token()
            user.save()

            # Build activation URL
            activation_path = reverse('vald:activate_account', kwargs={'token': token})
            activation_url = request.build_absolute_uri(activation_path)

            # Send activation email
            email_subject = 'VALD Account Activation'
            email_body = render_to_string('vald/email/activation.txt', {
                'user_name': user.name,
                'activation_url': activation_url,
                'token_max_age_days': settings.VALD_TOKEN_MAX_AGE_DAYS,
                'approved': False,
            })

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
        start_session(request, user, email)

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

        if not user.is_active:
            messages.error(request, inactive_account_message(user))
            return redirect('vald:index')

        if not user.token_is_valid():
            messages.error(
                request,
                'This activation link has expired. Please log in again to receive a new one.'
            )
            return redirect('vald:index')

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


@ratelimit(key='vald.ratelimit.client_ip', rate='5/h', method='POST', block=False)
def set_password(request):
    """Handle password setting for first-time activation"""
    if request.method != 'POST':
        return redirect('vald:index')

    # Check if rate limited
    if getattr(request, 'limited', False):
        messages.error(request, 'Too many attempts. Please try again later.')
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

    try:
        validate_password(password)
    except DjangoValidationError as e:
        for message in e.messages:
            messages.error(request, message)
        return redirect('vald:activate_account', token=activation_token)

    if password != password_confirm:
        messages.error(request, 'Passwords do not match.')
        return redirect('vald:activate_account', token=activation_token)

    # Get user and verify token
    try:
        user = User.objects.get(activation_token=activation_token)

        if not user.is_active:
            messages.error(request, inactive_account_message(user))
            return redirect('vald:index')

        if not user.token_is_valid():
            messages.error(
                request,
                'This activation link has expired. Please log in again to receive a new one.'
            )
            return redirect('vald:index')

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
        start_session(request, user, activation_email)

        messages.success(request, 'Password set successfully! You are now logged in.')
        return redirect('vald:index')

    except User.DoesNotExist:
        messages.error(request, 'Invalid or expired activation link. Please request a new one by logging in.')
        return redirect('vald:index')


@ratelimit(key='vald.ratelimit.client_ip', rate='3/h', method='POST', block=False)
def request_password_reset(request):
    """Handle password reset request form"""
    context = get_user_context(request)

    if request.method == 'POST':
        # Check if rate limited
        if getattr(request, 'limited', False):
            messages.error(request, 'Too many password reset requests. Please try again later.')
            context['form'] = PasswordResetRequestForm()
            return render(request, 'vald/request_password_reset.html', context)

        form = PasswordResetRequestForm(request.POST)

        if not form.is_valid():
            add_form_errors(request, form)
            context['form'] = form
            return render(request, 'vald/request_password_reset.html', context)

        email = form.cleaned_data['email']

        # Check if user exists
        try:
            user_email = UserEmail.objects.select_related('user').get(email=email)
            user = user_email.user

            # An unapproved account must not be able to set a password via reset,
            # which would otherwise bypass admin approval. Fall through to the
            # same generic response used for unknown addresses.
            if not user.is_active:
                raise UserEmail.DoesNotExist

            # Generate reset token
            token = user.generate_activation_token()
            user.save()

            # Build reset URL
            reset_path = reverse('vald:reset_password', kwargs={'token': token})
            reset_url = request.build_absolute_uri(reset_path)

            # Send reset email
            email_subject = 'VALD Password Reset'
            email_body = render_to_string('vald/email/password_reset.txt', {
                'user_name': user.name,
                'reset_url': reset_url,
                'token_max_age_days': settings.VALD_TOKEN_MAX_AGE_DAYS,
            })

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


@ratelimit(key='vald.ratelimit.client_ip', rate='5/h', method='POST', block=False)
def reset_password(request, token):
    """Handle password reset with token"""
    context = get_user_context(request)

    if getattr(request, 'limited', False):
        messages.error(request, 'Too many password reset attempts. Please try again later.')
        return redirect('vald:index')

    # Verify token
    try:
        user = User.objects.get(activation_token=token)
    except User.DoesNotExist:
        messages.error(request, 'Invalid or expired password reset link.')
        return redirect('vald:index')

    if not user.token_is_valid():
        messages.error(
            request,
            'This password reset link has expired. Please request a new one.'
        )
        return redirect('vald:request_password_reset')

    if request.method == 'POST':
        form = PasswordResetForm(request.POST)

        if not form.is_valid():
            add_form_errors(request, form)
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


def serve_path_or_none(path):
    """Return path if it exists inside VALD_FTP_DIR, else None.

    Guards the download views against serving anything outside the results
    directory, however output_file came to hold what it holds.
    """
    if not path:
        return None
    ftp_dir = Path(settings.VALD_FTP_DIR).resolve()
    try:
        resolved = Path(path).resolve()
        resolved.relative_to(ftp_dir)
    except (ValueError, OSError):
        logger.warning("Refusing to serve %s: outside %s", path, ftp_dir)
        return None
    return resolved if resolved.exists() else None


def require_login(view_func):
    """Decorator to require a session that is still valid.

    Checks the user, not just the session key: get_current_user() rejects (and
    clears) a session whose account has been suspended or whose password has
    changed since it was opened.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if get_current_user(request) is None:
            messages.error(request, 'You are not logged in. Please log in and try again.')
            return redirect('vald:index')
        return view_func(request, *args, **kwargs)
    return wrapper


def modify_initial_data(request, user):
    """Parameters of the request named by ?modify=, for pre-filling a form.

    Returns {} and explains itself for anything that does not name a request of
    `user`'s. `modify` is raw query string, so an unparseable value has to be
    caught here: a UUIDField lookup raises ValidationError, not DoesNotExist, for
    a value that is not a UUID at all - which made every hand-edited URL a 500.
    """
    modify_uuid = request.GET.get('modify')
    if not modify_uuid:
        return {}

    try:
        req_obj = Request.objects.get(uuid=modify_uuid)
    except (Request.DoesNotExist, DjangoValidationError, ValueError):
        messages.error(request, 'Request not found.')
        return {}

    # Only the owner may see a previous request's parameters, plus a staff member
    # logged into the admin in the same browser - which is what the rerun link on
    # the admin request page relies on. `request.user` is the django.contrib.auth
    # user of the admin session, unrelated to the front-end `user`, and
    # AnonymousUser.is_staff is False for everyone else.
    is_owner = req_obj.user_id == user.id
    if not is_owner and not request.user.is_staff:
        messages.error(request, 'You do not have permission to modify this request.')
        return {}

    if is_owner:
        messages.info(request, 'Form pre-filled with previous request values.')
    else:
        owner = req_obj.user.name if req_obj.user else 'an unknown user'
        messages.info(request, f'Form pre-filled from a request by {owner}. Submitting it '
                               f'creates a new request owned by you.')
    return req_obj.parameters or {}


STICKY_KEYS = UNIT_KEYS + ('email_notify',)


def last_used_choices(user, reqtype):
    """Sticky choices from this user's most recent request of the same type:
    the units, and whether they asked to be emailed.

    Units are per-request now, so without this a user who works in nm would pick
    nm again on every single form; email_notify rides along so "always email me"
    (or never) carries forward the same way. Read back rather than written to
    UserPreferences deliberately: the profile keeps exactly one writer (the Units
    page, as an explicit default), and habit is inferred from the request log.

    Old rows predate some of these keys, so only the ones present are returned -
    the caller layers this over the profile, which supplies the rest.
    """
    parameters = (Request.objects
                  .filter(user=user, request_type=reqtype)
                  .values_list('parameters', flat=True)
                  .first()) or {}
    return {key: parameters[key] for key in STICKY_KEYS if key in parameters}


def extract_form_view(name, form_class, template):
    """Build one of the four near-identical extraction form views."""
    @require_login
    def view(request):
        context = get_user_context(request)
        user = get_current_user(request)
        modify = modify_initial_data(request, user)
        # Profile as the floor, then habit, then the request being modified - so a
        # rerun reproduces the units its numbers were typed under rather than
        # today's, which is what stops 5000 A being resubmitted as 5000 nm.
        # The floor comes out of the context get_user_context already resolved
        # rather than a second get_preferences(), which is a get_or_create.
        initial = {**{key: context[key] for key in UNIT_KEYS},
                   **last_used_choices(user, name),
                   **modify}
        context['form'] = form_class(initial=initial, user=user)
        return render(request, template, context)
    view.__name__ = name
    return view


extractall = extract_form_view('extractall', ExtractAllForm, 'vald/extractall.html')
extractelement = extract_form_view('extractelement', ExtractElementForm, 'vald/extractelement.html')
extractstellar = extract_form_view('extractstellar', ExtractStellarForm, 'vald/extractstellar.html')
showline = extract_form_view('showline', ShowLineOnlineForm, 'vald/showline.html')

# Two URLs, one page - the legacy interface had separate "show line" and
# "show line ONLINE" entry points and existing links use both.
showline_online = showline


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
    if get_current_user(request) is None:
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


@ratelimit(key='vald.ratelimit.client_ip', rate='5/h', method='POST', block=False)
def handle_contact_request(request):
    """Handle contact form submission"""
    context = get_user_context(request)

    # Check if rate limited
    if getattr(request, 'limited', False):
        messages.error(request, 'Too many contact form submissions. Please try again later.')
        context['form'] = ContactForm()
        context['registration_form'] = RegistrationForm()
        return render(request, 'vald/contact.html', context)

    form = ContactForm(request.POST)

    if not form.is_valid():
        add_form_errors(request, form)
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


@ratelimit(key='vald.ratelimit.client_ip', rate='3/h', method='POST', block=False)
def handle_registration_request(request):
    """Handle registration form submission"""
    context = get_user_context(request)

    # Check if rate limited
    if getattr(request, 'limited', False):
        messages.error(request, 'Too many registration attempts. Please try again later.')
        context['registration_form'] = RegistrationForm()
        context['form'] = ContactForm()
        return render(request, 'vald/contact.html', context)

    form = RegistrationForm(request.POST)

    if not form.is_valid():
        add_form_errors(request, form)
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

    # Notify the admin. A failure here must not surface to the registrant or
    # undo the registration: the account is already created and discoverable
    # through the admin's "Pending approval" filter, so the mail is a prompt,
    # not the record.
    try:
        admin_url = request.build_absolute_uri(
            reverse('admin:vald_user_change', args=[user.id])
        )
        send_mail(
            'VALD: new account registration awaiting approval',
            render_to_string('vald/email/new_registration.txt', {
                'user_name': user.name,
                'affiliation': user.affiliation,
                'user_email': form.cleaned_data['email'],
                'submitted': timezone.now().strftime('%Y-%m-%d %H:%M:%S'),
                'admin_url': admin_url,
                'sitename': settings.SITENAME,
            }),
            settings.DEFAULT_FROM_EMAIL,
            [settings.VALD_ADMIN_EMAIL],
            fail_silently=False,
        )
    except Exception:
        logger.exception('Failed to send registration notification for %s', user.id)

    messages.success(
        request,
        f"Registration submitted successfully! Your account for {form.cleaned_data['email']} "
        "is pending approval. You will receive an email once your account is activated."
    )

    # Return clean forms
    context['registration_form'] = RegistrationForm()
    context['form'] = ContactForm()
    return render(request, 'vald/contact.html', context)


def start_background_worker(target):
    """Run a submitted request's worker in a daemon thread.

    Its own function so tests can decline it. The worker writes the job's status
    to the database after the response has gone out, so it outlives the request
    that started it - and in a test, the test itself, whose open transaction it
    then cannot get past.
    """
    threading.Thread(target=target, daemon=True).start()


def process_request(req_obj):
    """Run one request to completion, then record it and notify.

    Module level, and taking the row rather than closing over it, so the
    admin's rerun can reach the same code. Everything a finished job needs
    to have happened - status, output_file, the completion email - is in
    here, and duplicating any of it for a second caller is how the two
    would drift.

    Runs in a worker thread, so it never raises at its caller: every
    failure ends up on the row as status=failed.
    """
    from django import db

    # Close inherited DB connections from parent thread
    db.connections.close_all()

    try:
        # Import here to avoid circular imports
        from .backend import submit_request_direct

        # Update status to processing
        req_obj.status = 'processing'
        try:
            req_obj.save()
        except Exception as save_error:
            logger.exception(f"Failed to save processing status for request {req_obj.uuid}: {save_error}")
            raise  # Re-raise to be caught by outer exception handler

        # Submit directly to backend
        success, result = submit_request_direct(req_obj)

        if success:
            # Update request with output file
            req_obj.status = 'complete'
            # Store the filename only; Request.output_path resolves it
            # against the current VALD_FTP_DIR (R18).
            req_obj.output_file = Path(result).name
            req_obj.completed_at = timezone.now()
            try:
                req_obj.save()
            except Exception as save_error:
                logger.exception(f"Failed to save complete status for request {req_obj.uuid}: {save_error}")
                raise  # Re-raise to be caught by outer exception handler

            # Email is an optional extra now, not a delivery choice: the
            # download links exist either way. Absent key means unchecked.
            if req_obj.parameters.get('email_notify'):
                # Build URLs for email
                request_path = reverse('vald:request_detail', kwargs={'uuid': req_obj.uuid})
                # Filename in the URL, so wget/curl save the result under
                # its own name instead of index.html.
                download_path = reverse('vald:download_request',
                                        kwargs={'uuid': req_obj.uuid,
                                                'filename': req_obj.output_filename})
                # No bib companion for showline results, and reverse() has
                # no filename to build from in that case.
                bib_download_path = reverse(
                    'vald:download_bib_request',
                    kwargs={'uuid': req_obj.uuid,
                            'filename': req_obj.bib_output_filename}
                ) if req_obj.bib_output_exists() else None
                my_requests_path = reverse('vald:my_requests')

                # Use SITE_URL as base for email links (no request object in background)
                base_url = getattr(settings, 'SITE_URL', settings.SITENAME)
                # Add FORCE_SCRIPT_NAME prefix if configured (e.g., /new).
                # Django's global_settings defines it as None, so the getattr
                # default never fires - coerce it or the URLs grow a "None".
                # reverse() omits the prefix here because script_prefix is a
                # thread-local that this background thread never had set.
                script_name = getattr(settings, 'FORCE_SCRIPT_NAME', '') or ''
                request_url = f"{base_url}{script_name}{request_path}"
                download_url = f"{base_url}{script_name}{download_path}"
                bib_download_url = (f"{base_url}{script_name}{bib_download_path}"
                                    if bib_download_path else None)
                my_requests_url = f"{base_url}{script_name}{my_requests_path}"

                # Send email
                from django.core.mail import EmailMessage

                subject = f"VALD {req_obj.request_type} results ready"
                # The user's own label last, as the legacy backend did with
                # "Subject: Re: <comment>": it is what lets someone match
                # twenty result mails to the twenty ranges they submitted.
                comment = req_obj.comment()
                if comment:
                    # Legacy capped it at 68 chars (parserequest.c:1280); the
                    # form allows 200, which makes an unreadable subject line.
                    subject = f"{subject}: {Truncator(comment).chars(68)}"

                body = render_to_string('vald/email/results_ready.txt', {
                    'request_type': req_obj.request_type,
                    'submitted': req_obj.created_at.strftime('%Y-%m-%d %H:%M:%S'),
                    'download_url': download_url,
                    'bib_download_url': bib_download_url if req_obj.bib_output_exists() else None,
                    'request_url': request_url,
                    'my_requests_url': my_requests_url,
                    'retention': req_obj.retention_description(),
                    'sitename': settings.SITENAME,
                })

                # Create email with attachments
                email = EmailMessage(
                    subject=subject,
                    body=body,
                    from_email=settings.DEFAULT_FROM_EMAIL,
                    to=[req_obj.user_email]
                )

                # Attach the results, but only if small enough that mail
                # servers won't bounce them; otherwise the links above are
                # the fallback (R33).
                max_attach = getattr(settings, 'VALD_MAX_EMAIL_ATTACH_BYTES', 5 * 1024 * 1024)
                output_path = req_obj.output_path
                total = 0
                if output_path and output_path.exists():
                    total += output_path.stat().st_size
                if req_obj.bib_output_exists():
                    total += req_obj.bib_output_path.stat().st_size

                if total <= max_attach:
                    if output_path and output_path.exists():
                        email.attach_file(str(output_path))
                    if req_obj.bib_output_exists():
                        email.attach_file(str(req_obj.bib_output_path))
                else:
                    body += ("\n\nNote: the results were too large to attach "
                             "to this email - please use the download links above.")
                    email.body = body

                # Send email with logging on failure
                try:
                    email.send(fail_silently=False)
                except Exception as email_error:
                    logger.error(f"Failed to send completion email for request {req_obj.uuid}: {email_error}")

        else:
            # Processing failed
            req_obj.status = 'failed'
            req_obj.error_message = result
            req_obj.completed_at = timezone.now()
            try:
                req_obj.save()
            except Exception as save_error:
                logger.exception(f"Failed to save failed status for request {req_obj.uuid}: {save_error}")

    except Exception as e:
        # Mark request as failed
        req_obj.status = 'failed'
        req_obj.error_message = str(e)
        req_obj.completed_at = timezone.now()
        try:
            req_obj.save()
        except Exception as save_error:
            logger.exception(f"Failed to save exception status for request {req_obj.uuid}: {save_error}")


def rerun_request(req_obj):
    """Put a request back to how a fresh submission starts, and run it again.

    Keeps the row, so the uuid stays valid and the download links the user
    already holds - in a completion email, in a bookmark - lead to the new
    result. Callers are the admin's rerun button and the startup sweep for
    requests a restart stranded.

    Says nothing about whether the request is safe to run: only the process
    holding the queue knows that (backend.is_request_active), and the startup
    sweep runs when there is nothing to know.
    """
    req_obj.status = 'pending'
    req_obj.error_message = None
    req_obj.output_file = None
    req_obj.completed_at = None
    req_obj.save()
    start_background_worker(lambda: process_request(req_obj))


@ratelimit(key='vald.ratelimit.session_user',
           rate='vald.ratelimit.submit_rate', method='POST', block=False)
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

    # initial matters even bound: a unit the POST omits falls back to it, so a
    # submission that predates the unit fields still means "my saved defaults".
    prefs = user.get_preferences()
    form = form_class(request.POST, initial=prefs.as_dict(), user=user)

    if not form.is_valid():
        add_form_errors(request, form)
        context['form'] = form
        # Redirect to the appropriate form page
        template_map = {
            'extractall': 'vald/extractall.html',
            'extractelement': 'vald/extractelement.html',
            'extractstellar': 'vald/extractstellar.html',
            'showline': 'vald/showline.html',
        }
        return render(request, template_map[reqtype], context)

    # Only on an explicit tick. An automatic writeback on every submission would
    # let the profile drift with nothing to show who changed it or when, and the
    # units a request was made with are already recorded on the request itself.
    # Popped rather than read: it is an instruction, not a parameter of the job,
    # so it must not reach Request.parameters and come back pre-ticked through
    # ?modify=.
    if form.cleaned_data.pop('save_as_default', False):
        for key in UNIT_KEYS:
            if key in form.cleaned_data:
                setattr(prefs, key, form.cleaned_data[key])
        prefs.save()
        messages.success(request, 'These units are now your saved defaults.')

    # Build email context from validated form data
    email_context = {
        'reqtype': reqtype,
        'user_email': user.primary_email,
    }

    # Floor for anything the form does not carry; the cleaned_data loop below
    # overwrites the units it does.
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

    # Profile first, then the form on top: the unit fields live on the extract
    # forms now, and Show Line has carried its own isotopic_scaling all along.
    # The other order silently discarded both - the field was on screen, promised
    # to take precedence, and never reached create_job_config.
    request_params = {**prefs.as_dict(), **form.cleaned_data}

    template_map = {
        'extractall': 'vald/extractall.html',
        'extractelement': 'vald/extractelement.html',
        'extractstellar': 'vald/extractstellar.html',
        'showline': 'vald/showline.html',
    }

    def reject(message):
        messages.error(request, message)
        context['form'] = form
        return render(request, template_map[reqtype], context)

    if getattr(request, 'limited', False):
        return reject(
            'You have submitted a lot of requests in a short time. '
            'Please wait a little before submitting another.'
        )

    # Per-user in-flight cap: without this one user could occupy the whole
    # global admission queue and everyone else got "Server is busy". Requests
    # older than the reconciliation window are ignored so a job lost to a worker
    # restart cannot block the user permanently.
    max_per_user = getattr(settings, 'VALD_MAX_REQUESTS_PER_USER', 5)
    job_timeout = getattr(settings, 'VALD_JOB_TIMEOUT', 3600)
    stale_cutoff = timezone.now() - timedelta(seconds=2 * job_timeout)
    in_flight = Request.objects.filter(
        user=user,
        status__in=['pending', 'processing'],
        created_at__gte=stale_cutoff,
    ).count()
    if in_flight >= max_per_user:
        return reject(
            f'You already have {in_flight} requests being processed '
            f'(the limit is {max_per_user}). Please wait for one to finish '
            'before submitting another.'
        )

    # Check queue capacity before creating request
    from .backend import check_queue_capacity, notify_queue_full
    has_capacity, current_count, max_size = check_queue_capacity()
    if not has_capacity:
        notify_queue_full()
        return reject(
            f'Server is busy processing requests ({current_count}/{max_size} in queue). '
            'Please try again in a few minutes.'
        )

    # Create Request record for tracking
    req_obj = Request.objects.create(
        user=user,
        request_type=reqtype,
        parameters=request_params,
        status='pending'
    )

    # Start background processing
    start_background_worker(lambda: process_request(req_obj))

    # Immediately redirect to request detail page
    messages.success(request, 'Your request has been submitted and is being processed.')
    return redirect('vald:request_detail', uuid=req_obj.uuid)


@require_login
def account(request):
    """Show and edit the account details stored about the logged-in user.

    Until now affiliation was write-once at registration and visible only in the
    admin, so an imported account had no way to see or correct what
    clients.register happened to contain.
    """
    user = get_current_user(request)
    context = get_user_context(request)

    if request.method == 'POST':
        form = AccountDetailsForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, 'Your account details have been saved.')
            return redirect('vald:account')
        add_form_errors(request, form)
    else:
        form = AccountDetailsForm(instance=user)

    context['form'] = form
    return render(request, 'vald/account.html', context)


@require_login
def unitselection(request):
    """Unit selection page.

    Renders UserPreferencesForm - the same form save_units validates the POST
    with - so the five option labels come from the model's choices. Spelled out
    in the template they had drifted: this page offered "air" and "Default"
    where the per-request panel, which does render the form, offers
    "Air (λ > 200nm)" and "Default (single value)" for the same settings.
    """
    context = get_user_context(request)
    user = get_current_user(request)
    if user:
        context['form'] = UserPreferencesForm(instance=user.get_preferences())
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

    # Validate against the model's choices rather than trusting raw POST -
    # these values feed pres_in flag generation, so junk must not persist.
    prefs = user.get_preferences()
    form = UserPreferencesForm(request.POST, instance=prefs)
    if not form.is_valid():
        messages.error(request, 'Could not save preferences: invalid unit selection.')
        return redirect('vald:unitselection')
    form.save()

    messages.success(request, 'Your unit preferences have been saved successfully.')
    # Rendered by the page's own view rather than assembling the context again
    # here: it is the one place that knows what unitselection.html needs, and a
    # second copy is how the saved page came to be missing the form entirely.
    # A bound form would redisplay the POST; this re-reads what was stored.
    return unitselection(request)


def about(request):
    """The About VALD page."""
    return render(request, 'vald/about.html', get_user_context(request))


def contact(request):
    """Registration and contact form.

    The POST goes to submit_request with reqtype=contact, not back here.
    """
    context = get_user_context(request)
    context['registration_form'] = RegistrationForm()
    context['form'] = ContactForm()
    return render(request, 'vald/contact.html', context)


def rewrite_news_links(text):
    """Point a stored news item's links at the URLs that exist today.

    News files are kept verbatim as they were published, so they still carry
    links written for the PHP interface and for the doc/ file server that
    replaced it. Both named the contact page; neither resolves any more.
    """
    contact_url = reverse('vald:contact')
    for dead in ('doc/contact.html', '$thisscript?docpage=contact.html'):
        text = text.replace(f'href="{dead}"', f'href="{contact_url}"')
    return text


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
                content = rewrite_news_links(f.read())
                all_news.append({
                    'filename': Path(news_file).name,
                    'content': content
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
            news_content = rewrite_news_links(f.read())

        context.update({
            'show_all': False,
            'news_content': news_content,
            'news_files': file_list,
            'current_index': newsitem,
        })

    return render(request, 'vald/news.html', context)


@require_login
def persconf(request):
    """Personal configuration page - database-backed implementation.

    Two states, both reachable (R47):

      no personal config - requests use the VALD default and pick up whatever a
                           future release adds to it.
      personal config    - a snapshot taken at the first edit, plus the edits.
                           It does not follow the VALD default.

    Nothing here creates a config as a side effect of being looked at, which is
    what used to move every visitor into the second state without asking.
    """
    from .persconfig import (
        get_user_config, get_default_config, get_effective_config,
        remove_user_config, set_user_config_to_current_default,
        linelists_added_since, get_linelists_for_display,
        update_config_linelist, restore_linelist_to_default,
        get_modification_flags, clamp_rank, snapshot_source,
        get_alternative_configs, resolve_pconf, PCONF_DEFAULT
    )

    context = get_user_context(request)
    user = get_current_user(request)

    default_config = get_default_config()
    if not default_config:
        messages.error(request, 'No default configuration found. Please contact administrator.')
        return redirect('vald:index')

    def posted_linelist_id():
        """The Linelist named by the form, or None. Never a ConfigLinelist pk."""
        try:
            return int(request.POST.get('editid', ''))
        except (TypeError, ValueError):
            return None

    # Handle actions
    action = request.POST.get('action') if request.method == 'POST' else None
    editid = request.POST.get('editid')
    linelist_id = posted_linelist_id()

    if action == 'remove':
        if get_user_config(user):
            remove_user_config(user)
            messages.success(
                request,
                'Personal configuration removed. Your requests now use the VALD '
                'default, including linelists added in future VALD releases.')
        else:
            messages.info(request, 'You have no personal configuration to remove.')
        editid = action = None

    elif action == 'set_to_current_default':
        # An unknown or missing slug means the default, which is what every
        # submission meant before there was a choice.
        source = resolve_pconf(None, request.POST.get('source') or PCONF_DEFAULT)
        if source is None:
            source = default_config
        if set_user_config_to_current_default(user, source=source):
            messages.success(
                request,
                f'Personal configuration set to a copy of "{source.name}" as it is '
                'now. It will stay as it is when that configuration is updated.')
        else:
            messages.error(request, 'Could not copy that configuration.')
        editid = action = None

    elif action == 'save' and linelist_id is not None:
        is_enabled = bool(request.POST.get('linelist-checked'))

        # Rank values (indices 5-13 in old system = 9 rank values).
        # clamp_rank bounds them to the 1-9 the .cfg format allows.
        ranks = [clamp_rank(request.POST.get(f'edit-val-{j}', '3'))
                 for j in range(9)]

        had_config = get_user_config(user) is not None
        if update_config_linelist(linelist_id, user, is_enabled=is_enabled, ranks=ranks):
            if had_config:
                messages.success(request, 'Linelist settings saved successfully.')
            else:
                # The transition deserves saying out loud - it is the moment the
                # user stops tracking the VALD default.
                messages.success(
                    request,
                    'Linelist settings saved. This created your personal '
                    'configuration from the current VALD default; it will not '
                    'change when the VALD default is updated.')
        else:
            messages.error(request, 'Failed to save linelist settings.')

        editid = action = None

    elif action == 'restore' and linelist_id is not None:
        if restore_linelist_to_default(linelist_id, user):
            messages.success(request, 'Linelist restored to default settings.')
        else:
            messages.error(request, 'Failed to restore linelist.')

        editid = action = None

    elif action == 'cancel':
        editid = action = None

    # Read state only from here on
    user_config, is_personal = get_effective_config(user)

    linelists = get_linelists_for_display(user_config)
    # Compared against the config this snapshot came from, so the red "modified"
    # highlighting marks the user's own edits and not the gap between two system
    # configs. For someone tracking the default this is that same config, and
    # every flag comes out false.
    modifications = get_modification_flags(user_config, snapshot_source(user_config))

    # Add modification info to linelists
    for ll in linelists:
        mod = modifications.get(ll['id'], {})
        ll['mod_comment'] = mod.get('is_enabled', False)
        ll['mod_flags'] = mod.get('ranks', [False] * 9)
        ll['any_modification'] = mod.get('any', False)

    # Build context for template
    context.update({
        'linelists': linelists,
        'editid': linelist_id if action == 'edit' else None,
        'action': action,
        'config': user_config,
        'is_personal': is_personal,
        'snapshot_date': user_config.snapshot_date if is_personal else None,
        # The config this snapshot was copied from, so the page can name it. It is
        # no longer necessarily the VALD default, and saying so when it is not
        # tells the user their base is something it is not.
        'snapshot_of': snapshot_source(user_config) if is_personal else None,
        'added_since': linelists_added_since(user_config) if is_personal else [],
        # Same set the request forms offer, so "copy of X" and "run with X" cannot
        # disagree about what exists. The default is named by the PCONF_DEFAULT
        # token, not by its slug, exactly as the request menu does it: the slug is
        # blank on a config created by hand in the admin, and 'default' is what
        # resolve_pconf() answers to anyway.
        'config_sources': [
            {'value': PCONF_DEFAULT, 'name': default_config.name,
             'description': default_config.description, 'is_default': True}
        ] if default_config else []
    })
    context['config_sources'] += [
        {'value': c.slug, 'name': c.name, 'description': c.description,
         'is_default': False}
        for c in get_alternative_configs()
    ]

    return render(request, 'vald/persconf.html', context)

@require_login
def system_config(request, slug):
    """Read-only listing of a system configuration's linelists.

    The request forms let a user pick any of the system configs, so they need a
    way to see what one contains before choosing it - /persconf/ only ever shows
    the default or their own snapshot.

    Looked up by slug among system configs only. A personal config has no slug,
    so no URL here can reach one, which is what keeps this from becoming a way to
    read another user's configuration.
    """
    from .models import Config
    from .persconfig import get_linelists_for_display

    context = get_user_context(request)
    config = Config.objects.filter(user__isnull=True, slug=slug).first()
    if not config:
        context['error'] = 'No such linelist configuration.'
        return render(request, 'vald/error.html', context)

    linelists = get_linelists_for_display(config)
    context.update({
        'config': config,
        'linelists': linelists,
        'enabled_count': sum(1 for entry in linelists if entry['is_enabled']),
    })
    return render(request, 'vald/system_config.html', context)


@require_login
def my_requests(request):
    """Show all requests for the current user"""
    context = get_user_context(request)
    user = get_current_user(request)

    # Get all requests for this user (regardless of which email they used)
    all_requests = Request.objects.filter(user=user).order_by('-created_at')

    # Count by status across the whole set, before paginating
    pending_count = all_requests.filter(status__in=['pending', 'processing']).count()
    complete_count = all_requests.filter(status='complete').count()
    failed_count = all_requests.filter(status='failed').count()

    # Paginate so the page - and its per-row filesystem checks - stays bounded
    paginator = Paginator(all_requests, 25)
    page = paginator.get_page(request.GET.get('page'))

    context.update({
        'requests': page,
        'page_obj': page,
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

        # Format request parameters for display (email format for copy-paste)
        request_file_content = format_request_file(req_obj)

        # Check if output file exists
        output_ready = req_obj.output_exists()
        output_empty = req_obj.output_is_empty() if output_ready else False
        output_size = req_obj.get_output_size() if output_ready and not output_empty else None

        # Check if bib output file exists
        bib_output_ready = req_obj.bib_output_exists()
        bib_output_size = req_obj.get_bib_output_size() if bib_output_ready else None

        # For showline requests, read and display output content inline
        output_content = None
        if req_obj.request_type == 'showline' and output_ready:
            try:
                with open(req_obj.output_path, 'r') as f:
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
            'output_empty': output_empty,
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
def request_status(request, uuid):
    """Whether a request is still running, as JSON, for the detail page to poll.

    The detail page used to carry <meta http-equiv="refresh" content="10">,
    which reloads whatever the user was reading every ten seconds with no way
    to stop it - a WCAG 2.2.1 failure, and it threw away scroll position and
    text selection each time. The page now asks this and reloads once, when
    there is actually something new to show.

    Deliberately owner-only, unlike the result downloads: those are guarded by
    the uuid being the capability, but this is reached with the session cookie
    from a page that already required a login, so there is no reason to widen
    it. A request that is not yours is 404, not 403 - whether a given uuid
    exists is not something to confirm.
    """
    user = get_current_user(request)
    try:
        req_obj = Request.objects.get(uuid=uuid, user_id=user.id)
    except Request.DoesNotExist:
        return JsonResponse({'error': 'not found'}, status=404)

    # Recomputed the same way request_detail does; it is a count, not a promise.
    queue_position = None
    if req_obj.status == 'pending':
        queue_position = Request.objects.filter(
            status='pending',
            created_at__lt=req_obj.created_at
        ).count() + 1

    return JsonResponse({
        'status': req_obj.status,
        'pending': req_obj.is_pending(),
        'queue_position': queue_position,
    })


def _serve_result(request, uuid, which, filename):
    """Serve a result file by request UUID, to whoever holds the URL.

    Deliberately unauthenticated: the uuid4 *is* the capability, so a link
    copied out of the results page or the completion email can be fed straight
    to wget/curl on another machine. Requiring the session cookie meant those
    clients followed the login redirect and silently saved an HTML page instead
    of the data. This grants no more than the deployment already does - the
    vhost serves $VALD_HOME/WWW/public_html/FTP directly, where the same files
    sit under a guessable 6-digit name (R37).

    A URL without the trailing filename redirects to the one with it, so those
    same clients save the result under its own name rather than index.html.

    Every other failure is a status code with a plain-text body, never a
    redirect to a page: a redirect is what made a failed wget look like a
    successful one.
    """
    from django.http import FileResponse, HttpResponseNotFound
    import mimetypes

    try:
        req_obj = Request.objects.get(uuid=uuid)
    except Request.DoesNotExist:
        return HttpResponseNotFound('No such request.\n', content_type='text/plain')

    path = req_obj.output_path if which == 'output' else req_obj.bib_output_path

    # Confirm the file really is inside the results directory before opening it
    # (defence in depth - the value is server-generated, but nothing else
    # checks it).
    file_path = serve_path_or_none(path)
    if file_path is None:
        if req_obj.results_expired():
            return HttpResponseNotFound(
                f'Results have expired - they are kept {req_obj.retention_description()}.\n',
                content_type='text/plain')
        return HttpResponseNotFound('Result file not found.\n', content_type='text/plain')

    view = 'vald:download_request' if which == 'output' else 'vald:download_bib_request'
    if filename is None:
        return redirect(view, uuid=uuid, filename=file_path.name)
    if filename != file_path.name:
        return HttpResponseNotFound('Result file not found.\n', content_type='text/plain')

    content_type, _ = mimetypes.guess_type(file_path.name)
    try:
        return FileResponse(
            open(file_path, 'rb'),
            content_type=content_type or 'application/octet-stream',
            as_attachment=True,
            filename=file_path.name
        )
    except OSError as e:
        logger.error("Failed to open %s for request %s: %s", file_path, uuid, e)
        return HttpResponse('Result file could not be read.\n',
                            content_type='text/plain', status=500)


def download_request(request, uuid, filename=None):
    """Download the output file for a completed request"""
    return _serve_result(request, uuid, 'output', filename)


def download_bib_request(request, uuid, filename=None):
    """Download the .bib.gz output file for a completed request"""
    return _serve_result(request, uuid, 'bib', filename)
