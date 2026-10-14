# LabRat server

Single FastAPI process: WebSocket hubs (bridge / phone / ui), REST API, the agent, the digital twin, the RSI learning loop.
It serves `../web` at `/` and `../phone` at `/phone`. `../CONTRACT.md` is the source of truth.

```bash
cd server
uv sync
uv run uvicorn labrat.app:app --host 0.0.0.0 --port 8000
uv run --group dev pytest -q            # sim + end-to-end (fake bridge + UI) tests
```
Configure it with `../.env` (see `../.env.example`). **Every key is optional.** With no keys you get the rule-based agent
("make orange", "simulate", "run best", "approve", "abort", "home arms", "look", "status", "remember …", "recall …"),
an in-memory store, hashed bag-of-words embeddings, and text-only speech (the phone uses browser speechSynthesis).

## Modules
| file | what |
|---|---|
| `labrat/app.py` | FastAPI app: `/ws/bridge`, `/ws/phone`, `/ws/ui` (`?token=`), REST, static files |
| `labrat/hub.py` | bridge command dispatch (futures and timeouts), device state, UI and phone broadcast |
| `labrat/sim.py` | digital twin: deck, CNC trapezoid timing, tips, volumes, Beer–Lambert subtractive dye mixing → RGB, checks, frames, scoring, deterministic variant generator (50 variants in ~20 ms) |
| `labrat/lab.py` | experiments, simulate, select, run approval, protocol → bridge commands (safe Z, syringe primed to its air cushion above the liquid) |
| `labrat/agent.py` | tool-use loop (Anthropic `claude-sonnet-5` by default; `LLM_PROVIDER=fireworks` for Llama via the OpenAI-compatible API) plus the rule-based fallback |
| `labrat/learn.py` | RSI loop: observed vs predicted colour → ridge least squares fit of dye absorbance (and pipette gain) → versioned `sim_params` → re-score past variants → promote a procedure only if its prediction error improved |
| `labrat/store.py`, `embed.py` | Mongo plus typed memory (semantic, episodic, procedural, taxonomic) and vector search |
| `labrat/voice.py`, `telemetry.py` | ElevenLabs, Raindrop, Modelence |

Extra REST endpoints beyond the contract: `POST /api/runs/{id}/approve`, `POST /api/runs/{id}/observe {rating, rgb?, well?, vol_ul?}`,
`GET /api/learning`, `GET /api/experiments/{id}/variants/{vid}/frames`, `GET /api/memory/search?q=&type=`.

## Sponsor integrations
| sponsor | file(s) | what it does | fallback without a key |
|---|---|---|---|
| MongoDB Atlas | `labrat/store.py` | collections experiments, variants, runs, memories, events, sim_params, observations; creates the Atlas Vector Search index `memories_vector` (cosine, with a filter on `type`); `$vectorSearch` recall | in-memory dicts, cosine similarity in Python |
| Voyage AI | `labrat/embed.py` | `voyage-3.5` embeddings for every memory and query | hashed bag-of-words vectors |
| Raindrop | `labrat/telemetry.py` (called from `agent.py`, `lab.py`, `learn.py`) | `track_ai` for each chat, tool call, sim, run and `rsi.update` (uses the SDK if installed, otherwise REST) | logs once and becomes a no-op |
| ElevenLabs | `labrat/voice.py` | TTS (`eleven_flash_v2_5`, sends mp3 to the phone) and STT (`scribe_v1`, for phone audio) | sends text only |
| Modelence | `labrat/telemetry.py::modelence` | thin hook: POSTs JSON `{app, session, event, data}` with a bearer token to `MODELENCE_URL` | no-op |
| Anthropic / Fireworks | `labrat/agent.py` | LLM tool-use agent, plus LLM-proposed experiment variants | rule-based agent and deterministic generator |
| AWS | `../Dockerfile`, `../deploy/aws_ec2.sh` | EC2 t3.small (AL2023) running Docker, with Caddy giving automatic HTTPS on `<ip>.sslip.io` | run locally |

## Deploy
`deploy/aws_ec2.sh` (idempotent; set `REDEPLOY=1` to replace the instance). It uploads the code tarball and `.env` to a private
S3 bucket. The instance pulls them through presigned URLs, then builds and runs the container. The script prints `https://<ip-dashes>.sslip.io`.
