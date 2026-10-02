import re
import socket

# Un serveur d'hébergement héberge souvent des dizaines de sites : s'il ignore
# silencieusement notre IP (timeouts), insister site après site ne fait que
# gaspiller ~50 s par site et multiplier les requêtes en échec.
HOST_FAILURE_THRESHOLD = 2
# Des refus 403/429 d'affilée sur des sites différents signalent un blocage
# systématique (pare-feu, WAF, agent trop ancien) : continuer l'aggrave.
BLOCKING_STREAK_THRESHOLD = 5

_BLOCKING_RE = re.compile(r"\b(403|429)\b")
_CONNECTION_MARKERS = ("Délai de connexion", "Délai de réponse", "Connexion impossible", "Connexion refusée")


def classify_error(message: str) -> str:
    """'blocking' (403/429), 'connection' (injoignable) ou 'other'.
    Un 404 ou une 500 prouvent que le serveur répond : ils ne comptent pas."""
    text = message or ""
    if any(m in text for m in _CONNECTION_MARKERS):
        return "connection"
    if _BLOCKING_RE.search(text):
        return "blocking"
    return "other"


def resolve_ip(url: str) -> str | None:
    """IP du serveur d'un site, pour regrouper les sites d'un même hébergement."""
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


class JobGuard:
    """Garde-fou partagé par les workers d'un job de masse (appels depuis un seul
    event loop : pas de verrou nécessaire)."""

    def __init__(self):
        self.blocking_streak = 0
        self.host_failures: dict[str, int] = {}
        self.blocked_hosts: set[str] = set()
        self.aborted_reason: str | None = None

    def skip_reason(self, ip: str | None) -> str | None:
        if self.aborted_reason:
            return f"Ignoré : job arrêté ({self.aborted_reason})"
        if ip and ip in self.blocked_hosts:
            return f"Ignoré : le serveur {ip} ne répond pas depuis le backend ({HOST_FAILURE_THRESHOLD} échecs de connexion d'affilée)"
        return None

    def record(self, ip: str | None, kind: str):
        """kind : 'ok' (le serveur a répondu), 'blocking', 'connection' ou 'other'."""
        if kind == "blocking":
            self.blocking_streak += 1
            if self.blocking_streak >= BLOCKING_STREAK_THRESHOLD:
                self.aborted_reason = f"{self.blocking_streak} refus 403/429 consécutifs"
        elif kind in ("ok", "other"):
            self.blocking_streak = 0

        if ip:
            if kind == "connection":
                self.host_failures[ip] = self.host_failures.get(ip, 0) + 1
                if self.host_failures[ip] >= HOST_FAILURE_THRESHOLD:
                    self.blocked_hosts.add(ip)
            elif kind in ("ok", "other", "blocking"):
                self.host_failures[ip] = 0
