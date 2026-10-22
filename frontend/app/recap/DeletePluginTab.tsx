"use client";

import axios from "axios";
import { useEffect, useMemo, useState } from "react";
import { Card } from "@/components/ui/Card";
import { errorDetail, pollJob, type Scope } from "@/lib/jobs";

const API_URL = "/api/backend";

export type InstalledPlugin = {
  plugin_path: string;
  plugin_name: string;
  sites_count: number;
  sites: { client: string; url: string; version: string | null; is_active: boolean | null }[];
};

type SiteJob = {
  status: "pending" | "installing" | "done" | "error" | "skipped";
  message?: string;
};

const STATUS_LABEL: Record<SiteJob["status"], string> = {
  pending: "En attente",
  installing: "Suppression…",
  done: "✓ Supprimé",
  error: "✗ Erreur",
  skipped: "⏭ Ignoré",
};

const DANGER = "#dc2626";
const mutedText = { color: "rgba(23,25,28,0.5)" };

export function DeletePluginTab({
  scope,
  reloadKey,
  onDeleted,
}: {
  scope: Scope;
  reloadKey: number;
  onDeleted: () => void;
}) {
  // Liste à part du récap (volumineuse) : chargée à l'ouverture de l'onglet et après chaque rechargement.
  const [plugins, setPlugins] = useState<InstalledPlugin[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  useEffect(() => {
    axios
      .get<{ plugins: InstalledPlugin[] }>(`${API_URL}/plugins/installed`, { params: { scope } })
      .then(({ data }) => {
        setPlugins(data.plugins);
        setLoadError(null);
      })
      .catch(() => setLoadError("Impossible de charger la liste des plugins."));
  }, [scope, reloadKey]);


  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [selected, setSelected] = useState<Record<string, Set<string>>>({});

  const [running, setRunning] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [job, setJob] = useState<Record<string, SiteJob> | null>(null);
  const [jobPlugin, setJobPlugin] = useState<string | null>(null);
  const [abortReason, setAbortReason] = useState<string | null>(null);
  const [finished, setFinished] = useState(false);

  // Sans recherche : tous les plugins (la liste défile). Avec : par nom de plugin, ou par site
  // (alors seuls les sites correspondants sont listés).
  const view = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return plugins;
    return plugins.flatMap((p) => {
      const nameMatch = p.plugin_name.toLowerCase().includes(q) || p.plugin_path.toLowerCase().includes(q);
      const sites = nameMatch
        ? p.sites
        : p.sites.filter((s) => s.client.toLowerCase().includes(q) || s.url.toLowerCase().includes(q));
      return sites.length === 0 ? [] : [{ ...p, sites, sites_count: sites.length }];
    });
  }, [plugins, query]);

  const toggleAll = (path: string, urls: string[], all: boolean) =>
    setSelected((prev) => {
      const next = new Set(prev[path] ?? []);
      for (const u of urls) {
        if (all) next.delete(u);
        else next.add(u);
      }
      return { ...prev, [path]: next };
    });

  const handleDelete = async (plugin: InstalledPlugin) => {
    const urls = Array.from(selected[plugin.plugin_path] ?? []);
    if (urls.length === 0) return;
    const count = urls.length;
    const confirmed = window.confirm(
      `Supprimer définitivement "${plugin.plugin_name}" sur ${count} site${count > 1 ? "s" : ""} ?\n\n` +
        "Le plugin est désactivé puis ses fichiers sont effacés. " +
        "Cette action est irréversible et s'applique à de vrais sites clients."
    );
    if (!confirmed) return;
    // Deuxième barrière : retaper un mot, pour qu'un clic réflexe ne suffise pas.
    const typed = window.prompt(`Pour confirmer la suppression sur ${count} site${count > 1 ? "s" : ""}, tape : supprimer`);
    if (typed?.trim().toLowerCase() !== "supprimer") return;

    setInstallError(null);
    setAbortReason(null);
    setFinished(false);
    setRunning(true);
    setJobPlugin(plugin.plugin_name);
    setJob(Object.fromEntries(urls.map((u) => [u, { status: "pending" as const }])));
    try {
      const { data } = await axios.post<{ job_id: string }>(`${API_URL}/plugins/delete`, {
        plugin_path: plugin.plugin_path,
        urls,
      });

      // Le job dure plusieurs minutes : on suit sa progression par polling.
      await pollJob<{ finished: boolean; aborted_reason: string | null; sites: Record<string, SiteJob> }>(
        `/plugins/jobs/${data.job_id}`,
        (status) => {
          setJob(status.sites);
          setAbortReason(status.aborted_reason);
        }
      );
      setFinished(true);
      setSelected((prev) => ({ ...prev, [plugin.plugin_path]: new Set() }));
      onDeleted(); // recharge le récap : le plugin disparaît des sites où il a été supprimé
    } catch (e) {
      setInstallError(errorDetail(e, "Échec du lancement de la suppression"));
    } finally {
      setRunning(false);
    }
  };

  const clientByUrl = useMemo(() => new Map(plugins.flatMap((p) => p.sites.map((s) => [s.url, s.client] as const))), [plugins]);
  const clientOf = (url: string) => clientByUrl.get(url) ?? url;
  const jobEntries = job ? Object.entries(job) : [];
  const okCount = jobEntries.filter(([, j]) => j.status === "done").length;
  const errorCount = jobEntries.filter(([, j]) => j.status === "error").length;

  return (
    <Card title="Supprimer un plugin — plugins installés, par nombre de sites">
      <p className="text-xs mb-3" style={mutedText}>
        Le plugin est désactivé puis supprimé du site. L&apos;agent WeRocket n&apos;apparaît jamais dans cette liste. Cette action est
        irréversible.
      </p>

      {loadError ? (
        <p className="text-sm" style={{ color: DANGER }}>
          {loadError}
        </p>
      ) : plugins.length === 0 ? (
        <p className="text-sm" style={{ color: "#b45309" }}>
          Aucun plugin connu : la liste est enregistrée à chaque audit (méthode Agent). Clique sur « Actualiser (hors IA) »,
          au-dessus des onglets, pour la remplir sans audit complet.
        </p>
      ) : (
        <>
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Rechercher un plugin ou un site (nom, URL)…"
            className="w-full mb-4 px-3 py-2 rounded-lg border text-sm"
            style={{ borderColor: "rgba(23,25,28,0.15)", color: "var(--color-ink)" }}
          />
          {view.length === 0 && (
            <p className="text-sm" style={mutedText}>
              Aucun plugin ni site ne correspond à cette recherche.
            </p>
          )}
          <div className="max-h-[32rem] overflow-y-auto space-y-1.5">
            {view.map((p) => {
              const isExpanded = expanded === p.plugin_path;
              const sel = selected[p.plugin_path] ?? new Set<string>();
              const urls = p.sites.map((s) => s.url);
              const allSelected = urls.length > 0 && urls.every((u) => sel.has(u));

              return (
                <div key={p.plugin_path} className="rounded overflow-hidden" style={{ background: "rgba(23,25,28,0.02)" }}>
                  <button
                    onClick={() => setExpanded(isExpanded ? null : p.plugin_path)}
                    className="w-full flex items-center justify-between text-sm py-1.5 px-3 cursor-pointer"
                  >
                    <span className="font-medium" style={{ color: "var(--color-ink)" }}>
                      {p.plugin_name}
                    </span>
                    <div className="flex items-center gap-2">
                      <span
                        className="px-2 py-0.5 rounded-full text-xs font-semibold"
                        style={{ background: "rgba(23,25,28,0.08)", color: "var(--color-ink)" }}
                      >
                        {p.sites_count} site{p.sites_count > 1 ? "s" : ""}
                      </span>
                      <span style={{ color: "rgba(23,25,28,0.4)" }}>{isExpanded ? "▲" : "▼"}</span>
                    </div>
                  </button>

                  {isExpanded && (
                    <div className="px-3 pb-3 space-y-1">
                      <div className="flex items-center justify-between py-1.5">
                        <button
                          onClick={() => toggleAll(p.plugin_path, urls, allSelected)}
                          disabled={running}
                          className="text-xs font-medium cursor-pointer hover:underline disabled:opacity-50"
                          style={{ color: "var(--color-deep-teal)" }}
                        >
                          {allSelected ? "Tout désélectionner" : "Tout sélectionner"}
                        </button>
                        <button
                          onClick={() => handleDelete(p)}
                          disabled={sel.size === 0 || running}
                          className="px-3 py-1 rounded-full text-xs font-semibold cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                          style={{ background: DANGER, color: "var(--color-white)" }}
                        >
                          Supprimer sur la sélection ({sel.size})
                        </button>
                      </div>
                      {p.sites.map((site) => (
                        <label
                          key={site.url}
                          className="flex items-center gap-2 text-xs py-1 px-2 rounded cursor-pointer"
                          style={{ background: "rgba(255,255,255,0.6)" }}
                        >
                          <input
                            type="checkbox"
                            checked={sel.has(site.url)}
                            onChange={() => toggleAll(p.plugin_path, [site.url], sel.has(site.url))}
                            disabled={running}
                          />
                          <span className="flex-1" style={{ color: "var(--color-ink)" }}>
                            {site.client}
                          </span>
                          <span style={mutedText}>
                            {site.version ? `v${site.version}` : ""}
                            {site.is_active === false ? " · inactif" : ""}
                          </span>
                        </label>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </>
      )}

      {installError && (
        <p className="text-sm mt-3" style={{ color: DANGER }}>
          {installError}
        </p>
      )}
      {abortReason && (
        <p className="text-sm mt-3" style={{ color: "#b45309" }}>
          Job arrêté : {abortReason}. Les sites restants ont été ignorés pour ne pas aggraver un blocage.
        </p>
      )}

      {jobEntries.length > 0 && (
        <div className="mt-5">
          <p className="text-xs mb-2" style={mutedText}>
            {jobPlugin ? `${jobPlugin} — ` : ""}
            {finished
              ? `Terminé : ${okCount} réussi${okCount > 1 ? "s" : ""}, ${errorCount} en erreur.`
              : `${okCount + errorCount} / ${jobEntries.length} traités…`}
          </p>
          <div className="max-h-96 overflow-y-auto space-y-1">
            {jobEntries.map(([url, j]) => (
              <div
                key={url}
                className="flex items-start gap-3 text-xs py-1.5 px-2 rounded"
                style={{ background: "rgba(23,25,28,0.03)" }}
              >
                <span className="flex-1" style={{ color: "var(--color-ink)" }}>
                  {clientOf(url)}
                </span>
                <span
                  className="text-right"
                  style={{ color: j.status === "done" ? "#087A61" : j.status === "error" ? DANGER : "rgba(23,25,28,0.55)" }}
                >
                  {STATUS_LABEL[j.status]}
                  {j.message && j.status !== "done" ? ` — ${j.message}` : ""}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}
