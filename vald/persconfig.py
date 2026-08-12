"""
Personal configuration management - database-backed implementation.

Uses Linelist, Config, and ConfigLinelist models instead of .cfg files.
"""
from django.db import transaction
from .models import Linelist, Config, ConfigLinelist

# Quality rank weights, as observed in the shipped default.cfg: 0 through 9.
# The lower bound is 0, not 1 - the real default has 19 entries with a rank of 0
# (vdw_barklem_final and friends), so clamping to 1 would silently rewrite
# legitimate VALD data the moment a user edited one of those linelists. The
# model help_text saying "1-9" was wrong about this too.
#
# Clamped rather than rejected: the values arrive from nine separate form fields,
# and pinning an out-of-range one to the nearest legal value is friendlier than
# failing the whole save. Enforced here, at the only place the web UI persists
# them, so nothing downstream has to trust the view's parsing - an unbounded int
# reaches both the Fortran config parser and sqlite, and sqlite raises
# OverflowError past 2^63 (which surfaced as a 500 on the persconf page).
#
# The import commands deliberately do NOT clamp: a migration should carry legacy
# configuration across verbatim, and the legacy files do contain the occasional
# out-of-range value that the Fortran has evidently tolerated for years.
RANK_MIN = 0
RANK_MAX = 9


def clamp_rank(value, default=3):
    """Coerce a rank weight to an int within RANK_MIN..RANK_MAX."""
    try:
        return max(RANK_MIN, min(RANK_MAX, int(value)))
    except (TypeError, ValueError):
        return default


# A user is in one of two states, and both must be reachable:
#
#   no personal config  - requests use the VALD default, including whatever a
#                         future release adds to it.
#   personal config     - a snapshot taken when they first customised something,
#                         plus their edits. It does not change when the VALD
#                         default does.
#
# Only the second used to be reachable, because merely opening the page created
# a config. That silently froze every visitor at the linelists of the day they
# looked, with nothing in the interface saying so (R24/R47).


def get_user_config(user):
    """This user's personal config, or None if they track the VALD default.

    Read-only. Creating on read was the whole defect: a GET wrote 378 rows and
    moved the user into the frozen state without them asking. Use
    get_or_create_user_config() on the paths that genuinely mean "customise".
    """
    return Config.objects.filter(user=user, is_default=True).first()


def get_effective_config(user):
    """(config, is_personal) - the config this user's requests actually use."""
    personal = get_user_config(user)
    if personal:
        return personal, True
    return get_default_config(), False


def create_user_config(user):
    """
    Snapshot the current VALD default as this user's personal config.

    Returns:
        Config instance for this user, or None if there is no system default
    """
    default_config = Config.objects.filter(user__isnull=True, is_default=True).first()
    if not default_config:
        return None

    # Create user config by copying default
    with transaction.atomic():
        user_config = Config.objects.create(
            name=f"{user.name}'s Config",
            user=user,
            is_default=True,
            wl_window_ref=default_config.wl_window_ref,
            wl_ref=default_config.wl_ref,
            max_ionization=default_config.max_ionization,
            max_excitation_eV=default_config.max_excitation_eV,
        )
        
        # Copy all linelist associations
        for cl in default_config.configlinelist_set.all():
            ConfigLinelist.objects.create(
                config=user_config,
                linelist=cl.linelist,
                priority=cl.priority,
                is_enabled=cl.is_enabled,
                mergeable=cl.mergeable,
                replacement_window=cl.replacement_window,
                rank_wl=cl.rank_wl,
                rank_gf=cl.rank_gf,
                rank_rad=cl.rank_rad,
                rank_stark=cl.rank_stark,
                rank_waals=cl.rank_waals,
                rank_lande=cl.rank_lande,
                rank_term=cl.rank_term,
                rank_ext_vdw=cl.rank_ext_vdw,
                rank_zeeman=cl.rank_zeeman,
            )
    
    return user_config


def get_or_create_user_config(user):
    """The user's personal config, snapshotting the default if they have none.

    The transition from tracking the default to having a frozen copy. Only the
    paths that mean "customise this" may call it.
    """
    return get_user_config(user) or create_user_config(user)


def get_default_config():
    """Get the system default config."""
    return Config.objects.filter(user__isnull=True, is_default=True).first()


# What a request stores in parameters['pconf']. 'default' and 'personal' predate
# the other system configs and every request ever made uses one of them, so they
# keep their meaning: whatever is the system default now, and the user's own
# snapshot. Anything else is a system config's slug.
PCONF_DEFAULT = 'default'
PCONF_PERSONAL = 'personal'


def get_alternative_configs():
    """Selectable system configs other than the default, in display order.

    A system config without a slug is not offered: nothing could name it in a
    request, and the ones that exist are archived copies rather than choices.
    """
    return list(Config.objects.filter(user__isnull=True, is_default=False)
                .exclude(slug='').exclude(slug=PCONF_DEFAULT)
                .order_by('name'))


def resolve_pconf(user, pconf):
    """The Config a request with this parameters['pconf'] must run with.

    Returns None when the choice names nothing that exists - a config deleted
    after the request was stored. The caller has to fail rather than substitute
    the default: running the default while the request says otherwise is exactly
    the mismatch the disabled-'Custom' change went after.
    """
    if pconf == PCONF_PERSONAL:
        return get_user_config(user)
    if pconf == PCONF_DEFAULT or not pconf:
        return get_default_config()
    return Config.objects.filter(
        user__isnull=True, is_default=False, slug=pconf).first()


def remove_user_config(user):
    """Delete the personal config, so the user tracks the VALD default again."""
    Config.objects.filter(user=user).delete()


def set_user_config_to_current_default(user):
    """Replace the personal config with a fresh snapshot of today's default.

    Distinct from remove_user_config(): this keeps the user in the frozen state,
    pinned to the default as it is now. Deleting instead means following the
    default wherever it goes. Both are legitimate and the old single "reset"
    button silently did this one.
    """
    remove_user_config(user)
    return create_user_config(user)


def linelists_added_since(user_config):
    """Linelists in the current VALD default that this snapshot does not have.

    What the user is missing by being frozen. Shown on the page, because a
    snapshot the user cannot see the age of is the same trap with better
    buttons.
    """
    default_config = get_default_config()
    if not default_config or not user_config or user_config.user_id is None:
        return []

    mine = set(user_config.configlinelist_set.values_list('linelist_id', flat=True))
    missing = default_config.configlinelist_set.exclude(
        linelist_id__in=mine).values_list('linelist_id', flat=True)
    return list(Linelist.objects.filter(id__in=missing, is_active=True)
                .order_by('default_priority', 'path'))


def get_linelists_for_display(config):
    """
    Get linelists for display in the config editor.
    
    Returns list of dicts with:
        - id: linelist ID
        - name: linelist name
        - path: linelist path
        - priority: sort priority
        - is_enabled: whether enabled (not commented)
        - ranks: list of 9 rank values
        - config_linelist_id: ConfigLinelist pk for updates
    """
    linelists = []
    
    for cl in config.configlinelist_set.select_related('linelist').order_by('priority'):
        linelists.append({
            'id': cl.linelist.id,
            'name': cl.linelist.name,
            'path': cl.linelist.path,
            'priority': cl.priority,
            'is_enabled': cl.is_enabled,
            'mergeable': cl.mergeable,
            'element_min': cl.linelist.element_min,
            'element_max': cl.linelist.element_max,
            'ranks': [
                cl.rank_wl, cl.rank_gf, cl.rank_rad, cl.rank_stark,
                cl.rank_waals, cl.rank_lande, cl.rank_term,
                cl.rank_ext_vdw, cl.rank_zeeman
            ],
            'config_linelist_id': cl.id,
        })
    
    return linelists


def update_config_linelist(linelist_id, user, is_enabled=None, ranks=None):
    """
    Update one linelist's settings in this user's own config.

    Keyed by Linelist, not by ConfigLinelist. A user who tracks the default is
    shown the *system* config's rows, so a ConfigLinelist pk posted back would
    name a row they do not own - which is precisely R2. Naming a linelist
    instead leaves the choice of junction row to this function, always inside
    the user's own config, so the mistake cannot be expressed.

    Creates the personal config if the user has none: this is an edit, which is
    exactly the point at which they stop tracking the default.

    Args:
        linelist_id: pk of Linelist
        user: User whose config is edited
        is_enabled: new enabled state (or None to keep)
        ranks: list of 9 rank values (or None to keep)
    """
    # Validated against what the user is currently being shown, before anything
    # is created - a bogus id must not leave a personal config behind.
    source, _ = get_effective_config(user)
    if not source or not source.configlinelist_set.filter(linelist_id=linelist_id).exists():
        return False

    config = get_or_create_user_config(user)
    if not config:
        return False

    try:
        cl = ConfigLinelist.objects.get(config=config, linelist_id=linelist_id)

        if is_enabled is not None:
            cl.is_enabled = is_enabled
        
        if ranks and len(ranks) == 9:
            ranks = [clamp_rank(r) for r in ranks]
            cl.rank_wl = ranks[0]
            cl.rank_gf = ranks[1]
            cl.rank_rad = ranks[2]
            cl.rank_stark = ranks[3]
            cl.rank_waals = ranks[4]
            cl.rank_lande = ranks[5]
            cl.rank_term = ranks[6]
            cl.rank_ext_vdw = ranks[7]
            cl.rank_zeeman = ranks[8]
        
        cl.save()
        return True
    except ConfigLinelist.DoesNotExist:
        return False


def restore_linelist_to_default(linelist_id, user):
    """
    Restore a single linelist entry to system default values.

    Keyed by Linelist for the same reason as update_config_linelist. Unlike an
    edit this never creates a personal config: a user who tracks the default is
    already at the default, so restoring is a no-op success rather than a reason
    to freeze them.
    """
    config = get_user_config(user)
    if not config:
        return True

    default_config = get_default_config()
    if not default_config:
        return False

    try:
        cl = ConfigLinelist.objects.select_related('linelist').get(
            config=config, linelist_id=linelist_id
        )

        default_cl = ConfigLinelist.objects.filter(
            config=default_config,
            linelist_id=linelist_id
        ).first()

        if not default_cl:
            return False

        # Copy values from default
        cl.is_enabled = default_cl.is_enabled
        cl.priority = default_cl.priority
        cl.mergeable = default_cl.mergeable
        cl.replacement_window = default_cl.replacement_window
        cl.rank_wl = default_cl.rank_wl
        cl.rank_gf = default_cl.rank_gf
        cl.rank_rad = default_cl.rank_rad
        cl.rank_stark = default_cl.rank_stark
        cl.rank_waals = default_cl.rank_waals
        cl.rank_lande = default_cl.rank_lande
        cl.rank_term = default_cl.rank_term
        cl.rank_ext_vdw = default_cl.rank_ext_vdw
        cl.rank_zeeman = default_cl.rank_zeeman
        cl.save()
        
        return True
    except ConfigLinelist.DoesNotExist:
        return False


def get_modification_flags(user_config, default_config):
    """
    Compare user config with default to find modifications.
    
    Returns dict mapping linelist_id to modification info.
    """
    if not default_config:
        return {}
    
    # Build lookup of default values
    default_lookup = {}
    for cl in default_config.configlinelist_set.all():
        default_lookup[cl.linelist_id] = cl
    
    modifications = {}
    for cl in user_config.configlinelist_set.all():
        default_cl = default_lookup.get(cl.linelist_id)
        if not default_cl:
            continue
        
        mod = {
            'is_enabled': cl.is_enabled != default_cl.is_enabled,
            'ranks': [
                cl.rank_wl != default_cl.rank_wl,
                cl.rank_gf != default_cl.rank_gf,
                cl.rank_rad != default_cl.rank_rad,
                cl.rank_stark != default_cl.rank_stark,
                cl.rank_waals != default_cl.rank_waals,
                cl.rank_lande != default_cl.rank_lande,
                cl.rank_term != default_cl.rank_term,
                cl.rank_ext_vdw != default_cl.rank_ext_vdw,
                cl.rank_zeeman != default_cl.rank_zeeman,
            ],
        }
        mod['any'] = mod['is_enabled'] or any(mod['ranks'])
        modifications[cl.linelist_id] = mod
    
    return modifications

