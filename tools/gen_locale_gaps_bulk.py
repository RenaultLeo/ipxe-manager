#!/usr/bin/env python3
"""Génère tools/locale_gaps_bulk.py depuis un tableau compact key|de|es|it|pt."""
from __future__ import annotations

from pathlib import Path

# key | de | es | it | pt  (| dans le texte : éviter)
ROWS = r"""
admin.add_user|Neues Konto|Nueva cuenta|Nuovo account|Nova conta
admin.user_list|Vorhandene Konten|Cuentas existentes|Account esistenti|Contas existentes
admin.username_hint|Kleinbuchstaben, Ziffern, Bindestriche (3–32 Zeichen).|Minúsculas, dígitos y guiones (3–32 caracteres).|Minuscole, cifre e trattini (3–32 caratteri).|Minúsculas, dígitos e hífens (3–32 caracteres).
admin.role_user|Benutzer|Usuario|Utente|Utilizador
admin.role_admin|Administrator|Administrador|Amministratore|Administrador
admin.create|Anlegen|Crear|Crea|Criar
admin.delete_confirm|Dieses Konto löschen?|¿Eliminar esta cuenta?|Eliminare questo account?|Eliminar esta conta?
admin.no_users|Keine Konten.|Sin cuentas.|Nessun account.|Sem contas.
admin.user_created|Konto angelegt.|Cuenta creada.|Account creato.|Conta criada.
admin.user_deleted|Konto gelöscht.|Cuenta eliminada.|Account eliminato.|Conta eliminada.
admin.user_exists|Benutzername bereits vergeben.|El nombre de usuario ya existe.|Nome utente già in uso.|Nome de utilizador já em uso.
admin.user_invalid_username|Ungültiger Benutzername.|Nombre de usuario no válido.|Nome utente non valido.|Nome de utilizador inválido.
admin.user_password_short|Passwort zu kurz (mindestens 6 Zeichen).|Contraseña demasiado corta (mínimo 6 caracteres).|Password troppo corta (minimo 6 caratteri).|Palavra-passe demasiado curta (mínimo 6 caracteres).
admin.user_last_admin|Der letzte Administrator kann nicht gelöscht werden.|No se puede eliminar el último administrador.|Impossibile eliminare l'ultimo amministratore.|Não é possível eliminar o último administrador.
admin.user_has_isos|Dieses Konto besitzt noch {n} ISO-Version(en) — zuerst löschen.|Esta cuenta aún posee {n} versión(es) ISO — elimínelas primero.|Questo account possiede ancora {n} versione/i ISO — eliminarle prima.|Esta conta ainda possui {n} versão(ões) ISO — elimine-as primeiro.
admin.restart_services|Dienste neu starten|Reiniciar servicios|Riavvia servizi|Reiniciar serviços
admin.restart_confirm|ipxe-manager und ipxe-celery neu starten?|¿Reiniciar ipxe-manager e ipxe-celery?|Riavviare ipxe-manager e ipxe-celery?|Reiniciar ipxe-manager e ipxe-celery?
admin.service_ok|{unit}: OK|{unit}: OK|{unit}: OK|{unit}: OK
admin.service_fail|{unit}: fehlgeschlagen ({detail})|{unit}: error ({detail})|{unit}: errore ({detail})|{unit}: falha ({detail})
admin.service_no_systemctl|{unit}: systemctl nicht verfügbar (Dev-Umgebung?)|{unit}: systemctl no disponible (¿entorno de desarrollo?)|{unit}: systemctl non disponibile (ambiente di sviluppo?)|{unit}: systemctl indisponível (ambiente de desenvolvimento?)
fw.cancel_build_confirm|Laufende Erstellung abbrechen?\\n\\nQuellen bleiben auf der Festplatte; der nächste Build ist schneller.|¿Cancelar la compilación en curso?\\n\\nLas fuentes permanecen en disco; la próxima compilación será más rápida.|Annullare la compilazione in corso?\\n\\nLe sorgenti restano su disco; la prossima compilazione sarà più veloce.|Cancelar a compilação em curso?\\n\\nAs fontes permanecem no disco; a próxima compilação será mais rápida.
menu.confirm_delete_chain|Server „{name}“ löschen?\\nEr wird aus dem iPXE-Menü entfernt.|¿Eliminar el servidor «{name}»?\\nSe quitará del menú iPXE.|Eliminare il server «{name}»?\\nVerrà rimosso dal menu iPXE.|Eliminar o servidor «{name}»?\\nSerá removido do menu iPXE.
menu.info_after_nginx|Nach Änderung von Nginx: sudo systemctl reload nginx|Tras cambiar Nginx: sudo systemctl reload nginx|Dopo modifica Nginx: sudo systemctl reload nginx|Após alterar Nginx: sudo systemctl reload nginx
menu.load_failed|Menü konnte nicht geladen werden|No se pudo cargar el menú|Impossibile caricare il menu|Não foi possível carregar o menu
menu.load_timeout|Zeitüberschreitung beim Laden des Menüs|Tiempo de espera agotado al cargar el menú|Timeout caricamento menu|Tempo esgotado ao carregar o menu
menu.editor|Editor|Editor|Editor|Editor
menu.chain_col_name|Name|Nombre|Nome|Nome
iso.upload.custom_badge|Untermenü „Sonstiges“|Submenú «Otros»|Sottomenu «Altro»|Submenu «Outros»
iso.upload.custom_help|Wenn gesetzt, erscheint diese Version im OS-Untermenü „Sonstiges“ und verknüpft dieses Skript statt des Standard-Boots.|Si se indica, esta versión aparece en el submenú «Otros» del SO y encadena este script en lugar del arranque predeterminado.|Se indicato, questa versione compare nel sottomenu «Altro» del SO e collega questo script al posto dell'avvio predefinito.|Se indicado, esta versão aparece no submenu «Outros» do SO e encadeia este script em vez do arranque predefinido.
sett.subtitle|HTTP-URL, Sicherheit, Menüdarstellung und OS-Typen.|URL HTTP, seguridad, apariencia de menús y tipos de SO.|URL HTTP, sicurezza, aspetto menu e tipi OS.|URL HTTP, segurança, aparência dos menus e tipos de SO.
sett.section_server|Server|Servidor|Server|Servidor
sett.section_appearance|Darstellung|Apariencia|Aspetto|Aparência
sett.section_system|System|Sistema|Sistema|Sistema
sett.ipxe_debug_help|Globaler iPXE-Debug-Modus: aktiviert/deaktiviert Traces in .ipxe-Menüs und Firmware-Build.|Modo debug iPXE global: activa/desactiva trazas en menús .ipxe y compilación de firmware.|Modalità debug iPXE globale: abilita/disabilita tracce nei menu .ipxe e nella compilazione firmware.|Modo de depuração iPXE global: ativa/desativa rastos nos menus .ipxe e na compilação do firmware.
sett.ipxe_debug_state|iPXE-Debug: {state}|Debug iPXE: {state}|Debug iPXE: {state}|Depuração iPXE: {state}
sett.ipxe_debug_enable|Debug aktivieren|Activar depuración|Abilita debug|Ativar depuração
sett.ipxe_debug_disable|Debug deaktivieren|Desactivar depuración|Disabilita debug|Desativar depuração
sett.tls_renew_modal_body|Das Serverzertifikat gilt 2 Jahre und Nginx wird neu geladen. Anschließend müssen Sie die iPXE-Firmware neu erstellen (Seite Firmware).|El certificado del servidor será válido 2 años y Nginx se recargará. Luego deberá recompilar el firmware iPXE (página Firmware).|Il certificato server sarà valido 2 anni e Nginx verrà ricaricato. Dovrete poi ricompilare il firmware iPXE (pagina Firmware).|O certificado do servidor será válido por 2 anos e o Nginx será recarregado. Depois terá de recompilar o firmware iPXE (página Firmware).
sett.menu_logo_help|PNG oder JPEG, max. 3 MB. Ohne Upload wird der integrierte blaue Platzhalter („Customizable logo“ / „Settings · iPXE menu image“) verwendet. Nach dem Upload ersetzt Ihr Bild das Logo unten rechts im iPXE-Menü; Entfernen stellt den Standard wieder her. Menüs werden automatisch neu erzeugt.|PNG o JPEG, máx. 3 MB. Si no sube ninguno, se usa el marcador azul integrado («Customizable logo» / «Settings · iPXE menu image»). Tras la subida, su imagen reemplaza la esquina inferior derecha del menú iPXE; eliminar restaura el predeterminado. Los menús se regeneran automáticamente.|PNG o JPEG, max 3 MB. Senza caricamento si usa il segnaposto blu integrato («Customizable logo» / «Settings · iPXE menu image»). Dopo il caricamento l'immagine sostituisce il logo in basso a destra nel menu iPXE; rimuoverla ripristina il default. I menu si rigenerano automaticamente.|PNG ou JPEG, máx. 3 MB. Sem envio, usa-se o marcador azul integrado («Customizable logo» / «Settings · iPXE menu image»). Após o envio, a imagem substitui o canto inferior direito do menu iPXE; remover repõe o padrão. Os menus regeneram-se automaticamente.
boot.esxi_loader_mboot|Bootloader (mboot.efi)|Cargador de arranque (mboot.efi)|Bootloader (mboot.efi)|Carregador de arranque (mboot.efi)
boot.esxi_ipxe_boot_cfg_manual|ipxe-boot-manual.cfg (manuelle Installation)|ipxe-boot-manual.cfg (instalación manual)|ipxe-boot-manual.cfg (installazione manuale)|ipxe-boot-manual.cfg (instalação manual)
boot.esxi_modules_ipxe_hint|iPXE-Vorladereihenfolge (mboot.efi).|Orden de precarga iPXE (mboot.efi).|Ordine precaricamento iPXE (mboot.efi).|Ordem de pré-carregamento iPXE (mboot.efi).
iso.col_status|Status|Estado|Stato|Estado
iso.readonly_banner|Nur Lesen — Sie können nur von Ihnen hinzugefügte Versionen ändern.|Solo lectura — solo puede modificar versiones que haya añadido.|Sola lettura — potete modificare solo le versioni che avete aggiunto.|Somente leitura — só pode modificar versões que adicionou.
iso.readonly_no_boot|Keine Boot-Dateien (nur Lesen).|Sin archivos de arranque (solo lectura).|Nessun file di boot (sola lettura).|Sem ficheiros de arranque (somente leitura).
iso.detail.more|Mehr|Más|Altro|Mais
iso.detail.less|Weniger|Menos|Meno|Menos
iso.active_config_intro|Die aktive Konfiguration kopiert user-data und meta-data in die Wurzel von boot/ubuntu/…|La configuración activa copia user-data y meta-data a la raíz de boot/ubuntu/…|La configurazione attiva copia user-data e meta-data nella radice di boot/ubuntu/…|A configuração ativa copia user-data e meta-data para a raiz de boot/ubuntu/…
iso.active_config_select|Aktive Konfiguration|Configuración activa|Configurazione attiva|Configuração ativa
iso.active_config_apply|In Version veröffentlichen|Publicar en la versión|Pubblica nella versione|Publicar na versão
iso.active_config_clear|Aktive Konfiguration löschen|Borrar configuración activa|Cancella configurazione attiva|Limpar configuração ativa
iso.active_config_ok|Konfiguration unter boot/ veröffentlicht und als aktiv gesetzt.|Configuración publicada bajo boot/ y establecida como activa.|Configurazione pubblicata in boot/ e impostata come attiva.|Configuração publicada em boot/ e definida como ativa.
iso.active_config_need_extract|ISO zuerst extrahieren, um eine Konfiguration unter boot/ubuntu/… zu veröffentlichen.|Extraiga la ISO primero para publicar una configuración bajo boot/ubuntu/…|Estrarre prima l'ISO per pubblicare una configurazione sotto boot/ubuntu/…|Extraia a ISO primeiro para publicar uma configuração em boot/ubuntu/…
iso.purge_iso_toggle_short|ISO nach Extraktion löschen|Eliminar ISO tras extracción|Elimina ISO dopo estrazione|Eliminar ISO após extração
iso.extract_warning_title|Extraktion OK — Aktion erforderlich|Extracción OK — acción necesaria|Estrazione OK — azione necessaria|Extração OK — ação necessária
iso.extract_warning_hint|Boot-Dateien sind auf der Platte. iPXE-Menüs neu erzeugen (Seite Menüs) oder ausführen: sudo …|Los archivos de arranque están en disco. Regenerar menús iPXE (página Menús) o ejecutar: sudo …|I file di boot sono su disco. Rigenerare i menu iPXE (pagina Menu) o eseguire: sudo …|Os ficheiros de arranque estão no disco. Regenerar menus iPXE (página Menus) ou executar: sudo …
dash.col_status|Status|Estado|Stato|Estado
dash.quick_config_sub|Preseed / Kickstart / Unattend|Preseed / Kickstart / Unattend|Preseed / Kickstart / Unattend|Preseed / Kickstart / Unattend
super.run_quick|Schnellprüfung|Verificación rápida|Verifica rapida|Verificação rápida
super.run_full|Vollständiges Audit|Auditoría exhaustiva|Audit completo|Auditoria exaustiva
super.restart_services|Dienste neu starten|Reiniciar servicios|Riavvia servizi|Reiniciar serviços
super.restart_hint|Neustart über sudo und /usr/local/sbin/ipxe-service-ctl — Berechtigungen installieren: sudo bash deploy/install-service-sudo.sh|Reinicio vía sudo y /usr/local/sbin/ipxe-service-ctl — instale permisos: sudo bash deploy/install-service-sudo.sh|Riavvio tramite sudo e /usr/local/sbin/ipxe-service-ctl — installare permessi: sudo bash deploy/install-service-sudo.sh|Reinício via sudo e /usr/local/sbin/ipxe-service-ctl — instale permissões: sudo bash deploy/install-service-sudo.sh
super.sync_db|Datenbank synchronisieren|Sincronizar base de datos|Sincronizza database|Sincronizar base de dados
super.sync_db_confirm|SQLite-Migrationen (Tabellen + Spalten) und Benutzer-Bootstrap anwenden?|¿Aplicar migraciones SQLite (tablas + columnas) y bootstrap de usuarios?|Applicare migrazioni SQLite (tabelle + colonne) e bootstrap utenti?|Aplicar migrações SQLite (tabelas + colunas) e bootstrap de utilizadores?
super.sync_db_ok|Datenbank synchronisiert ({users} Konto/Konten, {os_types} OS-Typ(en)).|Base de datos sincronizada ({users} cuenta(s), {os_types} tipo(s) de SO).|Database sincronizzato ({users} account, {os_types} tipo/i OS).|Base de dados sincronizada ({users} conta(s), {os_types} tipo(s) de SO).
super.sync_db_fail|Synchronisierung fehlgeschlagen: {detail}|Sincronización fallida: {detail}|Sincronizzazione fallita: {detail}|Falha na sincronização: {detail}
super.restart_partial|Teilweiser Neustart oder Berechtigung verweigert.|Reinicio parcial o permiso denegado.|Riavvio parziale o permesso negato.|Reinício parcial ou permissão negada.
super.sudo_ok|sudo systemctl für den Dienstbenutzer erlaubt.|sudo systemctl permitido para el usuario del servicio.|sudo systemctl consentito per l'utente del servizio.|sudo systemctl permitido para o utilizador do serviço.
super.sudo_no|Neustart: sudo-Berechtigungen in deploy/setup.sh hinzufügen und sudoers neu installieren.|Reinicio: añadir permisos sudo en deploy/setup.sh y reinstalar sudoers.|Riavvio: aggiungere permessi sudo in deploy/setup.sh e reinstallare sudoers.|Reinício: adicionar permissões sudo em deploy/setup.sh e reinstalar sudoers.
super.last_update|Letzte Aktualisierung|Última actualización|Ultimo aggiornamento|Última atualização
super.no_verification_yet|Noch keine Prüfung von dieser Seite aus gestartet.|Aún no se ha iniciado verificación desde esta página.|Nessun controllo eseguito da questa pagina.|Ainda sem verificação iniciada nesta página.
super.log_title|Protokoll des letzten Audits|Registro de la última auditoría|Registro dell'ultimo audit|Registo da última auditoria
super.running|Läuft…|En ejecución…|In esecuzione…|Em execução…
super.restarting_title|Dienste werden neu gestartet…|Reiniciando servicios…|Riavvio servizi…|A reiniciar serviços…
super.restarting_wait|Bitte warten (bis zu 30 s)…|Espere (hasta 30 s)…|Attendere (fino a 30 s)…|Aguarde (até 30 s)…
super.chart_resources|Ressourcen|Recursos|Risorse|Recursos
super.chart_services|Dienste|Servicios|Servizi|Serviços
super.chart_disk|Festplatte|Disco|Disco|Disco
super.chart_ports|Ports|Puertos|Porte|Portas
super.open_ports|Offene Ports|Puertos abiertos|Porte aperti|Portas abertas
super.paths|Pfade|Rutas|Percorsi|Caminhos
super.col_name|Name|Nombre|Nome|Nome
super.col_path|Pfad|Ruta|Percorso|Caminho
super.active|Aktiv|Activo|Attivo|Ativo
super.verify_quick_ok|Schnellprüfung abgeschlossen ({n} Prüfungen).|Verificación rápida completada ({n} comprobaciones).|Verifica rapida completata ({n} controlli).|Verificação rápida concluída ({n} verificações).
super.verify_quick_fail|Schnellprüfung: {n} Fehler.|Verificación rápida: {n} error(es).|Verifica rapida: {n} errore/i.|Verificação rápida: {n} falha(s).
super.verify_full_ok|Vollständiges Audit in {sec} s abgeschlossen.|Auditoría exhaustiva completada en {sec} s.|Audit completo completato in {sec} s.|Auditoria exaustiva concluída em {sec} s.
super.verify_full_fail|Vollständiges Audit mit Fehlern ({sec} s) — siehe Protokoll.|Auditoría exhaustiva con errores ({sec} s) — ver el registro.|Audit completo con errori ({sec} s) — vedere il registro.|Auditoria exaustiva com erros ({sec} s) — ver o registo.
iso.delete_confirm|Diese ISO-Version und alle zugehörigen Dateien auf dem Server löschen? Dies kann nicht rückgängig gemacht werden.|¿Eliminar esta versión ISO y todos los archivos asociados en el servidor? No se puede deshacer.|Eliminare questa versione ISO e tutti i file associati sul server? Operazione irreversibile.|Eliminar esta versão ISO e todos os ficheiros associados no servidor? Não pode ser anulado.
iso.delete_version_confirm|{os} {version} und alle zugehörigen Dateien löschen? Dies kann nicht rückgängig gemacht werden.|¿Eliminar {os} {version} y todos los archivos asociados? No se puede deshacer.|Eliminare {os} {version} e tutti i file associati? Operazione irreversibile.|Eliminar {os} {version} e todos os ficheiros associados? Não pode ser anulado.
iso.extract_error_no_detail|Kein Detail gespeichert — Celery-Logs auf dem Server prüfen (journalctl -u ipxe-celery).|Sin detalle registrado — consulte los logs Celery en el servidor (journalctl -u ipxe-celery).|Nessun dettaglio registrato — controllare i log Celery sul server (journalctl -u ipxe-celery).|Sem detalhe registado — verifique os logs Celery no servidor (journalctl -u ipxe-celery).
iso.extract_failed|Extraktion konnte nicht gestartet werden (Celery-Worker nicht verfügbar oder Serverfehler).|No se pudo iniciar la extracción (worker Celery no disponible o error del servidor).|Impossibile avviare l'estrazione (worker Celery non disponibile o errore server).|Falha ao iniciar a extração (worker Celery indisponível ou erro do servidor).
iso.active_config_desktop_only|Veröffentlichen der aktiven Konfiguration gilt nur für Ubuntu-Desktop-Versionen.|Publicar la configuración activa solo aplica a versiones Ubuntu Desktop.|La pubblicazione della configurazione attiva vale solo per le versioni Ubuntu Desktop.|Publicar a configuração ativa aplica-se apenas a versões Ubuntu Desktop.
iso.active_config_not_ubuntu|Nur Ubuntu-Versionen.|Solo versiones Ubuntu.|Solo versioni Ubuntu.|Apenas versões Ubuntu.
iso.active_config_server_intro|Ubuntu Server: jede Cloud-init-Konfiguration nutzt conf-cloudInit-<slug>/; das Server-iPXE-Menü listet jeden Slug.|Ubuntu Server: cada configuración cloud-init usa conf-cloudInit-<slug>/; el menú iPXE Server lista cada slug.|Ubuntu Server: ogni configurazione cloud-init usa conf-cloudInit-<slug>/; il menu iPXE Server elenca ogni slug.|Ubuntu Server: cada configuração cloud-init usa conf-cloudInit-<slug>/; o menu iPXE Server lista cada slug.
iso.detail.ubuntu_variant|Ubuntu-Variante|Variante Ubuntu|Variante Ubuntu|Variante Ubuntu
iso.detail_alpine_repo_title|APK-Repository (Alpine Netboot)|Repositorio APK (Alpine netboot)|Repository APK (Alpine netboot)|Repositório APK (Alpine netboot)
iso.detail_alpine_repo_default_value|Öffentliches CDN (latest-stable/main)|CDN público (latest-stable/main)|CDN pubblico (latest-stable/main)|CDN público (latest-stable/main)
iso.detail_fedora_live_title|Live-Boot (squashfs)|Arranque Live (squashfs)|Avvio Live (squashfs)|Arranque Live (squashfs)
iso.detail_fedora_live_intro|Workstation Live: Root-Dateisystem aus LiveOS/squashfs.img. Aus: inst.stage2-Flow (DVD-Stil).|Workstation Live: sistema de archivos raíz desde LiveOS/squashfs.img. Desactivado: flujo inst.stage2 (estilo DVD).|Workstation Live: root da LiveOS/squashfs.img. Disattivo: flusso inst.stage2 (stile DVD).|Workstation Live: sistema de ficheiros raiz de LiveOS/squashfs.img. Desligado: fluxo inst.stage2 (estilo DVD).
iso.detail_iso_http_url|ISO-HTTP-URL (iPXE)|URL HTTP de ISO (iPXE)|URL HTTP ISO (iPXE)|URL HTTP da ISO (iPXE)
iso.esxi_efi_only_notice|ESXi-Hinweis: PXE-Boot im Legacy-BIOS wird ab ESXi 6.7 nicht mehr unterstützt. UEFI verwenden.|Nota ESXi: el arranque PXE en BIOS legacy ya no es compatible desde ESXi 6.7. Use UEFI.|Nota ESXi: il boot PXE in BIOS legacy non è più supportato da ESXi 6.7. Usare UEFI.|Nota ESXi: arranque PXE em BIOS legacy deixou de ser suportado a partir do ESXi 6.7. Use UEFI.
iso.esxi_need_extract|ESXi-ISO zuerst extrahieren, um eine Konfiguration zu aktivieren.|Extraiga la ISO ESXi primero para activar una configuración.|Estrarre prima l'ISO ESXi per attivare una configurazione.|Extraia a ISO ESXi primeiro para ativar uma configuração.
iso.esxi_active_config_bad_type|Nur ESXi-Kickstart-Konfigurationen können aktiviert werden.|Solo se pueden activar configuraciones Kickstart ESXi.|Solo le configurazioni Kickstart ESXi possono essere attivate.|Só configurações Kickstart ESXi podem ser ativadas.
iso.proxmox_active_config_apply|In proxmox-netboot-autoinstall.iso injizieren|Inyectar en proxmox-netboot-autoinstall.iso|Iniettare in proxmox-netboot-autoinstall.iso|Injetar em proxmox-netboot-autoinstall.iso
iso.proxmox_active_config_intro|Die aktive Konfiguration wird in proxmox-netboot-autoinstall.iso injiziert. proxmox-netboot.iso bleibt für manuelle Installation.|La configuración activa se inyecta en proxmox-netboot-autoinstall.iso. proxmox-netboot.iso permanece para instalación manual.|La configurazione attiva viene iniettata in proxmox-netboot-autoinstall.iso. proxmox-netboot.iso resta per installazione manuale.|A configuração ativa é injetada em proxmox-netboot-autoinstall.iso. proxmox-netboot.iso permanece para instalação manual.
iso.proxmox_netboot_manual_hint|Manuelle ISO (ohne answer.toml) — iPXE-Eintrag « manuelle Installation »|ISO manual (sin answer.toml) — entrada iPXE « instalación manual »|ISO manuale (senza answer.toml) — voce iPXE « installazione manuale »|ISO manual (sem answer.toml) — entrada iPXE « instalação manual »
iso.proxmox_netboot_autoinstall_hint|Auto-Install-ISO (answer.toml) — aktiver Konfigurations-iPXE-Eintrag|ISO de autoinstalación (answer.toml) — entrada iPXE de configuración activa|ISO autoinstall (answer.toml) — voce iPXE configurazione attiva|ISO de autoinstalação (answer.toml) — entrada iPXE de configuração ativa
iso.proxmox_autoinstall_not_built|Autoinstall-ISO noch nicht erstellt — auf Injizieren klicken.|ISO de autoinstalación aún no construida — pulse Inyectar.|ISO autoinstall non ancora creata — fare clic su Inietta.|ISO de autoinstalação ainda não criada — clique em Injetar.
iso.proxmox_inject_started|proxmox-netboot-autoinstall.iso wird erstellt — bitte warten…|Construyendo proxmox-netboot-autoinstall.iso — espere…|Creazione proxmox-netboot-autoinstall.iso — attendere…|A criar proxmox-netboot-autoinstall.iso — aguarde…
iso.proxmox_inject_ok|proxmox-netboot-autoinstall.iso ist bereit. proxmox-netboot.iso bleibt für manuelle Installation.|proxmox-netboot-autoinstall.iso está listo. proxmox-netboot.iso permanece para instalación manual.|proxmox-netboot-autoinstall.iso è pronto. proxmox-netboot.iso resta per installazione manuale.|proxmox-netboot-autoinstall.iso está pronto. proxmox-netboot.iso permanece para instalação manual.
iso.proxmox_inject_need_extract|Proxmox-ISO zuerst extrahieren (proxmox-netboot.iso erforderlich).|Extraiga la ISO Proxmox primero (se requiere proxmox-netboot.iso).|Estrarre prima l'ISO Proxmox (richiesto proxmox-netboot.iso).|Extraia a ISO Proxmox primeiro (necessário proxmox-netboot.iso).
iso.proxmox_active_config_bad_type|Nur answer.toml-Konfigurationen (proxmox-answer) können aktiviert werden.|Solo configuraciones answer.toml (proxmox-answer) pueden activarse.|Solo configurazioni answer.toml (proxmox-answer) possono essere attivate.|Só configurações answer.toml (proxmox-answer) podem ser ativadas.
iso.proxmox_inject_error|Injektion in proxmox-netboot-autoinstall.iso fehlgeschlagen (proxmox-auto-install-assistant erforderlich).|Error al inyectar en proxmox-netboot-autoinstall.iso (se requiere proxmox-auto-install-assistant).|Iniezione in proxmox-netboot-autoinstall.iso fallita (richiesto proxmox-auto-install-assistant).|Falha ao injetar em proxmox-netboot-autoinstall.iso (necessário proxmox-auto-install-assistant).
iso.ubuntu_nfs_toggle_label|NFS-Boot im Menü (casper)|Arranque NFS en el menú (casper)|Avvio NFS nel menu (casper)|Arranque NFS no menu (casper)
iso.ubuntu_nfs_toggle_short|NFS-Boot|Arranque NFS|Avvio NFS|Arranque NFS
iso.ubuntu_nfs_toggle_help|Aus: HTTP-Autoinstall (root=/dev/ram0, ISO url=). Ein: netboot=nfs im generierten iPXE-Menü.|Desactivado: autoinstalación HTTP (root=/dev/ram0, ISO url=). Activado: netboot=nfs en el menú iPXE generado.|Disattivo: autoinstall HTTP (root=/dev/ram0, ISO url=). Attivo: netboot=nfs nel menu iPXE generato.|Desligado: autoinstall HTTP (root=/dev/ram0, ISO url=). Ligado: netboot=nfs no menu iPXE gerado.
iso.ubuntu_nfs_toggle_unavailable|Zuerst vmlinuz/initrd extrahieren, um NFS-Boot zu aktivieren.|Extraiga vmlinuz/initrd primero para habilitar arranque NFS.|Estrarre prima vmlinuz/initrd per abilitare avvio NFS.|Extraia vmlinuz/initrd primeiro para ativar arranque NFS.
iso.ubuntu_nfs_not_ubuntu|Diese Option gilt nur für Ubuntu-Versionen.|Esta opción es solo para versiones Ubuntu.|Questa opzione è solo per versioni Ubuntu.|Esta opção é apenas para versões Ubuntu.
iso.ubuntu_nfs_pref_save_error|NFS-Boot-Einstellung konnte nicht gespeichert werden.|No se pudo guardar la preferencia de arranque NFS.|Impossibile salvare la preferenza avvio NFS.|Não foi possível guardar a preferência de arranque NFS.
iso.upload.ubuntu_variant_label|Ubuntu-Installationstyp|Tipo de instalación Ubuntu|Tipo installazione Ubuntu|Tipo de instalação Ubuntu
iso.upload.ubuntu_variant_help|Desktop: Menü mit aktiver Konfiguration im Boot-Ordner. Server: ein iPXE-Eintrag pro Cloud-init-Slug.|Desktop: menú con configuración activa en la raíz boot. Server: una entrada iPXE por slug cloud-init.|Desktop: menu con configurazione attiva nella root boot. Server: una voce iPXE per slug cloud-init.|Desktop: menu com configuração ativa na raiz boot. Server: uma entrada iPXE por slug cloud-init.
iso.upload.fedora_live_label|Fedora-Live-ISO (Workstation)|ISO Fedora Live (Workstation)|ISO Fedora Live (Workstation)|ISO Fedora Live (Workstation)
iso.upload.alpine_repo_switch|Benutzerdefiniertes APK-Repository|Repositorio APK personalizado|Repository APK personalizzato|Repositório APK personalizado
iso.upload.alpine_repo_url_label|APK-Repository-URL|URL del repositorio APK|URL repository APK|URL do repositório APK
sett.ipxe_debug_on|iPXE-Debug-Modus aktiviert (Menüs + Firmware-Build).|Modo debug iPXE activado (menús + compilación firmware).|Modalità debug iPXE attiva (menu + compilazione firmware).|Modo de depuração iPXE ativo (menus + compilação firmware).
sett.ipxe_debug_off|iPXE-Debug-Modus deaktiviert (Menüs + Firmware-Build).|Modo debug iPXE desactivado (menús + compilación firmware).|Modalità debug iPXE disattiva (menu + compilazione firmware).|Modo de depuração iPXE desativado (menus + compilação firmware).
sett.server_url_invalid|Server-URL abgelehnt: http(s):// gefolgt von IPv4 oder Hostname (kein Pfad, keine Abfrage).|URL del servidor rechazada: http(s):// seguido de IPv4 o nombre de host (sin ruta ni consulta).|URL server rifiutato: http(s):// seguito da IPv4 o hostname (senza percorso o query).|URL do servidor rejeitado: http(s):// seguido de IPv4 ou nome de anfitrião (sem caminho nem consulta).
sett.tls_renew_invalid_host|Ungültiger TLS-Host in SERVER_BASE_URL. Server-URL unter Einstellungen korrigieren und erneut versuchen.|Host TLS no válido en SERVER_BASE_URL. Corrija la URL del servidor en Ajustes e inténtelo de nuevo.|Host TLS non valido in SERVER_BASE_URL. Correggere l'URL server in Impostazioni e riprovare.|Host TLS inválido em SERVER_BASE_URL. Corrija o URL do servidor em Definições e tente novamente.
""".strip()


def main() -> int:
    from winpe_gaps_data import WINPE

    gaps: dict[str, dict[str, str]] = {}
    for key, (de, es, it, pt) in WINPE.items():
        gaps[key] = {"de": de, "es": es, "it": it, "pt": pt}
    for line in ROWS.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) != 5:
            raise ValueError(f"bad row ({len(parts)} parts): {line[:80]}")
        key, de, es, it, pt = parts
        gaps[key] = {"de": de, "es": es, "it": it, "pt": pt}

    out = Path(__file__).with_name("locale_gaps_bulk.py")
    lines = [
        '"""Traductions bulk (généré par gen_locale_gaps_bulk.py — ne pas éditer à la main)."""',
        "from __future__ import annotations",
        "",
        "BULK_GAPS: dict[str, dict[str, str]] = {",
    ]
    for key in sorted(gaps):
        row = gaps[key]
        lines.append(f'    "{key}": {{')
        for loc in ("de", "es", "it", "pt"):
            val = row[loc].replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f'        "{loc}": "{val}",')
        lines.append("    },")
    lines.append("}")
    lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {len(gaps)} keys to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
