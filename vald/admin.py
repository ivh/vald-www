from django.contrib import admin
from .models import Request, User, UserEmail, UserPreferences, PersonalConfig, LineList


@admin.register(Request)
class RequestAdmin(admin.ModelAdmin):
    list_display = ('uuid', 'request_type', 'user_email', 'status', 'created_at', 'has_output')
    list_filter = ('status', 'request_type', 'created_at')
    search_fields = ('uuid', 'user_email', 'user_name')
    readonly_fields = ('uuid', 'created_at', 'updated_at')
    fieldsets = (
        ('Request Information', {
            'fields': ('uuid', 'request_type', 'user_email', 'user_name')
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

    def has_output(self, obj):
        """Show if output file exists"""
        return obj.output_exists()
    has_output.boolean = True
    has_output.short_description = 'Output File'


class UserEmailInline(admin.TabularInline):
    model = UserEmail
    extra = 1
    fields = ('email', 'is_primary')


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('name', 'get_emails', 'has_password', 'is_active', 'created_at')
    list_filter = ('is_active', 'created_at')
    search_fields = ('name', 'affiliation', 'emails__email')
    readonly_fields = ('created_at', 'updated_at', 'activation_token')
    inlines = [UserEmailInline]
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

    def get_emails(self, obj):
        """Display all email addresses for the user"""
        return ', '.join(obj.emails.values_list('email', flat=True))
    get_emails.short_description = 'Email Addresses'

    def has_password(self, obj):
        """Show if user has set a password"""
        return bool(obj.password)
    has_password.boolean = True
    has_password.short_description = 'Has Password'


@admin.register(UserEmail)
class UserEmailAdmin(admin.ModelAdmin):
    list_display = ('email', 'user', 'is_primary', 'created_at')
    list_filter = ('is_primary',)
    search_fields = ('email', 'user__name')
    readonly_fields = ('created_at',)


@admin.register(UserPreferences)
class UserPreferencesAdmin(admin.ModelAdmin):
    list_display = ('email', 'name', 'energyunit', 'medium', 'waveunit', 'vdwformat', 'isotopic_scaling', 'updated_at')
    list_filter = ('energyunit', 'medium', 'waveunit', 'isotopic_scaling')
    search_fields = ('email', 'name')
    readonly_fields = ('created_at', 'updated_at')


class LineListInline(admin.TabularInline):
    model = LineList
    extra = 0
    fields = ('list_id', 'name', 'commented', 'param_5', 'param_6', 'param_7', 'param_8', 'param_9')
    readonly_fields = ('list_id', 'name')


@admin.register(PersonalConfig)
class PersonalConfigAdmin(admin.ModelAdmin):
    list_display = ('email', 'updated_at')
    search_fields = ('email',)
    readonly_fields = ('created_at', 'updated_at')
    inlines = [LineListInline]


@admin.register(LineList)
class LineListAdmin(admin.ModelAdmin):
    list_display = ('list_id', 'name', 'personal_config', 'commented', 'param_5', 'param_6', 'param_7')
    list_filter = ('commented',)
    search_fields = ('name', 'personal_config__email')
    readonly_fields = ('personal_config',)
