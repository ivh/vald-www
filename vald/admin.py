import os
from pathlib import Path

from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db.models import Q
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.template.loader import render_to_string
from django.conf import settings
from django.urls import reverse
from django.utils import timezone
from django import forms
from .models import Request, User, UserEmail, UserPreferences, Linelist, Config, ConfigLinelist


def get_queue_stats():
    """Get current job queue statistics from database."""
    from django.utils import timezone
    from datetime import timedelta
    from .models import Request
    
    cutoff = timezone.now() - timedelta(minutes=30)
    pending_count = Request.objects.filter(
        status__in=['pending', 'processing'],
        created_at__gte=cutoff
    ).count()
    max_queue_size = getattr(settings, 'VALD_MAX_QUEUE_SIZE', 10)
    max_workers = getattr(settings, 'VALD_MAX_WORKERS', 2)
    return {
        'queue_size': pending_count,
        'max_queue_size': max_queue_size,
        'max_workers': max_workers,
    }


# A password of '' means the same as NULL here, so every query about
# activation state has to say so. Kept as one expression rather than repeated
# lookups, which had let the "pending approval" filter and column disagree.
NO_PASSWORD = Q(password__isnull=True) | Q(password='')
PENDING_APPROVAL = Q(is_active=False) & NO_PASSWORD


@staff_member_required
def admin_help(request):
    """Operator reference for the states these admin screens expose.

    Wired into vald_web/urls.py ahead of admin.site.urls rather than into a
    ModelAdmin, since it spans models. Every number is read from settings or the
    live queryset, so the page cannot drift the way a separate wiki page would.
    """
    users = User.objects.all()
    changelist = reverse('admin:vald_user_changelist')
    account_states = [
        {
            'state': 'Pending approval',
            'is_active': False,
            'has_password': False,
            'how': 'Self-registered through the public form; no admin has acted on it yet.',
            'sees': 'Cannot log in: "awaiting approval by the VALD administrator". '
                    'A password reset request is answered as if the address were unknown, '
                    'so approval cannot be routed around.',
            'do': 'Approve and send activation email, or Reject.',
            'count': users.filter(PENDING_APPROVAL).count(),
            'query': '?pending_approval=yes',
        },
        {
            'state': 'Approved, not activated',
            'is_active': True,
            'has_password': False,
            'how': 'Approved by an admin, or had its password cleared, but the holder '
                   'has not set one yet.',
            'sees': 'Any login attempt on this address mails a fresh activation link, '
                    'whether or not a password was typed. Clicking it sets the password.',
            'do': 'Nothing - waiting on the user. Resend by having them try to log in.',
            'count': users.filter(Q(is_active=True) & NO_PASSWORD).count(),
            'query': '?is_active__exact=1&has_password=no',
        },
        {
            'state': 'Active',
            'is_active': True,
            'has_password': True,
            'how': 'Normal working account.',
            'sees': 'Logs in and submits requests.',
            'do': 'Nothing. Untick is_active to suspend, or clear the password to '
                  'force re-activation.',
            'count': users.filter(is_active=True).exclude(NO_PASSWORD).count(),
            'query': '?is_active__exact=1&has_password=yes',
        },
        {
            'state': 'Suspended',
            'is_active': False,
            'has_password': True,
            'how': 'Was working, then is_active was unticked.',
            'sees': 'Cannot log in: "account has been deactivated, contact the administrator".',
            'do': 'Tick is_active to reinstate; the old password still works. '
                  'Reject does not touch these - delete deliberately if that is the intent.',
            'count': users.filter(is_active=False).exclude(NO_PASSWORD).count(),
            'query': '?is_active__exact=0&has_password=yes',
        },
    ]
    for row in account_states:
        row['url'] = changelist + row['query']

    limits = [
        ('VALD_TOKEN_MAX_AGE_DAYS', settings.VALD_TOKEN_MAX_AGE_DAYS,
         'Lifetime of activation and password-reset links. Expired links send the '
         'user back to the login form for a new one.'),
        ('VALD_RESULT_RETENTION_DAYS', settings.VALD_RESULT_RETENTION_DAYS,
         'How long result files survive before the cleanup timer removes them. '
         'The Request row stays, and reports the results as expired.'),
        ('VALD_MAX_WORKERS', settings.VALD_MAX_WORKERS,
         'Jobs run in parallel. Everything else queues.'),
        ('VALD_MAX_QUEUE_SIZE', settings.VALD_MAX_QUEUE_SIZE,
         'Queued jobs before new submissions are refused site-wide.'),
        ('VALD_MAX_REQUESTS_PER_USER', settings.VALD_MAX_REQUESTS_PER_USER,
         'Per-user cap on queued jobs, so one user cannot fill the queue alone.'),
        ('VALD_MAX_LINES_PER_REQUEST', settings.VALD_MAX_LINES_PER_REQUEST,
         'Upper bound on the line count a single extraction may ask for.'),
        ('VALD_JOB_TIMEOUT', settings.VALD_JOB_TIMEOUT,
         'Seconds before a running Fortran job is killed and marked failed.'),
        ('VALD_SUBMIT_RATE', settings.VALD_SUBMIT_RATE,
         'Rate limit on request submission, per client IP.'),
        ('VALD_ADMIN_EMAIL', settings.VALD_ADMIN_EMAIL,
         'Recipient of new-registration and queue-full notifications.'),
        ('SITE_URL', settings.SITE_URL,
         'Base URL used to build activation and reset links in email. Wrong value '
         'here means links that go nowhere.'),
    ]

    # Deployment paths come from the running instance, and the unit list from
    # what is actually in the checkout, so adding a timer shows up here without
    # anyone remembering to edit this page.
    base_dir = Path(settings.BASE_DIR)
    unit_files = sorted(
        p.name for p in base_dir.iterdir()
        if p.suffix in ('.service', '.timer')
    )

    context = {
        **admin.site.each_context(request),
        'title': 'Admin help',
        'account_states': account_states,
        'user_total': users.count(),
        'limits': limits,
        'token_max_age_days': settings.VALD_TOKEN_MAX_AGE_DAYS,
        'retention_days': settings.VALD_RESULT_RETENTION_DAYS,
        'queue_stats': get_queue_stats(),
        'user_changelist': changelist,
        'base_dir': base_dir,
        'unit_files': unit_files,
        'settings_module': os.environ.get('DJANGO_SETTINGS_MODULE', '(default)'),
    }
    return render(request, 'admin/vald/help.html', context)


class HasPasswordFilter(admin.SimpleListFilter):
    title = 'has password'
    parameter_name = 'has_password'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Yes'),
            ('no', 'No'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.exclude(NO_PASSWORD)
        if self.value() == 'no':
            return queryset.filter(NO_PASSWORD)


class PendingApprovalFilter(admin.SimpleListFilter):
    title = 'pending approval'
    parameter_name = 'pending_approval'

    def lookups(self, request, model_admin):
        return (
            ('yes', 'Yes'),
            ('no', 'No'),
        )

    def queryset(self, request, queryset):
        if self.value() == 'yes':
            return queryset.filter(PENDING_APPROVAL)
        if self.value() == 'no':
            return queryset.exclude(PENDING_APPROVAL)


class UserChangeForm(forms.ModelForm):
    """Custom form for User admin with proper password display"""
    password = ReadOnlyPasswordHashField(
        label="Password",
        help_text=(
            "Raw passwords are not stored, so there is no way to see this "
            "user's password, but you can change the password using "
            '<a href="../password/">this form</a>.'
        ),
    )

    class Meta:
        model = User
        fields = '__all__'


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'request_type', 'get_user_email', 'status', 'created_at', 'has_output')
    list_filter = ('status', 'request_type', 'created_at')
    search_fields = ('uuid', 'user__name', 'user__emails__email')
    readonly_fields = ('uuid', 'created_at', 'updated_at')
    fieldsets = (
        ('Request Information', {
            'fields': ('uuid', 'request_type', 'user')
        }),
        ('Parameters', {
            'fields': ('parameters',),
            'classes': ('collapse',)
        }),
        ('Status', {
            'fields': ('status', 'queue_position', 'output_file', 'error_message')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'completed_at')
        }),
    )

    def changelist_view(self, request, extra_context=None):
        """Add queue stats to the changelist view."""
        extra_context = extra_context or {}
        extra_context['queue_stats'] = get_queue_stats()
        return super().changelist_view(request, extra_context=extra_context)

    def get_user_email(self, obj):
        """Display user's primary email"""
        return obj.user_email
    get_user_email.short_description = 'User Email'

    def has_output(self, obj):
        """Show if output file exists"""
        return obj.output_exists()
    has_output.boolean = True
    has_output.short_description = 'Output File'


class UserEmailInline(admin.TabularInline):
    model = UserEmail
    extra = 1
    fields = ('email', 'is_primary')


class UserPreferencesInline(admin.StackedInline):
    model = UserPreferences
    can_delete = False
    verbose_name_plural = 'Preferences'


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    form = UserChangeForm
    list_display = ('name', 'get_emails', 'has_password', 'is_active', 'is_pending', 'is_suspended', 'created_at')
    list_filter = ('is_active', HasPasswordFilter, PendingApprovalFilter, 'created_at')
    search_fields = ('name', 'affiliation', 'emails__email')
    readonly_fields = ('created_at', 'updated_at', 'activation_token')
    inlines = [UserEmailInline, UserPreferencesInline]
    actions = ['approve_and_send_activation', 'approve_without_email', 'clear_password', 'reject_registration']
    fieldsets = (
        ('User Information', {
            'fields': ('name', 'affiliation', 'is_active')
        }),
        ('Authentication', {
            'fields': ('password', 'activation_token')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )

    change_form_template = 'admin/vald/user/change_form.html'

    # Buttons added to the change form by that template. Each dispatches to the
    # changelist action of the same name with a one-row queryset, so the two
    # entry points cannot drift apart.
    CHANGE_FORM_ACTIONS = {
        '_approve_send': 'approve_and_send_activation',
        '_approve_quiet': 'approve_without_email',
        '_clear_password': 'clear_password',
    }

    def response_change(self, request, obj):
        for field, action_name in self.CHANGE_FORM_ACTIONS.items():
            if field in request.POST:
                action = getattr(self, action_name)
                action(request, self.model.objects.filter(pk=obj.pk))
                return HttpResponseRedirect(request.get_full_path())
        return super().response_change(request, obj)

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path(
                '<id>/password/',
                self.admin_site.admin_view(self.user_change_password),
                name='vald_user_password_change',
            ),
        ]
        return custom_urls + urls

    def user_change_password(self, request, id, form_url=''):
        from django.contrib import messages
        from django.shortcuts import redirect, render
        from django.contrib.admin.utils import unquote

        user = self.get_object(request, unquote(id))
        if user is None:
            raise self.model.DoesNotExist

        if request.method == 'POST':
            password = request.POST.get('password1')
            password2 = request.POST.get('password2')

            if not password:
                messages.error(request, 'Password cannot be empty.')
            elif password != password2:
                messages.error(request, 'Passwords do not match.')
            else:
                # AUTH_PASSWORD_VALIDATORS, same as the activation and reset
                # forms. This path used to accept anything six characters long,
                # so the one password an admin sets by hand was the weakest the
                # site allowed.
                try:
                    validate_password(password, user)
                except DjangoValidationError as e:
                    for message in e.messages:
                        messages.error(request, message)
                else:
                    user.set_password(password)
                    user.save()
                    messages.success(request, f'Password changed successfully for {user.name}.')
                    return redirect('admin:vald_user_change', user.id)

        context = {
            'user': user,
            'opts': self.model._meta,
            'title': f'Change password: {user.name}',
        }
        return render(request, 'admin/vald/user_password_change.html', context)

    def get_emails(self, obj):
        """Display all email addresses for the user"""
        return ', '.join(obj.emails.values_list('email', flat=True))
    get_emails.short_description = 'Email Addresses'

    def has_password(self, obj):
        """Show if user has set a password"""
        return bool(obj.password)
    has_password.boolean = True
    has_password.short_description = 'Has Password'
    has_password.admin_order_field = 'password'

    def is_pending(self, obj):
        """Show if user is pending approval (inactive with no password)"""
        return obj.is_pending_approval()
    is_pending.boolean = True
    is_pending.short_description = 'Pending Approval'

    def is_suspended(self, obj):
        """Inactive but already activated - switched off, not awaiting approval"""
        return obj.is_suspended()
    is_suspended.boolean = True
    is_suspended.short_description = 'Suspended'

    def approve_and_send_activation(self, request, queryset):
        """Approve selected users and send activation email"""
        count = 0
        for user in queryset:
            if not user.is_active:
                user.is_active = True
                token = user.generate_activation_token()
                user.save()
                self.log_change(request, user, 'Approved and activation email requested.')

                if user.primary_email:
                    activation_path = reverse('vald:activate_account', kwargs={'token': token})
                    activation_url = f"{settings.SITE_URL}{activation_path}"
                    try:
                        send_mail(
                            'VALD Account Activated',
                            render_to_string('vald/email/activation.txt', {
                                'user_name': user.name,
                                'activation_url': activation_url,
                                'token_max_age_days': settings.VALD_TOKEN_MAX_AGE_DAYS,
                                'approved': True,
                            }),
                            settings.DEFAULT_FROM_EMAIL,
                            [user.primary_email],
                            fail_silently=False,
                        )
                        count += 1
                    except Exception as e:
                        self.message_user(request, f'Error sending email to {user.name}: {e}', level='error')

        self.message_user(request, f'{count} user(s) approved and activation emails sent.')
    approve_and_send_activation.short_description = 'Approve and send activation email'

    def approve_without_email(self, request, queryset):
        """Approve selected users without sending email

        Equivalent to ticking Active and saving, which is why it also bumps
        updated_at and writes a history entry: a bare queryset.update() does
        neither, and an approval that leaves no trace of who granted it is worse
        than the extra query.
        """
        approved = [user for user in queryset if not user.is_active]
        queryset.update(is_active=True, updated_at=timezone.now())
        for user in approved:
            self.log_change(request, user, 'Approved without sending email.')
        self.message_user(request, f'{len(approved)} user(s) approved (no email sent).')
    approve_without_email.short_description = 'Approve without sending email'

    def clear_password(self, request, queryset):
        """Drop the password so the next login attempt re-triggers activation"""
        cleared = [user for user in queryset if user.password]
        queryset.update(password=None, activation_token=None, token_created_at=None,
                        updated_at=timezone.now())
        for user in cleared:
            self.log_change(request, user, 'Password cleared; re-activation required.')
        self.message_user(
            request,
            f'{len(cleared)} user(s) had their password removed; they will be sent an '
            f'activation link on their next login attempt.'
        )
    clear_password.short_description = 'Clear password (force re-activation)'

    def reject_registration(self, request, queryset):
        """Delete/reject selected pending users"""
        pending = queryset.filter(PENDING_APPROVAL)
        count = pending.count()
        self.log_deletions(request, pending)   # needs the rows, so before delete()
        pending.delete()
        self.message_user(request, f'{count} pending registration(s) rejected and deleted.')
    reject_registration.short_description = 'Reject pending registrations'


@admin.register(UserEmail)
class UserEmailAdmin(admin.ModelAdmin):
    list_display = ('email', 'user', 'is_primary', 'created_at')
    list_filter = ('is_primary',)
    search_fields = ('email', 'user__name')
    readonly_fields = ('created_at',)


@admin.register(UserPreferences)
class UserPreferencesAdmin(admin.ModelAdmin):
    list_display = ('user', 'energyunit', 'waveunit', 'medium', 'vdwformat', 'isotopic_scaling')
    list_filter = ('energyunit', 'waveunit', 'medium')
    search_fields = ('user__name',)
    readonly_fields = ('created_at', 'updated_at')


# ============================================================================
# Linelist Configuration Admin
# ============================================================================

class ConfigLinelistInline(admin.TabularInline):
    model = ConfigLinelist
    extra = 0
    fields = ('linelist', 'priority', 'is_enabled', 'mergeable')
    autocomplete_fields = ['linelist']
    ordering = ['priority']


@admin.register(Linelist)
class LinelistAdmin(admin.ModelAdmin):
    list_display = ('name', 'path', 'element_range', 'default_priority', 'is_molecular', 'is_active')
    list_filter = ('is_active', 'is_molecular', 'source')
    search_fields = ('name', 'path', 'source')
    readonly_fields = ('created_at', 'updated_at')
    ordering = ['default_priority', 'path']
    fieldsets = (
        ('Basic Information', {
            'fields': ('path', 'name', 'source', 'is_molecular', 'is_active')
        }),
        ('Element Range', {
            'fields': ('element_min', 'element_max')
        }),
        ('Default Settings', {
            'fields': ('default_priority',),
        }),
        ('Default Rank Weights', {
            'fields': (
                ('default_rank_wl', 'default_rank_gf', 'default_rank_rad'),
                ('default_rank_stark', 'default_rank_waals', 'default_rank_lande'),
                ('default_rank_term', 'default_rank_ext_vdw', 'default_rank_zeeman'),
            ),
            'classes': ('collapse',)
        }),
        ('Notes', {
            'fields': ('notes',),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )
    
    def element_range(self, obj):
        return f"{obj.element_min} - {obj.element_max}"
    element_range.short_description = 'Element Range'


@admin.register(Config)
class ConfigAdmin(admin.ModelAdmin):
    list_display = ('name', 'user', 'is_default', 'linelist_count', 'updated_at')
    list_filter = ('is_default', 'user')
    search_fields = ('name', 'user__name', 'description')
    readonly_fields = ('created_at', 'updated_at')
    inlines = [ConfigLinelistInline]
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'user', 'is_default', 'description')
        }),
        ('Global Parameters', {
            'fields': (
                ('wl_window_ref', 'wl_ref'),
                ('max_ionization', 'max_excitation_eV'),
            )
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        }),
    )
    
    def linelist_count(self, obj):
        return obj.configlinelist_set.count()
    linelist_count.short_description = 'Linelists'


@admin.register(ConfigLinelist)
class ConfigLinelistAdmin(admin.ModelAdmin):
    list_display = ('config', 'linelist', 'priority', 'is_enabled', 'mergeable')
    list_filter = ('is_enabled', 'mergeable', 'config')
    search_fields = ('config__name', 'linelist__name', 'linelist__path')
    autocomplete_fields = ['config', 'linelist']
    ordering = ['config', 'priority']
    fieldsets = (
        ('Association', {
            'fields': ('config', 'linelist', 'priority', 'is_enabled')
        }),
        ('Merge Settings', {
            'fields': ('mergeable', 'replacement_window')
        }),
        ('Rank Weights', {
            'fields': (
                ('rank_wl', 'rank_gf', 'rank_rad'),
                ('rank_stark', 'rank_waals', 'rank_lande'),
                ('rank_term', 'rank_ext_vdw', 'rank_zeeman'),
            ),
            'classes': ('collapse',)
        }),
    )
