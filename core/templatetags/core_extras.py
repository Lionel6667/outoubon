from django import template

register = template.Library()


@register.filter
def getitem(dictionary, key):
    """Access a dictionary value by key in templates: {{ mydict|getitem:key }}"""
    if isinstance(dictionary, dict):
        return dictionary.get(key, '')
    return ''


@register.filter
def default_if_none(value, default=''):
    return value if value is not None else default
