# fd_limits — FanDuel betslip max-wager probe

Measures the per-market **maximum wager** FanDuel reveals in the betslip
when you type an oversized stake (e.g. $1,000,000), for a small configured
sample of markets. Motivation: the max wager is the closest thing FanDuel
has to a published per-market limit, and limits are a standard proxy for
market efficiency. Research context in `PROGRESS.md` (2026-08-17 entry);
neither BettingPros (our odds source) nor any public FanDuel endpoint
carries limit data, so the authenticated betslip is the only readout.

**This tool never places a bet.** It clicks selection buttons, types a
stake, reads the response, and removes the selection. A hard guard refuses
to click anything named place/confirm/submit/deposit/login. Still, read
the risk notes below before running it.

## Where it runs

**Your machine only.** FanDuel requires your logged-in, geolocated
session; the repo's remote environment can't reach fanduel.com at all.
Nothing here is wired into any routine, and it must stay that way.

## Setup (once)

```bash
pip install playwright        # no `playwright install` needed
cd tools/fd_limits
python3 probe.py --setup      # opens Chrome with a dedicated profile;
                              # log in to FanDuel, pass geolocation,
                              # press Enter in the terminal
```

The login persists in `tools/fd_limits/chrome-profile/` (gitignored) for
later runs.

If FanDuel blocks the Playwright-launched Chrome at login (bot
detection), use the stealthier attach mode instead: quit Chrome, relaunch
it yourself with
`--remote-debugging-port=9222 --user-data-dir="$HOME/fd-probe-profile"`,
log in there, then add `--cdp http://localhost:9222` to every probe.py
command.

## Configure the sample

```bash
python3 probe.py --discover --url https://sportsbook.fanduel.com/basketball/wnba
```

prints every selection button's accessible name on the page. Copy
`probes.example.json` to `probes.json` (gitignored) and point each
probe's `match` regex at a real name. The example's stratification is the
experiment: star prop vs bench prop vs side vs total within WNBA, plus an
NBA/NFL anchor — enough to learn whether the max varies by market class
and within WNBA props at all. If it doesn't vary, stop here: a flat cap
carries no efficiency signal and further probing is pointless exposure.

## Run

```bash
python3 probe.py                 # runs probes.json, max 12 probes
```

Outputs, all in this directory (gitignored except this README and the
example config):

- `fd_limits.csv` — one row per probe: selection, max detected, and how
  (`message` = an explicit "maximum wager $X" text, `clamped` = the input
  silently capped, `none` = nothing found → check the screenshot).
- `captures/` — betslip text per probe, screenshots of misses, and **raw
  JSON of every betslip-related API response** (`index.jsonl` maps files
  to URLs). These captures are the interesting part: they identify the
  endpoint and field that carries the max, which is what a direct-API
  version (no browser) would be built from. Bring them to a repo session.

Selectors for FanDuel's DOM were written blind and may need one
iteration — every miss archives the betslip text and a screenshot, so a
single run produces what's needed to fix them.

## Risk notes (read once, seriously)

- The number you read is `min(house market cap, YOUR account's cap)`.
  While the account is unprofiled it approximates the house cap; if
  FanDuel ever limits the account, this data silently changes meaning.
- Automating an authenticated account is against FanDuel's ToS. The
  known profiling triggers are winning and bet-timing patterns, not
  betslip browsing, but the account this runs on is the account the live
  experiment bets from. Hence the defaults: ≤12 probes per run, jittered
  6s delays, no scheduling, no routine integration. Keep it that way,
  and prefer running it occasionally over running it often.
- If the max-wager numbers turn out flat across markets, delete the
  probes and don't run it again — the signal doesn't exist at FanDuel
  and the exposure buys nothing.
