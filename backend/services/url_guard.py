import ipaddress
import json
import os
import socket
from urllib.parse import urlparse

from .config import RESULTS_FILE, TEST_RESULTS_FILE


class UrlRejected(ValueError):
    pass


def host_of(url: str) -> str:
    """Hôte normalisé (minuscules, sans « www. ») : https://www.x.fr et x.fr sont le même site."""
    raw = (url or "").strip()
    if "://" not in raw:
        raw = "https://" + raw
    host = (urlparse(raw).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _check_public(url: str) -> None:
    raw = (url or "").strip()
    parsed = urlparse(raw if "://" in raw else "https://" + raw)
    if parsed.scheme not in ("http", "https"):
        raise UrlRejected("URL invalide : seuls http et https sont acceptés")
    if parsed.username or parsed.password:
        raise UrlRejected("URL invalide : identifiants dans l'URL refusés")
    host = parsed.hostname
    if not host:
        raise UrlRejected("URL invalide : hôte manquant")
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return  # Ne résout pas : rien d'interne à atteindre, l'appel échouera de lui-même.
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise UrlRejected("URL refusée : l'hôte pointe vers une adresse privée ou locale")


def _known_hosts() -> set[str]:
    """Hôtes de results.json (et du fichier de l'onglet Test). Fichier absent, illisible ou vide : ensemble vide, donc tout
    est refusé — un garde de sécurité ne doit pas s'ouvrir quand son référentiel manque."""
    hosts: set[str] = set()
    for path in (RESULTS_FILE, TEST_RESULTS_FILE):
        try:
            with open(path, "r", encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, ValueError):
            continue
        hosts |= {host_of(e.get("url", "")) for e in entries if isinstance(e, dict)}
    hosts.discard("")
    return hosts


def known_hosts() -> set[str]:
    return _known_hosts()


def validate_site_url(url: str, require_known: bool = True, known: set[str] | None = None) -> None:
    """Refuse les URLs qui ne sont pas un site public. Avec require_known, n'accepte
    que les sites déjà audités (results.json) : le backend ne signe et n'envoie des
    ordres qu'à des sites connus. Sans results.json lisible, tout est refusé."""
    _check_public(url)
    if require_known:
        if host_of(url) not in (known if known is not None else _known_hosts()):
            raise UrlRejected("Site inconnu : il doit d'abord être audité")
