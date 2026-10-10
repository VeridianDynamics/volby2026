#!/usr/bin/env python3
"""Election collector and dashboard. Python 3.10+, no third-party dependencies."""
import argparse
import hashlib
import json
import logging
import mimetypes
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent
SOURCES = [
    dict(id="jundrov", title="Brno-Jundrov", subtitle="Jundrov district council", kind="municipal",
         page="https://volby.gov.cz/app/kv2026/cs/20261009/results/!__1_6200_6202_551171",
         feed="https://volby.gov.cz/appdata/kv2026/20261009/vysled/6202/551171.json"),
    dict(id="brno", title="Brno", subtitle="City council · all of Brno", kind="municipal",
         page="https://volby.gov.cz/app/kv2026/cs/20261009/results/!__1_6200_6202_582786",
         feed="https://volby.gov.cz/appdata/kv2026/20261009/vysled/6202/582786.json"),
    dict(id="senate", title="Senate · district 60", subtitle="Brno-město · Senate election", kind="senate",
         page="https://volby.gov.cz/app/senat/20261009/cs/results/!_60",
         feed="https://volby.gov.cz/appdata/senat/20261009/vysled/obvod/60.json"),
]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def mandates(raw):
    """List-level allocation under Act 491/2001, §45; exact quotient arithmetic.

    A tie at the cutoff is returned as a seat range, never an invented lottery.
    Published official allocations take precedence over our provisional estimate.
    """
    overview, rows = raw["prehled"], raw["vysledky"]
    seats = int(overview[0])
    result = dict(total=seats, majority=seats // 2 + 1, status="waiting", parties=[])
    if overview[1] != 1:
        return dict(result, status="unavailable")
    if not overview[3] or not overview[12] or not rows:
        return result
    parties = [dict(id=str(r[0]), name=r[1], votes=int(r[2]), candidates=int(r[4])) for r in rows]
    if raw.get("zvoleno") and all(len(r) > 7 and isinstance(r[7], int) and r[7] >= 0 for r in rows):
        return dict(result, status="official", parties=[dict(p, minimum=r[7], maximum=r[7]) for p, r in zip(parties, rows)])
    total_votes = int(overview[12])
    threshold = 5 if len(parties) > 1 else 0
    while True:
        eligible = [p for p in parties if p["candidates"] > 0 and
                    p["votes"] * seats * 100 >= threshold * total_votes * min(p["candidates"], seats)]
        capacity = sum(p["candidates"] for p in eligible)
        if threshold == 0 or (len(eligible) >= 2 and capacity >= result["majority"] and capacity > 5):
            break
        threshold -= 1
    quotients = sorted([(Fraction(p["votes"], divisor), p["votes"], p["id"])
                        for p in eligible for divisor in range(1, min(seats, p["candidates"]) + 1)],
                       key=lambda q: q[:2], reverse=True)
    counts = {p["id"]: 0 for p in parties}
    upper = counts.copy()
    tie = False
    if quotients:
        allocated = min(seats, len(quotients))
        cutoff = quotients[allocated - 1][:2]
        tied = [q for q in quotients if q[:2] == cutoff]
        for q in quotients:
            if q[:2] > cutoff:
                counts[q[2]] += 1
        remaining = allocated - sum(counts.values())
        tie = remaining < len(tied)
        for p in parties:
            n = sum(q[2] == p["id"] for q in tied)
            upper[p["id"]] = counts[p["id"]] + min(n, remaining)
            counts[p["id"]] += max(0, remaining - (len(tied) - n))
    return dict(result, status="provisional", threshold=threshold, tie=tie,
                parties=[dict(p, minimum=counts[p["id"]], maximum=upper[p["id"]], qualified=p in eligible) for p in parties])


def normalize(raw, kind):
    """Decode the positional arrays used by the official 2026 applications."""
    overview = raw["prehled"]
    results = raw["vysledky"]
    if not isinstance(overview, list) or not isinstance(results, list):
        raise ValueError("Unexpected results schema")
    rounds = overview if kind == "senate" else [overview]
    normalized = []
    for p in rounds:
        if len(p) < (11 if kind == "senate" else 13):
            raise ValueError("Unexpected overview schema")
        senate = kind == "senate"
        round_no = int(p[0]) if senate else 1
        if senate and round_no not in (1, 2):
            raise ValueError("Unknown Senate round")
        total, counted, percent = p[1:4] if senate else p[2:5]
        turnout, votes = (p[6], p[9]) if senate else (p[7], p[12])
        entries = []
        for row in results:
            if len(row) < (10 if senate else 4):
                raise ValueError("Unexpected result row")
            vote_idx = (5 if round_no == 1 else 7) if senate else 2
            entries.append(dict(id=str(row[0]), name=row[1], party=row[2] if senate else "",
                                votes=row[vote_idx], share=row[vote_idx + 1]))
        normalized.append(dict(round=round_no, total=total, counted=counted, percent=percent,
                               turnout=turnout if counted else None, votes=votes, entries=entries))
    result = dict(generated=raw["generovano"], description=raw.get("popis", ""), rounds=normalized)
    if (kind == "municipal" and raw.get("param", {}).get("kodzastup") in (551171, 582786)
            and not raw.get("param", {}).get("obec_mc")):
        result["mandates"] = mandates(raw)
    return result


class Store:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY, source TEXT NOT NULL, captured TEXT NOT NULL,
                    digest TEXT NOT NULL, raw TEXT NOT NULL, normalized TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS source_history ON snapshots(source, id);
                CREATE TABLE IF NOT EXISTS status (
                    source TEXT PRIMARY KEY, checked TEXT, success TEXT, error TEXT);
            """)
            # Preserve the previous city-election captures under an archive key.
            # They must never be mixed with the district council's new history.
            moved = db.execute("""UPDATE snapshots SET source='jundrov-city-votes'
                WHERE source='jundrov' AND json_extract(raw, '$.param.kodzastup')=582786
                AND json_extract(raw, '$.param.obec_mc')=551171""").rowcount
            if moved and not db.execute("SELECT 1 FROM snapshots WHERE source='jundrov' LIMIT 1").fetchone():
                db.execute("UPDATE OR REPLACE status SET source='jundrov-city-votes' WHERE source='jundrov'")
            # Backfill derived mandate data without changing original captures.
            for row in db.execute("""SELECT id,raw FROM snapshots
                    WHERE source IN ('jundrov','brno')
                    AND json_type(normalized, '$.mandates') IS NULL""").fetchall():
                derived = normalize(json.loads(row["raw"]), "municipal")
                db.execute("UPDATE snapshots SET normalized=? WHERE id=?",
                           (json.dumps(derived, ensure_ascii=False), row["id"]))

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def save(self, source, raw):
        normalized = normalize(raw, source["kind"])
        payload = json.dumps(raw, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        captured = now()
        with self.connect() as db:
            last = db.execute("SELECT digest FROM snapshots WHERE source=? ORDER BY id DESC LIMIT 1", (source["id"],)).fetchone()
            if not last or last["digest"] != digest:
                db.execute("INSERT INTO snapshots(source,captured,digest,raw,normalized) VALUES(?,?,?,?,?)",
                           (source["id"], captured, digest, payload, json.dumps(normalized, ensure_ascii=False)))
            db.execute("INSERT INTO status VALUES(?,?,?,NULL) ON CONFLICT(source) DO UPDATE SET checked=excluded.checked,success=excluded.success,error=NULL",
                       (source["id"], captured, captured))

    def fail(self, source_id, error):
        with self.connect() as db:
            db.execute("INSERT INTO status(source,checked,error) VALUES(?,?,?) ON CONFLICT(source) DO UPDATE SET checked=excluded.checked,error=excluded.error",
                       (source_id, now(), str(error)))

    def dashboard(self, interval, next_poll):
        sources = []
        with self.connect() as db:
            for source in SOURCES:
                history = [dict(id=r["id"], captured=r["captured"], **json.loads(r["normalized"])) for r in
                           db.execute("SELECT id,captured,normalized FROM snapshots WHERE source=? ORDER BY id", (source["id"],))]
                status = db.execute("SELECT * FROM status WHERE source=?", (source["id"],)).fetchone()
                sources.append(dict(**source, history=history, status=dict(status) if status else {}))
        return dict(sources=sources, interval=interval, nextPoll=next_poll, serverTime=now())

    def export(self, source_id):
        with self.connect() as db:
            return [dict(captured=r["captured"], data=json.loads(r["raw"])) for r in
                    db.execute("SELECT captured,raw FROM snapshots WHERE source=? ORDER BY id", (source_id,))]


class Collector:
    def __init__(self, store, interval):
        self.store, self.interval = store, interval
        self.stop = threading.Event()
        self.next_poll = time.time()

    def capture(self, source):
        try:
            request = Request(source["feed"], headers={"User-Agent": "LocalElectionDashboard/1.0", "Cache-Control": "no-cache", "Accept": "application/json"})
            with urlopen(request, timeout=25) as response:
                raw = json.load(response)
            self.store.save(source, raw)
            logging.info("Captured %s (%s)", source["id"], raw["generovano"])
        except Exception as exc:
            logging.warning("%s: %s", source["id"], exc)
            self.store.fail(source["id"], exc)

    def run(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            while not self.stop.is_set():
                list(pool.map(self.capture, SOURCES))
                self.next_poll = time.time() + self.interval
                self.stop.wait(self.interval)


def handler_for(store, collector):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/api/dashboard":
                return self.send_json(store.dashboard(collector.interval, collector.next_poll))
            if parsed.path == "/api/export":
                source_id = parse_qs(parsed.query).get("source", [""])[0]
                if source_id not in {s["id"] for s in SOURCES}:
                    return self.send_error(400, "Unknown source")
                return self.send_json(store.export(source_id), f"{source_id}-snapshots.json")
            files = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
            if parsed.path not in files:
                return self.send_error(404)
            path = ROOT / "static" / files[parsed.path]
            self.send_bytes(path.read_bytes(), mimetypes.guess_type(path.name)[0] + "; charset=utf-8")

        def send_json(self, value, filename=None):
            self.send_bytes(json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8", filename)

        def send_bytes(self, payload, content_type, filename=None):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--interval", type=int, default=60, help="Seconds between collections (minimum 15)")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "elections.sqlite3")
    args = parser.parse_args()
    if args.interval < 15:
        parser.error("--interval must be at least 15 seconds")
    args.db.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    store = Store(args.db)
    collector = Collector(store, args.interval)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(store, collector))
    thread = threading.Thread(target=collector.run, daemon=True)
    thread.start()
    logging.info("Dashboard: http://%s:%s · database: %s", args.host, args.port, args.db)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        collector.stop.set()
        server.server_close()
        thread.join(timeout=30)


if __name__ == "__main__":
    main()
