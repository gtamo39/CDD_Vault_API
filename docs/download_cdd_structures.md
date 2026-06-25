# `download_cdd_structures.py` — Compound Structure PNG Downloader

Bulk-downloads compound 2D structure PNG images from CDD Vault for a saved
search. Each compound becomes one PNG file under the chosen output
directory. Resume-safe (skips existing files).

Script location: [python/download_cdd_structures.py](../python/download_cdd_structures.py)

Different from [get_library.py](../python/get_library.py): that one exports
collections to a CSV; this one fetches structure images for the molecules
in a **saved search**. The two are independent — pick whichever fits the
task.

---

## Quick start

```bash
# 1. Probe first — confirms token + endpoints without writing any files
python3 python/download_cdd_structures.py \
  --vault 7108 \
  --search 23196193 \
  --token-file ~/.cdd_token \
  --output ~/data/srb_png \
  --discover

# 2. If discover looks healthy, run the real download
python3 python/download_cdd_structures.py \
  --vault 7108 \
  --search 23196193 \
  --token-file ~/.cdd_token \
  --output ~/data/srb_png
```

The `--search` ID is the numeric prefix of the CDD Vault saved-search URL —
e.g. for `https://app.collaborativedrug.com/vaults/7108/searches/23196193-gd…`
it's `23196193`.

---

## CLI arguments

| Flag                 | Type   | Default       | Description                                                                                          |
| -------------------- | ------ | ------------- | ---------------------------------------------------------------------------------------------------- |
| `--vault N`          | int    | _required_    | CDD Vault numeric ID.                                                                                |
| `--search ID`        | str    | _optional_    | Numeric saved-search ID. If omitted, the script falls back to listing every molecule in the vault.   |
| `--token-file PATH`  | path   | _optional_    | File containing the API token on one line.                                                            |
| `--token STRING`     | str    | _optional_    | Token inline (less safe — appears in shell history). Use `--token-file` instead.                     |
| `--output PATH`      | path   | _required_    | Directory to write PNGs (created if absent).                                                          |
| `--size N`           | int    | `600`         | PNG width / height in pixels (square images).                                                         |
| `--workers N`        | int    | `12`          | Parallel download workers. Keep ≤ 12 — the image endpoint rate-limits aggressively at higher values. |
| `--delay SEC`        | float  | `0.0`         | Seconds to sleep between batches per worker. Bump to `0.05` if you see retries.                       |
| `--limit N`          | int    | _no limit_    | Stop after N molecules (smoke test).                                                                  |
| `--name-prefix STR`  | str    | `'SRB-'`      | Prefix used by the optional `--strip-prefix`.                                                          |
| `--strip-prefix`     | flag   | off           | If set, the prefix is removed from filenames (e.g. `SRB-0008912.png` → `0008912.png`).                |
| `--discover`         | flag   | off           | Probe endpoints + sample three molecules, write nothing.                                              |

One of `--token` / `--token-file` / `$CDD_TOKEN` (environment variable) is
required. Recommended: `--token-file ~/.cdd_token` so the token never
appears in shell history.

---

## Modes

### `--discover` mode

Writes nothing. Probes:

1. **Step 1** — hits a basic vault endpoint (`/vaults/{v}` or `/vaults`) to
   verify the token resolves.
2. **Step 2** — tries every known shape for the saved-search endpoint
   (`/vaults/{v}/searches/{id}`, `/vaults/{v}/searches/{id}/molecules`,
   query-param variants) and reports which one returns 200 with molecule data.
3. **Step 3** — pulls one sample molecule and walks the full
   image-fetch lifecycle (job submission → status polling → image retrieval).

Use this **first** on any new vault or token to catch authentication or
endpoint changes before the real run. Sample command:

```bash
python3 python/download_cdd_structures.py \
  --vault 7108 \
  --search 23196193 \
  --token-file ~/.cdd_token \
  --output /home/gtamo/MS_ML/data/srb_png \
  --discover
```

Output is metadata only — status codes, JSON keys, byte counts — no
compound names or structures appear in the terminal.

### Download mode (default)

When `--search` is provided, the script lists all molecules in that saved
search and downloads one PNG per molecule. Filenames default to the
compound name with `.png` appended (e.g. `SRB-0008912.png`); pass
`--strip-prefix` to drop the `SRB-` prefix (`0008912.png`).

If `--search` is omitted, the script paginates every molecule in the
vault — useful only for very small vaults; otherwise narrow with a search.

---

## How a download works (CDD async-job flow)

CDD's image endpoint is asynchronous. For each compound, the script:

1. **Submits the job** —
   `GET /vaults/{v}/molecules/{m}/image?width=W&height=W&format=png`.
   Response is JSON: `{"id": <JOB_ID>, "status": "new", ...}`.
2. **Polls the export endpoint** —
   `GET /vaults/{v}/exports/{JOB_ID}` until the response's `Content-Type`
   becomes `image/png`. Initial poll interval 0.3s, exponentially growing to
   2s, hard deadline 120s per job.
3. **Writes PNG bytes to disk** at `<output>/<compound name>.png`.

Errors are categorised (rate-limit, server error, timeout, no-job-id, …)
and summarised at the end so you know whether to retry or back off.

---

## Resume support

Re-running the same command **skips PNGs that already exist** on disk. This
means you can:

- Interrupt with `Ctrl-C` and re-run later — only missing molecules are
  fetched.
- Run with a small `--limit` first as a smoke test, then re-run without
  `--limit` to finish the rest.
- Recover from rate-limit failures by adjusting `--workers` / `--delay`
  and re-running.

Resume works on **filename** match. If you change `--name-prefix`,
`--strip-prefix`, or anything that affects the filename between runs,
the script won't recognise the older files and will re-fetch.

---

## Privacy notes

- The script runs entirely locally. PNGs land in `--output`, never the wire.
- The CDD API calls go straight to `app.collaborativedrug.com` (the vault
  service). No third parties involved.
- **Terminal output is metadata-only** by design: status codes, error
  reasons, counts, byte sizes, sample molecule **id integers** (digits) —
  never compound names, SMILES, or PNG bytes. Safe to paste into chat
  for debugging.
- The output directory is **not** auto-added to `.gitignore`. If you
  point `--output` at a path inside this repo (e.g. `./png_out`),
  remember to either add it to `.gitignore` yourself or use a path
  outside the repo (the example uses `~/data/srb_png` for that reason).

See [CLAUDE.md](../CLAUDE.md) for the full local-only data policy.

---

## Examples

**Smoke test** before a big run — caps to 5 PNGs:

```bash
python3 python/download_cdd_structures.py \
  --vault 7108 --search 23196193 \
  --token-file ~/.cdd_token \
  --output /tmp/png_smoke \
  --limit 5
```

**Stripped filenames** (`0008912.png` instead of `SRB-0008912.png`) —
handy when piping into downstream tooling that expects bare numeric ids:

```bash
python3 python/download_cdd_structures.py \
  --vault 7108 --search 23196193 \
  --token-file ~/.cdd_token \
  --output ~/data/srb_png \
  --strip-prefix
```

**Larger images** for figures (default is 600 × 600):

```bash
python3 python/download_cdd_structures.py \
  --vault 7108 --search 23196193 \
  --token-file ~/.cdd_token \
  --output ~/data/srb_png_1200 \
  --size 1200
```

**Recovery from rate-limiting** — observed if you see `429_rate_limited`
errors in the breakdown at the end of a run. Drop concurrency and add a
small per-worker delay, then re-run (resume kicks in automatically):

```bash
python3 python/download_cdd_structures.py \
  --vault 7108 --search 23196193 \
  --token-file ~/.cdd_token \
  --output ~/data/srb_png \
  --workers 4 --delay 0.1
```

---

## Troubleshooting

| Symptom in the error breakdown | What it means | Fix |
|---|---|---|
| `submit_429_rate_limited` | API throttled you on job submission | `--workers 4 --delay 0.1`, re-run (resume picks up where you stopped) |
| `submit_http_401` / `_403` | Token rejected or no access to that vault | Regenerate token in CDD; check the vault id |
| `submit_no_job_id:...` | The submit endpoint returned 200 but no job id field — API shape may have changed | Run `--discover` to inspect the response shape and report the keys back |
| `timeout:...:30polls` | Job took longer than 120s, polled 30 times | Possible CDD slowness; re-run later. If persistent, bump the `max_wait` in `fetch_one` |
| `not_png_magic` | Response was 200 + image/png header but bytes aren't a valid PNG | Rare; CDD returned a corrupt blob. Delete the file and re-run |

All errors are summarised at the end of a run with counts — no need to
grep through logs.

---

## When to use this vs the get_library workflow

| Use case | Tool |
|---|---|
| Need a CSV of compound properties, names, SMILES, descriptors | [get_library.py](../python/get_library.py) |
| Need PNG images of compound structures | **this script** |
| Need both | Run them separately — they're independent and complementary |

Both scripts share the same auth (`X-CDD-Token` header, `~/.cdd_token`
default) so the setup work in [docs/documentation.md](documentation.md) is
not duplicated here. See that file for token generation, venv setup, and
the project's data-privacy ground rules.
