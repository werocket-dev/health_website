"""Zips de plugins WordPress arbitraires, stockés sur la plateforme puis installés à distance.

Même principe que breakdance_zip.py (zip hébergé ici, agent qui le télécharge via un lien signé
et vérifie son sha256), mais pour n'importe quel plugin : un dossier par slug dans data/plugins/.
Les règles de validation reprennent celles de l'agent WP (/install-plugin-zip), pour refuser ici
plutôt que de le découvrir sur chaque site."""
import hashlib
import hmac
import json
import os
import re
import time
import zipfile
from datetime import datetime, timezone

from .breakdance_zip import (
    MAX_UNCOMPRESSED_BYTES,
    MAX_ZIP_BYTES,  # noqa: F401  (réexporté pour l'upload)
    MAX_ZIP_ENTRIES,
    TOKEN_TTL_SECONDS,
    ZipError,
    _b64,
    _dl_secret,
    _unb64,
)
from .config import DATA_DIR
from .json_store import write_json_atomic

PLUGINS_DIR = os.path.join(DATA_DIR, "plugins")
os.makedirs(PLUGINS_DIR, exist_ok=True)

_SLUG_RE = re.compile(r"^[a-zA-Z0-9_\-]+$")
_HEADER_RE = r"^[ \t/*#@]*{key}:[ \t]*(.+)$"


def _dir(slug: str) -> str:
    if not _SLUG_RE.match(slug or ""):
        raise ZipError("Slug de plugin invalide")
    return os.path.join(PLUGINS_DIR, slug)


def zip_path(slug: str) -> str:
    return os.path.join(_dir(slug), "plugin.zip")


def _meta_path(slug: str) -> str:
    return os.path.join(_dir(slug), "meta.json")


def _header(head: str, key: str) -> str | None:
    match = re.search(_HEADER_RE.format(key=key), head, re.MULTILINE | re.IGNORECASE)
    return match.group(1).strip().rstrip("*/ ").strip() if match else None


def validate_zip(path: str) -> dict:
    """Un seul dossier racine (le slug), sans chemin dangereux, avec un fichier PHP principal
    portant un en-tête « Plugin Name ». Renvoie slug, nom et version."""
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ZipError("Archive zip illisible")
    with zf:
        infos = [i for i in zf.infolist() if not i.filename.startswith("__MACOSX/")]
        if not infos:
            raise ZipError("Archive vide")
        if len(infos) > MAX_ZIP_ENTRIES:
            raise ZipError("Archive refusée : trop de fichiers")
        if sum(i.file_size for i in infos) > MAX_UNCOMPRESSED_BYTES:
            raise ZipError("Archive refusée : taille décompressée excessive")

        root = None
        main_head = None
        for info in infos:
            name = info.filename
            if ".." in name or name.startswith("/") or "\\" in name:
                raise ZipError("Chemin interdit dans le zip")
            parts = name.split("/", 1)
            if len(parts) < 2 or not _SLUG_RE.match(parts[0]):
                raise ZipError("Le zip doit contenir un seul dossier racine (le plugin)")
            if root is None:
                root = parts[0]
            if parts[0] != root:
                raise ZipError("Le zip doit contenir un seul dossier racine (le plugin)")
            if main_head is None and re.fullmatch(r"[a-zA-Z0-9_\-]+\.php", parts[1]):
                with zf.open(info) as f:
                    head = f.read(8192).decode("utf-8", errors="replace")
                if re.search(r"^[ \t/*#@]*Plugin Name:", head, re.MULTILINE | re.IGNORECASE):
                    main_head = head
        if main_head is None:
            raise ZipError('Aucun plugin WordPress valide dans le zip (en-tête "Plugin Name" introuvable)')
    return {
        "slug": root,
        "name": _header(main_head, "Plugin Name") or root,
        "version": _header(main_head, "Version") or "?",
    }


def store_zip(tmp_path: str, filename: str) -> dict:
    """Valide le zip reçu (déjà sur disque), remplace l'éventuelle version précédente du même
    plugin et enregistre ses métadonnées."""
    info = validate_zip(tmp_path)
    sha = hashlib.sha256()
    with open(tmp_path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha.update(chunk)
    meta = {
        **info,
        "filename": os.path.basename(filename) or "plugin.zip",
        "size": os.path.getsize(tmp_path),
        "sha256": sha.hexdigest(),
        "uploaded_at": datetime.now(timezone.utc).isoformat(),
    }
    os.makedirs(_dir(info["slug"]), exist_ok=True)
    os.replace(tmp_path, zip_path(info["slug"]))
    write_json_atomic(_meta_path(info["slug"]), meta)
    return meta


def get_meta(slug: str) -> dict | None:
    try:
        if not (os.path.exists(zip_path(slug)) and os.path.exists(_meta_path(slug))):
            return None
        with open(_meta_path(slug), "r", encoding="utf-8") as f:
            return json.load(f)
    except (ZipError, OSError, ValueError):
        return None


def list_zips() -> list[dict]:
    try:
        slugs = sorted(os.listdir(PLUGINS_DIR))
    except OSError:
        return []
    metas = [get_meta(s) for s in slugs if _SLUG_RE.match(s)]
    return sorted((m for m in metas if m), key=lambda m: m["uploaded_at"], reverse=True)


def delete_zip(slug: str) -> bool:
    meta = get_meta(slug)
    if not meta:
        return False
    for path in (zip_path(slug), _meta_path(slug)):
        try:
            os.remove(path)
        except OSError:
            pass
    try:
        os.rmdir(_dir(slug))
    except OSError:
        pass
    return True


def _sign(payload: str) -> str:
    return _b64(hmac.new(_dl_secret(), b"plugin|" + payload.encode(), hashlib.sha256).digest())


def make_token(slug: str, sha256: str, ttl: int = TOKEN_TTL_SECONDS) -> str:
    """Lié au slug ET au sha256 du zip courant : un nouveau zip invalide les anciens liens."""
    payload = _b64(json.dumps({"slug": slug, "sha": sha256, "exp": int(time.time()) + ttl}).encode())
    return f"{payload}.{_sign(payload)}"


def verify_token(slug: str, token: str) -> bool:
    try:
        payload, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _sign(payload)):
            return False
        data = json.loads(_unb64(payload))
        meta = get_meta(slug)
        return bool(meta) and data.get("slug") == slug and data.get("exp", 0) >= time.time() and data.get("sha") == meta["sha256"]
    except Exception:
        return False


def build_download_url(slug: str, sha256: str) -> str:
    base = (os.getenv("PUBLIC_BACKEND_URL") or "").rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise ZipError("PUBLIC_BACKEND_URL doit être défini (URL publique du backend)")
    # Contrairement au zip GPL de Breakdance, un plugin quelconque peut être premium : le lien
    # (valable 10 minutes) ne doit pas circuler en clair.
    if base.startswith("http://") and os.getenv("PLUGIN_ZIP_ALLOW_HTTP") != "1":
        raise ZipError(
            "Les zips de plugins exigent HTTPS : mettre une URL https:// dans PUBLIC_BACKEND_URL "
            "(ou PLUGIN_ZIP_ALLOW_HTTP=1 pour l'accepter en connaissance de cause)"
        )
    return f"{base}/dl/plugin/{slug}/{make_token(slug, sha256)}"
