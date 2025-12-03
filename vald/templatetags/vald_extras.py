from django import template

register = template.Library()


@register.filter(name='call')
def call_method(obj, method_name_or_arg):
    """
    Call a method on an object or pass an argument.
    Usage: {{ object.method|call:arg }}
    """
    if hasattr(obj, '__call__'):
        # obj is already a method, call it with the argument
        return obj(int(method_name_or_arg))
    elif hasattr(obj, method_name_or_arg):
        # Get the method by name and call it
        method = getattr(obj, method_name_or_arg)
        if callable(method):
            return method()
        return method
    return ''


@register.filter(name='get_param')
def get_param(linelist, index):
    """
    Get parameter by index.
    
    For DB-backed configs: indices 0-8 map to ranks[0-8]
    For legacy file-based: indices 5-13 map to params[5-13]
    """
    try:
        idx = int(index)
        if isinstance(linelist, dict):
            # New DB-backed: use 'ranks' list with 0-8 indices
            if 'ranks' in linelist:
                if 0 <= idx < len(linelist['ranks']):
                    return linelist['ranks'][idx]
                return ''
            # Legacy file-based: use 'params' list
            elif 'params' in linelist and idx < len(linelist['params']):
                return linelist['params'][idx]
            return ''
        else:
            return linelist.get_param(idx)
    except (ValueError, AttributeError, KeyError, IndexError):
        return ''


@register.filter(name='get_mod_flag')
def get_mod_flag(linelist, index):
    """
    Get modification flag by index.
    
    For DB-backed configs: indices 0-8 map to mod_flags[0-8]
    For legacy file-based: indices 5-13 map to mod_flags[5-13]
    """
    try:
        idx = int(index)
        if isinstance(linelist, dict):
            if 'mod_flags' in linelist and idx < len(linelist['mod_flags']):
                return linelist['mod_flags'][idx]
            return False
        else:
            return linelist.get_mod_flag(idx)
    except (ValueError, AttributeError, KeyError, IndexError):
        return False


@register.filter(name='pprint')
def pprint_filter(value):
    """Pretty-print JSON/dict data"""
    import json
    try:
        return json.dumps(value, indent=2, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)
