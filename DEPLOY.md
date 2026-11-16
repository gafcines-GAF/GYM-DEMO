# Despliegue en PythonAnywhere (plan gratuito)

- Usuario: `GAFarias`
- Dominio: https://gafarias.pythonanywhere.com
- Carpeta del proyecto en el servidor: `/home/GAFarias/Gym`

> Los nombres de host son insensibles a mayúsculas; en la configuración se usan en
> minúsculas porque así los envían los navegadores.

---

## 1. En tu computadora: subir el código a GitHub

Creá un repositorio **vacío** en GitHub (sin README ni .gitignore). Recomendado: privado.

```bash
git remote add origin https://github.com/TU_USUARIO_GITHUB/TU_REPO.git
git push -u origin main
```

Antes de pushear, verificá que no se suba ningún secreto ni la base local:

```bash
git ls-files | grep -E "^\.env$|db\.sqlite3|staticfiles/" || echo "OK: nada sensible versionado"
```

---

## 2. En PythonAnywhere: consola Bash

Abrí **Consoles → Bash** y ejecutá en orden. Reemplazá `TU_USUARIO_GITHUB/TU_REPO`.
Si el repositorio es privado, git te va a pedir usuario y, como contraseña, un
*Personal Access Token* de GitHub (no tu contraseña de GitHub).

```bash
# Versiones de Python disponibles (usá la más nueva, 3.12 o superior)
ls /usr/bin/python3.1*
```

```bash
cd ~
git clone https://github.com/TU_USUARIO_GITHUB/TU_REPO.git Gym
cd ~/Gym
```

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

Crear el `.env` de producción. La clave secreta y la ruta del admin se generan
**en el servidor**, así nunca pasan por tu computadora ni por el chat:

```bash
cat > .env <<EOF
DJANGO_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(50))')
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=gafarias.pythonanywhere.com
DJANGO_CSRF_TRUSTED_ORIGINS=https://gafarias.pythonanywhere.com
DJANGO_ADMIN_URL=gestion-$(python -c 'import secrets; print(secrets.token_hex(4))')/
EOF
chmod 600 .env
grep DJANGO_ADMIN_URL .env    # anotá esta ruta: es la dirección de tu panel de administración
```

Base de datos, estáticos y verificación:

```bash
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py check --deploy
python manage.py createsuperuser
```

> **No ejecutes `cargar_demo` en producción**: borra todos los datos y crea usuarios con
> contraseña conocida. Está bloqueado con `DEBUG=False`.

---

## 3. En PythonAnywhere: pestaña **Web**

1. **Add a new web app** → *Next* → **Manual configuration** (no el asistente "Django")
   → la misma versión de Python que usaste para el `.venv` (ej. 3.13).
2. Sección **Code**:
   - Source code: `/home/GAFarias/Gym`
   - Working directory: `/home/GAFarias/Gym`
3. Sección **Virtualenv**: `/home/GAFarias/Gym/.venv`
4. **WSGI configuration file**: hacé clic en el enlace (por ejemplo
   `/var/www/gafarias_pythonanywhere_com_wsgi.py`), **borrá todo su contenido** y pegá:

```python
# WSGI de PythonAnywhere para el proyecto Gym (Django)
import os
import sys

PROJECT_HOME = "/home/GAFarias/Gym"
if PROJECT_HOME not in sys.path:
    sys.path.insert(0, PROJECT_HOME)

# Variables de entorno desde /home/GAFarias/Gym/.env
from dotenv import load_dotenv

load_dotenv(os.path.join(PROJECT_HOME, ".env"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gimnasio.settings")

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
```

5. Sección **Static files** (Django no sirve estos archivos con `DEBUG=False`):

   | URL | Directory |
   |---|---|
   | `/static/` | `/home/GAFarias/Gym/staticfiles` |
   | `/media/` | `/home/GAFarias/Gym/media` |

6. Sección **Security**: **Force HTTPS** → *Enabled*.
7. Botón verde **Reload gafarias.pythonanywhere.com** y abrí https://gafarias.pythonanywhere.com

Si algo falla, mirá el **Error log** (enlace en la misma pestaña Web).

---

## 4. Tarea programada (pestaña **Tasks**)

El plan gratuito permite una tarea diaria. Los dos comandos son idempotentes, así que
correrlos todos los días es seguro: generan las cuotas del mes en curso y los turnos
del mes siguiente apenas corresponden. Hora en UTC (09:00 UTC = 06:00 en Argentina):

```bash
cd /home/GAFarias/Gym && .venv/bin/python manage.py generar_cuotas && .venv/bin/python manage.py generar_mes
```

---

## 5. Primeros pasos en el sistema

1. Entrá a `https://gafarias.pythonanywhere.com/<DJANGO_ADMIN_URL>` con el superusuario.
2. **Parámetros del gimnasio**: nombre, logo, encabezado, pie, cupo y horarios.
3. **Finanzas → Tarifas**: revisá los precios y cargá el plan de **1 día por semana**.
4. **Finanzas → Parámetros**: día de vencimiento y % de recargo.
5. **Usuarios**: creá los profesores (staff) y asignales nivel (6 o más para Finanzas).
6. **Alumnos**: alta con usuario, contraseña y semana tipo.

---

## 6. Actualizar después de nuevos cambios

En tu computadora: `git push`. En la consola Bash de PythonAnywhere:

```bash
cd ~/Gym && source .venv/bin/activate
git pull
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
touch /var/www/gafarias_pythonanywhere_com_wsgi.py   # recarga la web app
```

## 7. Copia de seguridad de la base

```bash
cd ~/Gym && cp db.sqlite3 ~/backup-$(date +%F).sqlite3
```

Descargala desde la pestaña **Files** de vez en cuando.

## Límites del plan gratuito

- La web app se desactiva a los 3 meses si no tocás **"Run until 3 months from today"** en la pestaña Web.
- Cuota diaria de CPU y 512 MB de disco.
- Acceso a internet saliente solo a sitios de una lista permitida (GitHub y PyPI están incluidos).
