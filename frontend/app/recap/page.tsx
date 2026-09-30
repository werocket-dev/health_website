"use client";

import axios from "axios";
import { useEffect, useState } from "react";
import { PageHeader } from "@/components/layout/PageHeader";
import { LogoutButton } from "@/components/layout/LogoutButton";

const API_URL = "/api/backend";

type IAFaibleSite = {
  client: string;
  url: string;
  scores: Record<string, number>;
};

type PluginSite = {
  client: string;
  url: string;
  risk: string;
};

type PluginFrequency = {
  plugin_slug: string;
  plugin_name: string;
  sites_count: number;
  risk_counts: Record<string, number>;
  sites: PluginSite[];
};

type PhpObsoleteSite = {
  client: string;
  url: string;
  php_version: string;
};

type BreakdanceLicenseSite = {
  client: string;
  url: string;
  valid: boolean | null;
  plugin_version: string | null;
};

type BreakdanceZip = {
  filename: string;
  size: number;
  sha256: string;
  version: string;
  uploaded_at: string;
};

type BreakdanceSiteJob = {
  status: "pending" | "installing" | "installed" | "activating" | "done" | "error";
  stage?: "install" | "activation";
  message?: string;
  old_version?: string | null;
  new_version?: string | null;
};

const BREAKDANCE_STATUS_LABEL: Record<BreakdanceSiteJob["status"], string> = {
  pending: "En attente",
  installing: "Installation…",
  installed: "Installé",
  activating: "Activation…",
  done: "✓ Installé et activé",
  error: "✗ Erreur",
};

function formatSize(bytes: number): string {
  return `${(bytes / 1024 / 1024).toFixed(1)} Mo`;
}

type ThemeInfo = {
  slug: string;
  name: string;
  version: string;
  is_active: boolean;
  is_parent_of_active: boolean;
};

type TooManyThemesSite = {
  client: string;
  url: string;
  themes_count: number;
  themes: ThemeInfo[];
};

type RecapData = {
  total_sites: number;
  ia_faible: IAFaibleSite[];
  plugin_frequency: PluginFrequency[];
  php_obsolete: PhpObsoleteSite[];
  methode_counts: Record<string, number>;
  breakdance_licenses_a_verifier: BreakdanceLicenseSite[];
  sites_trop_de_themes: TooManyThemesSite[];
  last_audit_finished_at: string | null;
};

const RISK_LABELS: Record<string, string> = {
  CRITIQUE: "Critique",
  ÉLEVÉ: "Élevé",
  MOYEN: "Moyen",
  FAIBLE: "Faible",
  INCONNU: "Inconnu",
  AUCUN: "Aucun",
};

function riskColor(risk: string): { bg: string; color: string } {
  switch (risk) {
    case "CRITIQUE":
      return { bg: "rgba(220,38,38,0.1)", color: "#dc2626" };
    case "ÉLEVÉ":
      return { bg: "rgba(234,88,12,0.1)", color: "#ea580c" };
    case "MOYEN":
      return { bg: "rgba(217,119,6,0.1)", color: "#d97706" };
    case "FAIBLE":
      return { bg: "rgba(0,226,138,0.12)", color: "#087A61" };
    default:
      return { bg: "rgba(23,25,28,0.06)", color: "rgba(23,25,28,0.55)" };
  }
}

function formatLastAudit(finishedAt: string | null): string {
  if (!finishedAt) return "aucun audit pour l'instant";
  // "YYYY-MM-DD HH:MM:SS" écrit en UTC côté backend — on le parse comme tel
  // pour afficher l'heure locale du navigateur, pas l'heure serveur brute.
  const date = new Date(finishedAt.replace(" ", "T") + "Z");
  if (Number.isNaN(date.getTime())) return "aucun audit pour l'instant";
  return date.toLocaleString("fr-FR", { dateStyle: "long", timeStyle: "short" });
}

function scoreLabel(key: string): string {
  if (key === "accueil") return "Accueil";
  if (key === "contact") return "Contact";
  if (key === "mobile") return "Mobile";
  return key;
}

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div
      className="rounded-2xl border shadow-sm p-6"
      style={{
        borderColor: "rgba(23,25,28,0.08)",
        background: "var(--color-white)",
        boxShadow: "0 16px 40px -30px rgba(0,0,0,0.35)",
      }}
    >
      <h2 className="text-sm font-semibold uppercase tracking-wide mb-4" style={{ color: "rgba(23,25,28,0.5)" }}>
        {title}
      </h2>
      {children}
    </div>
  );
}

export default function RecapPage() {
  const [data, setData] = useState<RecapData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);
  const [expandedPlugin, setExpandedPlugin] = useState<string | null>(null);
  const [expandedThemesSite, setExpandedThemesSite] = useState<string | null>(null);
  const [selectedSites, setSelectedSites] = useState<Record<string, Set<string>>>({});
  const [updateStatus, setUpdateStatus] = useState<Record<string, "loading" | "success" | "error">>({});

  const handleDeleteTheme = async (siteUrl: string, themeSlug: string, themeName: string) => {
    const confirmed = window.confirm(
      `Supprimer définitivement le thème "${themeName}" sur ce site ? Cette action est irréversible.`
    );
    if (!confirmed) return;

    const key = `${siteUrl}::theme::${themeSlug}`;
    setUpdateStatus((prev) => ({ ...prev, [key]: "loading" }));
    try {
      await axios.post(`${API_URL}/delete-theme`, { url: siteUrl, theme_slug: themeSlug });
      setUpdateStatus((prev) => ({ ...prev, [key]: "success" }));
    } catch {
      setUpdateStatus((prev) => ({ ...prev, [key]: "error" }));
    }
  };

  const handleBulkDeleteThemes = async (siteUrl: string) => {
    const slugs = Array.from(selectedSites[siteUrl] ?? []);
    if (slugs.length === 0) return;

    const confirmed = window.confirm(
      `Supprimer définitivement ces ${slugs.length} thème(s) sur ce site ? Cette action est irréversible.`
    );
    if (!confirmed) return;

    for (const slug of slugs) {
      const key = `${siteUrl}::theme::${slug}`;
      setUpdateStatus((prev) => ({ ...prev, [key]: "loading" }));
      try {
        await axios.post(`${API_URL}/delete-theme`, { url: siteUrl, theme_slug: slug });
        setUpdateStatus((prev) => ({ ...prev, [key]: "success" }));
      } catch {
        setUpdateStatus((prev) => ({ ...prev, [key]: "error" }));
      }
    }
  };

  const toggleSite = (pluginSlug: string, url: string) => {
    setSelectedSites((prev) => {
      const current = new Set(prev[pluginSlug] ?? []);
      if (current.has(url)) current.delete(url);
      else current.add(url);
      return { ...prev, [pluginSlug]: current };
    });
  };

  const toggleAllSites = (pluginSlug: string, urls: string[]) => {
    setSelectedSites((prev) => {
      const current = prev[pluginSlug] ?? new Set<string>();
      const allSelected = urls.every((u) => current.has(u));
      return { ...prev, [pluginSlug]: new Set(allSelected ? [] : urls) };
    });
  };

  const handleBulkUpdate = async (pluginSlug: string) => {
    const urls = Array.from(selectedSites[pluginSlug] ?? []);
    if (urls.length === 0) return;

    for (const url of urls) {
      const key = `${url}::${pluginSlug}`;
      setUpdateStatus((prev) => ({ ...prev, [key]: "loading" }));
      try {
        await axios.post(`${API_URL}/update-plugin`, { url, plugin_slug: pluginSlug });
        setUpdateStatus((prev) => ({ ...prev, [key]: "success" }));
      } catch {
        setUpdateStatus((prev) => ({ ...prev, [key]: "error" }));
      }
    }
  };

  const [showLicenseModal, setShowLicenseModal] = useState(false);
  const [licenseKeyInput, setLicenseKeyInput] = useState("");
  const [bdZip, setBdZip] = useState<BreakdanceZip | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [bdJob, setBdJob] = useState<Record<string, BreakdanceSiteJob>>({});
  const [bdRunning, setBdRunning] = useState(false);
  const [bdError, setBdError] = useState<string | null>(null);

  useEffect(() => {
    axios
      .get<{ zip: BreakdanceZip | null }>(`${API_URL}/breakdance/zip`)
      .then(({ data }) => setBdZip(data.zip))
      .catch(() => {});
  }, []);

  const handleUploadZip = async (file: File) => {
    setUploadError(null);
    setUploadProgress(0);
    try {
      // Corps brut (pas de multipart) vers la route dédiée, qui relaie le flux au backend.
      const { data } = await axios.post<BreakdanceZip>("/api/breakdance-upload", file, {
        headers: { "Content-Type": "application/octet-stream", "X-Filename": file.name },
        onUploadProgress: (e) => e.total && setUploadProgress(Math.round((e.loaded / e.total) * 100)),
      });
      setBdZip(data);
    } catch (e) {
      const detail = axios.isAxiosError(e) ? e.response?.data?.detail : null;
      setUploadError(typeof detail === "string" ? detail : "Échec de l'upload");
    } finally {
      setUploadProgress(null);
    }
  };

  const handleInstallAndActivate = async () => {
    const urls = Array.from(selectedSites["breakdance-license"] ?? []);
    if (urls.length === 0 || !bdZip) return;

    setBdError(null);
    setBdRunning(true);
    setBdJob(Object.fromEntries(urls.map((u) => [u, { status: "pending" as const }])));
    setShowLicenseModal(false);
    try {
      const key = licenseKeyInput.trim() || null;
      const { data } = await axios.post<{ job_id: string }>(`${API_URL}/breakdance/install-and-activate`, {
        sites: urls.map((url) => ({ url, license_key: key })),
      });
      setLicenseKeyInput("");

      // Le job dure plusieurs minutes : on suit sa progression par polling.
      let finished = false;
      while (!finished) {
        await new Promise((r) => setTimeout(r, 3000));
        try {
          const { data: job } = await axios.get<{ finished: boolean; sites: Record<string, BreakdanceSiteJob> }>(
            `${API_URL}/breakdance/jobs/${data.job_id}`
          );
          setBdJob(job.sites);
          finished = job.finished;
        } catch (e) {
          // Job perdu (backend redémarré) : inutile de boucler indéfiniment.
          if (axios.isAxiosError(e) && e.response?.status === 404) throw e;
        }
      }
      setRetryCount((c) => c + 1); // recharge le récap avec les nouveaux statuts
    } catch (e) {
      const detail = axios.isAxiosError(e) ? e.response?.data?.detail : null;
      setBdError(typeof detail === "string" ? detail : "Échec du lancement de l'installation");
    } finally {
      setBdRunning(false);
    }
  };

  useEffect(() => {
    setLoading(true);
    setError(null);
    axios
      .get<RecapData>(`${API_URL}/recap`)
      .then(({ data }) => {
        setData(data);
      })
      .catch(() => {
        setError(
          "Impossible de charger le récap (souvent un souci réseau passager vers PocketBase — réessaie dans quelques secondes)."
        );
      })
      .finally(() => setLoading(false));
  }, [retryCount]);

  return (
    <div className="flex flex-col h-screen">
      <PageHeader
        title="Récap du parc"
        nav={[
          { label: "Résultats", href: "/" },
          { label: "Récap", href: "/recap" },
        ]}
        actions={<LogoutButton />}
      />

      <div
        className="flex-1 p-8 overflow-y-auto"
        style={{
          background: "linear-gradient(130deg, var(--color-chalk), #ffffff 45%, var(--color-mist))",
        }}
      >
        <div className="max-w-7xl mx-auto space-y-6">
          {loading && <p style={{ color: "rgba(23,25,28,0.5)" }}>Chargement...</p>}
          {error && (
            <div className="flex items-center gap-3">
              <p style={{ color: "#dc2626" }}>{error}</p>
              <button
                onClick={() => setRetryCount((n) => n + 1)}
                className="px-3 py-1.5 rounded-full text-sm font-medium cursor-pointer"
                style={{ background: "rgba(23,25,28,0.06)", color: "var(--color-ink)" }}
              >
                Réessayer
              </button>
            </div>
          )}

          {data && (
            <>
              <Card title="Dernier audit">
                <p className="text-xs mb-3" style={{ color: "rgba(23,25,28,0.5)" }}>
                  Terminé le {formatLastAudit(data.last_audit_finished_at)}
                </p>
                <div className="flex gap-8">
                  <div>
                    <p className="text-3xl font-semibold" style={{ color: "var(--color-ink)" }}>
                      {data.total_sites}
                    </p>
                    <p className="text-xs" style={{ color: "rgba(23,25,28,0.5)" }}>
                      sites analysés dans ce récap
                    </p>
                  </div>
                  <div>
                    <p className="text-3xl font-semibold" style={{ color: "var(--color-ink)" }}>
                      {data.methode_counts["Agent"] ?? 0}
                      <span className="text-lg font-normal" style={{ color: "rgba(23,25,28,0.4)" }}>
                        {" "}
                        / {data.total_sites}
                      </span>
                    </p>
                    <p className="text-xs" style={{ color: "rgba(23,25,28,0.5)" }}>
                      sites joignables via l&apos;Agent (reste : scraping)
                    </p>
                  </div>
                </div>
              </Card>

              <Card title={`Sites avec un score IA faible (< 70%) — ${data.ia_faible.length}`}>
                {data.ia_faible.length === 0 ? (
                  <p className="text-sm" style={{ color: "rgba(23,25,28,0.5)" }}>
                    Aucun site en dessous de 70%.
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    {data.ia_faible.map((site) => (
                      <div
                        key={site.url}
                        className="flex items-center justify-between text-sm py-1.5 px-3 border-l-4 rounded-r"
                        style={{ borderColor: "#dc2626", background: "rgba(220,38,38,0.04)" }}
                      >
                        <a
                          href={site.url}
                          target="_blank"
                          rel="noreferrer"
                          className="font-medium hover:underline"
                          style={{ color: "var(--color-deep-teal)" }}
                        >
                          {site.client}
                        </a>
                        <div className="flex gap-3">
                          {Object.entries(site.scores).map(([key, score]) => (
                            <span key={key} style={{ color: "rgba(23,25,28,0.6)" }}>
                              {scoreLabel(key)} <strong style={{ color: "#dc2626" }}>{score}%</strong>
                            </span>
                          ))}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Card>

              <Card title="Plugins nécessitant une mise à jour, par fréquence">
                <div className="space-y-1.5">
                  {data.plugin_frequency.slice(0, 15).map((p) => {
                    const isExpanded = expandedPlugin === p.plugin_slug;
                    const selected = selectedSites[p.plugin_slug] ?? new Set<string>();
                    const urls = p.sites.map((s) => s.url);
                    const allSelected = urls.length > 0 && urls.every((u) => selected.has(u));

                    return (
                      <div key={p.plugin_slug} className="rounded overflow-hidden" style={{ background: "rgba(23,25,28,0.02)" }}>
                        <button
                          onClick={() => setExpandedPlugin(isExpanded ? null : p.plugin_slug)}
                          className="w-full flex items-center justify-between text-sm py-1.5 px-3 cursor-pointer"
                        >
                          <span className="font-medium" style={{ color: "var(--color-ink)" }}>
                            {p.plugin_name}
                          </span>
                          <div className="flex items-center gap-2">
                            {Object.entries(p.risk_counts).map(([risk, count]) => {
                              const { bg, color } = riskColor(risk);
                              return (
                                <span
                                  key={risk}
                                  className="px-2 py-0.5 rounded-full text-xs font-medium"
                                  style={{ background: bg, color }}
                                >
                                  {RISK_LABELS[risk] ?? risk} × {count}
                                </span>
                              );
                            })}
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
                                onClick={() => toggleAllSites(p.plugin_slug, urls)}
                                className="text-xs font-medium cursor-pointer hover:underline"
                                style={{ color: "var(--color-deep-teal)" }}
                              >
                                {allSelected ? "Tout désélectionner" : "Tout sélectionner"}
                              </button>
                              <button
                                onClick={() => handleBulkUpdate(p.plugin_slug)}
                                disabled={selected.size === 0}
                                className="px-3 py-1 rounded-full text-xs font-medium cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                                style={{ background: "var(--color-neon)", color: "var(--color-ink)" }}
                              >
                                Mettre à jour la sélection ({selected.size})
                              </button>
                            </div>
                            {p.sites.map((site) => {
                              const key = `${site.url}::${p.plugin_slug}`;
                              const status = updateStatus[key];
                              const { color } = riskColor(site.risk);
                              return (
                                <label
                                  key={site.url}
                                  className="flex items-center gap-2 text-xs py-1 px-2 rounded cursor-pointer"
                                  style={{ background: "rgba(255,255,255,0.6)" }}
                                >
                                  <input
                                    type="checkbox"
                                    checked={selected.has(site.url)}
                                    onChange={() => toggleSite(p.plugin_slug, site.url)}
                                  />
                                  <span className="flex-1" style={{ color: "var(--color-ink)" }}>
                                    {site.client}
                                  </span>
                                  <span style={{ color }}>{RISK_LABELS[site.risk] ?? site.risk}</span>
                                  {status && (
                                    <span
                                      style={{
                                        color: status === "success" ? "#087A61" : status === "error" ? "#dc2626" : "rgba(23,25,28,0.5)",
                                      }}
                                    >
                                      {status === "loading" ? "..." : status === "success" ? "✓ OK" : "✗ Erreur"}
                                    </span>
                                  )}
                                </label>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </Card>

              <Card title={`Sites sur une version PHP obsolète (< 8.1) — ${data.php_obsolete.length}`}>
                {data.php_obsolete.length === 0 ? (
                  <p className="text-sm" style={{ color: "rgba(23,25,28,0.5)" }}>
                    Aucun site en PHP obsolète.
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    {data.php_obsolete.map((site) => (
                      <div
                        key={site.url}
                        className="flex items-center justify-between text-sm py-1.5 px-3 border-l-4 rounded-r"
                        style={{ borderColor: "#dc2626", background: "rgba(220,38,38,0.04)" }}
                      >
                        <a
                          href={site.url}
                          target="_blank"
                          rel="noreferrer"
                          className="font-medium hover:underline"
                          style={{ color: "var(--color-deep-teal)" }}
                        >
                          {site.client}
                        </a>
                        <span style={{ color: "#dc2626" }}>PHP {site.php_version}</span>
                      </div>
                    ))}
                  </div>
                )}
              </Card>

              <Card title={`Sites avec plus de 3 thèmes WordPress — ${data.sites_trop_de_themes.length}`}>
                {data.sites_trop_de_themes.length === 0 ? (
                  <p className="text-sm" style={{ color: "rgba(23,25,28,0.5)" }}>
                    Aucun site avec un excès de thèmes.
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    {data.sites_trop_de_themes.map((site) => {
                      const isExpanded = expandedThemesSite === site.url;
                      return (
                        <div key={site.url} className="rounded overflow-hidden" style={{ background: "rgba(23,25,28,0.02)" }}>
                          <div
                            className="flex items-center justify-between text-sm py-1.5 px-3 border-l-4 rounded-r"
                            style={{ borderColor: "#d97706", background: "rgba(217,119,6,0.04)" }}
                          >
                            <a
                              href={site.url}
                              target="_blank"
                              rel="noreferrer"
                              className="font-medium hover:underline"
                              style={{ color: "var(--color-deep-teal)" }}
                            >
                              {site.client}
                            </a>
                            <button
                              onClick={() => setExpandedThemesSite(isExpanded ? null : site.url)}
                              className="flex items-center gap-2 cursor-pointer"
                              style={{ color: "#d97706" }}
                            >
                              {site.themes_count} thèmes installés
                              <span style={{ color: "rgba(23,25,28,0.4)" }}>{isExpanded ? "▲" : "▼"}</span>
                            </button>
                          </div>
                          {isExpanded && (() => {
                            const deletableSlugs = site.themes
                              .filter((t) => !t.is_active && !t.is_parent_of_active)
                              .map((t) => t.slug);
                            const selected = selectedSites[site.url] ?? new Set<string>();
                            const allSelected = deletableSlugs.length > 0 && deletableSlugs.every((s) => selected.has(s));
                            return (
                              <div className="px-3 pb-3 pt-2 space-y-1">
                                {deletableSlugs.length > 0 && (
                                  <div className="flex items-center justify-between py-1.5">
                                    <button
                                      onClick={() => toggleAllSites(site.url, deletableSlugs)}
                                      className="text-xs font-medium cursor-pointer hover:underline"
                                      style={{ color: "var(--color-deep-teal)" }}
                                    >
                                      {allSelected ? "Tout désélectionner" : "Tout sélectionner"}
                                    </button>
                                    <button
                                      onClick={() => handleBulkDeleteThemes(site.url)}
                                      disabled={selected.size === 0}
                                      className="px-3 py-1 rounded-full text-xs font-medium cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                                      style={{ background: "rgba(220,38,38,0.1)", color: "#dc2626" }}
                                    >
                                      Supprimer la sélection ({selected.size})
                                    </button>
                                  </div>
                                )}
                                {site.themes.map((theme) => {
                                  const key = `${site.url}::theme::${theme.slug}`;
                                  const state = updateStatus[key];
                                  const canDelete = !theme.is_active && !theme.is_parent_of_active;
                                  return (
                                    <label
                                      key={key}
                                      className="flex items-center gap-2 text-xs py-1.5 px-3 border-l-4 rounded-r"
                                      style={{
                                        color: "rgba(23,25,28,0.8)",
                                        borderColor: theme.is_active ? "#10b981" : "rgba(23,25,28,0.15)",
                                        background: theme.is_active ? "rgba(16,185,129,0.06)" : "rgba(255,255,255,0.6)",
                                        cursor: canDelete ? "pointer" : "default",
                                      }}
                                    >
                                      {canDelete && (
                                        <input
                                          type="checkbox"
                                          checked={selected.has(theme.slug)}
                                          onChange={() => toggleSite(site.url, theme.slug)}
                                        />
                                      )}
                                      <span className="flex-1">
                                        {theme.name} ({theme.version}){theme.is_active && " — actif"}
                                        {theme.is_parent_of_active && " — parent du thème actif"}
                                      </span>
                                      {state && (
                                        <span
                                          style={{
                                            color: state === "success" ? "#087A61" : state === "error" ? "#dc2626" : "rgba(23,25,28,0.5)",
                                          }}
                                        >
                                          {state === "loading" ? "..." : state === "success" ? "✓ Supprimé" : "✗ Erreur"}
                                        </span>
                                      )}
                                    </label>
                                  );
                                })}
                              </div>
                            );
                          })()}
                        </div>
                      );
                    })}
                  </div>
                )}
              </Card>

              <Card title={`Licences Breakdance à vérifier ou inactives — ${data.breakdance_licenses_a_verifier.length}`}>
                {data.breakdance_licenses_a_verifier.length === 0 ? (
                  <p className="text-sm" style={{ color: "rgba(23,25,28,0.5)" }}>
                    Aucune licence à vérifier ou inactive.
                  </p>
                ) : (
                  <>
                    <div className="mb-3 p-3 rounded-lg border text-xs space-y-2" style={{ borderColor: "rgba(23,25,28,0.1)" }}>
                      <div className="flex items-center justify-between gap-3">
                        <span style={{ color: "var(--color-ink)" }}>
                          {bdZip
                            ? `Zip actuel : Breakdance v${bdZip.version} — ${bdZip.filename} (${formatSize(bdZip.size)}, ${new Date(bdZip.uploaded_at).toLocaleDateString("fr-FR")})`
                            : "Aucun zip Breakdance uploadé"}
                        </span>
                        <label
                          className="px-3 py-1 rounded-full font-medium cursor-pointer whitespace-nowrap"
                          style={{ background: "rgba(23,25,28,0.06)", color: "var(--color-ink)", opacity: uploadProgress !== null ? 0.5 : 1 }}
                        >
                          {bdZip ? "Remplacer le zip" : "Uploader le zip"}
                          <input
                            type="file"
                            accept=".zip"
                            className="hidden"
                            disabled={uploadProgress !== null}
                            onChange={(e) => {
                              const f = e.target.files?.[0];
                              e.target.value = "";
                              if (f) handleUploadZip(f);
                            }}
                          />
                        </label>
                      </div>
                      {uploadProgress !== null && (
                        <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "rgba(23,25,28,0.08)" }}>
                          <div className="h-full" style={{ width: `${uploadProgress}%`, background: "var(--color-neon)" }} />
                        </div>
                      )}
                      {uploadError && <p style={{ color: "#dc2626" }}>{uploadError}</p>}
                      {bdError && <p style={{ color: "#dc2626" }}>{bdError}</p>}
                    </div>
                    {(() => {
                      const selected = selectedSites["breakdance-license"] ?? new Set<string>();
                      const urls = data.breakdance_licenses_a_verifier.map((s) => s.url);
                      const allSelected = urls.length > 0 && urls.every((u) => selected.has(u));
                      return (
                        <div className="space-y-1">
                          <div className="flex items-center justify-between py-1.5">
                            <button
                              onClick={() => toggleAllSites("breakdance-license", urls)}
                              className="text-xs font-medium cursor-pointer hover:underline"
                              style={{ color: "var(--color-deep-teal)" }}
                            >
                              {allSelected ? "Tout désélectionner" : "Tout sélectionner"}
                            </button>
                            <button
                              onClick={() => setShowLicenseModal(true)}
                              disabled={selected.size === 0 || !bdZip || bdRunning}
                              className="px-3 py-1 rounded-full text-xs font-medium cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                              style={{ background: "var(--color-neon)", color: "var(--color-ink)" }}
                            >
                              {bdRunning ? "Installation en cours…" : `Installer + activer sur la sélection (${selected.size})`}
                            </button>
                          </div>
                          {data.breakdance_licenses_a_verifier.map((site) => {
                            const job = bdJob[site.url];
                            const outdated = !!bdZip && !!site.plugin_version && site.plugin_version !== bdZip.version;
                            return (
                              <label
                                key={site.url}
                                className="flex items-center gap-2 text-xs py-1 px-2 rounded cursor-pointer"
                                style={{ background: "rgba(255,255,255,0.6)" }}
                              >
                                <input
                                  type="checkbox"
                                  checked={selected.has(site.url)}
                                  onChange={() => toggleSite("breakdance-license", site.url)}
                                />
                                <span className="flex-1" style={{ color: "var(--color-ink)" }}>
                                  {site.client}
                                </span>
                                <span style={{ color: site.valid === false ? "#dc2626" : "rgba(23,25,28,0.5)" }}>
                                  {site.valid === false ? "✗ Inactive" : "⚠ À vérifier"}
                                </span>
                                {site.plugin_version && (
                                  <span style={{ color: outdated ? "#b45309" : "rgba(23,25,28,0.5)" }}>
                                    v{site.plugin_version}
                                    {outdated ? " (ancienne)" : ""}
                                  </span>
                                )}
                                {job && (
                                  <span
                                    title={job.message}
                                    style={{
                                      color: job.status === "done" ? "#087A61" : job.status === "error" ? "#dc2626" : "rgba(23,25,28,0.5)",
                                    }}
                                  >
                                    {BREAKDANCE_STATUS_LABEL[job.status]}
                                    {job.status === "error" && job.message ? ` — ${job.message}` : ""}
                                    {job.status === "done" && job.new_version ? ` (v${job.new_version})` : ""}
                                  </span>
                                )}
                              </label>
                            );
                          })}
                        </div>
                      );
                    })()}
                  </>
                )}
              </Card>
            </>
          )}
        </div>
      </div>

      {showLicenseModal && (
        <div
          className="fixed inset-0 flex items-center justify-center z-50"
          style={{ background: "rgba(23,25,28,0.4)" }}
          onClick={() => setShowLicenseModal(false)}
        >
          <div
            className="rounded-2xl p-6 w-full max-w-sm space-y-4"
            style={{ background: "var(--color-white)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold" style={{ color: "var(--color-ink)" }}>
              Installer + activer Breakdance
            </h3>
            <p className="text-xs" style={{ color: "rgba(23,25,28,0.5)" }}>
              Le plugin Breakdance sera <strong>remplacé</strong> par la v{bdZip?.version} sur{" "}
              {(selectedSites["breakdance-license"] ?? new Set()).size} site(s) sélectionné(s), puis la clé ci-dessous sera appliquée.
              Sans clé, seule l&apos;installation est faite.
            </p>
            <input
              type="text"
              autoFocus
              placeholder="Clé de licence Breakdance (facultative)"
              value={licenseKeyInput}
              onChange={(e) => setLicenseKeyInput(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border text-sm"
              style={{ borderColor: "rgba(23,25,28,0.15)", color: "var(--color-ink)" }}
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowLicenseModal(false)}
                className="px-3 py-2 rounded-lg text-sm font-medium cursor-pointer disabled:opacity-50"
                style={{ background: "rgba(23,25,28,0.06)", color: "var(--color-ink)" }}
              >
                Annuler
              </button>
              <button
                onClick={handleInstallAndActivate}
                disabled={!bdZip}
                className="px-3 py-2 rounded-lg text-sm font-medium cursor-pointer disabled:opacity-50"
                style={{ background: "var(--color-ink)", color: "var(--color-white)" }}
              >
                Remplacer le plugin
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
