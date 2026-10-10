#!/usr/bin/env python3
"""Export a static dashboard, optionally resuming collection from published JSON."""
import argparse
import hashlib
import json
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from server import ROOT, SOURCES, Collector, Store, normalize, now


def restore(store, directory):
    """Restore exact capture times and IDs, so scheduled runs extend existing history."""
    previous_path = directory / "dashboard.json"
    if not previous_path.exists():
        return
    previous = json.loads(previous_path.read_text())
    with store.connect() as db:
        for source in previous["sources"]:
            captures = json.loads((directory / "exports" / f'{source["id"]}.json').read_text())
            if len(captures) != len(source["history"]):
                raise ValueError(f'History/export mismatch for {source["id"]}')
            for capture, snapshot in zip(captures, source["history"]):
                if capture["captured"] != snapshot["captured"]:
                    raise ValueError("Capture timestamp mismatch")
                raw = capture["data"]
                payload = json.dumps(raw, ensure_ascii=False, sort_keys=True)
                db.execute("INSERT INTO snapshots(id,source,captured,digest,raw,normalized) VALUES(?,?,?,?,?,?)",
                           (snapshot["id"], source["id"], capture["captured"],
                            hashlib.sha256(payload.encode()).hexdigest(), payload,
                            json.dumps(normalize(raw, source["kind"]), ensure_ascii=False)))
            status = source.get("status", {})
            if status:
                db.execute("INSERT INTO status VALUES(?,?,?,?)", (source["id"], status.get("checked"),
                           status.get("success"), status.get("error")))


def export_site(store, directory):
    # Freeze writers briefly so chart history and raw exports describe one snapshot.
    with store.connect() as lock:
        lock.execute("BEGIN IMMEDIATE")
        dashboard = store.dashboard(300, None)
        exports = {s["id"]: store.export(s["id"]) for s in SOURCES}
        archived = store.export("jundrov-city-votes")
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "exports").mkdir(exist_ok=True)
    for name in ("app.js", "style.css"):
        shutil.copyfile(ROOT / "static" / name, directory / name)
    html = (ROOT / "static" / "index.html").read_text()
    html = html.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n  <meta name="published-dashboard" content="true">')
    html = html.replace("Saved automatically", "Saved on GitHub automatically")
    (directory / "index.html").write_text(html)
    (directory / ".nojekyll").touch()
    dashboard["publishedAt"] = now()
    (directory / "dashboard.json").write_text(json.dumps(dashboard, ensure_ascii=False, indent=2) + "\n")
    for source in SOURCES:
        captures = exports[source["id"]]
        (directory / "exports" / f'{source["id"]}.json').write_text(json.dumps(captures, ensure_ascii=False, indent=2) + "\n")
    if archived:
        (directory / "exports" / "jundrov-city-votes.json").write_text(json.dumps(archived, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--collect", action="store_true", help="Restore published history, fetch new official results, then export")
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "elections.sqlite3")
    parser.add_argument("--output", type=Path, default=ROOT / "docs")
    args = parser.parse_args()
    if args.collect:
        with tempfile.TemporaryDirectory() as temporary:
            store = Store(Path(temporary) / "elections.sqlite3")
            restore(store, args.output)
            collector = Collector(store, 300)
            with ThreadPoolExecutor(max_workers=3) as pool:
                list(pool.map(collector.capture, SOURCES))
            export_site(store, args.output)
    else:
        if not args.db.exists():
            parser.error(f"Database does not exist: {args.db}")
        export_site(Store(args.db), args.output)
    print(f"Published static dashboard in {args.output}")


if __name__ == "__main__":
    main()
