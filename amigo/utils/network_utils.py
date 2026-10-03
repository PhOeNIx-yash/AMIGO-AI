"""
network_utils.py — Lightweight network and internet connectivity verification for Amigo.
"""

import socket
import time

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

    try:
        # Quick TCP connection to Cloudflare or Google DNS server (port 53)
        with socket.create_connection(("8.8.8.8", 53), timeout=timeout):
            _is_online_cache = True
    except OSError:
        try:
            with socket.create_connection(("1.1.1.1", 53), timeout=timeout):
                _is_online_cache = True
        except OSError:
            _is_online_cache = False

    _last_online_check = now
    return _is_online_cache
