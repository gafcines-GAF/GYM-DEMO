from .permisos import puede_gestionar_finanzas


def permisos(request):
    """`ve_finanzas` habilita el enlace "Finanzas" en el encabezado."""
    user = getattr(request, "user", None)
    return {"ve_finanzas": bool(user and user.is_staff and puede_gestionar_finanzas(user))}
