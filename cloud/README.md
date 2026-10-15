# LabRat cloud

The LabRat coordinator: one Cloudflare Worker, one Durable Object (`Lab`, a single instance named `lab`) and one D1
database. The only outside service is the Anthropic API (the agent's brain); without a key a rule-based agent answers.
The wire contract is [`docs/protocol.md`](../docs/protocol.md).

```
src/
  index.ts        Worker: token check, / -> /console/, /api|/ws|/healthz -> the DO, everything else -> static assets
  do.ts           Lab DO: hibernatable WebSockets (tags ui|phone|bridge), REST route table, boot, device events
  hub.ts          providers, device routing (most recent announcer), pending commands + timeouts, broadcast, frames,
                  probe-arm safety rules
  devices/        DO-side logical devices on the provider's servo `hub` (firmware lines, protocol.md §4)
    base.ts         serialised actions, channel cfg, persistence hooks
    pipette.ts      dead-reckoning syringe on ch0 (FS90R, firmware-timed `pulse`, `until: syr_home`)
    probe.ts        probe arm on ch1 (deploy / retract / set)
    stand.ts        phone pan/tilt on ch2/ch3 (aim, look at deck | operator | home | <beaker>)
  agent.ts        Anthropic tool-use loop + rule-based fallback
  llm.ts          Messages API over fetch
  lab.ts          experiments: create / edit / dry run / repeat, run (approval, execution, abort), results, CSV
  sim.ts          digital twin: deck, samples -> steps, motion timing, liquid accounting, dye mixing, checks, frames
  learn.ts        colour model: ridge refit in absorbance space from every result, kept only if it predicts better
  vision.ts       camera homography + beaker sampling of phone JPEGs (jpeg-js), Claude-vision fallback (measures a plate)
  deck_cal.ts     taught deck points, pipette and tool offsets
  tooling.ts      tool-rail moves (pipette / microscope / probe)
  store.ts        D1 access          linalg.ts  small dense solver          env.ts  Env + shared context
migrations/       D1 schema
test/             vitest in workerd (@cloudflare/vitest-plugin)
```

## Run

```sh
cd cloud
npm install
cp .dev.vars.example .dev.vars          # LABRAT_TOKEN, optional ANTHROPIC_API_KEY
npx wrangler d1 migrations apply labrat --local   # schema changed? rm -rf .wrangler/state/v3/d1 first
npx wrangler dev                        # http://localhost:8787/  (console), /phone/
npm test                                # vitest in the Workers runtime
npm run typecheck
```

Clients: the console at `http://localhost:8787/` (`?token=` if you changed `LABRAT_TOKEN`), the phone at `/phone/`,
the bridge with `--server ws://localhost:8787`, the CLI with `--server http://localhost:8787`.

Configuration (`wrangler.jsonc` vars, `.dev.vars`, or `wrangler secret put` when deployed):

| name | default | |
|---|---|---|
| `LABRAT_TOKEN` | `labrat` | required on every socket and `/api` call: `?token=`, `Authorization: Bearer`, or `x-labrat-token` |
| `ANTHROPIC_API_KEY` | unset | unset: rule-based agent |
| `ANTHROPIC_MODEL` | `claude-sonnet-5-5` | |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | |
| `LABRAT_AUTO_APPROVE` | `0` | start runs without human approval |
| `LABRAT_AUTO_OBSERVE` | `1` | after a run with a photo step, measure every beaker from the photo |
| `LABRAT_PHOTO_WAIT_S` | `3` | how long the photo step waits for a fresh phone frame |

## Deploy

`npx wrangler d1 create labrat`, put its id in `wrangler.jsonc`, `npx wrangler d1 migrations apply labrat --remote`,
`npx wrangler secret put LABRAT_TOKEN` (and `ANTHROPIC_API_KEY`), then `npx wrangler deploy`. The Worker serves
`apps/` as static assets (`apps/.assetsignore` keeps the CLI, Python, tests and docs out).

Routes, D1 tables, the logical devices and their safety rules: [`docs/architecture.md`](../docs/architecture.md).
