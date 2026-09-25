import os
import random
import string
from dotenv import load_dotenv
from pocketbase import PocketBase

load_dotenv()


def _generate_record_id() -> str:
    """
    Génère un id conforme au format PocketBase ([a-z0-9]{15}).

    ⚠️ Nécessaire pour la collection 'projects' : une modification de
    schéma (ajout de latest_mises_a_jour via ensure_fields) a réinitialisé
    par effet de bord le auto_generate_pattern du champ système "id" côté
    serveur (confirmé irréversible via l'API — PocketBase refuse de le
    réécrire une fois vidé). On génère donc l'id nous-mêmes à la création
    plutôt que de compter sur l'auto-génération serveur.
    """
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=15))

POCKETBASE_PUBLIC_URL = os.getenv("POCKETBASE_PUBLIC_URL", "").strip().rstrip("/")
POCKETBASE_ADMIN_EMAIL = os.getenv("POCKETBASE_ADMIN_EMAIL")
POCKETBASE_ADMIN_PASSWORD = os.getenv("POCKETBASE_ADMIN_PASSWORD")

_pb: PocketBase | None = None


def get_client() -> PocketBase:
    """
    Client PocketBase authentifié (singleton, ré-authentifie automatiquement
    si le token en cache n'est plus valide — la connexion a déjà montré des
    timeouts ponctuels en test, donc on ne présume jamais que le token tenu
    en mémoire est encore bon).
    """
    global _pb
    if _pb is not None and _pb.auth_store.is_valid:
        return _pb

    if not POCKETBASE_PUBLIC_URL:
        raise RuntimeError("POCKETBASE_PUBLIC_URL manquant dans backend/.env")

    # Timeout par défaut d'httpx (5s) trop court pour ce VPS distant sous charge
    # (les ConnectTimeout observés en test venaient de là, pas d'une vraie panne).
    pb = PocketBase(POCKETBASE_PUBLIC_URL, timeout=20.0)
    pb.collection("_superusers").auth_with_password(POCKETBASE_ADMIN_EMAIL, POCKETBASE_ADMIN_PASSWORD)
    _pb = pb
    return _pb


def _project_to_result(r) -> dict:
    """Formate un record `projects` au format attendu par le dashboard (SiteResult)."""
    return {
        "client": r.client_name,
        "url": r.url,
        "ia_status": r.latest_ia_status or "N/A",
        "ia_score": r.latest_ia_score or 0,
        "ia_status_contact": r.latest_ia_status_contact or "N/A",
        "ia_score_contact": r.latest_ia_score_contact or 0,
        "ia_status_mobile": r.latest_ia_status_mobile or "N/A",
        "ia_score_mobile": r.latest_ia_score_mobile or 0,
        "wp_version": r.latest_wp_version or "N/A",
        "php_version": r.latest_php_version or "N/A",
        "theme": r.latest_theme or "N/A",
        "builder": r.latest_builder or "N/A",
        "updates_count": r.latest_updates_count or 0,
        "mises_a_jour": r.latest_mises_a_jour or "Aucune",
        "methode": r.latest_methode_scan or "N/A",
        "licenses": r.latest_licenses or {},
    }


def _build_filter(pb, search: str = "", ia_status: str = "", maj: str = "") -> str:
    """
    Combine recherche texte + filtre IA + filtre MAJ en une seule expression
    PocketBase (ET logique entre les trois, OU logique entre client_name/url
    pour la recherche). Chaque morceau est optionnel.
    """
    clauses = []
    if search:
        clauses.append(pb.filter('(client_name ~ {:q} || url ~ {:q})', {"q": search}))
    if ia_status:
        clauses.append(pb.filter('latest_ia_status = {:s}', {"s": ia_status}))
    if maj == "avec_maj":
        clauses.append('latest_updates_count > 0')
    elif maj == "critiques":
        clauses.append(pb.filter('latest_mises_a_jour ~ {:c}', {"c": "🔴"}))
    elif maj == "a_jour":
        clauses.append('latest_updates_count = 0')
    return " && ".join(clauses)


def get_results_summary(
    page: int = 1, per_page: int = 25, search: str = "", ia_status: str = "", maj: str = ""
) -> dict:
    """
    Équivalent paginé du SELECT DISTINCT ON (project_id) ... FROM site_audits
    — sauf qu'ici chaque projet EST déjà son propre dernier état (latest_*),
    donc une simple lecture triée/paginée suffit, sans dédoublonnage.
    `search`/`ia_status`/`maj` filtrent côté PocketBase (pas de scan Python) :
    ia_status = "OK"/"ATTENTION"/"ALERTE", maj = "avec_maj"/"critiques"/"a_jour".
    """
    pb = get_client()
    query_params = {"sort": "-last_audit_at"}
    filter_expr = _build_filter(pb, search, ia_status, maj)
    if filter_expr:
        query_params["filter"] = filter_expr
    result = pb.collection("projects").get_list(page, per_page, query_params)
    return {
        "items": [_project_to_result(r) for r in result.items],
        "total": result.total_items,
        "page": result.page,
        "per_page": result.per_page,
        "total_pages": result.total_pages,
    }


def get_stats_summary() -> dict:
    """Statistiques globales pour les widgets du dashboard."""
    pb = get_client()
    projects = pb.collection("projects").get_full_list()

    total_sites = len(projects)
    ia_ok = sum(1 for p in projects if p.latest_ia_status == "OK")
    ia_attention = sum(1 for p in projects if p.latest_ia_status == "ATTENTION")
    ia_alerte = sum(1 for p in projects if p.latest_ia_status == "ALERTE")
    total_updates = sum(p.latest_updates_count or 0 for p in projects)
    scans_agent = sum(1 for p in projects if p.latest_methode_scan == "Agent")

    avec_maj = sum(1 for p in projects if (p.latest_updates_count or 0) > 0)
    critiques = sum(1 for p in projects if "🔴" in (p.latest_mises_a_jour or ""))
    a_jour = sum(1 for p in projects if (p.latest_updates_count or 0) == 0)

    return {
        "total_sites": total_sites,
        "ia_status": {"ok": ia_ok, "attention": ia_attention, "alerte": ia_alerte},
        "maj_status": {"toutes": total_sites, "avec_maj": avec_maj, "critiques": critiques, "a_jour": a_jour},
        "kpis": {"updates_pending": total_updates, "agent_coverage": scans_agent},
    }


_PHP_OBSOLETE_THRESHOLD = (8, 1)  # PHP < 8.1 : versions EOL ou proches de l'EOL


def is_php_obsolete(version: str) -> bool:
    """`version` du type '8.3.33' ou '7.4.1' — compare seulement major.minor."""
    if not version:
        return False
    parts = version.split(".")
    if len(parts) < 2:
        return False
    try:
        major, minor = int(parts[0]), int(parts[1])
    except ValueError:
        return False
    return (major, minor) < _PHP_OBSOLETE_THRESHOLD


def get_recap_summary() -> dict:
    """
    Fallback de /api/recap quand results.json n'existe pas encore (aucun
    audit lancé) — vue construite depuis tout le parc actif PocketBase à
    la place. Même forme que le calcul basé sur results.json.
    """
    pb = get_client()
    projects = pb.collection("projects").get_full_list(
        query_params={"filter": pb.filter("is_active = {:a}", {"a": True})}
    )

    audited = [p for p in projects if getattr(p, "last_audit_at", None)]

    # 1. Sites avec un score IA < 70% (accueil, contact ou mobile — en excluant
    # les diagnostics "N/A", ex. pas de page contact trouvée, pour ne pas les
    # confondre avec un vrai score bas)
    ia_faible = []
    for p in audited:
        checks = [
            ("accueil", p.latest_ia_status, p.latest_ia_score),
            ("contact", p.latest_ia_status_contact, p.latest_ia_score_contact),
            ("mobile", p.latest_ia_status_mobile, p.latest_ia_score_mobile),
        ]
        faibles = {label: score for label, status, score in checks if status and status != "N/A" and (score or 0) < 70}
        if faibles:
            ia_faible.append({"client": p.client_name, "url": p.url, "scores": faibles})
    ia_faible.sort(key=lambda s: min(s["scores"].values()))

    # 2. Fréquence des mises à jour par plugin, avec répartition par sévérité
    # et la liste des sites concernés (pour la sélection manuelle de MAJ groupée)
    project_lookup = {p.id: {"client": p.client_name, "url": p.url} for p in projects}
    plugin_records = pb.collection("site_plugins").get_full_list(
        query_params={"filter": pb.filter("update_available = {:u}", {"u": True})}
    )
    plugin_agg: dict[str, dict] = {}
    for rec in plugin_records:
        slug = rec.plugin_slug or rec.plugin_name
        agg = plugin_agg.setdefault(slug, {"plugin_name": rec.plugin_name, "sites": {}, "risk_counts": {}})
        risk = rec.risk_level or "INCONNU"
        project_info = project_lookup.get(rec.project, {})
        agg["sites"][rec.project] = {
            "client": project_info.get("client", "?"),
            "url": project_info.get("url", ""),
            "risk": risk,
        }
        agg["risk_counts"][risk] = agg["risk_counts"].get(risk, 0) + 1
    plugin_frequency = sorted(
        [
            {
                "plugin_slug": slug,
                "plugin_name": v["plugin_name"],
                "sites_count": len(v["sites"]),
                "risk_counts": v["risk_counts"],
                "sites": list(v["sites"].values()),
            }
            for slug, v in plugin_agg.items()
        ],
        key=lambda x: x["sites_count"],
        reverse=True,
    )

    # 3. Sites sur une version PHP obsolète (< 8.1)
    php_obsolete = [
        {"client": p.client_name, "url": p.url, "php_version": p.latest_php_version}
        for p in audited
        if is_php_obsolete(p.latest_php_version)
    ]

    # 5. Disponibilité de l'Agent WordPress (Agent vs fallback Scraping)
    methode_counts: dict[str, int] = {}
    for p in audited:
        methode = p.latest_methode_scan or "Inconnu"
        methode_counts[methode] = methode_counts.get(methode, 0) + 1

    # 6. Licences Breakdance jamais vérifiées OU explicitement invalides/désactivées
    breakdance_licenses_a_verifier = [
        {"client": p.client_name, "url": p.url}
        for p in audited
        for lic in [(p.latest_licenses or {}).get("breakdance")]
        if lic and lic.get("valid") is not True
    ]

    return {
        "total_sites": len(audited),
        "ia_faible": ia_faible,
        "plugin_frequency": plugin_frequency,
        "php_obsolete": php_obsolete,
        "methode_counts": methode_counts,
        "breakdance_licenses_a_verifier": breakdance_licenses_a_verifier,
        # Pas de données de thèmes stockées dans PocketBase (uniquement dans
        # results.json) — liste vide pour garder la même forme que l'autre calcul.
        "sites_trop_de_themes": [],
    }


def get_active_projects(limit: int | None = None) -> list[dict]:
    """
    Sites actifs à auditer (remplace le SELECT * FROM projects WHERE is_active).
    `limit` sert à borner un audit de test sans devoir désactiver des sites.
    """
    pb = get_client()
    records = pb.collection("projects").get_full_list(query_params={"filter": "is_active = true"})
    if limit:
        records = records[:limit]
    return [
        {
            "project_id": r.id,
            "project_name": r.client_name,
            "site_url": r.url,
        }
        for r in records
    ]


def get_project_by_url(url: str):
    """Retrouve un projet par son URL (unique). Retourne None si absent."""
    pb = get_client()
    try:
        return pb.collection("projects").get_first_list_item(
            pb.filter("url = {:url}", {"url": url})
        )
    except Exception:
        return None


def upsert_project_from_notion(client_name: str, url: str, date_mise_en_ligne, notion_page_id: str) -> dict:
    """
    Crée le projet s'il n'existe pas (par URL), sinon met à jour son nom et sa
    date — équivalent du ON CONFLICT(url) DO UPDATE de l'ancien import Postgres.

    is_active n'est forcé à True qu'à la création : sur une mise à jour, on
    ne touche pas ce champ pour ne pas réactiver silencieusement un site
    désactivé manuellement à chaque resynchronisation Notion.
    """
    pb = get_client()
    existing = get_project_by_url(url)

    data = {
        "client_name": client_name,
        "url": url,
        "date_mise_en_ligne": date_mise_en_ligne.isoformat() if date_mise_en_ligne else None,
    }
    if notion_page_id:
        data["notion_page_id"] = notion_page_id

    if existing:
        return pb.collection("projects").update(existing.id, data)
    data["is_active"] = True
    return pb.collection("projects").create({"id": _generate_record_id(), **data})


def get_random_active_projects(limit: int = 350) -> list[dict]:
    """
    Échantillon aléatoire de sites actifs — utilisé par le script de
    génération du dataset d'entraînement (3_generate_dataset.py).
    PocketBase ne supporte pas un ORDER BY RANDOM() natif, donc on tire
    l'échantillon côté Python après avoir récupéré la liste complète.
    """
    import random
    pb = get_client()
    records = pb.collection("projects").get_full_list(query_params={"filter": "is_active = true"})
    sample = random.sample(records, min(limit, len(records)))
    # Même forme que get_active_projects() (project_id notamment, sans quoi
    # l'écriture PocketBase de fin d'audit est silencieusement ignorée).
    return [{"project_id": r.id, "project_name": r.client_name, "site_url": r.url} for r in sample]


def update_project_status(project_id: str, latest: dict) -> dict:
    """
    Met à jour l'état courant d'un projet après un audit. Décale d'abord
    latest_ia_status/latest_updates_count/latest_licenses vers les champs
    previous_* — c'est cette paire previous/latest qui permet au rapport
    nocturne de détecter les changements sans requêter tout l'historique.

    `latest` attend les clés : methode_scan, ia_status, ia_score,
    ia_status_contact, ia_score_contact, ia_status_mobile, ia_score_mobile,
    wp_version, php_version, mysql_version, theme, theme_version, builder,
    builder_version, updates_count, licenses.
    """
    pb = get_client()
    current = pb.collection("projects").get_one(project_id)

    payload = {
        "previous_ia_status": current.latest_ia_status,
        "previous_updates_count": current.latest_updates_count,
        "previous_licenses": current.latest_licenses if isinstance(current.latest_licenses, dict) else {},

        "latest_methode_scan": latest["methode_scan"],
        "latest_ia_status": latest["ia_status"],
        "latest_ia_score": latest["ia_score"],
        "latest_ia_status_contact": latest["ia_status_contact"],
        "latest_ia_score_contact": latest["ia_score_contact"],
        "latest_ia_status_mobile": latest["ia_status_mobile"],
        "latest_ia_score_mobile": latest["ia_score_mobile"],
        "latest_wp_version": latest["wp_version"],
        "latest_php_version": latest["php_version"],
        "latest_mysql_version": latest["mysql_version"],
        "latest_theme": latest["theme"],
        "latest_theme_version": latest["theme_version"],
        "latest_builder": latest["builder"],
        "latest_builder_version": latest["builder_version"],
        "latest_updates_count": latest["updates_count"],
        "latest_mises_a_jour": latest.get("mises_a_jour", ""),
        "latest_licenses": latest["licenses"],
        "last_audit_at": latest.get("audited_at"),
    }
    return pb.collection("projects").update(project_id, payload)


def create_site_audit(project_id: str, entry: dict) -> dict:
    """Journal brut — une entrée par audit, pour la traçabilité."""
    pb = get_client()
    payload = {"project": project_id, **entry}
    return pb.collection("site_audits").create(payload)


def sync_site_plugins(project_id: str, plugins: list[dict]) -> None:
    """
    Remplace intégralement les site_plugins d'un site par la liste fraîche
    (suppression + recréation en un seul appel /api/batch) — plus simple et
    moins coûteux qu'un upsert plugin par plugin, et le coût reste
    proportionnel au nombre de SITES, pas au nombre de sites × plugins.

    `plugins` : liste de dicts avec plugin_slug, plugin_name, version,
    is_active, update_available, latest_available_version, risk_level.
    """
    pb = get_client()
    existing = pb.collection("site_plugins").get_full_list(
        query_params={"filter": pb.filter("project = {:project}", {"project": project_id})}
    )

    if not existing and not plugins:
        return

    # ⚠️ On n'utilise pas batch.send() du SDK : il encode toujours la requête
    # en multipart avec un champ "@jsonPayload" (prévu pour l'upload de
    # fichiers), même sans fichier — ce que l'API PocketBase rejette
    # (400 "Cannot be blank"). On construit donc le payload JSON attendu
    # nous-même et on l'envoie directement.
    requests_payload = [
        {"method": "DELETE", "url": f"/api/collections/site_plugins/records/{record.id}", "body": None, "headers": {}}
        for record in existing
    ] + [
        {"method": "POST", "url": "/api/collections/site_plugins/records", "body": {"project": project_id, **plugin}, "headers": {}}
        for plugin in plugins
    ]
    pb.send("/api/batch", {"method": "POST", "body": {"requests": requests_payload}})


def write_audit_results(entries: list[dict]) -> dict:
    """
    Écrit les résultats de TOUS les sites d'un audit en une seule connexion/
    authentification PocketBase (au lieu d'un subprocess+login par site) —
    évite la rafale de connexions neuves vers le VPS distant qui déclenchait
    des ConnectTimeout/EHOSTDOWN pendant les audits.

    `entries` : liste de dicts {project_id, latest, entry, plugins}.
    Une erreur sur un site n'interrompt pas les suivants ; les échecs sont
    remontés dans le résultat pour rester visibles dans les logs d'audit.
    """
    get_client()  # authentifie une fois, réutilisé par les 3 appels ci-dessous
    failed = []
    for item in entries:
        project_id = item["project_id"]
        try:
            update_project_status(project_id, item["latest"])
            create_site_audit(project_id, item["entry"])
            sync_site_plugins(project_id, item["plugins"])
        except Exception as e:
            failed.append({"project_id": project_id, "error": str(e)[:300]})
    return {"success": True, "written": len(entries) - len(failed), "failed": failed}
