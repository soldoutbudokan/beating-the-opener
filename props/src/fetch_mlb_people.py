"""Fetch MLB player bio (handedness, birth date, position) from the free
MLB StatsAPI, for every player id that shows up in this repo's box scores.

Why: pitcher_box_*.parquet / batter_box_*.parquet (written by fetch_mlb.py)
carry a bare numeric `pid` with no handedness or position attached, which
first-principles pricing needs (platoon splits, positional baselines).
The `/people` endpoint answers that per-id, batched.

Output (data/mlb/people.parquet, gitignored, regenerable):
  pid, name, pitch_hand ('L'/'R'/'S'), bat_side ('L'/'R'/'S'), birth_date, pos

Idempotent: existing pids are kept as-is; only pids missing from the file
are fetched. Refuses to write a file with fewer rows than it already has.

Batch size: verified empirically 2026-08-29 against
  GET https://statsapi.mlb.com/api/v1/people?personIds=1,2,3,...
Batches of 1000 ids succeed (url ~7KB); 1050+ ids returns HTTP 400 Bad
Request (not even a URL-length 414 - the API itself caps the id count
somewhere in (1000, 1050]). We use BATCH=500 to stay well clear of that
edge, with the `get()` retry/backoff from fetch_mlb.py handling transient
failures, plus an additional halving fallback: a batch that still fails
after retries is split in half and retried (down to single ids) so one
bad id in a batch of 500 doesn't blank the other 499.

Usage: python3 src/fetch_mlb_people.py
"""
import glob
import json
import os
import time
import urllib.request

import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
OUT = os.path.join(ROOT, "data", "mlb")
API = "https://statsapi.mlb.com/api/v1"
OUT_PATH = os.path.join(OUT, "people.parquet")
BATCH = 500


def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 - retry any transport error
            wait = 2 ** (i + 1)
            print(f"  retry {i+1} in {wait}s: {e}", flush=True)
            time.sleep(wait)
    return None


def collect_box_pids():
    ids = set()
    patterns = ["pitcher_box_*.parquet", "batter_box_*.parquet"]
    for pat in patterns:
        for f in sorted(glob.glob(os.path.join(OUT, pat))):
            df = pd.read_parquet(f, columns=["pid"])
            ids |= set(int(x) for x in df["pid"].unique())
    return ids


def parse_person(p):
    return {
        "pid": p.get("id"),
        "name": p.get("fullName"),
        "pitch_hand": (p.get("pitchHand") or {}).get("code"),
        "bat_side": (p.get("batSide") or {}).get("code"),
        "birth_date": p.get("birthDate"),
        "pos": (p.get("primaryPosition") or {}).get("abbreviation"),
    }


def fetch_batch(ids):
    """Fetch one batch; on failure, halve and retry (down to size 1).
    Returns (rows, failed_ids)."""
    if not ids:
        return [], []
    url = f"{API}/people?personIds={','.join(str(i) for i in ids)}"
    d = get(url)
    if d is not None:
        people = d.get("people", [])
        rows = [parse_person(p) for p in people]
        got = {r["pid"] for r in rows}
        missing = [i for i in ids if i not in got]
        return rows, missing
    if len(ids) == 1:
        print(f"  FAIL person {ids[0]}", flush=True)
        return [], list(ids)
    mid = len(ids) // 2
    rows1, fail1 = fetch_batch(ids[:mid])
    rows2, fail2 = fetch_batch(ids[mid:])
    return rows1 + rows2, fail1 + fail2


def main():
    os.makedirs(OUT, exist_ok=True)
    # canary: API reachable before touching anything
    if get(f"{API}/teams?sportId=1", tries=2) is None:
        print("FETCH_ABORTED: statsapi.mlb.com unreachable", flush=True)
        raise SystemExit(1)

    box_ids = collect_box_pids()
    print(f"{len(box_ids)} unique pids across box score files", flush=True)

    old = pd.read_parquet(OUT_PATH) if os.path.exists(OUT_PATH) else pd.DataFrame(
        columns=["pid", "name", "pitch_hand", "bat_side", "birth_date", "pos"])
    have = set(int(x) for x in old["pid"].unique()) if len(old) else set()

    missing = sorted(box_ids - have)
    print(f"{len(missing)} pids missing from {OUT_PATH}", flush=True)

    all_rows, all_fails = [], []
    for i in range(0, len(missing), BATCH):
        chunk = missing[i:i + BATCH]
        rows, fails = fetch_batch(chunk)
        all_rows.extend(rows)
        all_fails.extend(fails)
        time.sleep(0.15)

    new = pd.concat([old, pd.DataFrame(all_rows)], ignore_index=True)
    new = new.drop_duplicates("pid", keep="last").reset_index(drop=True)

    if len(old) and len(new) < len(old):
        raise RuntimeError(f"people.parquet would shrink ({len(old)} -> "
                            f"{len(new)}) - refusing")

    new.to_parquet(OUT_PATH)

    fetched = len(missing) - len(all_fails)
    print(f"+{fetched} people fetched ({len(all_fails)} failed) -> "
          f"{len(new)} total rows in {OUT_PATH}", flush=True)
    if all_fails:
        print(f"  no data returned for pids: {sorted(all_fails)}", flush=True)

    still_missing = box_ids - set(int(x) for x in new["pid"].unique())
    if not still_missing:
        print("PEOPLE_COMPLETE", flush=True)
    else:
        print(f"PEOPLE_PARTIAL ({len(still_missing)} missing)", flush=True)


if __name__ == "__main__":
    main()
