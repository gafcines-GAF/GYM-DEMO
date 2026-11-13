from decimal import Decimal, InvalidOperation


def pesos(valor):
    """1234.5 → $1.234,50 (formato argentino)."""
    try:
        v = Decimal(valor or 0).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return valor
    entero, dec = f"{abs(v):,.2f}".split(".")
    texto = "$" + entero.replace(",", ".") + ("" if dec == "00" else f",{dec}")
    return f"-{texto}" if v < 0 else texto
