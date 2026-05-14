"""Traductions FR / EN — clés utilisées dans les templates avec {{ t('clé') }}."""
from __future__ import annotations

LOCALE_COOKIE = "lang"
DEFAULT_LOCALE = "fr"
SUPPORTED_LOCALES = frozenset({"fr", "en"})


def resolve_lang(value: str | None) -> str:
    if not value:
        return DEFAULT_LOCALE
    v = value.lower().strip()[:8]
    return v if v in SUPPORTED_LOCALES else DEFAULT_LOCALE


def translate(locale: str, key: str, **kwargs: object) -> str:
    table = MESSAGES.get(locale) or MESSAGES[DEFAULT_LOCALE]
    fallback = MESSAGES[DEFAULT_LOCALE]
    template = table.get(key) or fallback.get(key) or key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, ValueError):
        return template


MESSAGES: dict[str, dict[str, str]] = {
    "fr": {
        "nav.dashboard": "Tableau de bord",
        "nav.isos": "ISOs",
        "nav.boot_files": "Fichiers boot",
        "nav.configs": "Configs auto",
        "nav.menus": "Menus iPXE",
        "nav.firmware": "Firmware",
        "nav.settings": "Paramètres",
        "nav.logout": "Déconnexion",
        "lang.label": "Langue",
        "lang.fr": "Français",
        "lang.en": "English",
        "dash.title": "Tableau de bord",
        "dash.jobs_running": "{n} job(s) en cours",
        "dash.kill_all_confirm": "Forcer l'arrêt de tous les jobs en cours ?",
        "dash.stop_all": "Tout arrêter",
        "dash.jobs_header": "Jobs en cours",
        "dash.timeout_hint": "Timeout automatique après {h} h",
        "dash.col_file": "Fichier",
        "dash.col_type": "Type",
        "dash.col_size": "Taille",
        "dash.col_started": "Démarré",
        "dash.col_duration": "Durée",
        "dash.col_action": "Action",
        "dash.kill_confirm": "Forcer l'arrêt de ce job ?",
        "dash.kill": "Stopper",
        "dash.disk_title": "Espace disque du serveur",
        "dash.disk_free": "{n} GB libres",
        "dash.disk_used": "Utilisé : {n} GB",
        "dash.disk_total": "Total : {n} GB",
        "dash.ready": "{n} prêtes",
        "dash.total": "{n} total",
        "dash.manage": "Gérer",
        "dash.no_os": "Aucun type d'OS configuré.",
        "dash.no_os_link": "Ajouter un OS",
        "dash.no_os_settings": "dans les paramètres.",
        "dash.quick_upload_title": "Ajouter une version",
        "dash.quick_upload_sub": "ISO ou fichiers boot manuels",
        "dash.quick_menus_title": "Menus iPXE",
        "dash.quick_menus_sub": "Voir et régénérer les menus",
        "dash.quick_config_title": "Nouvelle config auto",
        "dash.quick_config_sub": "Preseed / Kickstart / Unattend",
        "dash.uploads_recent": "Uploads récents",
        "dash.col_status": "Statut",
        "dash.col_date": "Date",
        "dash.status_done": "Terminé",
        "dash.status_processing": "En cours",
        "dash.status_error": "Erreur",
        "dash.status_pending": "En attente",
        "auth.subtitle": "Connexion administrateur",
        "auth.password": "Mot de passe",
        "auth.submit": "Se connecter",
        "auth.bad_password": "Mot de passe incorrect",
    },
    "en": {
        "nav.dashboard": "Dashboard",
        "nav.isos": "ISOs",
        "nav.boot_files": "Boot files",
        "nav.configs": "Auto configs",
        "nav.menus": "iPXE menus",
        "nav.firmware": "Firmware",
        "nav.settings": "Settings",
        "nav.logout": "Log out",
        "lang.label": "Language",
        "lang.fr": "Français",
        "lang.en": "English",
        "dash.title": "Dashboard",
        "dash.jobs_running": "{n} job(s) running",
        "dash.kill_all_confirm": "Force stop all running jobs?",
        "dash.stop_all": "Stop all",
        "dash.jobs_header": "Running jobs",
        "dash.timeout_hint": "Automatic timeout after {h} h",
        "dash.col_file": "File",
        "dash.col_type": "Type",
        "dash.col_size": "Size",
        "dash.col_started": "Started",
        "dash.col_duration": "Duration",
        "dash.col_action": "Action",
        "dash.kill_confirm": "Force stop this job?",
        "dash.kill": "Kill",
        "dash.disk_title": "Server disk space",
        "dash.disk_free": "{n} GB free",
        "dash.disk_used": "Used: {n} GB",
        "dash.disk_total": "Total: {n} GB",
        "dash.ready": "{n} ready",
        "dash.total": "{n} total",
        "dash.manage": "Manage",
        "dash.no_os": "No OS type configured.",
        "dash.no_os_link": "Add an OS",
        "dash.no_os_settings": "in settings.",
        "dash.quick_upload_title": "Add a release",
        "dash.quick_upload_sub": "ISO or manual boot files",
        "dash.quick_menus_title": "iPXE menus",
        "dash.quick_menus_sub": "View and regenerate menus",
        "dash.quick_config_title": "New auto config",
        "dash.quick_config_sub": "Preseed / Kickstart / Unattend",
        "dash.uploads_recent": "Recent uploads",
        "dash.col_status": "Status",
        "dash.col_date": "Date",
        "dash.status_done": "Done",
        "dash.status_processing": "Running",
        "dash.status_error": "Error",
        "dash.status_pending": "Pending",
        "auth.subtitle": "Administrator sign-in",
        "auth.password": "Password",
        "auth.submit": "Sign in",
        "auth.bad_password": "Invalid password",
    },
}
