"use client";

import axios from "axios";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/layout/PageHeader";

const API_URL = process.env.NEXT_PUBLIC_API_URL;

type IAStatus = "ALERTE" | "ATTENTION" | "OK" | "N/A" | "";

type LicenseInfo = {
  valid: boolean | null;
  status?: string;
  item?: string;
  expires?: string;
};

type ThemeInfo = {
  slug: string;
  name: string;
  version: string;
  is_active: boolean;
  is_parent_of_active: boolean;
};

type HealthResult = {
  url: string;
  client: string;
  ia_status: IAStatus;
  ia_score: number | null;
  ia_status_contact?: IAStatus;
  ia_score_contact?: number | null;
  ia_status_mobile?: IAStatus;
  ia_score_mobile?: number | null;
  updates_count: number;
  mises_a_jour?: string | null;
  wp_version?: string | null;
  php_version?: string | null;
  builder?: string | null;
  licenses?: Record<string, LicenseInfo>;
  themes?: ThemeInfo[];
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

const PER_PAGE = 25;

export default function SanteDesSitesPage() {
  const [results, setResults] = useState<HealthResult[]>([]);
  const [resultsLoading, setResultsLoading] = useState(true);
  const [expandedRow, setExpandedRow] = useState<string | null>(null);
  const [latestWP, setLatestWP] = useState<string | null>(null);
  const [filterIA, setFilterIA] = useState<IAFilter>("TOUS");
  const [filterMAJ, setFilterMAJ] = useState<MAJFilter>("TOUTES");
  const [apiUnavailable, setApiUnavailable] = useState<boolean>(false);
  const [updatingPlugins, setUpdatingPlugins] = useState<Record<string, "loading" | "success" | "error">>({});
  const [deletingThemes, setDeletingThemes] = useState<Record<string, "loading" | "success" | "error">>({});
  const [auditProgress, setAuditProgress] = useState<{ status: string; percent: number; label: string } | null>(null);
  const [launchingAudit, setLaunchingAudit] = useState(false);
  const [auditError, setAuditError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [searchInput, setSearchInput] = useState("");
  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalResults, setTotalResults] = useState(0);
  const [stats, setStats] = useState<{
    ia_status: { ok: number; attention: number; alerte: number };
    maj_status: { toutes: number; avec_maj: number; critiques: number; a_jour: number };
    total_sites: number;
  } | null>(null);

  const iaStatusParam = (f: IAFilter) => (f === "TOUS" ? "" : f);
  const majParam = (f: MAJFilter) =>
    f === "TOUTES" ? "" : f === "AVEC_MAJ" ? "avec_maj" : f === "CRITIQUES" ? "critiques" : "a_jour";

  // Debounce : on n'interroge l'API que 400ms après la dernière frappe
  useEffect(() => {
    const t = setTimeout(() => {
      setSearch(searchInput);
      setPage(1);
    }, 400);
    return () => clearTimeout(t);
  }, [searchInput]);

  useEffect(() => {
    fetch("https://api.wordpress.org/core/version-check/1.7/")
      .then((r) => r.json())
      .then((d) => setLatestWP(d?.offers?.[0]?.version ?? null))
      .catch(() => {});
  }, []);

  // Synchronise l'état au chargement — un audit peut déjà tourner depuis un
  // autre onglet, il faut le savoir tout de suite plutôt qu'après un clic.
  useEffect(() => {
    if (!API_URL) return;
    axios
      .get(`${API_URL}/api/progress`)
      .then(({ data }) => {
        if (data.status === "running") setAuditProgress(data);
      })
      .catch(() => {});
  }, []);

  const fetchStats = async () => {
    try {
      const response = await axios.get(`${API_URL}/api/stats`);
      setStats(response.data);
    } catch (error) {
      console.error("[audit] Erreur fetch stats:", error);
    }
  };

  const fetchResults = async () => {
    try {
      const response = await axios.get(`${API_URL}/api/results`, {
        params: {
          page,
          per_page: PER_PAGE,
          search,
          ia_status: iaStatusParam(filterIA),
          maj: majParam(filterMAJ),
        },
      });
      const incoming: HealthResult[] = Array.isArray(response.data?.items) ? response.data.items : [];
      setTotalPages(response.data?.total_pages ?? 1);
      setTotalResults(response.data?.total ?? incoming.length);

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
        const rankMA = statusRank(a.ia_status_mobile ?? ""), rankMB = statusRank(b.ia_status_mobile ?? "");
        if (rankMA !== rankMB) return rankMA - rankMB;
        const mobileA = a.ia_score_mobile ?? 100, mobileB = b.ia_score_mobile ?? 100;
        if (mobileA !== mobileB) return mobileA - mobileB;
        return b.updates_count - a.updates_count;
      });
      setResults(sorted);
    } catch (error) {
      console.error("[audit] Erreur fetch results:", error);
    } finally {
      setResultsLoading(false);
    }
  };

  useEffect(() => {
    if (!API_URL) { setApiUnavailable(true); return; }
    fetchResults(); // chargement immédiat
    const interval = setInterval(fetchResults, 5000);
    return () => clearInterval(interval);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page, search, filterIA, filterMAJ]);

  useEffect(() => {
    if (!API_URL) return;
    fetchStats();
    const interval = setInterval(fetchStats, 5000);
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

  const handleDeleteTheme = async (siteUrl: string, themeSlug: string, themeName: string) => {
    const confirmed = window.confirm(
      `Supprimer définitivement le thème "${themeName}" sur ce site ? Cette action est irréversible.`
    );
    if (!confirmed) return;

    const key = `${siteUrl}::theme::${themeSlug}`;
    setDeletingThemes((prev) => ({ ...prev, [key]: "loading" }));
    try {
      await axios.post(`${API_URL}/api/delete-theme`, { url: siteUrl, theme_slug: themeSlug });
      setDeletingThemes((prev) => ({ ...prev, [key]: "success" }));
    } catch {
      setDeletingThemes((prev) => ({ ...prev, [key]: "error" }));
    }
  };

  const auditRunning = launchingAudit || (!!auditProgress && auditProgress.status === "running");

  const handleLaunchAudit = async (limit?: number, randomSample?: boolean) => {
    if (!API_URL) { setAuditError("API non configurée."); return; }
    if (auditRunning) return; // Anti double-clic : un audit tourne déjà

    setAuditError(null);
    setLaunchingAudit(true);
    // Retour visuel immédiat au clic, avant même la réponse du serveur —
    // sans ça rien ne bougeait tant que la requête n'avait pas répondu,
    // ce qui poussait à cliquer plusieurs fois.
    setAuditProgress({ status: "running", percent: 0, label: "Lancement de l'audit..." });
    try {
      await axios.post(`${API_URL}/api/scan`, limit ? { limit, random_sample: !!randomSample } : {});
    } catch (error) {
      console.error("[audit] Erreur lancement audit:", error);
      setAuditProgress(null);
      const isConflict = axios.isAxiosError(error) && error.response?.status === 409;
      setAuditError(
        isConflict
          ? "Un audit est déjà en cours (peut-être depuis un autre onglet) — attends qu'il se termine."
          : "Erreur lors du lancement de l'audit (le serveur ne répond pas ?)"
      );
    } finally {
      setLaunchingAudit(false);
    }
  };

  // Les filtres IA/MAJ sont appliqués côté serveur (voir fetchResults) —
  // ces compteurs viennent de /api/stats, calculés sur tout le parc, pas
  // seulement sur la page affichée.
  const iaCount = (status: IAFilter) =>
    status === "TOUS" ? stats?.total_sites ?? 0 : stats?.ia_status[status.toLowerCase() as "ok" | "attention" | "alerte"] ?? 0;

  const majCount = (f: MAJFilter) => {
    if (f === "TOUTES") return stats?.maj_status.toutes ?? 0;
    if (f === "AVEC_MAJ") return stats?.maj_status.avec_maj ?? 0;
    if (f === "CRITIQUES") return stats?.maj_status.critiques ?? 0;
    if (f === "A_JOUR") return stats?.maj_status.a_jour ?? 0;
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
          setPage(1);
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
          setPage(1);
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
    <div className="flex flex-col h-screen">
      <PageHeader
        title="Sante des sites"
        nav={[
          { label: "Résultats", href: "/" },
          { label: "Récap", href: "/recap" },
        ]}
      />

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
              <div className="flex items-center gap-3">
                <button
                  onClick={() => handleLaunchAudit(10, true)}
                  disabled={auditRunning}
                  className="px-4 py-3 rounded-xl font-medium text-sm transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
                  style={{
                    background: "rgba(23,25,28,0.06)",
                    color: "var(--color-ink)",
                  }}
                  title="Lance l'audit sur 10 sites tirés au hasard dans le parc, pour tester"
                >
                  {auditRunning ? "Audit en cours..." : "Audit test (10 sites aléatoires)"}
                </button>
                <button
                  onClick={() => handleLaunchAudit()}
                  disabled={auditRunning}
                  className="px-6 py-3 rounded-xl font-semibold transition-transform shadow-lg hover:shadow-xl cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed disabled:shadow-none"
                  style={{
                    background: "var(--color-neon)",
                    color: "var(--color-ink)",
                  }}
                >
                  {auditRunning ? "Audit en cours..." : "Lancer l'audit global"}
                </button>
              </div>
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

            {auditError && (
              <div
                className="mt-4 rounded-xl px-4 py-3 text-sm flex items-center justify-between gap-3"
                style={{
                  background: "rgba(220,38,38,0.08)",
                  color: "#dc2626",
                }}
              >
                <span>{auditError}</span>
                <button
                  onClick={() => setAuditError(null)}
                  className="shrink-0 cursor-pointer font-medium"
                >
                  Fermer
                </button>
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

          <div
            className="rounded-2xl border px-6 py-4"
            style={{
              borderColor: "rgba(23,25,28,0.08)",
              background: "rgba(255,255,255,0.9)",
            }}
          >
            <input
              type="text"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Rechercher un client ou une URL..."
              className="w-full max-w-sm px-4 py-2 rounded-xl text-sm outline-none border"
              style={{
                borderColor: "rgba(23,25,28,0.12)",
                background: "rgba(23,25,28,0.02)",
                color: "var(--color-ink)",
              }}
            />
          </div>

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
              className="flex gap-4 px-6 py-3 text-xs font-semibold uppercase tracking-wide"
              style={{
                background: "rgba(23,25,28,0.04)",
                color: "rgba(23,25,28,0.55)",
              }}
            >
              <div className="flex-1 min-w-0">Client</div>
              <div className="flex-1 min-w-0">IA Accueil</div>
              <div className="flex-1 min-w-0">IA Contact</div>
              <div className="flex-1 min-w-0">IA Mobile</div>
              <div className="flex-1 min-w-0">Mises a jour</div>
              <div className="flex-3 min-w-0">Versions</div>
            </div>

            {resultsLoading ? (
              <div
                className="px-6 py-12 text-center"
                style={{ color: "rgba(23,25,28,0.4)" }}
              >
                <p>Chargement...</p>
              </div>
            ) : totalResults === 0 ? (
              <div
                className="px-6 py-12 text-center"
                style={{ color: "rgba(23,25,28,0.4)" }}
              >
                <p>
                  {search || filterIA !== "TOUS" || filterMAJ !== "TOUTES"
                    ? "Aucun resultat pour cette combinaison de filtres."
                    : "Aucune donnee. Lancez un audit pour afficher les resultats."}
                </p>
              </div>
            ) : (
              results.map((result) => {
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
                    <div className="flex gap-4 px-6 py-4 text-sm items-center transition-colors hover:bg-[rgba(23,25,28,0.03)]">
                      <div className="flex-1 min-w-0">
                        <a
                          href={
                            result.url.startsWith("http")
                              ? result.url
                              : `https://${result.url}`
                          }
                          target="_blank"
                          rel="noopener noreferrer"
                          className={`inline-flex items-center gap-1.5 hover:underline ${clientStyle(result)}`}
                        >
                          {result.client}
                          <svg
                            width="12"
                            height="12"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="2"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            className="shrink-0 opacity-50"
                          >
                            <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
                            <path d="M15 3h6v6" />
                            <path d="M10 14 21 3" />
                          </svg>
                        </a>
                      </div>
                      <div className="flex-1 min-w-0">
                        {iaBadge(result.ia_status, result.ia_score)}
                      </div>
                      <div className="flex-1 min-w-0">
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
                      <div className="flex-1 min-w-0">
                        {result.ia_status_mobile === "N/A" ||
                        !result.ia_status_mobile ? (
                          <span
                            className="text-xs"
                            style={{ color: "rgba(23,25,28,0.35)" }}
                          >
                            —
                          </span>
                        ) : (
                          iaBadge(
                            result.ia_status_mobile,
                            result.ia_score_mobile ?? null,
                          )
                        )}
                      </div>
                      <div className="flex-1 min-w-0">
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
                      <div className="flex-3 min-w-0 text-xs space-y-1">
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
                        {result.builder && result.builder !== "Inconnu" && (
                          <div className="flex items-center gap-1.5">
                            <span
                              className="font-medium"
                              style={{ color: "rgba(23,25,28,0.75)" }}
                            >
                              Builder
                            </span>
                            <span style={{ color: "rgba(23,25,28,0.6)" }}>
                              {result.builder}
                            </span>
                          </div>
                        )}
                        {result.licenses &&
                          Object.entries(result.licenses).map(([key, lic]) => (
                            <div key={key} className="flex items-center gap-1.5 whitespace-nowrap">
                              <span
                                className="font-medium capitalize"
                                style={{ color: "rgba(23,25,28,0.75)" }}
                              >
                                {key}
                              </span>
                              <span
                                style={{
                                  color:
                                    lic.valid === true
                                      ? "#087A61"
                                      : lic.valid === false
                                      ? "#dc2626"
                                      : "rgba(23,25,28,0.5)",
                                }}
                              >
                                {lic.valid === true
                                  ? "✓ Active"
                                  : lic.valid === false
                                  ? "✗ Inactive"
                                  : "⚠ À vérifier"}
                              </span>
                              {lic.expires && (
                                <span style={{ color: "rgba(23,25,28,0.5)" }}>
                                  jusqu&apos;au{" "}
                                  {lic.expires
                                    .split(" ")[0]
                                    .split("-")
                                    .reverse()
                                    .join("/")}
                                </span>
                              )}
                            </div>
                          ))}
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
                    {isExpanded && result.themes && result.themes.length > 0 && (
                      <div className="px-6 pb-4 space-y-1.5">
                        <p
                          className="text-xs font-semibold uppercase tracking-wide mb-2"
                          style={{ color: "rgba(23,25,28,0.45)" }}
                        >
                          Thèmes installés ({result.themes.length})
                        </p>
                        {result.themes.map((theme) => {
                          const key = `${result.url}::theme::${theme.slug}`;
                          const state = deletingThemes[key];
                          const canDelete = !theme.is_active && !theme.is_parent_of_active;

                          return (
                            <div
                              key={key}
                              className="flex items-center justify-between text-xs py-1.5 px-3 border-l-4 rounded-r"
                              style={{
                                color: "rgba(23,25,28,0.8)",
                                borderColor: theme.is_active ? "#10b981" : "rgba(23,25,28,0.15)",
                                background: theme.is_active ? "rgba(16,185,129,0.06)" : "rgba(23,25,28,0.02)",
                              }}
                            >
                              <span className="flex-1 mr-3">
                                {theme.name} ({theme.version}){theme.is_active && " — actif"}
                                {theme.is_parent_of_active && " — parent du thème actif"}
                              </span>
                              {canDelete && (
                                <button
                                  onClick={() => handleDeleteTheme(result.url, theme.slug, theme.name)}
                                  disabled={!!state}
                                  className="shrink-0 px-2 py-0.5 rounded text-xs font-medium transition-colors cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed"
                                  style={{
                                    background: state === "success" ? "rgba(0,226,158,0.15)" : state === "error" ? "rgba(220,38,38,0.1)" : "rgba(220,38,38,0.08)",
                                    color: state === "success" ? "#087A61" : "#dc2626",
                                  }}
                                >
                                  {state === "loading" ? "..." : state === "success" ? "✓ Supprimé" : state === "error" ? "✗ Erreur" : "Supprimer"}
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

          {totalResults > 0 && (
            <div className="flex items-center justify-between gap-4 px-2 text-sm">
              <span style={{ color: "rgba(23,25,28,0.55)" }}>
                {totalResults} site{totalResults > 1 ? "s" : ""} — page {page} / {totalPages}
              </span>
              <div className="flex gap-2">
                <button
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1}
                  className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                  style={{ background: "rgba(23,25,28,0.06)", color: "var(--color-ink)" }}
                >
                  ← Précédent
                </button>
                <button
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages}
                  className="px-3 py-1.5 rounded-lg text-xs font-medium transition-colors cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                  style={{ background: "rgba(23,25,28,0.06)", color: "var(--color-ink)" }}
                >
                  Suivant →
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
