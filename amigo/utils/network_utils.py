"""
network_utils.py — Lightweight network and internet connectivity verification for Amigo.
"""

import logging
import socket
import time

logger = logging.getLogger("amigo.network_utils")

_is_online_cache: bool = True
_last_online_check: float = 0.0


def is_internet_connected(timeout: float = 0.8, cache_ttl: float = 2.0) -> bool:
    """Fast, non-blocking check to determine if PC has active internet connectivity.
    Cached for cache_ttl seconds to avoid repeated socket handshakes.
    """
    global _is_online_cache, _last_online_check
    now = time.time()
    if now - _last_online_check < cache_ttl:
        return _is_online_cache

    _is_online_cache = False
    probe_errors = []
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 443), ("8.8.8.8", 53), ("1.1.1.1", 53)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                _is_online_cache = True
                break
        except OSError as exc:
            probe_errors.append(f"{host}:{port} -> {type(exc).__name__}: {exc}")
            continue

    if not _is_online_cache and probe_errors:
        logger.warning("[network_utils] Internet probe failed: %s", "; ".join(probe_errors))

    _last_online_check = now
    return _is_online_cache
