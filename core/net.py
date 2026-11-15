"""Small network helpers shared by the HTTP-facing service and adapters."""

import ipaddress
import os

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DOCKER_HOSTS = {"host.docker.internal", "gateway.docker.internal"}
PRIVATE_SUFFIXES = (".local", ".lan", ".home", ".internal")
CGNAT = ipaddress.ip_network("100.64.0.0/10")


def is_private_host(hostname, extra_env=""):
    """Return True for loopback / RFC1918 / link-local / local suffixes.

    Plain HTTP to these hosts is safe for a self-hosted app: traffic stays on
    the machine or the LAN.  Public HTTP hosts are still rejected unless they
    are explicitly listed in ``extra_env``.
    """
    host = str(hostname or "").strip().lower().rstrip(".")
    if not host:
        return False
    extra = {
        item.strip().lower().rstrip(".")
        for item in os.getenv(extra_env, "").split(",")
        if item.strip()
    }
    if host in extra or host in LOOPBACK_HOSTS or host in DOCKER_HOSTS:
        return True
    if host.endswith(PRIVATE_SUFFIXES):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped:
        address = address.ipv4_mapped
    return bool(
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address in CGNAT
    )


def is_lan_bind_host(hostname):
    """Allow a wildcard bind or a loopback/private address for the server."""
    host = str(hostname or "").strip().lower()
    if host in {"0.0.0.0", "::", "localhost"}:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return bool(address.is_loopback or address.is_private or address.is_unspecified)
