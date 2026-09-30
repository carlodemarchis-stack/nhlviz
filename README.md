# NHL Season Film + Playoff Race

The NHL entry in the AGWAS sport-viz family, at **nhl.aguywithascarf.com**. One app, two
ways to read a season:

- **`index.html` — the Film.** A cover, the 100 leading scorers, the 15 goalies with the
  most wins, six charts that rank players, all 32 teams in order of points, then the
  standings. Horizontal, deep-linkable, keyboard-driven. Everyone else who played has an
  unlisted card, fetched per team on demand from `data/roster/`.
- **`towers.html` — the Playoff Race** (and the **Bracket**). One bar per team: points won,
  striped on to every point still possible, against the playoff line in the NHL's own
  format (3 per division + 2 wild cards). The file keeps its old name so links still work;
  the original towers were removed on 2026-09-30 — at 82 games a box was ~3px and no game
  could be read. The film's Team stats **season barcode** is the game-by-game view.

Built for **2025-26** (complete) and **2026-27** (live, 84 games a team — the first
84-game season), behind a season switcher.

## Build it

```bash
python3 tools/fetch_nhl.py teams 2025-26        # 32 teams + final standings
python3 tools/fetch_nhl.py schedule 2025-26
python3 tools/fetch_nhl.py games 2025-26        # boxscore + play-by-play, ~140s, cached gzipped
python3 tools/fetch_nhl.py stats 2025-26        # the league's own season summaries
python3 tools/build_cards.py 2025-26            # reconciles everything it derives (see below)
python3 tools/fetch_images.py crests
python3 tools/fetch_images.py heads 2025-26
python3 tools/validate.py 2025-26               # MUST pass
python3 render.py 2025-26                       # writes every season's pages; 2025-26 is the landing
```

`scratch/` (gitignored) holds the raw payloads. The live season is refreshed by
`.github/workflows/update.yml` twice a day: standings, schedule, the new games, stats,
rebuild, gate, commit.

## One source, and its one quirk

Everything comes from the NHL's public APIs: `api-web.nhle.com/v1` (schedules,
standings, boxscores, play-by-play) and `api.nhle.com/stats/rest/en` (season summaries,
bios), images from `assets.nhle.com`. No key.

- **It 403s urllib's default `Python-urllib/x.y` User-Agent** — the mirror image of ESPN
  on the NBA film, which 403s everything *except* that. Any other UA works; the tools send
  `agwas-nhl/1.0`.
- The `/now` style paths answer with a 307; follow redirects.
- A season is `20252026`; a playoff game id's last three digits are round, series, game.

## What is reconciled, and against what

`build_cards.py` rebuilds every number game by game and checks it against the league:

```
skaters   940/940  games, goals, primary+secondary assists, points = season summary
goalies    15/15   games, shots against, goals against, wins
records    32/32   W-L-OT and points = official standings        (validate.py)
pointers   every game-log entry resolves to a game on the same date
```

Traps it handles, each of which produced a wrong number once:

1. **A skater listed at 00:00 did not play.** Kucherov is in one box score with no
   shifts; counting it gave him 77 games to the league's 76.
2. **Shootout goals are not goals** — excluded from every tally and from the rink.
3. **A playoff overtime loss is a loss.** Only the regular season has an "OT loss" worth a
   point.
4. **The play-by-play and the box score disagree by a shot now and then** (McDavid: three
   located shots on goal in a game the box score credits with two). 89 of the 100 carded
   skaters match exactly, the rest within 2. The card prints the official total; the rink
   draws what was located.
5. **The backup goalie is in every box score** at 00:00 — dropped, or every goalie would
   have played 82 games.

## The rink

Every located shot, rotated so it travels towards the net on the right whichever end his
team attacked that period (`homeTeamDefendingSide` + the shooting team). Verified over
22,000 shots: every offensive-zone shot lands in the right-hand zone. Shots from a
player's own half (~3%, mostly dump-ins a goalie had to handle) are counted in the header
rather than drawn — pinned to the red line they stacked into a false hot spot. Packed 3
base-62 chars per shot: `(x*87 + y+43)*3 + kind`, kind 0 saved / 1 goal / 2 missed.

Colours: **teal is a goal everywhere** (the points split, the rink, shot volume), violet
the primary assist, pink the secondary, pale ink a saved shot, amber an OT loss.

## Independence

This project shares no files with `nba-cards/`. The template was derived from the NBA
film once and is now owned here; nothing is imported, linked or read across. Its own
repo, domain, localStorage keys (`nhl.*`, `nhltow.*`) and analytics site (`UMAMI_ID` in
`render.py` — the tag is dropped while it is empty, never pointed at another app's site).
