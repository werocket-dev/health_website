#!/usr/bin/env python3
"""
Exécute une opération PocketBase dans un sous-processus isolé.

Nécessaire car authentifier le client PocketBase (httpx) dans le même
processus/event-loop que Playwright casse le lancement du driver Playwright
sur macOS — conflit du même type que celui déjà contourné pour TensorFlow
(cf. predict_visual() dans audit_engine.py).

Usage : python3 pb_worker.py <commande> '<json_args>'
Écrit le résultat en JSON sur stdout.
"""
import sys
import json

from services import pocketbase_client as pbc


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "commande manquante"}))
        sys.exit(1)

    command = sys.argv[1]
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    if command == "get_active_projects":
        result = pbc.get_active_projects()
    elif command == "update_project_status":
        pbc.update_project_status(args["project_id"], args["latest"])
        result = {"success": True}
    elif command == "create_site_audit":
        pbc.create_site_audit(args["project_id"], args["entry"])
        result = {"success": True}
    elif command == "sync_site_plugins":
        pbc.sync_site_plugins(args["project_id"], args["plugins"])
        result = {"success": True}
    else:
        print(json.dumps({"error": f"commande inconnue: {command}"}))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
