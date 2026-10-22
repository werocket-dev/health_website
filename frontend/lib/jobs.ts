import axios from "axios";

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
 * fini. Une coupure passagère est ignorée ; un 404 (backend redémarré, job perdu) est relancé
 * pour ne pas boucler indéfiniment.
 */
export async function pollJob<T extends { finished: boolean }>(jobPath: string, onUpdate: (job: T) => void): Promise<void> {
  for (;;) {
    await new Promise((r) => setTimeout(r, 3000));
    try {
      const { data } = await axios.get<T>(`${API_URL}${jobPath}`);
      onUpdate(data);
      if (data.finished) return;
    } catch (e) {
      if (axios.isAxiosError(e) && e.response?.status === 404) throw e;
    }
  }
}
