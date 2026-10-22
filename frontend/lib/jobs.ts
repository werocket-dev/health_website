import axios from "axios";
import { useEffect, useRef } from "react";

const API_URL = "/api/backend";

/** Données affichées : le parc entier, ou l'échantillon de l'onglet Test. */
export type Scope = "parc" | "test";

/** Message d'erreur renvoyé par le backend (champ `detail`), ou le texte de repli. */
export function errorDetail(e: unknown, fallback: string): string {
  const detail = axios.isAxiosError(e) ? e.response?.data?.detail : null;
  return typeof detail === "string" ? detail : fallback;
}

/**
 * Suit un job en arrière-plan (install, suppression, actualisation, Breakdance) : interroge
 * `jobPath` toutes les 3 s, appelle `onUpdate` à chaque réponse, rend la main quand le job est
 * fini. Une coupure passagère est tolérée ; un 404, un 401/403 ou une coupure qui dure sont
 * relancés pour ne pas boucler indéfiniment.
 *
 * Renvoie true si le job est fini, false si le suivi a été interrompu par `signal` (page quittée) :
 * le job, lui, continue sur le serveur. `firstDelay` à 0 pour reprendre un job déjà en cours.
 */
export async function pollJob<T extends { finished: boolean }>(
  jobPath: string,
  onUpdate: (job: T) => void,
  options: { signal?: AbortSignal; firstDelay?: number } = {}
): Promise<boolean> {
  const { signal, firstDelay = 3000 } = options;
  let failures = 0;
  let delay = firstDelay;
  for (;;) {
    if (delay > 0) await new Promise((r) => setTimeout(r, delay));
    delay = 3000;
    if (signal?.aborted) return false;
    try {
      const { data } = await axios.get<T>(`${API_URL}${jobPath}`);
      if (signal?.aborted) return false;
      failures = 0;
      onUpdate(data);
      if (data.finished) return true;
    } catch (e) {
      const status = axios.isAxiosError(e) ? e.response?.status : undefined;
      // Job perdu (404) ou session expirée (401/403) : inutile de réessayer ; une coupure
      // passagère est tolérée, mais pas indéfiniment (~1 min).
      if (status === 404 || status === 401 || status === 403 || ++failures >= 20) throw e;
    }
  }
}

/**
 * Signal annulé quand le composant est quitté, pour arrêter son sondage. On lit le signal au moment
 * de l'appel (`getSignal()`), pas au rendu : en développement React monte, démonte puis remonte.
 */
export function useJobSignal(): () => AbortSignal {
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    controller.current = new AbortController();
    return () => controller.current?.abort();
  }, []);
  return () => (controller.current ??= new AbortController()).signal;
}

/**
 * À l'ouverture du composant, reprend le suivi d'un job déjà lancé (avant un changement de page ou
 * un rechargement) s'il est d'un des types demandés : `resume(jobId, kind)` est alors appelé.
 */
export function useResumeJob(kinds: string[], resume: (jobId: string, kind: string) => void) {
  const latest = useRef(resume);
  useEffect(() => {
    latest.current = resume;
  });
  const wanted = kinds.join(",");
  useEffect(() => {
    let cancelled = false;
    axios
      .get<{ job_id: string | null; kind: string | null }>(`${API_URL}/jobs/current`)
      .then(({ data }) => {
        if (!cancelled && data.job_id && data.kind && wanted.split(",").includes(data.kind)) {
          latest.current(data.job_id, data.kind);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [wanted]);
}
