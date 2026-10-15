// Worker entry: token check, then everything dynamic goes to the single Lab Durable Object; the rest is static assets
// (apps/console at /console/, apps/phone at /phone/).
import type { Env } from "./env";

export { Lab } from "./do";

function authed(req: Request, url: URL, env: Env): boolean {
  const token = env.LABRAT_TOKEN || "labrat";
  const given = url.searchParams.get("token") ?? req.headers.get("x-labrat-token") ?? req.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  return given === token;
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    const p = url.pathname;
    if (p === "/" || p === "/console") return Response.redirect(new URL("/console/" + url.search, url).toString(), 302);
    if (p === "/healthz" || p.startsWith("/api/") || p.startsWith("/ws/")) {
      if (p !== "/healthz" && !authed(req, url, env)) {
        if (req.headers.get("Upgrade")?.toLowerCase() === "websocket") // the DO accepts, then closes with 4401
          return env.LAB.get(env.LAB.idFromName("lab")).fetch(new Request(req, { headers: { ...Object.fromEntries(req.headers), "x-labrat-reject": "1" } }));
        return Response.json({ error: "unauthorized (token)" }, { status: 401 });
      }
      return env.LAB.get(env.LAB.idFromName("lab")).fetch(req);
    }
    return env.ASSETS.fetch(req);
  },
} satisfies ExportedHandler<Env>;
