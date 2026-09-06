# The data behind this experiment

The new data describes what happened within games: where players shot, which
shots followed a teammate's assist, which teammates supplied those assists,
how players were substituted, and how their actions changed late in games.
That is more informative than a list of final points, rebounds and assists.
It still does not tell us everything. In particular, it does not measure every
pass, every touch, who defended each possession, or who was expected to miss
the next game.

This file records what was actually downloaded and checked. A source being
available does not mean its extra information improves predictions. The model
comparison tests that separately.

## What we downloaded

The main source is the public
[SportsDataverse WNBA archive](https://github.com/sportsdataverse/wehoop-wnba-data),
which collects ESPN game data. We froze its version at
[commit db0d492](https://github.com/sportsdataverse/wehoop-wnba-data/commit/db0d4921daa58aec06da2931eec713d9e9a52c6e).
All downloads use that full commit identifier, rather than a moving `main`
branch. The data totals 48,271,529 bytes across 85 files.

| Data | Seasons downloaded | Rows | Purpose |
| --- | --- | ---: | --- |
| Player game records | 2003–2025 | 120,980 | Minutes, production, shooting opportunities and prior roles |
| Team game records | 2003–2025 | 10,594 | Opponent pace, shot opportunities, rebounding and team style |
| Game schedules | 2003–2025 | 5,397 | Game identifiers, tip times, home/away and dates |
| Play-by-play events | 2010–2025 | 1,402,249 | Shot types and locations, assisted scoring, substitutions and game situations |

No 2026 file was downloaded. The historical source files include regular-season
and playoff games; the research code decides which observations belong in a
given training or evaluation period.

Run the downloader from the repository root:

```bash
python3 wnba/research/rich_context/sources.py --qc
```

`--qc` checks the files and writes a coverage report. The ordinary download
uses Python's standard library; checking Parquet tables also needs pandas and
pyarrow. Files are kept under `wnba/data/rich_context/raw/`, which Git ignores.
The downloader leaves earlier odds and box-score archives alone.

The committed [source manifest](results/source_manifest.json) records every
download URL, exact size and SHA-256 checksum. A checksum is a fingerprint of
the file's contents: it lets another person establish that they used the same
data. The [source quality report](results/source_quality.json) records table
sizes, dates, columns, missing game matches and known limitations.

The download routes are:

```text
https://raw.githubusercontent.com/sportsdataverse/wehoop-wnba-data/{commit}/wnba/player_box/parquet/player_box_{year}.parquet
https://raw.githubusercontent.com/sportsdataverse/wehoop-wnba-data/{commit}/wnba/team_box/parquet/team_box_{year}.parquet
https://raw.githubusercontent.com/sportsdataverse/wehoop-wnba-data/{commit}/wnba/schedules/parquet/wnba_schedule_{year}.parquet
https://raw.githubusercontent.com/sportsdataverse/wehoop-wnba-data/{commit}/wnba/pbp/parquet/play_by_play_{year}.parquet
```

## How the records connect

All four tables use ESPN game and team identifiers. Player game records use
`athlete_id`; a play's main participant uses `athlete_id_1`. These identifiers
are much safer than matching names, which can change spelling or collide.

For an assisted made basket, the first participant is the shooter and the
second is the assister. For a blocked attempt, the first is still the shooter,
but the second is the blocker. The description must distinguish those cases.
For a substitution, the first player enters and the second leaves.

The 2025 files contain 312 games with both player records and play-by-play.
The coverage also matches exactly in 2021–2024. A few earlier games have box
scores but no play-by-play: two games each in 2016, 2018 and 2019, and one in
2020. Their identifiers are in the quality report. Missing play-by-play must
not become a claim that the player attempted no shots or made no passes.

We also found one repeated player/game identifier in the 2005 boxes: Adrienne
Goodson is listed under both teams in one game, with missing minutes and
statistics in both rows. That is an inactive-roster artifact, not two observed
performances. Some players who appeared for one or a few minutes have no
recorded actions. This can be a valid quiet appearance rather than a missing
game feed.

## Where the data needs care

**The format changes over time.** Older descriptions say “made,” “missed,”
“22 ft” and “Assisted by.” Newer descriptions say “makes,” “misses,” “22-foot”
and “assists.” A parser that only recognizes the newer language silently loses
old observations. An explicit `points_attempted` field only appears in the
2024–2025 files. Earlier seasons need event types, descriptions and scoring
information. A blocked attempt remains a shot even if its text contains
neither “makes” nor “misses.”

**Coordinates are not automatically valid.** The files contain large numeric
placeholders for missing positions, including values around 214 million.
Coordinates need physical bounds checks. They are recorded event locations,
not continuous measurements of player movement. Shot descriptions and
coordinates also have limited precision; a location near a zone boundary
should not be treated as exact.

**Final box scores and play-by-play sometimes disagree.** Small attribution
changes occur: the same team total can be preserved while an attempt or assist
moves from one player to another. Checking reconstructed shots and assists
against the box score detects these disagreements. The feature builder applies
its own exclusions; the source inventory alone does not certify every event.

**Some columns would answer the question with hindsight.** The archive carries
betting spreads and future-event convenience columns. The supplied loader
leaves those out of play-by-play. A game's eventual minutes, score, lineup,
winner and statistics can describe that completed game for a future forecast;
they cannot be inputs to a prediction made before that same game. Historical
`active` and `reason` fields can also conflict with recorded participation, so
they are not a trustworthy archive of pregame injury information.

**A historical file is not an archive of publication times.** We retrieved
these files after the games. They can contain later corrections. An event's
wall-clock timestamp establishes when the event occurred, not when every
corrected field became available. Wall-clock timestamps are absent from the
2010–2012 files. The model must use prior completed games with a stated delay,
and the report must describe the remaining uncertainty about availability.

## Other sources we checked

These were actual small download probes, not assumptions based on a website
description. They are alternatives for later work; they are not additional
inputs to this experiment unless a model report explicitly says so.

| Source | What the probe returned | Main limitation |
| --- | --- | --- |
| Official WNBA shot-chart API | 499 Rhyne Howard regular-season shot records for 2025, with distance, coordinates, shot zone, type and result | Uses WNBA identifiers, requiring a checked mapping to ESPN; these are events, not defender tracking |
| Official WNBA play-by-play CDN | 519 events for game `1022500001`, with shot distance, coordinates, possession team, explicit assister identifiers and event timestamps | Requires game-by-game collection and separate identifiers; later edits remain possible |
| `shufinskiy/nba_data` public archive | Downloaded the 2025 shot-detail file and 2024–2025 PBP Stats files | The PBP Stats export repeats possession totals across event rows and does not contain the full on-court lineup in the inspected schema |
| PBP Stats software | Its documented parser can reconstruct lineups and possessions from official feeds | Event-order and period-starter corrections are sometimes required; reconstruction needs validation |
| PBP Stats tracking site | Public documentation describes hand-recorded shot and rebound context | Access to that detailed database requires a patron account; it was not used |

The successful official probes were a
[WNBA shot-chart request](https://stats.wnba.com/stats/shotchartdetail?LeagueID=10&Season=2025&SeasonType=Regular+Season&PlayerID=1631009&TeamID=0&ContextMeasure=FGA&LastNGames=0&Month=0&OpponentTeamID=0&Period=0)
and the
[WNBA event feed for game 1022500001](https://cdn.wnba.com/static/json/liveData/playbyplay/playbyplay_1022500001.json).
The alternate bulk archives are documented in
[nba_data](https://github.com/shufinskiy/nba_data). The
[PBP Stats guide](https://pbpstats.readthedocs.io/en/latest/quickstart.html)
explains its identifier scheme and manual corrections; its
[tracking site](https://tracking.pbpstats.com/) explains the restricted data.

The league introduced arena-wide optical tracking for the 2024 season,
according to its [official announcement](https://www.wnba.com/news/wnba-genius-sports-data-tracking).
That does not establish access to a complete public historical database of
touches, potential assists, defender distances or defensive matchups. We did
not obtain or claim to use those measurements. They would be meaningful
additions if a source with reliable coverage and historical timing became
available.
