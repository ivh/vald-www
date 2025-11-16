from django.contrib import admin
from .models import UserPreferences, PersonalConfig, LineList


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
