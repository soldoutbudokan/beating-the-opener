# Source clock contracts

Decision: keep historical source availability separate from game and retrieval times. 2025 is reused development data; the experiment inventory is in the main audit and results.json.

| Column | Zone / unit | Parsing | Meaning |
| --- | --- | --- | --- |
| `bp.created` | UTC | Timezone-aware instant; normalize to UTC; naive input only under this documented source zone | provider-reported opener creation |
| `bp.updated` | UTC | Timezone-aware instant; normalize to UTC; naive input only under this documented source zone | provider-reported quote update |
| `bp.scheduled` | UTC | Timezone-aware instant; normalize to UTC; naive input only under this documented source zone | scheduled tip |
| `bp.utc` | UTC | Timezone-aware instant; normalize to UTC; naive input only under this documented source zone | response timestamp, not historical availability |
| `bp.ts` | UTC | Finite epoch seconds; reject milliseconds/out-of-range | response epoch seconds |
| `wehoop.date` | UTC | Timezone-aware instant; normalize to UTC; naive input only under this documented source zone | schedule tip |
| `wehoop.game_date_time` | America/New_York | Timezone-aware instant; normalize to UTC; naive input only under this documented source zone | Eastern schedule tip |
| `wehoop.game_date` | America/New_York | ISO calendar date, never converted to midnight | game calendar date only |
| `wehoop.wallclock` | explicit offset required | Timezone-aware instant; normalize to UTC | event wall clock, not publication time |
| `wehoop.completed_at` | explicit offset required | Timezone-aware instant; normalize to UTC | completion instant, if provided |
| `source.retrieved_at` | explicit offset required | Timezone-aware instant; normalize to UTC | local retrieval, not historic publication |
| `source.available_at` | explicit offset required | Timezone-aware instant; normalize to UTC | observed or explicitly assumed availability |
| `espn.captured_utc` | UTC | Timezone-aware instant; normalize to UTC; naive input only under this documented source zone | normalized snapshot capture |
| `espn.event.date` | explicit offset required | Timezone-aware instant; normalize to UTC | event scheduled tip |
| `espn.utc_tip` | explicit offset required | Timezone-aware instant; normalize to UTC | fallback box scheduled tip |
| `espn.box.date` | America/New_York | ISO calendar date, never converted to midnight | fallback slate calendar date |
| `espn.return_date` | local calendar; no instant | ISO calendar date, never converted to midnight | return timeline; not observation time |
| `news.published` | explicit offset required | Timezone-aware instant; normalize to UTC | provider-reported news publication |
| `override.added` | UTC | Timezone-aware instant; normalize to UTC; naive input only under this documented source zone | human entry timestamp |
| `override.game_date` | America/New_York | ISO calendar date, never converted to midnight | target date; null means until cleared |
| `pm.start_date` | explicit offset required | Timezone-aware instant; normalize to UTC | market start, not match start |
| `pm.accepting_orders_ts` | explicit offset required | Timezone-aware instant; normalize to UTC | market opens for orders |
| `pm.end_date` | explicit offset required | Timezone-aware instant; normalize to UTC | scheduled market end; not verified resolution |
| `pm.closed_time` | explicit offset required | Timezone-aware instant; normalize to UTC | provider-reported closure; resolution semantics unverified |
| `pm.game_start_label` | explicit offset required | Timezone-aware instant; normalize to UTC | nominal match start; known hour errors |
| `pm.t` | UTC | Finite epoch seconds; reject milliseconds/out-of-range | price observation epoch seconds |
| `pm.onset_utc` | explicit offset required | Timezone-aware instant; normalize to UTC | inferred from future price path; not observed start |
| `cricsheet.date` | local calendar; no instant | ISO calendar date, never converted to midnight | venue-local match date; hour/zone unavailable |
| `forecast.as_of` | explicit offset required | Timezone-aware instant; normalize to UTC | strict forecast information cutoff |
| `quote.open_created_over` | explicit offset required | Timezone-aware instant; normalize to UTC | normalized over opener timestamp |
| `quote.open_created_under` | explicit offset required | Timezone-aware instant; normalize to UTC | normalized under opener timestamp |
| `quote.tip_at` | explicit offset required | Timezone-aware instant; normalize to UTC | normalized scheduled tip |

`schedule_tip()` requires the UTC and Eastern columns to agree. DST folds/gaps require explicit valid offsets. `before()` rejects equal or later source times. Missing values do not acquire invented times.

Provider timestamps are source assertions. `source.available_at` derived by adding 8/24 hours is an assumption, not observed publication. The original wehoop paired columns still require inspection from the pinned parquets. Tests do not certify source truth.
