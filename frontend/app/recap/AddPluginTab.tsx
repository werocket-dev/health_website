"use client";

import axios from "axios";
import { useEffect, useMemo, useRef, useState } from "react";
import { Card } from "@/components/ui/Card";
import { errorDetail, pollJob, type Scope } from "@/lib/jobs";

const API_URL = "/api/backend";

type PluginZip = {
  slug: string;
  name: string;
  version: string;
  filename: string;
  size: number;
  uploaded_at: string;
};

type Site = { client: string; url: string };

type SiteJob = {
  status: "pending" | "installing" | "done" | "error" | "skipped";
  message?: string;
  installed?: boolean | null;
  old_version?: string | null;
  new_version?: string | null;
};

const STATUS_LABEL: Record<SiteJob["status"], string> = {
  pending: "En attente",
  installing: "Installation…",
  done: "✓ Fait",
  error: "✗ Erreur",
  skipped: "⏭ Ignoré",
};

function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} Mo` : `${Math.max(1, Math.round(bytes / 1024))} Ko`;
}

function doneLabel(job: SiteJob): string {
  if (job.installed) return `✓ Installé${job.new_version ? ` (v${job.new_version})` : ""}`;
  if (job.old_version && job.new_version) return `✓ Mis à jour (${job.old_version} → ${job.new_version})`;
  return "✓ Fait";
}

const mutedText = { color: "rgba(23,25,28,0.5)" };

export function AddPluginTab({ onInstalled, scope = "parc" }: { onInstalled: () => void; scope?: Scope }) {
  const [zips, setZips] = useState<PluginZip[]>([]);
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const [sites, setSites] = useState<Site[]>([]);
  const [sitesError, setSitesError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [selectedUrls, setSelectedUrls] = useState<Set<string>>(new Set());
  const [licenseKey, setLicenseKey] = useState("");
  const [showSelectedOnly, setShowSelectedOnly] = useState(false);

  const [running, setRunning] = useState(false);
  const [installError, setInstallError] = useState<string | null>(null);
  const [job, setJob] = useState<Record<string, SiteJob> | null>(null);
  const [abortReason, setAbortReason] = useState<string | null>(null);
  const [finished, setFinished] = useState(false);

  useEffect(() => {
    axios
      .get<{ zips: PluginZip[] }>(`${API_URL}/plugins/zips`)
      .then(({ data }) => {
        setZips(data.zips);
        setSelectedSlug((current) => current ?? data.zips[0]?.slug ?? null);
      })
      .catch(() => setUploadError("Impossible de charger les zips enregistrés."));
    axios
      .get<{ sites: Site[] }>(`${API_URL}/sites`, { params: { scope } })
      .then(({ data }) => setSites(data.sites))
      .catch(() => setSitesError("Impossible de charger la liste des sites."));
  }, [scope]);

  const selectedZip = zips.find((z) => z.slug === selectedSlug) ?? null;
  const isBreakdance = selectedZip?.slug === "breakdance";

  // Sans sélection, le mode « sélection seulement » n'a rien à montrer : on l'ignore.
  const onlySelected = showSelectedOnly && selectedUrls.size > 0;

  const filteredSites = useMemo(() => {
    const q = query.trim().toLowerCase();
    return sites.filter(
      (s) =>
        (!onlySelected || selectedUrls.has(s.url)) &&
        (!q || s.client.toLowerCase().includes(q) || s.url.toLowerCase().includes(q))
    );
  }, [sites, query, onlySelected, selectedUrls]);

  const allFilteredSelected = filteredSites.length > 0 && filteredSites.every((s) => selectedUrls.has(s.url));

  const toggleUrl = (url: string) =>
    setSelectedUrls((prev) => {
      const next = new Set(prev);
      if (next.has(url)) next.delete(url);
      else next.add(url);
      return next;
    });

  const toggleAllFiltered = () =>
    setSelectedUrls((prev) => {
      const next = new Set(prev);
      for (const s of filteredSites) {
        if (allFilteredSelected) next.delete(s.url);
        else next.add(s.url);
      }
      return next;
    });

  const handleUpload = async (file: File) => {
    setUploadError(null);
    setUploadProgress(0);
    try {
      // Corps brut (pas de multipart) vers la route dédiée, qui relaie le flux au backend.
      const { data } = await axios.post<PluginZip>("/api/plugin-upload", file, {
        headers: { "Content-Type": "application/octet-stream", "X-Filename": file.name },
        onUploadProgress: (e) => e.total && setUploadProgress(Math.round((e.loaded / e.total) * 100)),
      });
      setZips((prev) => [data, ...prev.filter((z) => z.slug !== data.slug)]);
      setSelectedSlug(data.slug);
    } catch (e) {
      setUploadError(errorDetail(e, "Échec de l'upload"));
    } finally {
      setUploadProgress(null);
      if (fileInput.current) fileInput.current.value = "";
    }
  };

  const handleDeleteZip = async (zip: PluginZip) => {
    if (!window.confirm(`Supprimer le zip "${zip.name}" (v${zip.version}) de la plateforme ? Les sites où il est installé ne sont pas touchés.`)) return;
    try {
      await axios.post(`${API_URL}/plugins/zip/${zip.slug}/delete`, {});
      setZips((prev) => prev.filter((z) => z.slug !== zip.slug));
      setSelectedSlug((current) => (current === zip.slug ? null : current));
    } catch (e) {
      setUploadError(errorDetail(e, "Échec de la suppression du zip"));
    }
  };

  const handleInstall = async () => {
    if (!selectedZip || selectedUrls.size === 0) return;
    const count = selectedUrls.size;
    const confirmed = window.confirm(
      `Installer "${selectedZip.name}" v${selectedZip.version} sur ${count} site${count > 1 ? "s" : ""} ?\n\n` +
        "Un plugin déjà présent est remplacé (son état actif ou inactif est conservé). " +
        "Un nouveau plugin est activé. Cette action s'applique à de vrais sites clients."
    );
    if (!confirmed) return;

    const urls = Array.from(selectedUrls);
    setInstallError(null);
    setAbortReason(null);
    setFinished(false);
    setRunning(true);
    setJob(Object.fromEntries(urls.map((u) => [u, { status: "pending" as const }])));
    try {
      const { data } = await axios.post<{ job_id: string }>(`${API_URL}/plugins/install`, {
        slug: selectedZip.slug,
        urls,
        license_key: licenseKey.trim() || null, // le backend ne l'applique qu'à Breakdance
      });
      setLicenseKey("");

      // Le job dure plusieurs minutes : on suit sa progression par polling.
      await pollJob<{ finished: boolean; aborted_reason: string | null; sites: Record<string, SiteJob> }>(
        `/plugins/jobs/${data.job_id}`,
        (status) => {
          setJob(status.sites);
          setAbortReason(status.aborted_reason);
        }
      );
      setFinished(true);
      onInstalled(); // recharge le récap avec les nouveaux états
    } catch (e) {
      setInstallError(errorDetail(e, "Échec du lancement de l'installation"));
    } finally {
      setRunning(false);
    }
  };

  const clientOf = (url: string) => sites.find((s) => s.url === url)?.client ?? url;
  const jobEntries = job ? Object.entries(job) : [];
  const okCount = jobEntries.filter(([, j]) => j.status === "done").length;
  const errorCount = jobEntries.filter(([, j]) => j.status === "error").length;

  return (
    <div className="space-y-6">
      <Card title="1. Choisir le plugin (zip)">
        <p className="text-xs mb-3" style={mutedText}>
          Le zip doit contenir un seul dossier (le plugin), avec un fichier PHP portant l&apos;en-tête « Plugin Name ». Le plugin
          est ensuite téléchargé par chaque site depuis la plateforme (lien signé, valable 10 minutes).
        </p>

        <div className="flex items-center gap-3 mb-4">
          <input
            ref={fileInput}
            type="file"
            accept=".zip,application/zip"
            className="hidden"
            onChange={(e) => e.target.files?.[0] && handleUpload(e.target.files[0])}
          />
          <button
            onClick={() => fileInput.current?.click()}
            disabled={uploadProgress !== null || running}
            className="px-3 py-2 rounded-lg text-sm font-medium cursor-pointer disabled:opacity-50"
            style={{ background: "var(--color-ink)", color: "var(--color-white)" }}
          >
            {uploadProgress !== null ? `Envoi… ${uploadProgress}%` : "Ajouter un zip"}
          </button>
          {uploadError && (
            <span className="text-xs" style={{ color: "#dc2626" }}>
              {uploadError}
            </span>
          )}
        </div>

        {zips.length === 0 ? (
          <p className="text-sm" style={mutedText}>
            Aucun zip enregistré pour l&apos;instant.
          </p>
        ) : (
          <div className="space-y-1.5">
            {zips.map((zip) => (
              <label
                key={zip.slug}
                className="flex items-center gap-3 text-sm py-2 px-3 rounded cursor-pointer"
                style={{
                  background: selectedSlug === zip.slug ? "rgba(0,226,138,0.1)" : "rgba(23,25,28,0.03)",
                }}
              >
                <input
                  type="radio"
                  name="plugin-zip"
                  checked={selectedSlug === zip.slug}
                  onChange={() => setSelectedSlug(zip.slug)}
                />
                <span className="flex-1">
                  <strong style={{ color: "var(--color-ink)" }}>{zip.name}</strong>{" "}
                  <span style={mutedText}>
                    v{zip.version} · {zip.slug} · {formatSize(zip.size)} · {new Date(zip.uploaded_at).toLocaleDateString("fr-FR")}
                  </span>
                </span>
                <button
                  onClick={(e) => {
                    e.preventDefault();
                    handleDeleteZip(zip);
                  }}
                  disabled={running}
                  className="text-xs font-medium cursor-pointer hover:underline disabled:opacity-50"
                  style={{ color: "#dc2626" }}
                >
                  Supprimer le zip
                </button>
              </label>
            ))}
          </div>
        )}
      </Card>

      <Card title={`2. Choisir les sites — ${selectedUrls.size} sélectionné${selectedUrls.size > 1 ? "s" : ""} sur ${sites.length}`}>
        {sitesError && (
          <p className="text-sm mb-3" style={{ color: "#dc2626" }}>
            {sitesError}
          </p>
        )}
        {!sitesError && sites.length === 0 && (
          <p className="text-sm mb-3" style={{ color: "#b45309" }}>
            Aucun site connu : la liste vient du dernier audit (results.json) et le backend n&apos;en a pas. Lance un audit
            complet, ou vérifie que ce front est branché sur le bon backend.
          </p>
        )}
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Filtrer les sites (nom ou URL)…"
          className="w-full mb-3 px-3 py-2 rounded-lg border text-sm"
          style={{ borderColor: "rgba(23,25,28,0.15)", color: "var(--color-ink)" }}
        />
        <div className="flex items-center justify-between mb-2">
          <button
            onClick={toggleAllFiltered}
            disabled={filteredSites.length === 0 || running}
            className="text-xs font-medium cursor-pointer hover:underline disabled:opacity-50"
            style={{ color: "var(--color-deep-teal)" }}
          >
            {allFilteredSelected ? "Tout désélectionner" : "Tout sélectionner"}
            {query.trim() || onlySelected ? ` (${filteredSites.length} résultat${filteredSites.length > 1 ? "s" : ""})` : ""}
          </button>
          {selectedUrls.size > 0 && (
            <div className="flex items-center gap-4">
              <button
                onClick={() => setShowSelectedOnly((v) => !v)}
                className="text-xs font-medium cursor-pointer hover:underline"
                style={{ color: "var(--color-deep-teal)" }}
              >
                {onlySelected ? "Voir tous les sites" : `Voir la sélection (${selectedUrls.size})`}
              </button>
              <button
                onClick={() => setSelectedUrls(new Set())}
                disabled={running}
                className="text-xs cursor-pointer hover:underline disabled:opacity-50"
                style={mutedText}
              >
                Vider la sélection
              </button>
            </div>
          )}
        </div>
        <div className="max-h-64 overflow-y-auto space-y-1">
          {filteredSites.map((site) => (
            <label
              key={site.url}
              className="flex items-center gap-2 text-xs py-1 px-2 rounded cursor-pointer"
              style={{ background: "rgba(23,25,28,0.03)" }}
            >
              <input type="checkbox" checked={selectedUrls.has(site.url)} onChange={() => toggleUrl(site.url)} disabled={running} />
              <span className="flex-1" style={{ color: "var(--color-ink)" }}>
                {site.client}
              </span>
              <span style={mutedText}>{site.url.replace(/^https?:\/\//, "")}</span>
            </label>
          ))}
          {filteredSites.length === 0 && sites.length > 0 && (
            <p className="text-sm py-2" style={mutedText}>
              Aucun site ne correspond.
            </p>
          )}
        </div>
      </Card>

      <Card title="3. Installer">
        <p className="text-sm mb-4" style={{ color: "var(--color-ink)" }}>
          {selectedZip
            ? `${selectedZip.name} v${selectedZip.version} sur ${selectedUrls.size} site${selectedUrls.size > 1 ? "s" : ""}`
            : "Choisis d'abord un zip."}
        </p>
        {isBreakdance && (
          <label className="block mb-4 text-xs" style={mutedText}>
            Clé de licence Breakdance (facultative) — appliquée sur chaque site après l&apos;installation
            <input
              type="text"
              value={licenseKey}
              onChange={(e) => setLicenseKey(e.target.value)}
              disabled={running}
              placeholder="Sans clé, seule l'installation est faite"
              className="block mt-1 w-full max-w-md px-3 py-2 rounded-lg border text-sm"
              style={{ borderColor: "rgba(23,25,28,0.15)", color: "var(--color-ink)" }}
            />
          </label>
        )}
        <button
          onClick={handleInstall}
          disabled={!selectedZip || selectedUrls.size === 0 || running}
          className="px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer disabled:opacity-40 disabled:cursor-not-allowed"
          style={{ background: "var(--color-neon)", color: "var(--color-ink)" }}
        >
          {running ? "Installation en cours…" : "Installer sur la sélection"}
        </button>
        {installError && (
          <p className="text-sm mt-3" style={{ color: "#dc2626" }}>
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
                    style={{ color: j.status === "done" ? "#087A61" : j.status === "error" ? "#dc2626" : "rgba(23,25,28,0.55)" }}
                  >
                    {j.status === "done" ? doneLabel(j) : STATUS_LABEL[j.status]}
                    {j.message && j.status !== "done" ? ` — ${j.message}` : ""}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
