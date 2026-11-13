from datetime import date


class FechaConverter:
    regex = r"\d{4}-\d{2}-\d{2}"

    def to_python(self, value):
        try:
            return date.fromisoformat(value)
        except ValueError as e:
            raise ValueError from e   # Django lo convierte en 404

    def to_url(self, value):
        return value.isoformat() if isinstance(value, date) else value
