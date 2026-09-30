import os
import re
import json
import hmac
import time
import base64
import hashlib
import zipfile
from datetime import datetime, timezone

from .config import DATA_DIR

# Dans DATA_DIR (volume persistant, voir Dockerfile) et hors de tout dossier
# servi statiquement : le zip n'est accessible que par /dl/breakdance/{token}.
ZIP_DIR = os.path.join(DATA_DIR, "breakdance")
os.makedirs(ZIP_DIR, exist_ok=True)
ZIP_FILE = os.path.join(ZIP_DIR, "breakdance.zip")
META_FILE = os.path.join(ZIP_DIR, "meta.json")

MAX_ZIP_BYTES = 200 * 1024 * 1024
TOKEN_TTL_SECONDS = 600

# Mêmes règles que l'agent WP (/install-plugin-zip) : uniquement breakdance/,
# avec plugin.php — on refuse ici plutôt que de le découvrir sur chaque site.
_MAIN_FILE = "breakdance/plugin.php"


class ZipError(ValueError):
    pass


def _dl_secret() -> bytes:
    # Secret dédié si défini ; sinon dérivé de BACKEND_API_KEY pour ne pas
    # imposer une variable d'env de plus. Sans rapport avec la clé Ed25519.
    secret = os.getenv("BREAKDANCE_DL_SECRET") or os.getenv("BACKEND_API_KEY") or ""
    if not secret:
        raise ZipError("Ni BREAKDANCE_DL_SECRET ni BACKEND_API_KEY ne sont définis")
    return hashlib.sha256(b"breakdance-dl|" + secret.encode("utf-8")).digest()


def _validate_and_read_version(path: str) -> str:
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ZipError("Archive zip illisible")
    with zf:
        names = zf.namelist()
        if not names:
            raise ZipError("Archive vide")
        for name in names:
            if not name.startswith("breakdance/") or ".." in name:
                raise ZipError("Le zip doit contenir uniquement le dossier breakdance/")
        if _MAIN_FILE not in names:
            raise ZipError("breakdance/plugin.php absent du zip")
        # L'en-tête du plugin est dans les premiers Ko.
        header = zf.read(_MAIN_FILE)[:8192].decode("utf-8", errors="replace")
    match = re.search(r"^[ \t/*#@]*Version:[ \t]*([^\s*]+)", header, re.MULTILINE | re.IGNORECASE)
    if not match:
        raise ZipError("Version introuvable dans l'en-tête de breakdance/plugin.php")
    return match.group(1)


def store_zip(tmp_path: str, filename: str) -> dict:
    """Valide le zip reçu (déjà écrit sur disque), le remplace atomiquement
    et enregistre ses métadonnées."""
    version = _validate_and_read_version(tmp_path)

    sha = hashlib.sha256()
    with open(tmp_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)

    meta = {
        "filename": os.path.basename(filename) or "breakdance.zip",
        "size": os.path.getsize(tmp_path),
        "sha256": sha.hexdigest(),
        "version": version,
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    os.replace(tmp_path, ZIP_FILE)
    tmp_meta = META_FILE + ".tmp"
    with open(tmp_meta, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp_meta, META_FILE)
    return meta


def get_meta() -> dict | None:
    if not (os.path.exists(ZIP_FILE) and os.path.exists(META_FILE)):
        return None
    try:
        with open(META_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def make_token(sha256: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    """Token lié au sha256 du zip courant : si un nouveau zip est uploadé,
    les anciens tokens cessent de fonctionner au lieu de servir un autre fichier."""
    payload = _b64(json.dumps({"sha": sha256, "exp": int(time.time()) + ttl}).encode())
    sig = _b64(hmac.new(_dl_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(token: str) -> bool:
    try:
        payload, sig = token.split(".", 1)
        expected = _b64(hmac.new(_dl_secret(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return False
        data = json.loads(_unb64(payload))
        meta = get_meta()
        return bool(meta) and data.get("exp", 0) >= time.time() and data.get("sha") == meta["sha256"]
    except Exception:
        return False


def build_download_url(sha256: str) -> str:
    base = (os.getenv("PUBLIC_BACKEND_URL") or "").rstrip("/")
    # HTTP accepté : l'intégrité repose sur le sha256 que l'agent vérifie après
    # téléchargement, pas sur le transport (le zip GPL n'a rien de confidentiel).
    if not base.startswith(("http://", "https://")):
        raise ZipError("PUBLIC_BACKEND_URL doit être défini (http:// ou https://, URL publique du backend)")
    return f"{base}/dl/breakdance/{make_token(sha256)}"
