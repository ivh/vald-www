from django.db import migrations, models


def demote_extra_system_defaults(apps, schema_editor):
    """Leave exactly one system default, so the new constraint can be applied.

    Which one is kept has to match what get_default_config() has been returning
    all along, or applying this migration would silently change every user's
    linelist selection: that is .first() under Meta.ordering = ['user', 'name'],
    and user is NULL for all of these, so it comes down to name then pk.

    The losers are demoted rather than deleted - they still own ConfigLinelist
    rows, and a migration is no place to destroy configuration an admin may have
    built on purpose.
    """
    Config = apps.get_model('vald', 'Config')
    system_defaults = Config.objects.filter(
        user__isnull=True, is_default=True).order_by('name', 'pk')

    keep = system_defaults.first()
    if keep is None:
        return
    system_defaults.exclude(pk=keep.pk).update(is_default=False)


class Migration(migrations.Migration):

    dependencies = [
        ('vald', '0009_user_token_created_at'),
    ]

    operations = [
        migrations.RunPython(demote_extra_system_defaults,
                             migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='config',
            constraint=models.UniqueConstraint(
                condition=models.Q(('is_default', True), ('user__isnull', True)),
                fields=('is_default',),
                name='unique_system_default_config'),
        ),
    ]
