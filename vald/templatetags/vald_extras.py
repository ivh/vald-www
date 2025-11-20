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
    """Get parameter by index - works with both dict and model objects"""
    try:
        idx = int(index)
        # Check if it's a dict (new file-based implementation)
        if isinstance(linelist, dict):
            if 'params' in linelist and idx < len(linelist['params']):
                return linelist['params'][idx]
            return ''
        # Otherwise assume it's a model object (legacy)
        else:
            return linelist.get_param(idx)
    except (ValueError, AttributeError, KeyError, IndexError):
        return ''


@register.filter(name='get_mod_flag')
def get_mod_flag(linelist, index):
    """Get modification flag by index - works with both dict and model objects"""
    try:
        idx = int(index)
        # Check if it's a dict (new file-based implementation)
        if isinstance(linelist, dict):
            if 'mod_flags' in linelist and idx < len(linelist['mod_flags']):
                return linelist['mod_flags'][idx]
            return False
        # Otherwise assume it's a model object (legacy)
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
