from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("cuentas/", include("django.contrib.auth.urls")),
    path("finanzas/", include("finanzas.urls")),
    path("", include("turnos.urls")),
]

if settings.DEBUG:
    # En producción las imágenes subidas las sirve el servidor web (nginx, etc.)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
