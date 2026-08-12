from django.db import migrations, models


def slug_the_system_default(apps, schema_editor):
    """Give the existing system default the slug every stored request names.

    parameters['pconf'] has only ever held 'default' or 'personal', and
    resolve_pconf() maps 'default' to whichever config is is_default=True, so
    this is not what makes old requests keep working - it is here so the slug
    column is not the one thing about the default config that is empty, and so
    that a variant cannot later be imported under the same name.
    """
    Config = apps.get_model('vald', 'Config')
    Config.objects.filter(user__isnull=True, is_default=True).update(slug='default')


class Migration(migrations.Migration):

    dependencies = [
        ('vald', '0013_config_snapshot_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='config',
            name='slug',
            field=models.SlugField(
                blank=True, default='', max_length=50,
                help_text='Stable identifier for a selectable system config '
                          '(blank for personal configs)'),
        ),
        migrations.AddConstraint(
            model_name='config',
            constraint=models.UniqueConstraint(
                condition=models.Q(('user__isnull', True), models.Q(('slug', ''), _negated=True)),
                fields=('slug',),
                name='unique_system_config_slug'),
        ),
        migrations.RunPython(slug_the_system_default, migrations.RunPython.noop),
    ]
