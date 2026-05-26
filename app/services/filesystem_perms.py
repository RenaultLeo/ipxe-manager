"""Permissions disque : boot/, menus/, etc. (Celery ipxe, Nginx www-data)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def fix_tree_permissions(
    path: Path,
    *,
    dir_mode: int = 0o755,
    file_mode: int = 0o644,
) -> None:
    """
    Dossiers lisibles/exécutables, fichiers lisibles (Nginx).
    7z peut laisser des modes 400/500 sur l’ISO extrait.
    """
    path = Path(path)
    if not path.exists():
        return
    try:
        for p in path.rglob("*"):
            try:
                if p.is_dir():
                    p.chmod(dir_mode)
                else:
                    p.chmod(file_mode)
            except OSError:
                pass
        if path.is_dir():
            path.chmod(dir_mode)
        elif path.is_file():
            path.chmod(file_mode)
        logger.debug("Permissions ajustées sous %s", path)
    except OSError as exc:
        logger.warning("Impossible de corriger les permissions sur %s : %s", path, exc)


def prepare_writable_dir(directory: Path, *, dir_mode: int = 0o2775) -> None:
    """Répertoire créé/ouvert en écriture pour l’utilisateur du service (ipxe)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for mode in (dir_mode, 0o775, 0o755):
        try:
            directory.chmod(mode)
            break
        except OSError:
            continue
    try:
        for child in directory.iterdir():
            try:
                if child.is_dir():
                    child.chmod(0o775)
                else:
                    child.chmod(0o664)
            except OSError:
                pass
    except OSError as exc:
        logger.warning("prepare_writable_dir(%s) : %s", directory, exc)


def write_text_file(
    path: Path,
    content: str,
    *,
    file_mode: int = 0o664,
    encoding: str = "utf-8",
) -> None:
    """
    Écrit un fichier texte même si un ancien fichier appartient à root
    (suppression puis recréation si le répertoire parent est inscriptible).
    """
    path = Path(path)
    prepare_writable_dir(path.parent)
    data = content

    def _write(target: Path) -> None:
        target.write_text(data, encoding=encoding)
        try:
            target.chmod(file_mode)
        except OSError:
            pass

    if path.exists() and not os.access(path, os.W_OK):
        try:
            path.unlink()
        except OSError as exc:
            raise PermissionError(
                f"Impossible de remplacer {path} (propriétaire ou droits) : {exc}"
            ) from exc

    tmp = path.with_name(path.name + ".tmp")
    try:
        _write(tmp)
        tmp.replace(path)
        return
    except OSError:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    try:
        _write(path)
    except PermissionError:
        raise
    except OSError as exc:
        if path.exists():
            try:
                path.unlink()
                _write(path)
                return
            except OSError:
                pass
        raise PermissionError(f"Écriture impossible : {path}") from exc


def prepare_menus_dir(menus_dir: Path) -> None:
    """Prépare /srv/ipxe/http/menus pour Celery (ipxe) et lecture Nginx."""
    menus_dir = Path(menus_dir)
    prepare_writable_dir(menus_dir)
    try:
        os.chmod(menus_dir, menus_dir.stat().st_mode | 0o755)
    except OSError:
        pass
