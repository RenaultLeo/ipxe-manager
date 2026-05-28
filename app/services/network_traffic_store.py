"""Collecte persistante du trafic réseau pour Supervision."""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psutil

from app.config import settings

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_HISTORY: list[dict[str, Any]] = []
_MAX_POINTS = 8640  # 24h @ 10s
_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


def _utc_iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _day_key(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _store_dir() -> Path:
    root = Path(settings.build_dir)
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    out = root / "supervision-network"
    out.mkdir(parents=True, exist_ok=True)
    return out


def _day_file(ts: float) -> Path:
    return _store_dir() / f"traffic-{_day_key(ts)}.jsonl"


def _cleanup_old_files(today_key: str) -> None:
    folder = _store_dir()
    for f in folder.glob("traffic-*.jsonl"):
        if f.stem != f"traffic-{today_key}":
            try:
                f.unlink(missing_ok=True)
            except OSError:
                logger.warning("Impossible de supprimer %s", f)


def _totals() -> tuple[int, int, int]:
    stats = psutil.net_io_counters(pernic=True)
    rx = 0
    tx = 0
    ifaces = 0
    for nic, st in stats.items():
        if nic == "lo":
            continue
        ifaces += 1
        rx += int(st.bytes_recv)
        tx += int(st.bytes_sent)
    return rx, tx, ifaces


def _append_point(point: dict[str, Any]) -> None:
    with _LOCK:
        _HISTORY.append(point)
        if len(_HISTORY) > _MAX_POINTS:
            del _HISTORY[: len(_HISTORY) - _MAX_POINTS]
    f = _day_file(point["ts"])
    try:
        with f.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(point, ensure_ascii=True) + "\n")
    except OSError:
        logger.exception("Erreur écriture trafic réseau: %s", f)


def _load_today_history() -> None:
    now = time.time()
    f = _day_file(now)
    if not f.is_file():
        return
    loaded: list[dict[str, Any]] = []
    try:
        with f.open("r", encoding="utf-8") as fh:
            for line in fh:
                raw = line.strip()
                if not raw:
                    continue
                try:
                    row = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and "ts" in row:
                    loaded.append(row)
    except OSError:
        logger.warning("Impossible de lire l'historique réseau %s", f)
        return
    with _LOCK:
        _HISTORY.clear()
        _HISTORY.extend(loaded[-_MAX_POINTS:])


def get_network_history(limit: int = 240) -> list[dict[str, Any]]:
    cap = max(1, min(int(limit or 240), _MAX_POINTS))
    with _LOCK:
        return list(_HISTORY[-cap:])


def _collector_loop(sample_interval_sec: float = 10.0) -> None:
    _load_today_history()
    last_cleanup_day = _day_key(time.time())
    _cleanup_old_files(last_cleanup_day)
    prev_rx: int | None = None
    prev_tx: int | None = None
    prev_ts: float | None = None

    existing = get_network_history(limit=1)
    if existing:
        tail = existing[-1]
        prev_rx = int(tail.get("rx_bytes", 0))
        prev_tx = int(tail.get("tx_bytes", 0))
        prev_ts = float(tail.get("ts", 0))

    while not _STOP_EVENT.is_set():
        ts = time.time()
        day = _day_key(ts)
        if day != last_cleanup_day:
            _cleanup_old_files(day)
            last_cleanup_day = day
        try:
            rx, tx, iface_count = _totals()
            rx_rate = 0.0
            tx_rate = 0.0
            if prev_ts is not None and ts > prev_ts:
                dt = ts - prev_ts
                rx_rate = max(0.0, (rx - (prev_rx or 0)) / dt)
                tx_rate = max(0.0, (tx - (prev_tx or 0)) / dt)
            point = {
                "ts": ts,
                "at": _utc_iso(ts),
                "rx_bytes": rx,
                "tx_bytes": tx,
                "rx_rate_bps": rx_rate,
                "tx_rate_bps": tx_rate,
                "iface_count": iface_count,
            }
            _append_point(point)
            prev_rx, prev_tx, prev_ts = rx, tx, ts
        except Exception:
            logger.exception("Erreur collecteur trafic réseau")
        _STOP_EVENT.wait(sample_interval_sec)


def start_network_traffic_collector(sample_interval_sec: float = 10.0) -> None:
    global _THREAD
    with _LOCK:
        already_running = _THREAD is not None and _THREAD.is_alive()
    if already_running:
        return
    _STOP_EVENT.clear()
    t = threading.Thread(
        target=_collector_loop,
        kwargs={"sample_interval_sec": sample_interval_sec},
        daemon=True,
        name="network-traffic-collector",
    )
    t.start()
    with _LOCK:
        _THREAD = t
    logger.info("Collecteur trafic réseau démarré (interval %.1fs)", sample_interval_sec)

