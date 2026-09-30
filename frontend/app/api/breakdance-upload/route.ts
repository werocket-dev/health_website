import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, verifySessionToken } from "@/lib/session";

// Route dédiée à l'upload du zip Breakdance : le proxy générique
// (api/backend/[...path]) lit le corps en texte, ce qui corrompt un binaire.
// Ici le corps est relayé tel quel, en flux, vers le backend.
export async function POST(request: NextRequest) {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token || !(await verifySessionToken(token))) {
    return NextResponse.json({ detail: "Non authentifié" }, { status: 401 });
  }

  const backendUrl = process.env.BACKEND_URL;
  const apiKey = process.env.BACKEND_API_KEY;
  if (!backendUrl || !apiKey || !request.body) {
    return NextResponse.json({ detail: "Backend non configuré ou fichier manquant" }, { status: 500 });
  }

  let response: Response;
  try {
    response = await fetch(`${backendUrl}/api/breakdance/zip`, {
      method: "POST",
      headers: {
        "Content-Type": "application/octet-stream",
        "X-API-Key": apiKey,
        "X-Filename": request.headers.get("x-filename") || "breakdance.zip",
      },
      body: request.body,
      // @ts-expect-error — requis par Node pour envoyer un ReadableStream comme corps
      duplex: "half",
      cache: "no-store",
    });
  } catch (e) {
    console.error("[breakdance-upload] Backend injoignable :", e);
    return NextResponse.json({ detail: "Backend injoignable" }, { status: 502 });
  }

  return new NextResponse(await response.text(), {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") || "application/json" },
  });
}
