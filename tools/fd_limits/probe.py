#!/usr/bin/env python3
"""FanDuel betslip max-wager probe.

Reads the per-market maximum wager FanDuel reveals in the betslip when an
oversized stake is typed, for a configured sample of markets, and archives
both the reading and every betslip-related API response (the captures are
what lets a later version skip the browser entirely).

Runs ONLY on the owner's machine, against the owner's logged-in session —
FanDuel is geo-fenced and authenticated, so this cannot run in the repo's
remote environment. It types stakes but NEVER places a bet: any control
whose name looks like place/confirm/submit is refused by a hard guard.

Usage:
  python3 probe.py --setup                 # open browser, log in once, exit
  python3 probe.py --discover --url URL    # list clickable selections on a page
  python3 probe.py                         # run probes from probes.json
  python3 probe.py --cdp http://localhost:9222   # attach to your own Chrome

Outputs (created next to this script):
  fd_limits.csv    one row per probe: label, selection, max detected, method
  captures/        raw JSON of betslip-related API responses + screenshots
"""
import argparse
import csv
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("playwright not installed: pip install playwright "
             "(no 'playwright install' needed — this drives your own Chrome)")

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.join(HERE, "chrome-profile")
CAPTURES = os.path.join(HERE, "captures")
CSV_PATH = os.path.join(HERE, "fd_limits.csv")
DEFAULT_URL = "https://sportsbook.fanduel.com/basketball/wnba"

# Hard guard: nothing matching this is ever clicked, no matter what a
# selector resolves to. Typing a stake is observation; these are actions.
FORBIDDEN = re.compile(
    r"place|submit|confirm|accept|deposit|withdraw|log\s*in|sign\s*up|join",
    re.I)

MAX_WAGER_RE = re.compile(
    r"max(?:imum)?[^$\n]{0,60}\$\s*([\d,]+(?:\.\d+)?)", re.I)
CAPTURE_URL_RE = re.compile(r"bet|slip|price|quote|wager|stake", re.I)
CAPTURE_BODY_RE = re.compile(r"max.{0,30}(stake|wager|bet|risk)", re.I)


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def assert_safe(name):
    if name and FORBIDDEN.search(name):
        raise RuntimeError(f"refusing to click forbidden control: {name!r}")


def attach(p, args):
    """Attach to a browser: CDP to the user's own Chrome (stealthiest) or a
    persistent probe profile launched through the installed Chrome."""
    if args.cdp:
        browser = p.chromium.connect_over_cdp(args.cdp)
        ctx = browser.contexts[0] if browser.contexts \
            else browser.new_context()
        return browser, ctx
    ctx = p.chromium.launch_persistent_context(
        PROFILE, channel="chrome", headless=False,
        ignore_default_args=["--enable-automation"],
        args=["--disable-blink-features=AutomationControlled"],
        viewport=None)
    return None, ctx


def get_page(ctx):
    return ctx.pages[0] if ctx.pages else ctx.new_page()


def candidate_buttons(page):
    """All odds-shaped clickable selections on the page: (locator, name)."""
    out = []
    for el in page.locator("[role='button'], button").all():
        try:
            if not el.is_visible():
                continue
            name = (el.get_attribute("aria-label")
                    or el.inner_text(timeout=500) or "").strip()
        except Exception:
            continue
        # a selection button carries american odds somewhere in its name
        if name and re.search(r"[+−-]\d{2,4}", name):
            out.append((el, re.sub(r"\s+", " ", name)))
    return out


def discover(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)
    cands = candidate_buttons(page)
    log(f"{len(cands)} selection-like buttons on {url}")
    for i, (_, name) in enumerate(cands):
        print(f"  [{i:3d}] {name}")
    print("\nWrite probes.json 'match' regexes against these names.")


def clear_betslip(page):
    """Remove every selection currently in the slip (never touches the
    forbidden controls)."""
    for _ in range(10):
        btns = page.locator(
            "[aria-label*='emove' i], [title*='emove' i]").all()
        live = [b for b in btns if b.is_visible()]
        if not live:
            break
        name = live[0].get_attribute("aria-label") or "remove"
        assert_safe(name)
        try:
            live[0].click(timeout=3000)
        except Exception:
            break
        page.wait_for_timeout(800)


def find_stake_input(page):
    for sel in ("input[aria-label*='stake' i]",
                "input[aria-label*='wager' i]",
                "input[placeholder*='wager' i]",
                "input[inputmode='decimal']",
                "input[type='tel']"):
        loc = page.locator(sel)
        for el in loc.all():
            if el.is_visible():
                return el
    return None


def betslip_text(page):
    """Text of the betslip region — or the whole page as a fallback, so the
    raw material is always archived even when selectors miss."""
    for sel in ("[class*='betslip' i]", "[data-test*='betslip' i]", "aside"):
        loc = page.locator(sel)
        for el in loc.all():
            try:
                if el.is_visible():
                    txt = el.inner_text(timeout=2000)
                    if txt.strip():
                        return txt
            except Exception:
                continue
    return page.locator("body").inner_text(timeout=5000)


def extract_max(slip_txt, input_el, typed):
    """(max, method): explicit 'maximum wager $X' message, else a clamped
    input value (some flows silently cap the field), else nothing."""
    m = MAX_WAGER_RE.search(slip_txt)
    if m:
        return float(m.group(1).replace(",", "")), "message"
    try:
        val = re.sub(r"[^\d.]", "", input_el.input_value())
        if val and float(val) < typed:
            return float(val), "clamped"
    except Exception:
        pass
    return None, "none"


def harvest(responses, label, run_id):
    """Persist betslip-relevant JSON responses collected during one probe.
    Bodies are fetched here, after the interaction, not inside the event
    handler (sync API restriction)."""
    os.makedirs(CAPTURES, exist_ok=True)
    idx_path = os.path.join(CAPTURES, "index.jsonl")
    saved = 0
    with open(idx_path, "a") as idx:
        for i, resp in enumerate(responses):
            try:
                ctype = resp.headers.get("content-type", "")
                if "json" not in ctype:
                    continue
                url_hit = bool(CAPTURE_URL_RE.search(resp.url))
                body = resp.text()
                if len(body) > 2_000_000:
                    continue
                if not url_hit and not CAPTURE_BODY_RE.search(body):
                    continue
                fn = f"{run_id}_{label}_{i:03d}.json"
                with open(os.path.join(CAPTURES, fn), "w") as f:
                    f.write(body)
                idx.write(json.dumps({"ts": utcnow(), "probe": label,
                                      "url": resp.url,
                                      "status": resp.status,
                                      "file": fn}) + "\n")
                saved += 1
            except Exception:
                continue
    return saved


def run_probe(page, probe, stake, run_id, debug):
    label = probe["label"]
    url = probe.get("url", DEFAULT_URL)
    pattern = re.compile(probe["match"], re.I)
    nth = int(probe.get("nth", 0))

    if page.url.split("#")[0] != url:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
    clear_betslip(page)

    hits = [(el, name) for el, name in candidate_buttons(page)
            if pattern.search(name)]
    if len(hits) <= nth:
        log(f"  {label}: no selection matching /{probe['match']}/ "
            f"({len(hits)} hits, wanted #{nth}) — SKIP")
        return {"label": label, "selection": "", "max": "",
                "method": "no-match", "notes": f"{len(hits)} hits"}
    el, name = hits[nth]
    assert_safe(name)
    log(f"  {label}: clicking {name!r}")

    responses = []
    handler = lambda r: responses.append(r)   # noqa: E731 — collect only
    page.on("response", handler)
    try:
        el.click(timeout=10000)
        page.wait_for_timeout(3000)
        inp = find_stake_input(page)
        if inp is None:
            log(f"  {label}: no stake input found — SKIP (see screenshot)")
            page.screenshot(
                path=os.path.join(CAPTURES, f"{run_id}_{label}_noinput.png"),
                full_page=False)
            return {"label": label, "selection": name, "max": "",
                    "method": "no-input", "notes": ""}
        inp.click()
        inp.fill(str(stake))
        page.keyboard.press("Tab")
        page.wait_for_timeout(3500)

        slip = betslip_text(page)
        mx, method = extract_max(slip, inp, stake)
        os.makedirs(CAPTURES, exist_ok=True)
        with open(os.path.join(CAPTURES,
                               f"{run_id}_{label}_slip.txt"), "w") as f:
            f.write(slip)
        if debug or mx is None:
            page.screenshot(
                path=os.path.join(CAPTURES, f"{run_id}_{label}.png"),
                full_page=False)
        inp.fill("")
        clear_betslip(page)
    finally:
        page.remove_listener("response", handler)
    n_cap = harvest(responses, label, run_id)
    log(f"  {label}: max={mx} ({method}), {n_cap} API captures")
    return {"label": label, "selection": name,
            "max": mx if mx is not None else "", "method": method,
            "notes": f"{n_cap} captures"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--config", default=os.path.join(HERE, "probes.json"))
    ap.add_argument("--stake", type=float, default=1_000_000)
    ap.add_argument("--cdp", help="attach to a running Chrome, e.g. "
                    "http://localhost:9222 (see README)")
    ap.add_argument("--setup", action="store_true",
                    help="open the probe browser to log in, then exit")
    ap.add_argument("--discover", action="store_true",
                    help="list selection buttons on --url and exit")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--delay", type=float, default=6.0,
                    help="mean seconds between probes (jittered)")
    ap.add_argument("--limit", type=int, default=12,
                    help="max probes per run — keep this small")
    ap.add_argument("--debug", action="store_true",
                    help="screenshot every probe")
    args = ap.parse_args()

    with sync_playwright() as p:
        browser, ctx = attach(p, args)
        page = get_page(ctx)

        if args.setup:
            page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
            input("Log in to FanDuel in the opened browser, pass the "
                  "geolocation check, then press Enter here to save the "
                  "profile and exit... ")
            ctx.close()
            return

        if args.discover:
            discover(page, args.url)
            ctx.close()
            return

        with open(args.config) as f:
            cfg = json.load(f)
        probes = cfg["probes"][:args.limit]
        if len(cfg["probes"]) > args.limit:
            log(f"NOTE: config has {len(cfg['probes'])} probes; running the "
                f"first {args.limit} (--limit). Small samples on purpose.")
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")

        page.goto(args.url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        if re.search(r"log in|join now", page.locator("body")
                     .inner_text(timeout=10000), re.I):
            input("Looks like you're not logged in. Log in in the browser "
                  "window, then press Enter to continue... ")

        rows = []
        for i, probe in enumerate(probes):
            log(f"probe {i + 1}/{len(probes)}")
            row = {"ts_utc": utcnow(), "stake_typed": args.stake,
                   "url": probe.get("url", DEFAULT_URL)}
            try:
                row.update(run_probe(page, probe, args.stake, run_id,
                                     args.debug))
            except Exception as ex:
                log(f"  {probe['label']}: ERROR {ex}")
                row.update({"label": probe["label"], "selection": "",
                            "max": "", "method": "error", "notes": str(ex)})
            rows.append(row)
            if i < len(probes) - 1:
                time.sleep(random.uniform(0.6, 1.6) * args.delay)

        clear_betslip(page)
        header = ["ts_utc", "label", "url", "selection", "stake_typed",
                  "max", "method", "notes"]
        new = not os.path.exists(CSV_PATH)
        with open(CSV_PATH, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=header, extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerows(rows)
        log(f"done: {len(rows)} probes -> {CSV_PATH}")
        got = [r for r in rows if r.get("max")]
        for r in got:
            print(f"  {r['label']:32s} ${r['max']}")
        if len(got) < len(rows):
            print(f"  ({len(rows) - len(got)} probes read nothing — check "
                  f"captures/ screenshots and slip texts, then adjust "
                  f"selectors/regexes)")
        ctx.close()


if __name__ == "__main__":
    main()
