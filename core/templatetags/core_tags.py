from django import template

register = template.Library()


@register.filter
def has_module(user, module_key):
    """Template helper mirroring ``accounts.User.has_module``.

    Returns False for anonymous users so navigation entries hide cleanly.
    """
    if not getattr(user, 'is_authenticated', False):
        return False
    return user.has_module(module_key)
