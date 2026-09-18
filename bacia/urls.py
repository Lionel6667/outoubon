from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic.base import RedirectView
from core.push_sw import firebase_messaging_sw
from core import views as core_views

urlpatterns = [
    # ── Root SEO & Web Manifest Routes ──
    path("robots.txt", core_views.robots_txt_view, name="root_robots_txt"),
    path("sitemap.xml", core_views.sitemap_xml_view, name="root_sitemap_xml"),
    path("manifest.json", core_views.manifest_json_view, name="root_manifest_json"),
    path("site.webmanifest", core_views.manifest_json_view, name="root_site_webmanifest"),
    path("cgu/", core_views.cgu_view, name="root_cgu"),
    path("cgu", core_views.cgu_view),

    # ── Root-level shortcuts for main features (Indexable by Googlebot) ──
    path("cours/", core_views.cours_view, name="root_cours"),
    path("quiz/", core_views.quiz_view, name="root_quiz"),
    path("examen-blanc/", core_views.examen_blanc_view, name="root_examen_blanc"),
    path("exercices/", core_views.exercices_view, name="root_exercices"),
    path("fiches/", core_views.fiches_view, name="root_fiches"),
    path("library/", core_views.library_view, name="root_library"),

    path("firebase-messaging-sw.js", firebase_messaging_sw),
    path("admin/", admin.site.urls),
    path("favicon.ico", RedirectView.as_view(url="/static/img/logo.png", permanent=True)),
    path("", include("accounts.urls")),
    path("dashboard/", include("core.urls")),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
