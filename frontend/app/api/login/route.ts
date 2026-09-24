import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, createSessionToken } from "@/lib/session";

export async function POST(request: NextRequest) {
  const { password } = await request.json();

  if (!process.env.DASHBOARD_PASSWORD || password !== process.env.DASHBOARD_PASSWORD) {
    return NextResponse.json({ detail: "Mot de passe incorrect" }, { status: 401 });
  }

  const token = await createSessionToken();
  const response = NextResponse.json({ success: true });
  // Le flag Secure dépend du protocole réellement utilisé par le client, pas
  // du mode build : derrière Traefik (Dokploy), la connexion interne vers ce
  // serveur Next.js est toujours en HTTP, seul le header transmet le vrai
  // protocole externe. Un domaine sslip.io ne supporte pas HTTPS — poser un
  // cookie Secure dessus le rendrait silencieusement inutilisable.
  const isHttps =
    request.headers.get("x-forwarded-proto") === "https" ||
    request.nextUrl.protocol === "https:";
  response.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: isHttps,
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 24 * 7,
  });
  return response;
}
