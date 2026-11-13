"""
Genera las imágenes originales del sitio (logo, encabezado y pie) en
media/sitio/default/. Son diseños propios, sin derechos de terceros.

Uso: python manage.py crear_imagenes_base [--forzar]
"""
import math
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from PIL import Image, ImageDraw, ImageFilter, ImageFont

AZUL_OSCURO = (20, 36, 58)
AZUL = (58, 110, 165)
TURQUESA = (47, 143, 143)
NARANJA = (242, 140, 40)
BLANCO = (255, 255, 255)


def fuente(tamano, negrita=True):
    candidatos = (["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"] if negrita
                  else ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf"])
    for nombre in candidatos:
        for carpeta in ("C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu", ""):
            try:
                return ImageFont.truetype(str(Path(carpeta) / nombre) if carpeta else nombre, tamano)
            except OSError:
                continue
    return ImageFont.load_default(size=tamano)


def degradado(ancho, alto, c1, c2, diagonal=True):
    img = Image.new("RGB", (ancho, alto))
    px = img.load()
    total = ancho + alto if diagonal else alto
    for y in range(alto):
        for x in range(ancho):
            t = (x + y) / total if diagonal else y / alto
            px[x, y] = tuple(round(a + (b - a) * t) for a, b in zip(c1, c2))
    return img


def mancuerna(draw, cx, cy, escala, color):
    """Mancuerna horizontal centrada en (cx, cy)."""
    s = escala
    draw.rounded_rectangle([cx - 70 * s, cy - 7 * s, cx + 70 * s, cy + 7 * s], radius=4 * s, fill=color)
    for lado in (-1, 1):
        for desp, alto, ancho in ((48, 46, 14), (66, 34, 12), (82, 22, 8)):
            x = cx + lado * desp * s
            draw.rounded_rectangle([x - ancho * s / 2, cy - alto * s, x + ancho * s / 2, cy + alto * s],
                                   radius=3 * s, fill=color)


def crear_logo(destino: Path):
    tam = 512
    base = Image.new("RGBA", (tam, tam), (0, 0, 0, 0))
    fondo = degradado(tam, tam, AZUL, AZUL_OSCURO).convert("RGBA")
    mascara = Image.new("L", (tam, tam), 0)
    ImageDraw.Draw(mascara).ellipse([8, 8, tam - 8, tam - 8], fill=255)
    base.paste(fondo, (0, 0), mascara)

    d = ImageDraw.Draw(base)
    d.ellipse([8, 8, tam - 8, tam - 8], outline=NARANJA, width=14)
    d.ellipse([34, 34, tam - 34, tam - 34], outline=(255, 255, 255, 70), width=3)
    mancuerna(d, tam / 2, 200, 2.2, BLANCO)

    f = fuente(92)
    texto = "GYM"
    w = d.textlength(texto, font=f)
    d.text(((tam - w) / 2, 300), texto, font=f, fill=BLANCO)
    f2 = fuente(30, negrita=False)
    sub = "TURNOS · CLASES"
    w2 = d.textlength(sub, font=f2)
    d.text(((tam - w2) / 2, 410), sub, font=f2, fill=NARANJA)
    base.save(destino / "logo.png", optimize=True)


def crear_encabezado(destino: Path):
    ancho, alto = 1920, 360
    img = degradado(ancho // 4, alto // 4, AZUL_OSCURO, TURQUESA).resize((ancho, alto), Image.BICUBIC)
    capa = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)

    # Líneas diagonales de "movimiento"
    for x in range(-alto, ancho, 46):
        d.line([(x, alto), (x + alto, 0)], fill=(255, 255, 255, 14), width=10)

    # Discos (pesas) desenfocados de fondo
    for cx, cy, r, a in ((1500, 120, 210, 40), (1780, 300, 150, 32), (1180, 330, 110, 26)):
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, a), width=28)
        d.ellipse([cx - r * .25, cy - r * .25, cx + r * .25, cy + r * .25], fill=(255, 255, 255, a))

    # Mancuernas
    mancuerna(d, 1560, 250, 1.6, (255, 255, 255, 60))
    mancuerna(d, 1290, 110, 1.1, (255, 255, 255, 40))

    # Franja de acento
    d.polygon([(0, alto - 14), (ancho, alto - 40), (ancho, alto), (0, alto)], fill=NARANJA + (230,))

    capa = capa.filter(ImageFilter.GaussianBlur(1.2))
    img = Image.alpha_composite(img.convert("RGBA"), capa).convert("RGB")
    img.save(destino / "encabezado.jpg", quality=88, optimize=True)


def crear_pie(destino: Path):
    ancho, alto = 1920, 220
    img = degradado(ancho // 4, alto // 4, (12, 20, 32), AZUL_OSCURO).resize((ancho, alto), Image.BICUBIC)
    capa = Image.new("RGBA", (ancho, alto), (0, 0, 0, 0))
    d = ImageDraw.Draw(capa)
    d.rectangle([0, 0, ancho, 6], fill=NARANJA + (255,))
    # Patrón de puntos tipo "piso de goma"
    for y in range(24, alto, 26):
        for x in range((y // 26) % 2 * 13, ancho, 26):
            d.ellipse([x - 2, y - 2, x + 2, y + 2], fill=(255, 255, 255, 18))
    # Onda de pulso (cardio)
    puntos, base_y = [], 120
    for x in range(0, ancho, 6):
        fase = (x % 480) / 480
        y = base_y
        if .40 < fase < .46:
            y -= 70 * math.sin((fase - .40) / .06 * math.pi)
        elif .46 < fase < .50:
            y += 30 * math.sin((fase - .46) / .04 * math.pi)
        puntos.append((x, y))
    d.line(puntos, fill=TURQUESA + (120,), width=4)
    img = Image.alpha_composite(img.convert("RGBA"), capa).convert("RGB")
    img.save(destino / "pie.jpg", quality=88, optimize=True)


class Command(BaseCommand):
    help = "Genera el logo, el encabezado y el pie originales en media/sitio/default/."

    def add_arguments(self, parser):
        parser.add_argument("--forzar", action="store_true", help="Sobrescribe si ya existen.")

    def handle(self, *args, forzar=False, **opts):
        destino = Path(settings.MEDIA_ROOT) / "sitio" / "default"
        destino.mkdir(parents=True, exist_ok=True)
        tareas = {"logo.png": crear_logo, "encabezado.jpg": crear_encabezado, "pie.jpg": crear_pie}
        for nombre, funcion in tareas.items():
            if (destino / nombre).exists() and not forzar:
                self.stdout.write(f"{nombre}: ya existe (usá --forzar para regenerarla)")
                continue
            funcion(destino)
            self.stdout.write(self.style.SUCCESS(f"{nombre}: creada"))
