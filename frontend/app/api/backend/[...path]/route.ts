import { NextRequest, NextResponse } from "next/server";
import { SESSION_COOKIE, verifySessionToken } from "@/lib/session";

async function proxy(request: NextRequest, path: string[]) {
  const token = request.cookies.get(SESSION_COOKIE)?.value;
  if (!token || !(await verifySessionToken(token))) {
    return NextResponse.json({ detail: "Non authentifié" }, { status: 401 });
  }

  const backendUrl = process.env.BACKEND_URL;
  const apiKey = process.env.BACKEND_API_KEY;
  if (!backendUrl || !apiKey) {
    return NextResponse.json({ detail: "Backend non configuré" }, { status: 500 });
  }

  const url = `${backendUrl}/api/${path.join("/")}${request.nextUrl.search}`;
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let response: Response;
  try {
    response = await fetch(url, {
      method: request.method,
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": apiKey,
      },
      body: hasBody ? await request.text() : undefined,
      cache: "no-store",
    });
  } catch (e) {
    // Sans ce garde-fou, une coupure réseau vers le backend fait planter ce
    // fetch() sans être rattrapée — Next.js renvoie alors un 500 générique
    // au navigateur, sans indication que le souci vient du backend et non du proxy.
    console.error(`[proxy] Backend injoignable (${url}) :`, e);
    return NextResponse.json({ detail: "Backend injoignable" }, { status: 502 });
  }

  const body = await response.text();
  return new NextResponse(body, {
    status: response.status,
    headers: { "Content-Type": response.headers.get("Content-Type") || "application/json" },
  });
}

export async function GET(request: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await params).path);
}

export async function POST(request: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await params).path);
}
