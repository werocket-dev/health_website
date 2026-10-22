"use client";

import axios from "axios";
import { useEffect, useMemo, useState } from "react";
import { Card } from "@/components/ui/Card";
import { RecapView } from "../recap/RecapView";

const API_URL = "/api/backend";
const MAX_RANDOM = 50;
const MAX_SUGGESTIONS = 6;

type Site = { client: string; url: string };
type Progress = { status: "idle" | "running" | "done" | "error"; percent: number; label: string };

const mutedText = { color: "rgba(23,25,28,0.5)" };

export function TestView() {
  const [count, setCount] = useState(5);
  const [sites, setSites] = useState<Site[]>([]);
  const [query, setQuery] = useState("");
  const [chosen, setChosen] = useState<Site[]>([]);

  const [progress, setProgress] = useState<Progress | null>(null);
  const [launching, setLaunching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const running = launching || progress?.status === "running";

  useEffect(() => {
    axios
      .get<{ sites: Site[] }>(`${API_URL}/sites`, { params: { scope: "parc" } })
      .then(({ data }) => setSites(data.sites))
      .catch(() => {});
    // Un audit peut déjà tourner (autre onglet) : on le reprend tout de suite.
    axios
      .get<Progress>(`${API_URL}/progress`)
      .then(({ data }) => data.status === "running" && setProgress(data))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (progress?.status !== "running") return;
    const interval = setInterval(async () => {
      try {
        const { data } = await axios.get<Progress>(`${API_URL}/progress`);
        if (data.status === "error") {
          setError(data.label);
          setProgress(null);
          return;
        }
        setProgress(data);
        if (data.status === "done") {
          setReloadKey((n) => n + 1);
          setTimeout(() => setProgress(null), 3000);
        }
      } catch {
        /* silencieux : on réessaie au prochain tick */
      }
    }, 1000);
    return () => clearInterval(interval);
  }, [progress?.status]);

  const suggestions = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    const taken = new Set(chosen.map((s) => s.url));
    return sites
      .filter((s) => !taken.has(s.url) && (s.client.toLowerCase().includes(q) || s.url.toLowerCase().includes(q)))
      .slice(0, MAX_SUGGESTIONS);
  }, [sites, query, chosen]);

  const launch = async (body: Record<string, unknown>) => {
    if (running) return;
    setError(null);
    setLaunching(true);
    setProgress({ status: "running", percent: 0, label: "Lancement du test..." });
    try {
      await axios.post(`${API_URL}/scan`, { scope: "test", ...body });
    } catch (e) {
      setProgress(null);
      const status = axios.isAxiosError(e) ? e.response?.status : null;
      const detail = axios.isAxiosError(e) ? e.response?.data?.detail : null;
      setError(
        status === 409
          ? "Un audit est déjà en cours — attends qu'il se termine."
          : typeof detail === "string"
            ? detail
            : "Erreur lors du lancement du test (le serveur ne répond pas ?)"
      );
    } finally {
      setLaunching(false);
    }
  };

  const clampedCount = Math.min(MAX_RANDOM, Math.max(1, Math.floor(count) || 1));

  const controls = (
    <Card title="Lancer un test">
      <p className="text-xs mb-4" style={mutedText}>
        Un test audite quelques sites seulement et garde ses résultats à part : le récap du parc n&apos;est pas modifié. Chaque
        nouveau test remplace le précédent. Les actions (mise à jour, ajout, suppression) agissent sur les vrais sites.
      </p>

      <div className="flex flex-wrap items-end gap-3 mb-5">
        <label className="text-xs" style={mutedText}>
          Nombre de sites au hasard
          <input
            type="number"
            min={1}
            max={MAX_RANDOM}
            value={count}
            onChange={(e) => setCount(Number(e.target.value))}
            disabled={running}
            className="block mt-1 w-28 px-3 py-2 rounded-lg border text-sm"
            style={{ borderColor: "rgba(23,25,28,0.15)", color: "var(--color-ink)" }}
          />
        </label>
        <button
          onClick={() => launch({ limit: clampedCount, random_sample: true })}
          disabled={running}
          className="px-4 py-2 rounded-lg text-sm font-semibold cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          style={{ background: "var(--color-neon)", color: "var(--color-ink)" }}
        >
          {running ? "Test en cours…" : `Tirer ${clampedCount} site${clampedCount > 1 ? "s" : ""} au hasard et auditer`}
        </button>
      </div>

      <div>
        <p className="text-xs mb-1" style={mutedText}>
          Ou choisir les sites à tester
        </p>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={running}
          placeholder="Chercher un site (nom ou URL)…"
          className="w-full px-3 py-2 rounded-lg border text-sm"
          style={{ borderColor: "rgba(23,25,28,0.15)", color: "var(--color-ink)" }}
        />
        {suggestions.length > 0 && (
          <div className="mt-1 space-y-1">
            {suggestions.map((s) => (
              <button
                key={s.url}
                onClick={() => {
                  setChosen((prev) => [...prev, s]);
                  setQuery("");
                }}
                className="w-full flex items-center gap-2 text-xs py-1.5 px-2 rounded cursor-pointer text-left"
                style={{ background: "rgba(23,25,28,0.03)" }}
              >
                <span className="flex-1" style={{ color: "var(--color-ink)" }}>
                  {s.client}
                </span>
                <span style={mutedText}>{s.url.replace(/^https?:\/\//, "")}</span>
              </button>
            ))}
          </div>
        )}
        {query.trim() && suggestions.length === 0 && (
          <p className="text-xs mt-1" style={mutedText}>
            Aucun site ne correspond.
          </p>
        )}

        {chosen.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {chosen.map((s) => (
              <span
                key={s.url}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
                style={{ background: "rgba(0,226,138,0.12)", color: "var(--color-ink)" }}
              >
                {s.client}
                <button
                  onClick={() => setChosen((prev) => prev.filter((x) => x.url !== s.url))}
                  disabled={running}
                  className="cursor-pointer disabled:opacity-50"
                  aria-label={`Retirer ${s.client}`}
                  style={mutedText}
                >
                  ✕
                </button>
              </span>
            ))}
            <button
              onClick={() => launch({ urls: chosen.map((s) => s.url) })}
              disabled={running}
              className="px-3 py-1.5 rounded-lg text-xs font-semibold cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              style={{ background: "var(--color-ink)", color: "var(--color-white)" }}
            >
              Auditer {chosen.length === 1 ? "ce site" : `ces ${chosen.length} sites`}
            </button>
          </div>
        )}
      </div>

      {error && (
        <p className="text-sm mt-4" style={{ color: "#dc2626" }}>
          {error}
        </p>
      )}
      {progress && progress.status !== "idle" && (
        <div className="mt-4">
          <div className="h-2 rounded-full overflow-hidden" style={{ background: "rgba(23,25,28,0.08)" }}>
            <div
              className="h-full transition-all"
              style={{ width: `${progress.percent}%`, background: "var(--color-neon)" }}
            />
          </div>
          <p className="text-xs mt-1.5" style={mutedText}>
            {progress.label}
          </p>
        </div>
      )}
    </Card>
  );

  return <RecapView scope="test" topSlot={controls} reloadKey={reloadKey} />;
}
