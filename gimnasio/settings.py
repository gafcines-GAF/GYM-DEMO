"""
Configuración de Django para el proyecto gimnasio.

Los valores sensibles o que cambian entre entornos (clave secreta, DEBUG, hosts,
ruta del admin) se leen de variables de entorno, cargadas desde el archivo `.env`
de la raíz del proyecto. Ver `.env.example`.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Carga .env (no pisa variables que ya estén definidas en el entorno)
load_dotenv(BASE_DIR / ".env")


def env_bool(nombre: str, defecto: bool = False) -> bool:
    valor = os.environ.get(nombre)
    if valor is None:
        return defecto
    return valor.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


def env_list(nombre: str, defecto: str = "") -> list[str]:
    return [x.strip() for x in os.environ.get(nombre, defecto).split(",") if x.strip()]


# ──────────────────────────────────────────────────────────────
# Seguridad básica
# ──────────────────────────────────────────────────────────────
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "Falta DJANGO_SECRET_KEY. Copiá .env.example a .env y completá los valores."
    )

# Por defecto, producción: DEBUG solo se activa explícitamente en el .env local
DEBUG = env_bool("DJANGO_DEBUG", False)

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "gafarias.pythonanywhere.com")

# Orígenes en minúsculas: los navegadores envían el host así y Django los compara tal cual
CSRF_TRUSTED_ORIGINS = env_list("DJANGO_CSRF_TRUSTED_ORIGINS",
                                "https://gafarias.pythonanywhere.com")

# Ruta del admin (cambiarla dificulta los escaneos automáticos de /admin/)
ADMIN_URL = os.environ.get("DJANGO_ADMIN_URL", "admin/").strip("/") + "/"


# ──────────────────────────────────────────────────────────────
# Aplicaciones
# ──────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'turnos',
    'finanzas',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'gimnasio.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'turnos.context_processors.sitio',
                'finanzas.context_processors.permisos',
            ],
        },
    },
]

WSGI_APPLICATION = 'gimnasio.wsgi.application'


# ──────────────────────────────────────────────────────────────
# Base de datos (SQLite)
# ──────────────────────────────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# ──────────────────────────────────────────────────────────────
# Contraseñas
# ──────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


# ──────────────────────────────────────────────────────────────
# Idioma y zona horaria
# ──────────────────────────────────────────────────────────────
LANGUAGE_CODE = 'es-ar'
TIME_ZONE = 'America/Argentina/Buenos_Aires'
USE_I18N = True
USE_TZ = True


# ──────────────────────────────────────────────────────────────
# Estáticos y archivos subidos
# ──────────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'          # destino de `collectstatic`

MEDIA_URL = '/media/'                           # logo, encabezado y pie
MEDIA_ROOT = BASE_DIR / 'media'

# Límite de las subidas (las imágenes del sitio validan además 5 MB por archivo)
DATA_UPLOAD_MAX_MEMORY_SIZE = 10 * 1024 * 1024
FILE_UPLOAD_PERMISSIONS = 0o644

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'turnos:inicio'
LOGOUT_REDIRECT_URL = 'login'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'


# ──────────────────────────────────────────────────────────────
# HTTPS y cabeceras de seguridad (solo en producción)
# ──────────────────────────────────────────────────────────────
# En desarrollo (DEBUG=True) se sirve por http://localhost, así que estas
# opciones se activan por defecto únicamente cuando DEBUG=False.
SEGURO = env_bool("DJANGO_SECURE", not DEBUG)

X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
SESSION_COOKIE_HTTPONLY = True

if SEGURO:
    # PythonAnywhere termina HTTPS en su proxy y le pasa la petición a Django por
    # HTTP con la cabecera X-Forwarded-Proto. Sin esto, SECURE_SSL_REDIRECT
    # redirige en bucle y la verificación CSRF de origen falla.
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # HSTS: el navegador solo usará HTTPS con este dominio durante este tiempo
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", 60 * 60 * 24 * 30))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False      # no controlamos otros subdominios
    SECURE_HSTS_PRELOAD = False

# Advertencias de `check --deploy` que no aplican a un subdominio de pythonanywhere.com:
# no podemos incluir subdominios en HSTS ni anotar el dominio en la lista de precarga.
SILENCED_SYSTEM_CHECKS = ["security.W005", "security.W021"]


# ──────────────────────────────────────────────────────────────
# Logs: los errores 500 van a la consola (en PythonAnywhere, al error log)
# ──────────────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"consola": {"class": "logging.StreamHandler"}},
    "loggers": {
        "django.request": {"handlers": ["consola"], "level": "ERROR", "propagate": False},
        "django.security": {"handlers": ["consola"], "level": "WARNING", "propagate": False},
    },
}
