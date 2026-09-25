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
};

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
  const [activatingLicenses, setActivatingLicenses] = useState(false);

  const handleActivateLicenses = async () => {
    const urls = Array.from(selectedSites["breakdance-license"] ?? []);
    if (urls.length === 0 || !licenseKeyInput.trim()) return;

    setActivatingLicenses(true);
    for (const url of urls) {
      const key = `${url}::breakdance-license`;
      setUpdateStatus((prev) => ({ ...prev, [key]: "loading" }));
      try {
        await axios.post(`${API_URL}/activate-license`, { url, license_key: licenseKeyInput.trim() });
        setUpdateStatus((prev) => ({ ...prev, [key]: "success" }));
      } catch {
        setUpdateStatus((prev) => ({ ...prev, [key]: "error" }));
      }
    }
    setActivatingLicenses(false);
    setShowLicenseModal(false);
    setLicenseKeyInput("");
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
                          {isExpanded && (
                            <div className="px-3 pb-3 pt-2 space-y-1">
                              {site.themes.map((theme) => {
                                const key = `${site.url}::theme::${theme.slug}`;
                                const state = updateStatus[key];
                                const canDelete = !theme.is_active && !theme.is_parent_of_active;
                                return (
                                  <div
                                    key={key}
                                    className="flex items-center justify-between text-xs py-1.5 px-3 border-l-4 rounded-r"
                                    style={{
                                      color: "rgba(23,25,28,0.8)",
                                      borderColor: theme.is_active ? "#10b981" : "rgba(23,25,28,0.15)",
                                      background: theme.is_active ? "rgba(16,185,129,0.06)" : "rgba(255,255,255,0.6)",
                                    }}
                                  >
                                    <span className="flex-1 mr-3">
                                      {theme.name} ({theme.version}){theme.is_active && " — actif"}
                                      {theme.is_parent_of_active && " — parent du thème actif"}
                                    </span>
                                    {canDelete && (
                                      <button
                                        onClick={() => handleDeleteTheme(site.url, theme.slug, theme.name)}
                                        disabled={!!state}
                                        className="shrink-0 px-2 py-0.5 rounded text-xs font-medium transition-colors cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed"
                                        style={{
                                          background: state === "success" ? "rgba(0,226,158,0.15)" : "rgba(220,38,38,0.08)",
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
                              disabled={selected.size === 0}
                              className="px-3 py-1 rounded-full text-xs font-medium cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
                              style={{ background: "var(--color-neon)", color: "var(--color-ink)" }}
                            >
                              Activer la licence sur la sélection ({selected.size})
                            </button>
                          </div>
                          {data.breakdance_licenses_a_verifier.map((site) => {
                            const key = `${site.url}::breakdance-license`;
                            const status = updateStatus[key];
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
          onClick={() => !activatingLicenses && setShowLicenseModal(false)}
        >
          <div
            className="rounded-2xl p-6 w-full max-w-sm space-y-4"
            style={{ background: "var(--color-white)" }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="text-sm font-semibold" style={{ color: "var(--color-ink)" }}>
              Activer la licence Breakdance
            </h3>
            <p className="text-xs" style={{ color: "rgba(23,25,28,0.5)" }}>
              Cette clé sera appliquée à {(selectedSites["breakdance-license"] ?? new Set()).size} site(s) sélectionné(s).
            </p>
            <input
              type="text"
              autoFocus
              placeholder="Clé de licence Breakdance"
              value={licenseKeyInput}
              onChange={(e) => setLicenseKeyInput(e.target.value)}
              className="w-full px-3 py-2 rounded-lg border text-sm"
              style={{ borderColor: "rgba(23,25,28,0.15)", color: "var(--color-ink)" }}
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => setShowLicenseModal(false)}
                disabled={activatingLicenses}
                className="px-3 py-2 rounded-lg text-sm font-medium cursor-pointer disabled:opacity-50"
                style={{ background: "rgba(23,25,28,0.06)", color: "var(--color-ink)" }}
              >
                Annuler
              </button>
              <button
                onClick={handleActivateLicenses}
                disabled={activatingLicenses || !licenseKeyInput.trim()}
                className="px-3 py-2 rounded-lg text-sm font-medium cursor-pointer disabled:opacity-50"
                style={{ background: "var(--color-ink)", color: "var(--color-white)" }}
              >
                {activatingLicenses ? "Activation..." : "Activer"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
