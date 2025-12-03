"""
Personal configuration management - database-backed implementation.

Uses Linelist, Config, and ConfigLinelist models instead of .cfg files.
"""
from django.db import transaction
from .models import Linelist, Config, ConfigLinelist


def get_user_config(user):
    """
    Get or create the user's personal config.
    If user has no config, creates one by copying the system default.
    
    Returns:
        Config instance for this user
    """
    # Try to get existing user config
    user_config = Config.objects.filter(user=user, is_default=True).first()
    if user_config:
        return user_config
    
    # Get system default config
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


def get_default_config():
    """Get the system default config."""
    return Config.objects.filter(user__isnull=True, is_default=True).first()


def reset_user_config(user):
    """
    Reset user's config to system default by deleting their personal config.
    Next call to get_user_config will recreate it from default.
    """
    Config.objects.filter(user=user).delete()


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


def update_config_linelist(config_linelist_id, is_enabled=None, ranks=None):
    """
    Update a ConfigLinelist entry.
    
    Args:
        config_linelist_id: pk of ConfigLinelist
        is_enabled: new enabled state (or None to keep)
        ranks: list of 9 rank values (or None to keep)
    """
    try:
        cl = ConfigLinelist.objects.get(pk=config_linelist_id)
        
        if is_enabled is not None:
            cl.is_enabled = is_enabled
        
        if ranks and len(ranks) == 9:
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


def restore_linelist_to_default(config_linelist_id):
    """
    Restore a single linelist entry to system default values.
    """
    try:
        cl = ConfigLinelist.objects.select_related('config', 'linelist').get(pk=config_linelist_id)
        
        # Find default config's entry for this linelist
        default_config = get_default_config()
        if not default_config:
            return False
        
        default_cl = ConfigLinelist.objects.filter(
            config=default_config,
            linelist=cl.linelist
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

