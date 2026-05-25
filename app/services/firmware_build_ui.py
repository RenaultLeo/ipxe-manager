"""Étapes UI compilation firmware — alignées sur ipxe_compiler.py."""
from __future__ import annotations

# Ordre affiché dans firmware_status.html (clés = completed_steps / step Celery)
FIRMWARE_UI_STEPS: tuple[str, ...] = (
    "git_clone",
    "git_pull",
    "embed",
    "patch_ipxe_config",
    "compile_bios",
    "compile_efi",
    "copy",
)

# Étapes internes non affichées seules (fusionnées dans patch_ipxe_config)
_PATCH_STEPS = frozenset({"patch_ipxe_config", "patch_ipxe_https"})


def normalize_completed_steps(completed: list[str] | None) -> set[str]:
    """Normalise les clés renvoyées par Celery / compile_ipxe_firmware."""
    s = set(completed or [])
    # Ancien code : une seule étape « git »
    if "git" in s:
        s.add("git_clone")
        s.add("git_pull")
    if "patch_ipxe_https" in s:
        s.add("patch_ipxe_config")
    return s


def _index(step_id: str) -> int:
    try:
        return FIRMWARE_UI_STEPS.index(step_id)
    except ValueError:
        return -1


def _current_ui_step(current: str) -> str | None:
    cur = (current or "").strip()
    if not cur or cur == "init":
        return None
    if cur == "git":
        return "git_pull"
    if cur in _PATCH_STEPS:
        return "patch_ipxe_config"
    if cur in FIRMWARE_UI_STEPS:
        return cur
    return None


def build_step_badges(
    completed: list[str] | None,
    current: str | None,
) -> list[dict[str, str]]:
    """
    Retourne [{id, status}, ...] avec status in ('done', 'active', 'pending').
    Les étapes avant l'étape courante sont « done » même si absentes de completed
    (filet de sécurité si une mise à jour Celery est perdue).
    """
    done = normalize_completed_steps(completed)
    cur = _current_ui_step(current)
    cur_idx = _index(cur) if cur else -1

    badges: list[dict[str, str]] = []
    for i, step_id in enumerate(FIRMWARE_UI_STEPS):
        if step_id in done or (cur_idx >= 0 and i < cur_idx):
            status = "done"
        elif cur == step_id:
            status = "active"
        elif step_id == "git_clone" and cur == "git_pull":
            status = "done"
        else:
            status = "pending"
        badges.append({"id": step_id, "status": status})
    return badges


def extract_progress_meta(
    *,
    state: str,
    meta: object,
    result: object,
    done: bool,
    success: bool,
) -> tuple[str, list[str], list[str]]:
    """Lit step, completed_steps et logs depuis meta Celery ou résultat final."""
    step = ""
    completed: list[str] = []
    logs: list[str] = []

    def _from_dict(d: dict) -> None:
        nonlocal step, completed, logs
        step = str(d.get("step") or step)
        raw_cs = d.get("completed_steps")
        if isinstance(raw_cs, list):
            completed = [str(x) for x in raw_cs]
        raw_logs = d.get("logs")
        if isinstance(raw_logs, list):
            logs = [str(x) for x in raw_logs]
        elif isinstance(raw_logs, str) and raw_logs.strip():
            logs = raw_logs.splitlines()

    if isinstance(meta, dict):
        _from_dict(meta)

    if done and success and isinstance(result, dict):
        _from_dict(result)
        if not completed and result.get("completed_steps"):
            completed = list(result["completed_steps"])
        if not logs and result.get("logs"):
            log_text = result.get("logs")
            if isinstance(log_text, str):
                logs = log_text.splitlines()

    return step, completed, logs
