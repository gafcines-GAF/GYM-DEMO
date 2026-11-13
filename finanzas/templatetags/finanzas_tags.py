from django import template

from finanzas.formato import pesos as _pesos

register = template.Library()


@register.filter
def pesos(valor):
    return _pesos(valor)
