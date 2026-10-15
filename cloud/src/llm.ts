// Anthropic Messages API over plain fetch (no SDK in the Worker bundle).
import type { Env } from "./env";

export const model = (env: Env) => env.ANTHROPIC_MODEL || "claude-sonnet-5-5";
export const llmAvailable = (env: Env) => !!env.ANTHROPIC_API_KEY;

// Current models that take `effort` and the server-side refusal fallback ("default" routing by refusal category).
const MODERN = /^claude-(opus-5|sonnet-5-5|fable-5)/;

export interface ContentBlock { type: string; text?: string; id?: string; name?: string; input?: any; [k: string]: unknown }
export interface Message { content: ContentBlock[]; stop_reason: string | null; model: string; usage?: Record<string, number> }

export class LlmRefusal extends Error {}

export async function messages(env: Env, body: Record<string, unknown>, effort: "low" | "medium" | "high" = "medium"): Promise<Message> {
  const m = model(env), modern = MODERN.test(m);
  const headers: Record<string, string> = { "content-type": "application/json", "x-api-key": env.ANTHROPIC_API_KEY ?? "", "anthropic-version": "2023-06-01" };
  if (modern) headers["anthropic-beta"] = "server-side-fallback-2026-07-01";
  const payload = JSON.stringify({ model: m, ...body, ...(modern ? { fallbacks: "default", output_config: { effort } } : {}) });
  const url = `${env.ANTHROPIC_BASE_URL || "https://api.anthropic.com"}/v1/messages`;
  for (let attempt = 0; ; attempt++) {
    const r = await fetch(url, { method: "POST", headers, body: payload });
    if (r.ok) {
      const msg = (await r.json()) as Message;
      if (msg.stop_reason === "refusal") throw new LlmRefusal("the model declined this request");
      return msg;
    }
    const retryable = r.status === 429 || r.status >= 500;
    if (!retryable || attempt >= 2) throw new Error(`anthropic ${r.status}: ${(await r.text()).slice(0, 300)}`);
    await new Promise((res) => setTimeout(res, 1000 * 2 ** attempt));
  }
}

export const textOf = (m: Message) => m.content.filter((b) => b.type === "text").map((b) => b.text ?? "").join("");

export async function completeText(env: Env, content: string | ContentBlock[], maxTokens = 2000): Promise<string> {
  return textOf(await messages(env, { max_tokens: maxTokens, messages: [{ role: "user", content }] }, "low"));
}
