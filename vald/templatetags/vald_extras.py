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
    """Get parameter by index"""
    try:
        return linelist.get_param(int(index))
    except (ValueError, AttributeError):
        return ''


@register.filter(name='get_mod_flag')
def get_mod_flag(linelist, index):
    """Get modification flag by index"""
    try:
        return linelist.get_mod_flag(int(index))
    except (ValueError, AttributeError):
        return False
