# Design notes index

_Moved from the old CLAUDE.md ("Design notes")._

`docs/design/NN-*.md`, one per addendum, in the order they were written. Each
records what was decided, what was measured, and what was verified. Where an
older note and a newer one disagree, the newer one wins, and this file wins
over both.

| # | Note | Covers |
|---|------|--------|
| 00 | `v1-brief` | The original brief, before any addendum |
| 01 | `rtt-failover` | Source abstraction, `SourceManager`, failover, status fields (its RTT API half is superseded by 02) |
| 02 | `rtt-next-generation` | RTT v2 API, token exchange, status mapping, rate-limit guard |
| 03 | `board-layout-and-theme-contract` | Sizing model, **theme module contract**, per-theme specifics, reasons, checklist |
| 04 | `rtt-allowance` | Never spend RTT calls locally; tests offline; recording fixtures |
| 05 | `palette` | The seven colour roles, `colours.js`, admin swatches and contrast |
| 06 | `profiles` | Time-of-day profiles, `ConfigStore.active()`, poller switching, admin editor |
| 07 | `position-and-formation` | Where the train is, coaches and loading, from LDBWS |
| 08 | `journey-and-formation` | Full journey on thameslink, car strip, consecutive-failure failover fix |
| 09 | `route-scrolling` | thameslink `scroll_route` |
| 10 | `station-lookup` | `stations.json` from NaPTAN, the admin combobox |
| 11 | `bar-colour-role` | thameslink's `bar` colour role |
| 12 | `calling-point-times` | `show_calling_times` and the time rules |
| 13 | `credentials-and-updates` | Write-only keys from /admin; updates from GitHub |
| 14 | `splitflap-canvas` | Flaps blitted from a tile sheet; the measurements |
| 15 | `weather` | Open-Meteo strip on modern and thameslink |

A new feature gets the next number. Write it the way the others are: the
decision, the reason, the numbers measured, the trap found, and how it was
checked.
