# Brno election watch · 2026

**Live website:** https://veridiandynamics.github.io/volby2026/

**Repository:** https://github.com/VeridianDynamics/volby2026

## Automatic remote publishing

GitHub Actions runs `.github/workflows/publish.yml` on pushes and on a five-minute schedule. Each run restores the previously published capture history, fetches all three official feeds, commits updated data under `docs/`, and deploys the same interactive dashboard to GitHub Pages. It runs on GitHub, so the laptop can be turned off. GitHub's scheduler can delay jobs; the page shows its actual publication time and marks updates delayed after 20 minutes.

The website refreshes its data every minute. Charts, mandate estimates, hover highlighting, detailed results, and JSON exports work just as they do locally. The initial publication includes the history captured on this computer; subsequent website captures are taken by GitHub, independently of the local 60-second collector. No credentials are embedded in the published files. The workflow uses GitHub's built-in job token.

To trigger an immediate update:

```sh
gh workflow run publish.yml --repo VeridianDynamics/volby2026
```

To stop periodic publishing:

```sh
gh workflow disable publish.yml --repo VeridianDynamics/volby2026
```

`python3 publish.py` exports the local database to `docs/`. `python3 publish.py --collect` resumes from the exported JSON in a temporary database and adds one new collection cycle. Git history retains each committed publication, while the JSON exports retain each changed official result payload.

An auto-refreshing dashboard for Jundrov district council, Brno city council, and Senate district 60. Python 3.10+ is the only requirement: no packages, build step, API keys, or external chart CDN.

## Run

```sh
python3 server.py
```

Open **http://localhost:8765**. The collector starts immediately and checks the three official JSON feeds concurrently every 60 seconds. The browser checks the local server every 10 seconds.

Keep the server running to keep recording. Collection continues with the browser closed; restarting the server resumes the existing history. Stopping the server or putting the computer to sleep stops collection. Historical results from before the first capture cannot be reconstructed by this app.

Options:

```sh
python3 server.py --port 8080 --interval 30 --db /absolute/path/elections.sqlite3
```

The default binding is localhost. To view it from other devices on your network, add `--host 0.0.0.0` and visit the server's IP address and port.

## What it shows

- **Brno-Jundrov:** the **Jundrov district-council election**, with provisional party mandates for its 15 seats (8 needed for a majority). This replaces the originally supplied city-election view following clarification.
- **Brno:** city-council results across all of Brno, with provisional mandates for its 55 seats (28 needed for a majority).
- **Senate district 60:** Brno-město, with a round selector if the source publishes multiple rounds.
- Current standings, vote totals, turnout, counted precincts, and percentage-point changes since the previous captured source update.
- Full-width vote-share charts with party names and shares at line ends. Hover over a name to highlight its line. Select all history or the last 1, 3, or 6 hours. Detailed tables and counting-progress / turnout charts are expandable.
- Per-feed fetch errors with last-known results retained and automatic retry.

Municipal totals count candidate votes, not voters. Turnout is for processed precincts. Unreported results are shown as waiting rather than a fabricated zero-share trend. The charts use actual capture time, shown in Europe/Prague; source-generation timestamps are also displayed separately.

## Persistence and export

`data/elections.sqlite3` is created automatically. SQLite stores complete original JSON payloads (including municipal candidate-level votes), normalized chart data, UTC capture timestamps, and collection status. Each changed payload creates a snapshot; identical consecutive payloads only update the successful-check timestamp. Corrections and reversions are retained. There is no automatic history deletion.

Older Jundrov captures from the Brno city-election feed are preserved under the `jundrov-city-votes` source key in SQLite. They are excluded from the new district-council history, which starts when collection of the corrected feed begins.

### Council mandate calculation

The estimate applies [§45 of Act 491/2001](https://www.zakonyprolidi.cz/cs/2001-491#p45) to counted votes: D’Hondt divisors 1, 2, 3, …; a 5% threshold adjusted to each list's candidate count (capped at the council size); statutory threshold reductions; and no more seats than candidates. Exact rational arithmetic avoids floating-point quotient ties. Equal quotients favour higher total votes; if a lottery would affect the last seat, the affected parties show ranges. Published official mandates (`vysledky[][7]` when `zvoleno` is true) supersede the estimate. Before any votes arrive, it shows a waiting state. This estimates the allocation **if counted votes were the final result**, rather than predicting the votes still to arrive.

Both Jundrov and Brno council charts grey out parties whose current seat allocation is zero, including their line-end labels. Parties with an unresolved seat range remain colored if they could receive a seat. Senate candidates retain their colors because municipal seat-allocation rules do not apply to that election. Existing council snapshots are backfilled with derived mandate data on startup without changing their original payloads or capture times.

Use **Export snapshots** on each election to download all its raw payloads with capture timestamps as JSON. For a database backup while running, use SQLite's backup API:

```sh
python3 -c "import sqlite3; src=sqlite3.connect('data/elections.sqlite3'); dst=sqlite3.connect('elections-backup.sqlite3'); src.backup(dst); dst.close(); src.close()"
```

## Data feeds

The feeds are the same public endpoints used by the official single-page applications:

```text
https://volby.gov.cz/appdata/kv2026/20261009/vysled/6202/551171.json
https://volby.gov.cz/appdata/kv2026/20261009/vysled/6202/582786.json
https://volby.gov.cz/appdata/senat/20261009/vysled/obvod/60.json
```

Source configuration and the positional-array adapters live in `server.py`. The UI lives in `static/`. Invalid payloads do not replace the last good snapshot. This is a local personal dashboard, with read-only HTTP endpoints.

## Checks

```sh
python3 -m unittest discover -s tests -v
node --check static/app.js
```
