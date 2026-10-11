"""Persistence: MongoDB Atlas (write-through) with in-memory primary + fallback.

Collections: experiments, variants, runs, memories, events.
Memory search: Atlas Vector Search ($vectorSearch on memories.embedding) if available,
else cosine similarity in Python.
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .config import env, log, warn_once
from .embed import cosine, embed
from .status import mark

COLLS = ("experiments", "variants", "runs", "memories", "events", "sim_params", "observations")
MEMORY_TYPES = ("semantic", "episodic", "procedural", "taxonomic")
INDEX_NAME = "memories_vector"


class Store:
    def __init__(self) -> None:
        self.mem: dict[str, dict[str, dict]] = {c: {} for c in COLLS}
        self.db = None
        self.vector_ok = False
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.on_loaded = None  # callback after Mongo bootstrap (e.g. apply latest sim_params)
        uri = env("MONGODB_URI")
        if not uri:
            warn_once("mongo", "MONGODB_URI not set; using in-memory store")
            return
        try:
            from pymongo import MongoClient

            client = MongoClient(uri, serverSelectionTimeoutMS=4000, connectTimeoutMS=4000)
            client.admin.command("ping")
            self.db = client[env("MONGODB_DB", "labrat")]
            log.info("MongoDB connected")
            self.pool.submit(self._bootstrap)
        except Exception as e:  # noqa: BLE001
            warn_once("mongo_err", f"MongoDB unavailable ({e}); using in-memory store")
            self.db = None

    def _bootstrap(self) -> None:
        try:
            for d in self.db.memories.find({}, {"_id": 0}).sort("ts", -1).limit(500):
                self.mem["memories"][d["id"]] = d
            for d in self.db.experiments.find({}, {"_id": 0}).sort("ts", -1).limit(50):
                self.mem["experiments"][d["id"]] = d
            self._rehydrate_variants(list(self.mem["experiments"]))
            for coll in ("sim_params", "observations"):
                for d in self.db[coll].find({}, {"_id": 0}).sort("ts", 1).limit(500):
                    self.mem[coll][d["id"]] = d
            if self.on_loaded:
                self.on_loaded()
        except Exception as e:  # noqa: BLE001
            log.warning(f"mongo bootstrap load failed: {e}")
        try:
            from pymongo.operations import SearchIndexModel

            names = [i.get("name") for i in self.db.memories.list_search_indexes()]
            if INDEX_NAME not in names:
                dim = 1024 if env("VOYAGE_API_KEY") else 256
                self.db.memories.create_search_index(
                    SearchIndexModel(
                        name=INDEX_NAME,
                        type="vectorSearch",
                        definition={"fields": [{"type": "vector", "path": "embedding", "numDimensions": dim, "similarity": "cosine"}, {"type": "filter", "path": "type"}]},
                    )
                )
                log.info("created Atlas vector search index")
            self.vector_ok = True
        except Exception as e:  # noqa: BLE001
            warn_once("vec_idx", f"Atlas vector index unavailable ({e}); cosine fallback")

    def _rehydrate_variants(self, exp_ids: list[str]) -> None:
        """Experiments are stored in Mongo without their variants (those live in `variants`, id "<exp>:<vid>"); re-embed them."""
        by_exp: dict[str, list[dict]] = {}
        for d in self.db.variants.find({"experiment_id": {"$in": exp_ids}}, {"_id": 0}):
            vid = str(d.get("id", "")).split(":", 1)[-1]
            v = {k: x for k, x in d.items() if k not in ("experiment_id", "ts")}
            v["id"] = vid
            by_exp.setdefault(d["experiment_id"], []).append(v)
        for eid, vs in by_exp.items():
            exp = self.mem["experiments"].get(eid)
            if exp is not None and not exp.get("variants"):
                exp["variants"] = sorted(vs, key=lambda v: v["id"])
        for exp in self.mem["experiments"].values():
            exp.setdefault("variants", [])

    # --- generic ---
    def put(self, coll: str, doc: dict) -> dict:
        doc.setdefault("id", uuid.uuid4().hex[:10])
        doc.setdefault("ts", time.time())
        self.mem[coll][doc["id"]] = doc
        if self.db is not None:
            snapshot = {k: v for k, v in doc.items() if k != "_id"}
            if coll == "experiments":
                snapshot = {k: v for k, v in snapshot.items() if k != "variants"}
            self.pool.submit(self._write, coll, snapshot)
        return doc

    def _write(self, coll: str, doc: dict) -> None:
        try:
            self.db[coll].replace_one({"id": doc["id"]}, doc, upsert=True)
            mark("mongo", True, f"write {coll} ok")
        except Exception as e:  # noqa: BLE001
            mark("mongo", False, f"write {coll} failed: {e}")
            warn_once(f"mongo_w_{coll}", f"mongo write {coll} failed: {e}")

    def get(self, coll: str, id_: str) -> dict | None:
        return self.mem[coll].get(id_)

    def list(self, coll: str, limit: int = 100) -> list[dict]:
        return sorted(self.mem[coll].values(), key=lambda d: d.get("ts", 0), reverse=True)[:limit]

    def event(self, kind: str, **data: Any) -> None:
        doc = {"kind": kind, **data}
        self.put("events", doc)
        ev = self.mem["events"]
        if len(ev) > 2000:
            for k in list(ev)[:500]:
                ev.pop(k, None)

    # --- memory ---
    async def remember(self, text: str, tags: list[str] | None = None, meta: dict | None = None, type: str = "semantic",
                       source_run_id: str | None = None, confidence: float = 0.7) -> dict:
        """Typed memory: semantic | episodic | procedural | taxonomic (Voyage-embedded)."""
        if type not in MEMORY_TYPES:
            type = "semantic"
        vecs, model = await embed([text], "document")
        return self.put("memories", {"type": type, "text": text, "tags": tags or [], "meta": meta or {}, "embedding": vecs[0],
                                     "embed_model": model, "source_run_id": source_run_id, "confidence": round(float(confidence), 3)})

    async def recall(self, query: str, k: int = 5, type: str | None = None) -> list[dict]:
        vecs, model = await embed([query], "query")
        q = vecs[0]
        if self.db is not None and self.vector_ok:
            try:
                import asyncio

                def run():
                    return list(self.db.memories.aggregate([
                        {"$vectorSearch": {"index": INDEX_NAME, "path": "embedding", "queryVector": q, "numCandidates": 100, "limit": k,
                                           **({"filter": {"type": type}} if type else {})}},
                        {"$project": {"_id": 0, "embedding": 0, "score": {"$meta": "vectorSearchScore"}}},
                    ]))

                res = await asyncio.get_running_loop().run_in_executor(self.pool, run)
                if res:
                    return res
            except Exception as e:  # noqa: BLE001
                warn_once("vec_q", f"$vectorSearch failed ({e}); cosine fallback")
        scored = []
        qw = set(query.lower().split())
        for m in self.mem["memories"].values():
            if type and m.get("type") != type:
                continue
            s = cosine(q, m.get("embedding") or [])
            s += 0.05 * len(qw & set(m["text"].lower().split()))
            scored.append((s, m))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{**{k2: v for k2, v in m.items() if k2 != "embedding"}, "score": round(s, 4)} for s, m in scored[:k]]


store = Store()
