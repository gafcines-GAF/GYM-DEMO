from datetime import date


class FechaConverter:
    # Solo años 2000-2099: evita errores 500 por fechas extremas (p. ej. 0001-01-01)
    regex = r"20\d{2}-\d{2}-\d{2}"

    def to_python(self, value):
        try:
            return date.fromisoformat(value)
        except ValueError as e:
            raise ValueError from e   # Django lo convierte en 404

    def to_url(self, value):
        return value.isoformat() if isinstance(value, date) else value
