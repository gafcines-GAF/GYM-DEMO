# Gimnasio · Gestión de turnos

Sistema Django de turnos mensuales: semana tipo por alumno replicada en el mes, control de cupo,
cambios de horario (puntual o permanente) y asignación manual de profesores con alerta de sobrecupo.

## Puesta en marcha

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python manage.py migrate
.venv\Scripts\python manage.py cargar_demo      # datos de prueba (borra turnos existentes)
.venv\Scripts\python manage.py runserver
```

Abrir http://localhost:8000

### Usuarios de prueba (solo desarrollo, creados por `cargar_demo`)

| Rol | Usuario | Contraseña |
|---|---|---|
| Profesor (superusuario, nivel 10) | `profesor` | `demo1234` |
| Profesor nivel 8 (accede a Finanzas) | `profe8` | `demo1234` |
| Profesor nivel 5 (sin acceso a Finanzas) | `profe5` | `demo1234` |
| Alumno | `alumno` | `demo1234` |

## Comandos

- `manage.py generar_mes [--anio A --mes M]`: replica la semana tipo (por defecto el mes siguiente).
  Idempotente; programarlo el día 25 de cada mes. También hay un botón en la vista Mes (profesores).
- `manage.py crear_imagenes_base [--forzar]`: vuelve a generar el logo, encabezado y pie
  originales en `media/sitio/default/` (diseños propios, sin derechos de terceros).
- `manage.py generar_cuotas [--periodo AAAA-MM]`: genera las cuotas del mes (por defecto el actual)
  para los alumnos activos. Idempotente; programarlo el día 1 de cada mes. También hay un botón en
  el dashboard de Finanzas.
- `manage.py cargar_demo_finanzas`: cuotas, pagos y descuentos de ejemplo (lo llama `cargar_demo`).
- `manage.py test turnos finanzas`: tests de reglas de negocio, vistas y admin.

## Estructura

| Archivo | Contenido |
|---|---|
| `turnos/models.py` | Parámetros (fila única), Alumno, HorarioFijo (semana tipo), Clase, Inscripción |
| `turnos/services.py` | Reglas de negocio: cupo con bloqueo, cambios, sobrecupo, generación mensual |
| `turnos/calendario.py` | Armado de grillas Día/Semana/Mes (2 consultas por pantalla) |
| `turnos/views.py` | Vistas finas que llaman a los servicios |
| `turnos/admin.py` | Admin: parámetros, alta de alumno + usuario + semana tipo, clases, inscripciones |
| `templates/` | Vistas y modal de cambio |
| `turnos/static/turnos/turnos.css` | Colores por ocupación (verde / amarillo / rojo / sobrecupo) |

## Apariencia (logo, encabezado y pie)

Las pantallas de usuarios logueados muestran un encabezado y un pie con imagen, más el logo.
Se cambian desde **Admin → Parámetros del gimnasio → Apariencia** (también el nombre y el texto
del pie). Las imágenes subidas se guardan en `media/sitio/`; al reemplazar una se borra la anterior.
La casilla «Restaurar imágenes originales» vuelve a las de `media/sitio/default/`.

## Módulo de finanzas (app `finanzas`)

App independiente que depende de `turnos` (no al revés). En `/finanzas/`:

- **Dashboard:** ingresos del mes (por fecha de pago), cobranza de las cuotas del mes, deuda vencida,
  alumnos al día / por vencer / morosos, ingresos de los últimos 6 meses y por método, deudores con
  el acumulado y exportación a CSV.
- **Cobrar:** búsqueda de alumno, cuotas impagas con saldo y recargo, registro y anulación de pagos.
- **Admin:** tarifas por días/semana (con vigencia), descuentos del alumno (en su ficha), parámetros
  (día de vencimiento, % de recargo, nivel mínimo).

Reglas: cuota completa aunque el alta sea a mitad de mes (se genera sola al dar de alta);
descuentos % + fijos, nunca negativo; no se puede pagar más que el saldo; el recargo se aplica una vez
si la fecha de pago es posterior al vencimiento; los pagos no se borran, se anulan con motivo.
Acceso: superusuarios y profesores con nivel ≥ 6 (nivel en Admin → Usuarios).

Archivos: `finanzas/models.py`, `services.py` (generación, pagos, recargo), `consultas.py`
(agregaciones del dashboard), `permisos.py`, `signals.py`, `views.py`, `templates/finanzas/`.

## Reglas implementadas

- Lunes a viernes, clases de 1 h de 07:00 a 21:00 (configurable en Parámetros).
- Cupo por clase (default 7) configurable; cupo especial opcional por clase.
- Un alumno: una clase por día (garantizado también por la base de datos).
- Cambios del alumno con al menos 1 día de anticipación (configurable). Profesores sin límite.
- Cambio permanente: todo o nada; la nueva semana tipo sigue vigente los meses siguientes.
- Profesores pueden asignar con la clase llena: queda marcada como sobrecupo con alerta visual.
- Clases canceladas (feriados) se saltean al generar el mes.
