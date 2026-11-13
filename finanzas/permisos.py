"""Quién puede gestionar finanzas: superusuarios y profesores con nivel >= nivel_minimo."""
from functools import wraps

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied

from turnos.models import nivel_profesor

from .models import ParametrosFinanzas


def puede_gestionar_finanzas(user) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    return bool(user.is_staff and nivel_profesor(user) >= ParametrosFinanzas.load().nivel_minimo)


def exigir_permiso(user):
    if not puede_gestionar_finanzas(user):
        minimo = ParametrosFinanzas.load().nivel_minimo
        raise PermissionDenied(f"Necesitás nivel de profesor {minimo} o más para gestionar finanzas.")


def requiere_finanzas(vista):
    @wraps(vista)
    def envoltura(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path())
        exigir_permiso(request.user)
        return vista(request, *args, **kwargs)
    return envoltura


class SoloFinanzasAdminMixin:
    """La misma regla en el Admin. Nada se borra desde el Admin: se anula."""

    def has_module_permission(self, request):
        return puede_gestionar_finanzas(request.user)

    def has_view_permission(self, request, obj=None):
        return puede_gestionar_finanzas(request.user)

    def has_add_permission(self, request, *args):
        return puede_gestionar_finanzas(request.user)

    def has_change_permission(self, request, obj=None):
        return puede_gestionar_finanzas(request.user)

    def has_delete_permission(self, request, obj=None):
        return False
