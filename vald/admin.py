from django.contrib import admin
from django.contrib.auth.forms import ReadOnlyPasswordHashField
from django.core.mail import send_mail
from django.conf import settings
from django.urls import reverse
from django import forms
from .models import Request, User, UserEmail, UserPreferences


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
            return queryset.exclude(password__isnull=True).exclude(password='')
        if self.value() == 'no':
            return queryset.filter(password__isnull=True) | queryset.filter(password='')


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
            return queryset.filter(is_active=False, password__isnull=True)
        if self.value() == 'no':
            return queryset.exclude(is_active=False, password__isnull=True)


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
    list_display = ('name', 'get_emails', 'has_password', 'is_active', 'is_pending', 'created_at')
    list_filter = ('is_active', HasPasswordFilter, PendingApprovalFilter, 'created_at')
    search_fields = ('name', 'affiliation', 'emails__email')
    readonly_fields = ('created_at', 'updated_at', 'activation_token')
    inlines = [UserEmailInline, UserPreferencesInline]
    actions = ['approve_and_send_activation', 'approve_without_email', 'reject_registration']
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
            elif len(password) < 6:
                messages.error(request, 'Password must be at least 6 characters.')
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
        return not obj.is_active and not obj.password
    is_pending.boolean = True
    is_pending.short_description = 'Pending Approval'

    def approve_and_send_activation(self, request, queryset):
        """Approve selected users and send activation email"""
        count = 0
        for user in queryset:
            if not user.is_active:
                user.is_active = True
                token = user.generate_activation_token()
                user.save()

                if user.primary_email:
                    activation_path = reverse('vald:activate_account', kwargs={'token': token})
                    activation_url = f"{settings.SITE_URL}{activation_path}"
                    try:
                        send_mail(
                            'VALD Account Activated',
                            f'Hello {user.name},\n\n'
                            f'Your VALD account has been approved!\n\n'
                            f'Please click the following link to set your password and activate your account:\n'
                            f'{activation_url}\n\n'
                            f'This link will expire in 7 days.\n\n'
                            f'Best regards,\n'
                            f'VALD Team',
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
        """Approve selected users without sending email"""
        updated = queryset.update(is_active=True)
        self.message_user(request, f'{updated} user(s) approved (no email sent).')
    approve_without_email.short_description = 'Approve without sending email'

    def reject_registration(self, request, queryset):
        """Delete/reject selected pending users"""
        count = queryset.filter(is_active=False, password__isnull=True).count()
        queryset.filter(is_active=False, password__isnull=True).delete()
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
