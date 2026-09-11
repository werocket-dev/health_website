"use client";

import axios from "axios";
import { useEffect, useMemo, useState } from "react";
import { PageHeader } from "@/components/layout/PageHeader";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

type IAStatus = "ALERTE" | "ATTENTION" | "OK" | "N/A" | "";

type HealthResult = {
  url: string;
  client: string;
  ia_status: IAStatus;
  ia_score: number | null;
  ia_status_contact?: IAStatus;
  ia_score_contact?: number | null;
  updates_count: number;
  mises_a_jour?: string | null;
  wp_version?: string | null;
  php_version?: string | null;
};

type IAFilter = "TOUS" | "ALERTE" | "ATTENTION" | "OK";

type MAJFilter = "TOUTES" | "AVEC_MAJ" | "CRITIQUES" | "A_JOUR";

function getUrgency(result: HealthResult): number {
  if (result.ia_status === "ALERTE") return 3;
  if (result.updates_count >= 5) return 2;
  if (result.updates_count > 0) return 1;
  return 0;
}

function clientStyle(result: HealthResult): string {
  const urgency = getUrgency(result);
  if (urgency === 3) return "font-semibold text-red-600";
  if (urgency === 2) return "font-semibold text-orange-500";
  if (urgency === 1) return "font-medium text-yellow-600";
  return "text-[rgba(23,25,28,0.7)]";
}

function iaBadge(status: IAStatus, score: number | null) {
  const styles: Record<string, string> = {
    OK: "bg-emerald-100 text-emerald-700",
    ATTENTION: "bg-amber-100 text-amber-800",
    ALERTE: "bg-red-100 text-red-700",
  };

  const safeScore = typeof score === "number" ? `${score}%` : "—";

  return (
    <span
      className={`px-2 py-0.5 rounded-full text-xs font-medium ${styles[status] ?? "bg-slate-100 text-slate-600"}`}
    >
      {status} {safeScore}
    </span>
  );
}

function pluginDisplay(plugin: string): string {
  const sepIdx = plugin.indexOf("||");
  return sepIdx !== -1 ? plugin.slice(sepIdx + 2) : plugin;
}

function pluginPriority(plugin: string): number {
  const d = pluginDisplay(plugin);
  if (d.startsWith("🔴")) return 0;
  if (d.startsWith("🟠")) return 1;
  if (d.startsWith("🟡")) return 2;
  return 3;
}

function pluginBadgeColor(plugin: string): string {
  if (plugin.startsWith("🔴")) return "border-l-red-400 bg-red-50";
  if (plugin.startsWith("🟠")) return "border-l-orange-400 bg-orange-50";
  if (plugin.startsWith("🟡")) return "border-l-amber-400 bg-amber-50";
  return "border-l-emerald-400 bg-emerald-50";
}

export default function SanteDesSitesPage() {
  const [results, setResults] = useState<HealthResult[]>([]);
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [latestWP, setLatestWP] = useState<string | null>(null);
  const [filterIA, setFilterIA] = useState<IAFilter>("TOUS");
  const [filterMAJ, setFilterMAJ] = useState<MAJFilter>("TOUTES");
  const [apiUnavailable, setApiUnavailable] = useState<boolean>(false);
  const [updatingPlugins, setUpdatingPlugins] = useState<Record<string, "loading" | "success" | "error">>({});
  const [auditProgress, setAuditProgress] = useState<{ status: string; percent: number; label: string } | null>(null);

  useEffect(() => {
    fetch("https://api.wordpress.org/core/version-check/1.7/")
      .then((r) => r.json())
      .then((d) => setLatestWP(d?.offers?.[0]?.version ?? null))
      .catch(() => {});
  }, []);

  const fetchResults = async () => {
    try {
      const response = await axios.get(`${API_URL}/api/results`);
      const incoming = Array.isArray(response.data) ? (response.data as HealthResult[]) : [];
      const statusRank = (s: IAStatus) => s === "ALERTE" ? 0 : s === "ATTENTION" ? 1 : s === "OK" ? 2 : 3;
      const sorted = [...incoming].sort((a, b) => {
        const rankA = statusRank(a.ia_status), rankB = statusRank(b.ia_status);
        if (rankA !== rankB) return rankA - rankB;
        const scoreA = a.ia_score ?? 100, scoreB = b.ia_score ?? 100;
        if (scoreA !== scoreB) return scoreA - scoreB;
        const rankCA = statusRank(a.ia_status_contact ?? ""), rankCB = statusRank(b.ia_status_contact ?? "");
        if (rankCA !== rankCB) return rankCA - rankCB;
        const contactA = a.ia_score_contact ?? 100, contactB = b.ia_score_contact ?? 100;
        if (contactA !== contactB) return contactA - contactB;
        return b.updates_count - a.updates_count;
      });
      setResults(sorted);
    } catch (error) {
      console.error("[audit] Erreur fetch results:", error);
    }
  };

  useEffect(() => {
    if (!API_URL) { setApiUnavailable(true); return; }
    fetchResults(); // chargement immédiat
    const interval = setInterval(fetchResults, 5000);
    return () => clearInterval(interval);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Polling progression audit
  useEffect(() => {
    if (!API_URL || !auditProgress || auditProgress.status === "idle") return;
    if (auditProgress.status === "done") return;

    const interval = setInterval(async () => {
      try {
        const { data } = await axios.get(`${API_URL}/api/progress`);
        setAuditProgress(data);
        if (data.status === "done") {
          fetchResults();
          setTimeout(() => setAuditProgress(null), 3000);
        }
      } catch { /* silencieux */ }
    }, 1000);

    return () => clearInterval(interval);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auditProgress?.status]);

  const handleUpdatePlugin = async (siteUrl: string, pluginSlug: string) => {
    const key = `${siteUrl}::${pluginSlug}`;

    setUpdatingPlugins((prev) => ({ ...prev, [key]: "loading" }));
    try {
      await axios.post(`${API_URL}/api/update-plugin`, { url: siteUrl, plugin_slug: pluginSlug });
      setUpdatingPlugins((prev) => ({ ...prev, [key]: "success" }));
    } catch {
      setUpdatingPlugins((prev) => ({ ...prev, [key]: "error" }));
    }
  };

  const handleUpdateCore = async (siteUrl: string) => {
    const key = `${siteUrl}::wp-core`;

    setUpdatingPlugins((prev) => ({ ...prev, [key]: "loading" }));
    try {
      await axios.post(`${API_URL}/api/update-core`, { url: siteUrl });
      setUpdatingPlugins((prev) => ({ ...prev, [key]: "success" }));
    } catch {
      setUpdatingPlugins((prev) => ({ ...prev, [key]: "error" }));
    }
  };

  const handleLaunchAudit = async () => {
    if (!API_URL) { alert("API non configurée."); return; }
    try {
      await axios.post(`${API_URL}/api/scan`);
      setAuditProgress({ status: "running", percent: 0, label: "Démarrage de l'audit..." });
    } catch (error) {
      console.error("[audit] Erreur lancement audit:", error);
      alert("Erreur lors du lancement de l'audit");
    }
  };

  const afterIAFilter = useMemo(() => {
    if (filterIA === "TOUS") return results;
    return results.filter((r) => r.ia_status === filterIA);
  }, [filterIA, results]);

  const filteredResults = useMemo(() => {
    return afterIAFilter.filter((r) => {
      if (filterMAJ === "TOUTES") return true;
      if (filterMAJ === "AVEC_MAJ") return r.updates_count > 0;
      if (filterMAJ === "CRITIQUES")
        return !!r.mises_a_jour && r.mises_a_jour.includes("🔴");
      if (filterMAJ === "A_JOUR") return r.updates_count === 0;
      return true;
    });
  }, [afterIAFilter, filterMAJ]);

  const iaCount = (status: IAFilter) =>
    status === "TOUS"
      ? results.length
      : results.filter((r) => r.ia_status === status).length;

  const majCount = (f: MAJFilter) => {
    if (f === "TOUTES") return afterIAFilter.length;
    if (f === "AVEC_MAJ")
      return afterIAFilter.filter((r) => r.updates_count > 0).length;
    if (f === "CRITIQUES")
      return afterIAFilter.filter(
        (r) => r.mises_a_jour && r.mises_a_jour.includes("🔴"),
      ).length;
    if (f === "A_JOUR")
      return afterIAFilter.filter((r) => r.updates_count === 0).length;
    return 0;
  };

  function filterIABtn(label: string, value: IAFilter, activeClass: string) {
    const active = filterIA === value;
    return (
      <button
        key={value}
        onClick={() => {
          setFilterIA(value);
          setExpandedRow(null);
        }}
        className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors cursor-pointer ${
          active
            ? activeClass
            : "bg-[rgba(23,25,28,0.06)] text-[rgba(23,25,28,0.6)] hover:bg-[rgba(23,25,28,0.12)]"
        }`}
      >
        {label} <span className="opacity-70">({iaCount(value)})</span>
      </button>
    );
  }

  function filterMAJBtn(label: string, value: MAJFilter, activeClass: string) {
    const active = filterMAJ === value;
    return (
      <button
        key={value}
        onClick={() => {
          setFilterMAJ(value);
          setExpandedRow(null);
        }}
        className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors cursor-pointer ${
          active
            ? activeClass
            : "bg-[rgba(23,25,28,0.06)] text-[rgba(23,25,28,0.6)] hover:bg-[rgba(23,25,28,0.12)]"
        }`}
      >
        {label} <span className="opacity-70">({majCount(value)})</span>
      </button>
    );
  }

  return (
    <div className="flex flex-col h-full">
      <PageHeader title="Sante des sites" />

      <div
        className="flex-1 p-8"
        style={{
          background:
            "linear-gradient(130deg, var(--color-chalk), #ffffff 45%, var(--color-mist))",
        }}
      >
        <div className="max-w-7xl mx-auto space-y-6">
          <div
            className="rounded-2xl border shadow-sm p-6"
            style={{
              borderColor: "rgba(23,25,28,0.08)",
              background: "var(--color-white)",
              boxShadow: "0 16px 40px -30px rgba(0,0,0,0.35)",
            }}
          >
            <div className="flex flex-wrap items-center justify-between gap-4">
              <div>
                <p
                  className="text-xs font-semibold tracking-[0.24em] uppercase"
                  style={{ color: "rgba(23,25,28,0.45)" }}
                >
                  WeRocket Maintenance
                </p>
                <h2
                  className="mt-2 text-2xl font-bold"
                  style={{ color: "var(--color-ink)" }}
                >
                  Tableau de bord des audits WordPress
                </h2>
                <p
                  className="mt-1 text-sm"
                  style={{ color: "rgba(23,25,28,0.55)" }}
                >
                  Suivi IA, mises a jour et versions critiques en temps reel.
                </p>
              </div>
              <button
                onClick={handleLaunchAudit}
                className="px-6 py-3 rounded-xl font-semibold transition-transform shadow-lg hover:shadow-xl cursor-pointer"
                style={{
                  background: "var(--color-neon)",
                  color: "var(--color-ink)",
                }}
              >
                Lancer l&apos;audit global
              </button>
            </div>

            {apiUnavailable && (
              <div
                className="mt-4 rounded-xl px-4 py-3 text-sm"
                style={{
                  background: "rgba(0,55,62,0.08)",
                  color: "var(--color-deep-teal)",
                }}
              >
                API non configuree. Ajoute NEXT_PUBLIC_API_URL dans ton .env
                pour activer les audits.
              </div>
            )}
          </div>

          {auditProgress && auditProgress.status !== "idle" && (
            <div
              className="rounded-2xl border px-5 py-4"
              style={{ borderColor: "rgba(23,25,28,0.08)", background: "rgba(255,255,255,0.95)" }}
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-sm" style={{ color: "rgba(23,25,28,0.7)" }}>
                  {auditProgress.label}
                </span>
                <span
                  className="text-xs font-semibold tabular-nums"
                  style={{ color: auditProgress.status === "done" ? "#087A61" : "var(--color-deep-teal)" }}
                >
                  {auditProgress.percent}%
                </span>
              </div>
              <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "rgba(23,25,28,0.08)" }}>
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{
                    width: `${auditProgress.percent}%`,
                    background: auditProgress.status === "done"
                      ? "var(--color-neon)"
                      : "linear-gradient(90deg, var(--color-deep-teal), var(--color-neon))",
                  }}
                />
              </div>
            </div>
          )}

          {results.length > 0 && (
            <div
              className="rounded-2xl border px-6 py-4 space-y-3"
              style={{
                borderColor: "rgba(23,25,28,0.08)",
                background: "rgba(255,255,255,0.9)",
              }}
            >
              <div className="flex items-center gap-3 flex-wrap">
                <span
                  className="text-xs font-semibold uppercase tracking-wide w-28 shrink-0"
                  style={{ color: "rgba(23,25,28,0.45)" }}
                >
                  IA Accueil
                </span>
                <div className="flex gap-2 flex-wrap">
                  {filterIABtn(
                    "Tous",
                    "TOUS",
                    "bg-[var(--color-ink)] text-white",
                  )}
                  {filterIABtn("Alerte", "ALERTE", "bg-red-600 text-white")}
                  {filterIABtn(
                    "Attention",
                    "ATTENTION",
                    "bg-amber-400 text-white",
                  )}
                  {filterIABtn("OK", "OK", "bg-emerald-600 text-white")}
                </div>
              </div>
              <div
                className="flex items-center gap-3 flex-wrap border-t pt-3"
                style={{ borderColor: "rgba(23,25,28,0.06)" }}
              >
                <span
                  className="text-xs font-semibold uppercase tracking-wide w-28 shrink-0"
                  style={{ color: "rgba(23,25,28,0.45)" }}
                >
                  Mises a jour
                </span>
                <div className="flex gap-2 flex-wrap">
                  {filterMAJBtn(
                    "Toutes",
                    "TOUTES",
                    "bg-[var(--color-ink)] text-white",
                  )}
                  {filterMAJBtn(
                    "Avec MAJ",
                    "AVEC_MAJ",
                    "bg-orange-500 text-white",
                  )}
                  {filterMAJBtn(
                    "Critiques",
                    "CRITIQUES",
                    "bg-red-600 text-white",
                  )}
                  {filterMAJBtn(
                    "A jour",
                    "A_JOUR",
                    "bg-emerald-600 text-white",
                  )}
                </div>
              </div>
            </div>
          )}

          <div
            className="rounded-2xl border overflow-hidden"
            style={{
              borderColor: "rgba(23,25,28,0.08)",
              background: "rgba(255,255,255,0.9)",
            }}
          >
            <div
              className="grid grid-cols-12 gap-4 px-6 py-3 text-xs font-semibold uppercase tracking-wide"
              style={{
                background: "rgba(23,25,28,0.04)",
                color: "rgba(23,25,28,0.55)",
              }}
            >
              <div className="col-span-3">Client</div>
              <div className="col-span-2">IA Accueil</div>
              <div className="col-span-2">IA Contact</div>
              <div className="col-span-2">Mises a jour</div>
              <div className="col-span-3">Versions</div>
            </div>

            {results.length === 0 ? (
              <div
                className="px-6 py-12 text-center"
                style={{ color: "rgba(23,25,28,0.4)" }}
              >
                <p>
                  Aucune donnee. Lancez un audit pour afficher les resultats.
                </p>
              </div>
            ) : filteredResults.length === 0 ? (
              <div
                className="px-6 py-12 text-center"
                style={{ color: "rgba(23,25,28,0.4)" }}
              >
                <p>Aucun resultat pour cette combinaison de filtres.</p>
              </div>
            ) : (
              filteredResults.map((result) => {
                const rowKey = result.url;
                const isExpanded = expandedRow === rowKey;
                const plugins =
                  result.mises_a_jour && result.mises_a_jour !== "Aucune"
                    ? result.mises_a_jour
                        .split(", ")
                        .filter(Boolean)
                        .sort((a, b) => pluginPriority(a) - pluginPriority(b))
                    : [];

                return (
                  <div
                    key={rowKey}
                    className="border-b last:border-0"
                    style={{ borderColor: "rgba(23,25,28,0.06)" }}
                  >
                    <div className="grid grid-cols-12 gap-4 px-6 py-4 text-sm items-center transition-colors hover:bg-[rgba(23,25,28,0.03)]">
                      <div className={`col-span-3 ${clientStyle(result)}`}>
                        {result.client}
                      </div>
                      <div className="col-span-2">
                        {iaBadge(result.ia_status, result.ia_score)}
                      </div>
                      <div className="col-span-2">
                        {result.ia_status_contact === "N/A" ||
                        !result.ia_status_contact ? (
                          <span
                            className="text-xs"
                            style={{ color: "rgba(23,25,28,0.35)" }}
                          >
                            —
                          </span>
                        ) : (
                          iaBadge(
                            result.ia_status_contact,
                            result.ia_score_contact ?? null,
                          )
                        )}
                      </div>
                      <div className="col-span-2">
                        {result.updates_count > 0 ? (
                          <button
                            onClick={() =>
                              setExpandedRow(isExpanded ? null : rowKey)
                            }
                            className="flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium transition-colors cursor-pointer"
                            style={{
                              background: "rgba(0,55,62,0.08)",
                              color: "var(--color-deep-teal)",
                            }}
                          >
                            {result.updates_count} mise
                            {result.updates_count > 1 ? "s" : ""} a jour
                            <span style={{ color: "rgba(0,55,62,0.5)" }}>
                              {isExpanded ? "▲" : "▼"}
                            </span>
                          </button>
                        ) : (
                          <span
                            className="px-3 py-1 rounded-full text-xs font-medium"
                            style={{
                              background: "rgba(0,226,158,0.12)",
                              color: "#087A61",
                            }}
                          >
                            A jour
                          </span>
                        )}
                      </div>
                      <div className="col-span-3 text-xs space-y-1">
                        <div className="flex items-center gap-1.5">
                          <span
                            className="font-medium"
                            style={{ color: "rgba(23,25,28,0.75)" }}
                          >
                            WP
                          </span>
                          <span style={{ color: "rgba(23,25,28,0.6)" }}>
                            {result.wp_version}
                          </span>
                          {latestWP &&
                            result.wp_version &&
                            result.wp_version !== "N/A" &&
                            result.wp_version !== latestWP && (
                              <>
                                <span className="text-orange-500">
                                  → {latestWP}
                                </span>
                                {(() => {
                                  const coreKey = `${result.url}::wp-core`;
                                  const coreState = updatingPlugins[coreKey];
                                  return (
                                    <button
                                      onClick={() => handleUpdateCore(result.url)}
                                      disabled={!!coreState}
                                      className="shrink-0 px-2 py-0.5 rounded text-xs font-medium transition-colors cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed"
                                      style={{
                                        background: coreState === "success" ? "rgba(0,226,158,0.15)" : coreState === "error" ? "rgba(220,38,38,0.1)" : "rgba(0,55,62,0.1)",
                                        color: coreState === "success" ? "#087A61" : coreState === "error" ? "#dc2626" : "var(--color-deep-teal)",
                                      }}
                                    >
                                      {coreState === "loading" ? "..." : coreState === "success" ? "✓ OK" : coreState === "error" ? "✗ Erreur" : "Mettre à jour"}
                                    </button>
                                  );
                                })()}
                              </>
                            )}
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span
                            className="font-medium"
                            style={{ color: "rgba(23,25,28,0.75)" }}
                          >
                            PHP
                          </span>
                          <span style={{ color: "rgba(23,25,28,0.6)" }}>
                            {result.php_version}
                          </span>
                        </div>
                      </div>
                    </div>

                    {isExpanded && plugins.length > 0 && (
                      <div className="px-6 pb-4 space-y-1.5">
                        <p
                          className="text-xs font-semibold uppercase tracking-wide mb-2"
                          style={{ color: "rgba(23,25,28,0.45)" }}
                        >
                          Plugins a mettre a jour
                        </p>
                        {plugins.map((plugin, i) => {
                          const sepIdx = plugin.indexOf("||");
                          const pluginSlug = sepIdx !== -1 ? plugin.slice(0, sepIdx) : "";
                          const displayText = sepIdx !== -1 ? plugin.slice(sepIdx + 2) : plugin;
                          const key = `${rowKey}::${pluginSlug}`;
                          const updateState = updatingPlugins[key];
                          const canUpdate = !!pluginSlug && displayText.includes("→");

                          return (
                            <div
                              key={`${rowKey}-${i}`}
                              className={`flex items-center justify-between text-xs py-1.5 px-3 border-l-4 rounded-r ${pluginBadgeColor(displayText)}`}
                              style={{ color: "rgba(23,25,28,0.8)" }}
                            >
                              <span className="flex-1 mr-3">{displayText}</span>
                              {canUpdate && (
                                <button
                                  onClick={() => handleUpdatePlugin(result.url, pluginSlug)}
                                  disabled={!!updateState}
                                  className="shrink-0 px-2 py-0.5 rounded text-xs font-medium transition-colors cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed"
                                  style={{
                                    background: updateState === "success" ? "rgba(0,226,158,0.15)" : updateState === "error" ? "rgba(220,38,38,0.1)" : "rgba(0,55,62,0.1)",
                                    color: updateState === "success" ? "#087A61" : updateState === "error" ? "#dc2626" : "var(--color-deep-teal)",
                                  }}
                                >
                                  {updateState === "loading" ? "..." : updateState === "success" ? "✓ OK" : updateState === "error" ? "✗ Erreur" : "Mettre à jour"}
                                </button>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
