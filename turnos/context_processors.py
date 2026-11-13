from .models import ParametrosGimnasio


def sitio(request):
    """Deja `sitio` (nombre, logo, encabezado, pie) disponible en todos los templates."""
    return {"sitio": ParametrosGimnasio.load()}
