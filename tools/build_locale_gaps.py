#!/usr/bin/env python3
"""Génère tools/locale_gaps.json (traductions DE/ES/IT/PT pour clés récentes ou incomplètes)."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tools" / "locale_gaps.json"

# Clé i18n → {de, es, it, pt}
GAPS: dict[str, dict[str, str]] = {
    "sett.ot_meta_title_edit": {
        "de": "OS-Typ bearbeiten — Einstellungen",
        "es": "Editar tipo de SO — ajustes",
        "it": "Modifica tipo OS — impostazioni",
        "pt": "Editar tipo de SO — definições",
    },
    "sett.ot_section_identity": {
        "de": "Identität",
        "es": "Identidad",
        "it": "Identità",
        "pt": "Identidade",
    },
    "sett.ot_slug_help": {
        "de": "Nur Kleinbuchstaben, Ziffern und Bindestrich — höchstens 31 Zeichen.",
        "es": "Solo minúsculas, dígitos y guion — máximo 31 caracteres.",
        "it": "Solo minuscole, cifre e trattino — al massimo 31 caratteri.",
        "pt": "Apenas minúsculas, dígitos e hífen — no máximo 31 caracteres.",
    },
    "sett.ot_section_extract": {
        "de": "ISO-Extraktion",
        "es": "Extracción de ISO",
        "it": "Estrazione ISO",
        "pt": "Extração de ISO",
    },
    "sett.ot_extract_full_label": {
        "de": "Vollständige ISO-Extraktion",
        "es": "Extracción completa de ISO",
        "it": "Estrazione completa ISO",
        "pt": "Extração completa de ISO",
    },
    "sett.ot_extract_full_help": {
        "de": "Aktiviert: die komplette ISO wird unter boot/os/version/ ausgepackt. Basenamen hier erfassen; nach Extraktion listet die Seite dieser Version alle gefundenen Pfade. Ohne Vollextraktion werden nur übereinstimmende Dateien extrahiert (siehe Hinweis). Sonst greift automatische Boot-Typ-Erkennung.",
        "es": "Si está marcado: la ISO completa se descomprime en boot/os/version/. Indique los nombres base a seguir; tras la extracción, la página de esta versión lista todas las rutas. Sin extracción completa, solo se extraen coincidencias (véase la ayuda). Si no, se aplica detección automática del tipo de arranque.",
        "it": "Se attivo: l'ISO completa viene estratta in boot/os/version/. Elencare i nomi base da tracciare; dopo l'estrazione la pagina di questa versione elenca tutti i percorsi trovati. Senza estrazione completa, solo i file corrispondenti (vedi suggerimento). Altrimenti si applica il rilevamento automatico del tipo di boot.",
        "pt": "Se marcado: a ISO completa é descompactada em boot/os/version/. Indique os nomes base a seguir; após a extração, a página desta versão lista todos os caminhos. Sem extração completa, só coincidências (ver ajuda). Caso contrário, deteção automática do tipo de arranque.",
    },
    "sett.ot_manual_ipxe_warn": {
        "de": "Für jeden hier manuell angelegten OS-Typ erstellen Sie ein eigenes iPXE-Skript oder -Menü für diese ISO (generierte Menüs bleiben generisch).",
        "es": "Para cada tipo de SO definido aquí debe crear su propio script o menú iPXE para esta ISO (los menús generados siguen siendo genéricos).",
        "it": "Per ogni tipo OS definito qui dovete creare uno script o menu iPXE dedicato per questa ISO (i menu generati restano generici).",
        "pt": "Para cada tipo de SO definido aqui deve criar o seu script ou menu iPXE para esta ISO (os menus gerados permanecem genéricos).",
    },
    "sett.ot_section_patterns": {
        "de": "Zu suchende / extrahierende Dateien (selektiv oder Prüfliste nach Vollextraktion)",
        "es": "Archivos a buscar / extraer (extracción selectiva o auditoría tras extracción completa)",
        "it": "File da cercare / estrarre (estrazione selettiva o audit dopo estrazione completa)",
        "pt": "Ficheiros a procurar / extrair (extração seletiva ou auditoria após extração completa)",
    },
    "sett.ot_patterns_hint": {
        "de": "Einen einfachen Dateinamen eintragen (z. B. vmlinuz, initrd). Mehrere Treffer: bei Vollextraktion werden Pfade aufgelistet; bei selektiver Extraktion wird eine Datei ins Versions-Stammverzeichnis kopiert, mehrere Treffer behalten relative Unterpfade. Alte fnmatch-Zeilen mit * ? oder [ ] bleiben unterstützt (aus ISO oder Verzeichnisbaum).",
        "es": "Indique un nombre de archivo simple (p. ej. vmlinuz, initrd). Varias coincidencias: extracción completa = listar rutas; selectiva = un archivo en la raíz de versión, varias conservan subrutas. Patrones fnmatch legacy con * ? o [ ] siguen admitidos.",
        "it": "Inserire un nome file semplice (es. vmlinuz, initrd). Più corrispondenze: estrazione completa = elenca percorsi; selettiva = un file nella root versione, più corrispondenze mantengono sottopercorsi. Pattern fnmatch legacy con * ? o [ ] ancora supportati.",
        "pt": "Indique um nome de ficheiro simples (ex. vmlinuz, initrd). Várias correspondências: extração completa = listar caminhos; seletiva = um ficheiro na raiz da versão, várias mantêm subcaminhos. Padrões fnmatch legacy com * ? ou [ ] ainda suportados.",
    },
    "sett.ot_col_pattern": {
        "de": "Dateiname oder Legacy-Muster",
        "es": "Nombre de archivo o patrón legacy",
        "it": "Nome file o pattern legacy",
        "pt": "Nome de ficheiro ou padrão legacy",
    },
    "sett.ot_col_max": {
        "de": "Max.",
        "es": "Máx.",
        "it": "Max",
        "pt": "Máx.",
    },
    "sett.ot_rm_row": {
        "de": "Zeile entfernen",
        "es": "Quitar fila",
        "it": "Rimuovi riga",
        "pt": "Remover linha",
    },
    "sett.ot_add_pattern": {
        "de": "Zeile hinzufügen",
        "es": "Añadir fila",
        "it": "Aggiungi riga",
        "pt": "Adicionar linha",
    },
    "sett.ot_edit": {
        "de": "Bearbeiten",
        "es": "Editar",
        "it": "Modifica",
        "pt": "Editar",
    },
    "sett.os_builtin_locked": {
        "de": "(integriert)",
        "es": "(integrado)",
        "it": "(integrato)",
        "pt": "(integrado)",
    },
    "sett.msg_builtin_no_delete": {
        "de": "Integrierte OS-Typen können nicht gelöscht werden.",
        "es": "Los tipos integrados no se pueden eliminar.",
        "it": "I tipi OS integrati non possono essere eliminati.",
        "pt": "Os tipos integrados não podem ser eliminados.",
    },
    "sett.msg_builtin_no_edit": {
        "de": "Integrierte OS-Typen können hier nicht bearbeitet werden.",
        "es": "Los tipos integrados no se pueden editar aquí.",
        "it": "I tipi OS integrati non possono essere modificati qui.",
        "pt": "Os tipos integrados não podem ser editados aqui.",
    },
    "sett.ot_err_slug": {
        "de": "Ungültiger Slug.",
        "es": "Slug no válido.",
        "it": "Slug non valido.",
        "pt": "Slug inválido.",
    },
    "sett.ot_err_label": {
        "de": "Bezeichnung ist erforderlich.",
        "es": "La etiqueta es obligatoria.",
        "it": "L'etichetta è obbligatoria.",
        "pt": "A etiqueta é obrigatória.",
    },
    "sett.ot_err_boot_type": {
        "de": "Ungültiger Boot-Typ.",
        "es": "Tipo de arranque no válido.",
        "it": "Tipo di boot non valido.",
        "pt": "Tipo de arranque inválido.",
    },
    "sett.ot_err_duplicate": {
        "de": "Dieser Slug ist bereits vergeben.",
        "es": "Este slug ya está en uso.",
        "it": "Questo slug è già in uso.",
        "pt": "Este slug já está em uso.",
    },
    "sett.ot_err_patterns": {
        "de": "Vollextraktion aktivieren oder mindestens einen Dateinamen (oder Legacy-Muster) hinzufügen.",
        "es": "Active la extracción completa o añada al menos un nombre de archivo (o patrón legacy).",
        "it": "Abilitare l'estrazione completa o aggiungere almeno un nome file (o pattern legacy).",
        "pt": "Ative a extração completa ou adicione pelo menos um nome de ficheiro (ou padrão legacy).",
    },
    "sett.ot_autoconfig_label": {
        "de": "Typ der automatischen Konfiguration",
        "es": "Tipo de configuración automática",
        "it": "Tipo di configurazione automatica",
        "pt": "Tipo de configuração automática",
    },
    "sett.ot_autoconfig_help": {
        "de": "Bei Aktivierung ist der Typ beim Anlegen einer Auto-Konfiguration für eine ISO dieser OS-Gruppe festgelegt (wie Debian → preseed). Leer lassen, um den Typ pro Konfiguration zu wählen.",
        "es": "Si se define, al crear una config automática para una ISO de este grupo el tipo queda fijado (como Debian → preseed). Déjelo vacío para elegir el tipo en cada config.",
        "it": "Se impostato, creando una config automatica per un'ISO di questo gruppo il tipo è bloccato (come Debian → preseed). Lasciare vuoto per scegliere il tipo a ogni config.",
        "pt": "Se definido, ao criar uma config automática para uma ISO deste grupo o tipo fica fixo (como Debian → preseed). Deixe vazio para escolher o tipo em cada config.",
    },
    "sett.ot_autoconfig_none": {
        "de": "(ohne Vorgabe — beim Anlegen jeder Konfiguration wählen)",
        "es": "(sin restricción — elegir al crear cada config)",
        "it": "(nessun vincolo — scegliere alla creazione di ogni config)",
        "pt": "(sem restrição — escolher ao criar cada config)",
    },
    "sett.ot_autoconfig_new_opt": {
        "de": "Neuer Typ (Kennung eingeben)…",
        "es": "Nuevo tipo (introduzca un identificador)…",
        "it": "Nuovo tipo (inserire un identificatore)…",
        "pt": "Novo tipo (introduza um identificador)…",
    },
    "sett.ot_autoconfig_new_name": {
        "de": "Kennung des neuen Typs",
        "es": "Identificador del nuevo tipo",
        "it": "Identificatore del nuovo tipo",
        "pt": "Identificador do novo tipo",
    },
    "sett.ot_autoconfig_new_ph": {
        "de": "z. B. nixos-config",
        "es": "p. ej. nixos-config",
        "it": "es. nixos-config",
        "pt": "ex. nixos-config",
    },
    "sett.ot_err_autoconfig": {
        "de": "Ungültiger Typ oder abgewiesene Kennung (nur Kleinbuchstaben, Ziffern, Bindestrich).",
        "es": "Tipo no válido o identificador rechazado (solo minúsculas, dígitos y guion).",
        "it": "Tipo non valido o identificatore rifiutato (solo minuscole, cifre e trattino).",
        "pt": "Tipo inválido ou identificador rejeitado (apenas minúsculas, dígitos e hífen).",
    },
    "iso.upload.extract_plan_intro": {
        "de": "Mit ISO folgt die Extraktion den „ISO-Extraktions“-Einstellungen dieses OS-Typs (unter Einstellungen).",
        "es": "Con una ISO, la extracción sigue los ajustes de «Extracción de ISO» de este tipo (en Ajustes).",
        "it": "Con un'ISO, l'estrazione segue le impostazioni «Estrazione ISO» di questo tipo (in Impostazioni).",
        "pt": "Com uma ISO, a extração segue as definições «Extração de ISO» deste tipo (em Definições).",
    },
    "iso.upload.extract_plan_badges": {
        "de": "In der ISO gesuchte Dateinamen:",
        "es": "Nombres buscados en la ISO:",
        "it": "Nomi cercati nell'ISO:",
        "pt": "Nomes procurados na ISO:",
    },
    "iso.upload.extract_plan_auto": {
        "de": "Noch keine Einträge für diesen OS-Typ: automatische Erkennung; ggf. Namen unter Einstellungen ergänzen.",
        "es": "Aún no hay entradas en este tipo: detección automática; añada nombres en Ajustes si hace falta.",
        "it": "Nessuna voce su questo tipo OS: rilevamento automatico; aggiungere nomi in Impostazioni se necessario.",
        "pt": "Ainda sem entradas neste tipo: deteção automática; adicione nomes em Definições se necessário.",
    },
    "iso.upload.extract_plan_full_hint": {
        "de": "Vollextraktion ist aktiv: Es wird alles ausgepackt; anschließend werden diese Namen im Baum gesucht.",
        "es": "Extracción completa activada: se descomprime todo y luego se localizan estos nombres en el árbol.",
        "it": "Estrazione completa attiva: tutto viene estratto; poi questi nomi sono individuati nell'albero.",
        "pt": "Extração completa ativa: tudo é descompactado; depois estes nomes são localizados na árvore.",
    },
    "iso.detail.info_card": {
        "de": "Informationen",
        "es": "Información",
        "it": "Informazioni",
        "pt": "Informação",
    },
    "iso.detail.added_on": {
        "de": "Hinzugefügt am",
        "es": "Añadido el",
        "it": "Aggiunto il",
        "pt": "Adicionado em",
    },
    "iso.detail.notes_label": {
        "de": "Notizen",
        "es": "Notas",
        "it": "Note",
        "pt": "Notas",
    },
    "iso.detail.boot_files_title": {
        "de": "Boot-Dateien",
        "es": "Archivos de arranque",
        "it": "File di avvio",
        "pt": "Ficheiros de arranque",
    },
    "iso.detail.extract_from_iso": {
        "de": "Aus ISO extrahieren",
        "es": "Extraer desde ISO",
        "it": "Estrai dall'ISO",
        "pt": "Extrair da ISO",
    },
    "iso.detail.replace_boot_wim": {
        "de": "boot.wim ersetzen",
        "es": "Reemplazar boot.wim",
        "it": "Sostituisci boot.wim",
        "pt": "Substituir boot.wim",
    },
    "iso.detail.no_boot_files": {
        "de": "Noch keine Boot-Dateien extrahiert.",
        "es": "Aún no hay archivos de arranque extraídos.",
        "it": "Nessun file di avvio estratto.",
        "pt": "Ainda sem ficheiros de arranque extraídos.",
    },
    "iso.detail.upload_manual": {
        "de": "Manuell hochladen",
        "es": "Subir manualmente",
        "it": "Carica manualmente",
        "pt": "Enviar manualmente",
    },
    "iso.detail.or_run_extract": {
        "de": "oder Extraktion starten.",
        "es": "o ejecutar la extracción.",
        "it": "oppure avviare l'estrazione.",
        "pt": "ou executar a extração.",
    },
    "iso.detail.replace_wim_hint": {
        "de": "Ersetzt nur boot.wim — alle anderen Windows-Dateien bleiben unverändert. Die vorherige Datei wird als .wim.bak gesichert.",
        "es": "Solo reemplaza boot.wim — el resto de archivos Windows no cambia. El archivo anterior se guarda como .wim.bak.",
        "it": "Sostituisce solo boot.wim — gli altri file Windows restano intatti. Il file precedente è salvato come .wim.bak.",
        "pt": "Substitui apenas boot.wim — os restantes ficheiros Windows mantêm-se. O ficheiro anterior é guardado como .wim.bak.",
    },
    "iso.detail.upload_replace_wim": {
        "de": "Hochladen und ersetzen",
        "es": "Subir y reemplazar",
        "it": "Carica e sostituisci",
        "pt": "Enviar e substituir",
    },
    "iso.detail.winpe_th_folder": {
        "de": "Ordner",
        "es": "Carpeta",
        "it": "Cartella",
        "pt": "Pasta",
    },
    "iso.detail.winpe_th_label": {
        "de": "Bezeichnung",
        "es": "Etiqueta",
        "it": "Etichetta",
        "pt": "Etiqueta",
    },
    "iso.detail.winpe_th_index": {
        "de": "Index",
        "es": "Índice",
        "it": "Indice",
        "pt": "Índice",
    },
    "iso.detail.no_autoconfig_version": {
        "de": "Keine automatische Konfiguration für diese Version.",
        "es": "No hay configuración automática para esta versión.",
        "it": "Nessuna configurazione automatica per questa versione.",
        "pt": "Sem configuração automática para esta versão.",
    },
    "iso.detail.delete_this_version": {
        "de": "Diese Version löschen",
        "es": "Eliminar esta versión",
        "it": "Elimina questa versione",
        "pt": "Eliminar esta versão",
    },
    "iso.detail.cfg_th_type": {
        "de": "Typ",
        "es": "Tipo",
        "it": "Tipo",
        "pt": "Tipo",
    },
    "iso.detail.cfg_th_label": {
        "de": "Bezeichnung",
        "es": "Etiqueta",
        "it": "Etichetta",
        "pt": "Etiqueta",
    },
    "iso.detail.cfg_th_file": {
        "de": "Datei",
        "es": "Archivo",
        "it": "File",
        "pt": "Ficheiro",
    },
    "fw.https_banner_active": {
        "de": "HTTPS aktiv — SERVER_BASE_URL nutzt https://.",
        "es": "HTTPS activo — SERVER_BASE_URL usa https://.",
        "it": "HTTPS attivo — SERVER_BASE_URL usa https://.",
        "pt": "HTTPS ativo — SERVER_BASE_URL usa https://.",
    },
    "fw.https_banner_rebuild": {
        "de": "Firmware neu erstellen, um CERT/TRUST={ca_path} einzubetten und DOWNLOAD_PROTO_HTTPS in iPXE zu aktivieren.",
        "es": "Recompile el firmware para integrar CERT/TRUST={ca_path} y activar DOWNLOAD_PROTO_HTTPS en iPXE.",
        "it": "Ricompilare il firmware per incorporare CERT/TRUST={ca_path} e abilitare DOWNLOAD_PROTO_HTTPS in iPXE.",
        "pt": "Recompile o firmware para incorporar CERT/TRUST={ca_path} e ativar DOWNLOAD_PROTO_HTTPS no iPXE.",
    },
    "fw.https_banner_ca_missing": {
        "de": "CA-Zertifikat fehlt: sudo bash /srv/ipxe/app/deploy/enable-https.sh",
        "es": "Falta el certificado CA: sudo bash /srv/ipxe/app/deploy/enable-https.sh",
        "it": "Certificato CA assente: sudo bash /srv/ipxe/app/deploy/enable-https.sh",
        "pt": "Certificado CA em falta: sudo bash /srv/ipxe/app/deploy/enable-https.sh",
    },
    "fw.https_banner_http_url": {
        "de": "CA vorhanden ({ca_path}), aber SERVER_BASE_URL ist noch HTTP.",
        "es": "CA presente ({ca_path}) pero SERVER_BASE_URL sigue en HTTP.",
        "it": "CA presente ({ca_path}) ma SERVER_BASE_URL è ancora HTTP.",
        "pt": "CA presente ({ca_path}) mas SERVER_BASE_URL ainda está em HTTP.",
    },
    # Corrections DE (anciennes entrées en français)
    "iso.delete_confirm": {
        "de": "Diese ISO-Version und alle zugehörigen Dateien auf dem Server löschen? Dies kann nicht rückgängig gemacht werden.",
    },
    "iso.delete_version_confirm": {
        "de": "{os} {version} und alle zugehörigen Dateien löschen? Dies kann nicht rückgängig gemacht werden.",
    },
    "iso.extract_error_no_detail": {
        "de": "Kein Detail gespeichert — Celery-Logs auf dem Server prüfen (journalctl -u ipxe-celery).",
    },
    # IT / PT — administration & supervision (souvent encore en anglais)
    "super.restart_hint": {
        "it": "Riavvio tramite sudo e /usr/local/sbin/ipxe-service-ctl — installare i permessi: sudo bash deploy/install-service-sudo.sh",
        "pt": "Reinício via sudo e /usr/local/sbin/ipxe-service-ctl — instale permissões: sudo bash deploy/install-service-sudo.sh",
    },
    "super.sync_db_confirm": {
        "it": "Applicare le migrazioni SQLite (tabelle + colonne) e il bootstrap utenti?",
        "pt": "Aplicar migrações SQLite (tabelas + colunas) e bootstrap de utilizadores?",
    },
    "super.sync_db_ok": {
        "it": "Database sincronizzato ({users} account, {os_types} tipo/i OS).",
        "pt": "Base de dados sincronizada ({users} conta(s), {os_types} tipo(s) de SO).",
    },
    "super.sync_db_fail": {
        "it": "Sincronizzazione fallita: {detail}",
        "pt": "Falha na sincronização: {detail}",
    },
    "super.restart_partial": {
        "it": "Riavvio parziale o permesso negato.",
        "pt": "Reinício parcial ou permissão negada.",
    },
    "super.sudo_ok": {
        "it": "sudo systemctl consentito per l'utente del servizio.",
        "pt": "sudo systemctl permitido para o utilizador do serviço.",
    },
    "super.sudo_no": {
        "it": "Riavvio: aggiungere permessi sudo in deploy/setup.sh e reinstallare sudoers.",
        "pt": "Reinício: adicione permissões sudo em deploy/setup.sh e reinstale sudoers.",
    },
    "super.host": {
        "de": "Host",
        "es": "Host",
        "it": "Host",
        "pt": "Host",
    },
    "super.app": {
        "de": "Anwendung",
        "es": "Aplicación",
        "it": "Applicazione",
        "pt": "Aplicação",
    },
    "super.checks": {
        "de": "Komponenten",
        "es": "Componentes",
        "it": "Componenti",
        "pt": "Componentes",
    },
    "super.last_update": {
        "it": "Ultimo aggiornamento",
        "pt": "Última atualização",
    },
    "super.no_verification_yet": {
        "it": "Nessun controllo eseguito da questa pagina.",
        "pt": "Ainda sem verificação iniciada nesta página.",
    },
    "super.log_title": {
        "it": "Registro dell'ultimo audit",
        "pt": "Registo da última auditoria",
    },
    "super.running": {
        "it": "In esecuzione…",
        "pt": "Em execução…",
    },
    "admin.add_user": {
        "es": "Nueva cuenta",
        "it": "Nuovo account",
        "pt": "Nova conta",
    },
    "admin.user_list": {
        "es": "Cuentas existentes",
        "it": "Account esistenti",
        "pt": "Contas existentes",
    },
    "admin.username_hint": {
        "es": "Minúsculas, dígitos y guiones (3–32 caracteres).",
        "it": "Minuscole, cifre e trattini (3–32 caratteri).",
        "pt": "Minúsculas, dígitos e hífens (3–32 caracteres).",
    },
    "admin.delete_confirm": {
        "es": "¿Eliminar esta cuenta?",
        "it": "Eliminare questo account?",
        "pt": "Eliminar esta conta?",
    },
    "admin.no_users": {
        "es": "Sin cuentas.",
        "it": "Nessun account.",
        "pt": "Sem contas.",
    },
    "admin.user_created": {
        "es": "Cuenta creada.",
        "it": "Account creato.",
        "pt": "Conta criada.",
    },
    "admin.user_deleted": {
        "es": "Cuenta eliminada.",
        "it": "Account eliminato.",
        "pt": "Conta eliminada.",
    },
    "admin.user_exists": {
        "es": "Nombre de usuario ya en uso.",
        "it": "Nome utente già in uso.",
        "pt": "Nome de utilizador já em uso.",
    },
    "admin.user_invalid_username": {
        "es": "Nombre de usuario no válido.",
        "it": "Nome utente non valido.",
        "pt": "Nome de utilizador inválido.",
    },
    "admin.user_password_short": {
        "es": "Contraseña demasiado corta (mínimo 6 caracteres).",
        "it": "Password troppo corta (minimo 6 caratteri).",
        "pt": "Palavra-passe demasiado curta (mínimo 6 caracteres).",
    },
    "admin.user_last_admin": {
        "es": "No se puede eliminar el último administrador.",
        "it": "Impossibile eliminare l'ultimo amministratore.",
        "pt": "Não é possível eliminar o último administrador.",
    },
    "admin.user_has_isos": {
        "es": "Esta cuenta aún posee {n} versión(es) ISO — elimínelas primero.",
        "it": "Questo account possiede ancora {n} versione/i ISO — eliminarle prima.",
        "pt": "Esta conta ainda possui {n} versão(ões) ISO — elimine-as primeiro.",
    },
    "admin.restart_confirm": {
        "es": "¿Reiniciar ipxe-manager e ipxe-celery?",
        "it": "Riavviare ipxe-manager e ipxe-celery?",
        "pt": "Reiniciar ipxe-manager e ipxe-celery?",
    },
    "admin.services_restarted": {
        "es": "Servicios reiniciados.",
        "it": "Servizi riavviati.",
        "pt": "Serviços reiniciados.",
    },
    "admin.service_ok": {
        "es": "{unit}: OK",
        "it": "{unit}: OK",
        "pt": "{unit}: OK",
    },
    "admin.service_fail": {
        "es": "{unit}: error ({detail})",
        "it": "{unit}: errore ({detail})",
        "pt": "{unit}: falha ({detail})",
    },
    "admin.service_no_systemctl": {
        "es": "{unit}: systemctl no disponible (¿entorno de desarrollo?)",
        "it": "{unit}: systemctl non disponibile (ambiente di sviluppo?)",
        "pt": "{unit}: systemctl indisponível (ambiente de desenvolvimento?)",
    },
    "common.confirm_title": {
        "de": "Bestätigung",
        "es": "Confirmación",
        "it": "Conferma",
        "pt": "Confirmação",
    },
    "common.confirm_btn": {
        "de": "Bestätigen",
        "es": "Confirmar",
        "it": "Conferma",
        "pt": "Confirmar",
    },
    "nav.group_admin": {
        "de": "Verwaltung",
        "es": "Administración",
        "it": "Amministrazione",
        "pt": "Administração",
    },
    "dash.quick_config_sub": {
        "de": "Preseed / Kickstart / Unattend",
        "es": "Preseed / Kickstart / Unattend",
        "it": "Preseed / Kickstart / Unattend",
        "pt": "Preseed / Kickstart / Unattend",
    },
    "super.no_verification_yet": {
        "es": "Aún no se ha ejecutado ninguna comprobación desde esta página.",
    },
    "super.log_title": {
        "es": "Registro de la última auditoría",
    },
    "admin.services_restarted": {
        "de": "Dienste neu gestartet.",
    },
    "iso.delete_confirm": {
        "es": "¿Eliminar esta versión ISO y todos los archivos asociados en el servidor? Esta acción no se puede deshacer.",
        "it": "Eliminare questa versione ISO e tutti i file associati sul server? Questa azione è irreversibile.",
        "pt": "Eliminar esta versão ISO e todos os ficheiros associados no servidor? Esta ação é irreversível.",
    },
    "iso.delete_version_confirm": {
        "es": "¿Eliminar {os} {version} y todos los archivos asociados? Esta acción no se puede deshacer.",
        "it": "Eliminare {os} {version} e tutti i file associati? Questa azione è irreversibile.",
        "pt": "Eliminar {os} {version} e todos os ficheiros associados? Esta ação é irreversível.",
    },
    "iso.extract_error_title": {
        "de": "Letzte Extraktion fehlgeschlagen",
        "es": "Última extracción fallida",
        "it": "Ultima estrazione non riuscita",
        "pt": "Última extração falhada",
    },
    "iso.extract_error_no_detail": {
        "es": "Sin detalle registrado — consulte los logs Celery en el servidor (journalctl -u ipxe-celery).",
        "it": "Nessun dettaglio registrato — controllare i log Celery sul server (journalctl -u ipxe-celery).",
        "pt": "Sem detalhe registado — verifique os logs Celery no servidor (journalctl -u ipxe-celery).",
    },
    "dash.status_error": {
        "es": "Error",
    },
    "common.error": {
        "es": "Error",
    },
    "iso.status_error": {
        "es": "Error",
    },
    "auth.password": {
        "it": "Password",
        "pt": "Palavra-passe",
    },
    "iso.add_version": {
        "it": "Aggiungi versione",
        "pt": "Adicionar versão",
    },
    "dash.col_file": {
        "it": "File",
    },
    "iso.detail.cfg_th_file": {
        "it": "File",
    },
}


def main() -> int:
    OUT.write_text(json.dumps(GAPS, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(GAPS)} keys to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
