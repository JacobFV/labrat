"""LabRat from Python: one experiment = one plate. Plain dicts in and out.

    from labrat_cli import Lab
    lab = Lab()                                   # LABRAT_SERVER / LABRAT_TOKEN, else http://127.0.0.1:8787, "labrat"
    e = lab.new("Orange sweep", target="orange", samples=[{"red": r, "yellow": 200 - r} for r in range(20, 200, 20)])
    e = lab.run(e["id"], approve=True, wait=True)
    lab.get(e["id"])["beakers"]

API contract: docs/experiments.md.
"""
from __future__ import annotations

import os
import time

import httpx

FINISHED = ("done", "failed", "aborted")


class LabError(RuntimeError):
    """The server said no; the message is its {"error": ...}."""


class Lab:
    def __init__(self, server: str | None = None, token: str | None = None, timeout: float = 60, transport=None) -> None:
        self.server = (server or os.environ.get("LABRAT_SERVER") or "http://127.0.0.1:8787").rstrip("/")
        token = token or os.environ.get("LABRAT_TOKEN") or "labrat"
        self.http = httpx.Client(base_url=self.server, timeout=timeout, transport=transport,
                                 headers={"authorization": f"Bearer {token}"})

    def _call(self, method: str, path: str, body=None, **params):
        r = self.http.request(method, path, json=body, params={k: v for k, v in params.items() if v is not None})
        if r.status_code >= 400:
            try:
                msg = r.json().get("error") or r.text
            except Exception:  # noqa: BLE001
                msg = r.text
            raise LabError(f"{method} {path}: {msg}")
        return r.json() if "json" in r.headers.get("content-type", "") else r.text

    # ---- experiments ----
    def experiments(self) -> list[dict]:
        return self._call("GET", "/api/experiments")

    def get(self, id: str) -> dict:
        return self._call("GET", f"/api/experiments/{id}")

    def new(self, title: str, samples: list[dict] | None = None, target=None, steps: list[dict] | None = None,
            notes: str | None = None) -> dict:
        body = {"title": title, "target": target, "samples": samples, "steps": steps, "notes": notes}
        return self._call("POST", "/api/experiments", {k: v for k, v in body.items() if v is not None})

    def edit(self, id: str, **fields) -> dict:
        return self._call("PATCH", f"/api/experiments/{id}", fields)

    def repeat(self, id: str) -> dict:
        return self._call("POST", f"/api/experiments/{id}/repeat")

    def dry_run(self, samples: list[dict] | None = None, steps: list[dict] | None = None) -> dict:
        return self._call("POST", "/api/dry_run", {"samples": samples} if steps is None else {"steps": steps})

    # ---- running ----
    def run(self, id: str, approve: bool = False, wait: bool = False, timeout: float = 3600) -> dict:
        """Ask to run. approve=True approves it yourself; wait=True blocks until done/failed/aborted."""
        e = self._call("POST", f"/api/experiments/{id}/run", {"auto": approve})
        return self.wait(id, timeout) if wait else e

    def approve(self, id: str | None = None) -> dict:
        if id is None:
            waiting = [a for a in self._call("GET", "/api/state").get("active", []) if a["status"] == "awaiting_approval"]
            if not waiting:
                raise LabError("no experiment is waiting for approval")
            id = waiting[0]["id"]
        return self._call("POST", f"/api/experiments/{id}/approve")

    def wait(self, id: str, timeout: float = 3600, poll: float = 2) -> dict:
        end = time.monotonic() + timeout
        while (e := self.get(id))["status"] not in FINISHED:
            if e["status"] in ("draft", "ready"):
                raise LabError(f"{id} is {e['status']}: it is not running (run it first)")
            if time.monotonic() > end:
                raise TimeoutError(f"{id} still {e['status']} after {timeout:.0f} s")
            time.sleep(poll)
        return e

    def abort(self) -> dict:
        return self._call("POST", "/api/abort")

    # ---- results ----
    def results(self, id: str, rows: list[dict]) -> dict:
        """Record readings: [{well, rgb: [r, g, b]?, rating: 1-5?, note?}]. Returns {results, model}."""
        return self._call("POST", f"/api/experiments/{id}/results", {"results": rows})

    def measure(self, id: str) -> dict:
        """Read every beaker from the latest phone photo."""
        return self._call("POST", f"/api/experiments/{id}/measure")

    def csv(self, id: str | None = None) -> str:
        """One experiment's beaker table, or every measured beaker ever (id=None)."""
        return self._call("GET", f"/api/experiments/{id}.csv" if id else "/api/results.csv")

    # ---- notebook, model, agent ----
    def notes(self, q: str = "", experiment_id: str | None = None) -> list[dict]:
        return self._call("GET", "/api/notes", q=q, experiment=experiment_id)

    def note(self, text: str, experiment_id: str | None = None) -> dict:
        return self._call("POST", "/api/notes", {"text": text, "experiment_id": experiment_id})

    def model(self) -> dict:
        return self._call("GET", "/api/model")

    def chat(self, text: str) -> str:
        return self._call("POST", "/api/chat", {"text": text}).get("reply", "")

    def state(self) -> dict:
        return self._call("GET", "/api/state")
