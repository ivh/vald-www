from django.db import migrations

# Everything generate_cfg_content() emits for one linelist. Two configs that
# agree on all of these produce byte-identical .cfg files, which is the only
# sense of "identical" that matters here.
ENTRY_FIELDS = (
    'priority', 'is_enabled', 'mergeable', 'replacement_window',
    'rank_wl', 'rank_gf', 'rank_rad', 'rank_stark', 'rank_waals',
    'rank_lande', 'rank_term', 'rank_ext_vdw', 'rank_zeeman',
)
GLOBAL_FIELDS = ('wl_window_ref', 'wl_ref', 'max_ionization', 'max_excitation_eV')


def _fingerprint(config):
    entries = config.configlinelist_set.all().values('linelist_id', *ENTRY_FIELDS)
    return (
        tuple(getattr(config, f) for f in GLOBAL_FIELDS),
        sorted((e['linelist_id'],) + tuple(e[f] for f in ENTRY_FIELDS) for e in entries),
    )


def drop_configs_identical_to_default(apps, schema_editor):
    """Delete personal configs that are indistinguishable from the VALD default.

    Until now, merely opening /persconf/ created one, so most of these were
    never a decision by anyone. Under the two-state model that matters: holding
    a config means "frozen at this snapshot, do not follow the VALD default",
    and these users never asked for that. Deleting theirs moves them back to
    tracking the default.

    Only exact copies go. A config that differs anywhere - including in fields
    the web UI cannot edit, which is how the imported legacy persconf files
    differ - is a real customisation and is left alone.
    """
    Config = apps.get_model('vald', 'Config')

    default = Config.objects.filter(user__isnull=True, is_default=True).first()
    if default is None:
        return

    default_fingerprint = _fingerprint(default)
    doomed = [
        config.pk
        for config in Config.objects.filter(user__isnull=False)
        if _fingerprint(config) == default_fingerprint
    ]
    Config.objects.filter(pk__in=doomed).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('vald', '0010_system_default_config_unique'),
    ]

    operations = [
        # Irreversible in the sense that matters: re-creating the deleted rows
        # would put those users back in the frozen state this removes them from.
        migrations.RunPython(drop_configs_identical_to_default,
                             migrations.RunPython.noop),
    ]
