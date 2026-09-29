#!/usr/bin/env python3
"""Fetch everything from the NHL's public API into data/ and scratch/.

    python3 tools/fetch_nhl.py teams 2025-26        # 32 teams + final standings
    python3 tools/fetch_nhl.py schedule 2025-26     # every club's full schedule
    python3 tools/fetch_nhl.py games 2025-26        # boxscore + play-by-play per played game
    python3 tools/fetch_nhl.py stats 2025-26        # skater + goalie season summaries

One source, plain curl-able, no UA games: api-web.nhle.com (redirects on the `/now`
style paths, so follow them) and api.nhle.com/stats/rest. The NHL names a season by
BOTH years, 20252026, which is what the label 2025-26 maps to.

Raw game payloads are gzipped into scratch/games/ and never refetched once the game is
final, so an in-season rerun only pulls the new nights.
"""
import gzip
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(ROOT, "scratch", "raw")
GAMES = os.path.join(ROOT, "scratch", "games")
WEB = "https://api-web.nhle.com/v1"
STATS = "https://api.nhle.com/stats/rest/en"

# The API carries no team colours. Brand primary + secondary; near-black primaries are
# lifted for legibility by the page (winColor), as on the NBA film.
COLORS = {
    "ANA": ("#F47A38", "#B9975B"), "BOS": ("#FFB81C", "#111111"),
    "BUF": ("#003087", "#FFB81C"), "CGY": ("#C8102E", "#F1BE48"),
    "CAR": ("#CE1126", "#111111"), "CHI": ("#CF0A2C", "#111111"),
    "COL": ("#6F263D", "#236192"), "CBJ": ("#002654", "#CE1126"),
    "DAL": ("#006847", "#8F8F8C"), "DET": ("#CE1126", "#FFFFFF"),
    "EDM": ("#041E42", "#FF4C00"), "FLA": ("#C8102E", "#B9975B"),
    "LAK": ("#111111", "#A2AAAD"), "MIN": ("#154734", "#A6192E"),
    "MTL": ("#AF1E2D", "#192168"), "NSH": ("#FFB81C", "#041E42"),
    "NJD": ("#CE1126", "#111111"), "NYI": ("#00539B", "#F47D30"),
    "NYR": ("#0038A8", "#CE1126"), "OTT": ("#C52032", "#C2912C"),
    "PHI": ("#F74902", "#111111"), "PIT": ("#FCB514", "#111111"),
    "SJS": ("#006D75", "#EA7200"), "SEA": ("#001628", "#99D9D9"),
    "STL": ("#002F87", "#FCB514"), "TBL": ("#002868", "#FFFFFF"),
    "TOR": ("#00205B", "#FFFFFF"), "UTA": ("#6CACE4", "#111111"),
    "VAN": ("#00205B", "#00843D"), "VGK": ("#B4975A", "#333F42"),
    "WSH": ("#C8102E", "#041E42"), "WPG": ("#041E42", "#004C97"),
}


def sid(label):
    """'2025-26' -> '20252026'"""
    a = int(label[:4])
    return f"{a}{a + 1}"


def get(url, tries=4):
    for i in range(tries):
        try:
            # The mirror image of ESPN on the NBA film: the NHL 403s urllib's default
            # "Python-urllib/x.y" UA and serves anything else, custom ones included.
            req = urllib.request.Request(url, headers={"User-Agent": "agwas-nhl/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def save(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
    print(f"  wrote {os.path.relpath(path, ROOT)}  {os.path.getsize(path) / 1024:.0f} KB")


def standings_end(label):
    for s in get(f"{WEB}/standings-season")["seasons"]:
        if str(s["id"]) == sid(label):
            return s["standingsEnd"]
    sys.exit(f"FAIL no standings season for {label}")


def teams(label):
    """The 32 teams, with conference/division, from the standings on the last day."""
    day = standings_end(label)
    st = get(f"{WEB}/standings/{day}")["standings"]
    save(os.path.join(RAW, f"standings-{label}.json"), st)
    out = {}
    for t in st:
        ab = t["teamAbbrev"]["default"]
        pri, sec = COLORS[ab]
        out[ab] = {"abbr": ab, "name": t["teamName"]["default"],
                   "city": t["placeName"]["default"], "nick": t["teamCommonName"]["default"],
                   "conf": t["conferenceName"], "div": t["divisionName"],
                   "primary": pri, "secondary": sec}
    if len(out) != 32:
        sys.exit(f"FAIL {len(out)} teams, expected 32")
    save(os.path.join(DATA, "teams.json"), out)


def standings(label):
    """Just the standings on the latest date -- the in-season refresh. teams.json keeps
    its identities; this only feeds seeds and clinch marks."""
    st = get(f"{WEB}/standings/{standings_end(label)}")["standings"]
    save(os.path.join(RAW, f"standings-{label}.json"), st)


def schedule(label):
    tm = json.load(open(os.path.join(DATA, "teams.json")))
    s = sid(label)

    def one(ab):
        return ab, get(f"{WEB}/club-schedule-season/{ab}/{s}")["games"]

    with ThreadPoolExecutor(8) as ex:
        raw = dict(ex.map(one, tm))
    save(os.path.join(RAW, f"schedule-{label}.json"), raw)

    out, po = {}, {}
    for ab, games in raw.items():
        rows = {2: [], 3: []}
        for g in games:
            if g["gameType"] not in (2, 3):          # 1 = preseason, 4 = all-star etc.
                continue
            home = g["homeTeam"]["abbrev"] == ab
            me, them = (g["homeTeam"], g["awayTeam"]) if home else (g["awayTeam"], g["homeTeam"])
            done = g.get("gameState") in ("OFF", "FINAL")
            last = (g.get("gameOutcome") or {}).get("lastPeriodType") if done else None
            res = None
            if done:
                # Only the regular season has an "OT loss": a playoff overtime loss is a
                # plain loss, worth nothing, and the series count treats it as one.
                ot_loss = last in ("OT", "SO") and g["gameType"] == 2
                res = "W" if me["score"] > them["score"] else ("OTL" if ot_loss else "L")
            rows[g["gameType"]].append({
                "id": g["id"], "date": g["gameDate"], "opp": them["abbrev"],
                "ha": "H" if home else "A", "res": res,
                "us": me.get("score") if done else None,
                "them": them.get("score") if done else None,
                "end": last if last in ("OT", "SO") else None,
                "venue": (g.get("venue") or {}).get("default"),
                "neutral": bool(g.get("neutralSite")),
                "state": g.get("gameState"),
            })
        rows[2].sort(key=lambda x: (x["date"], x["id"]))
        for i, r in enumerate(rows[2], 1):
            r["g"] = i
        out[ab] = rows[2]
        if rows[3]:
            po[ab] = sorted(rows[3], key=lambda x: (x["date"], x["id"]))
    save(os.path.join(DATA, f"schedule-{label}.json"), out)
    if po:
        save(os.path.join(DATA, f"playoffs-{label}.json"), po)
    n = {len(v) for v in out.values()}
    played = sum(1 for v in out.values() for g in v if g["res"]) // 2
    print(f"  games per team {sorted(n)}; {played} regular-season games played; "
          f"{sum(len(v) for v in po.values()) // 2} playoff games")


def games(label):
    """Boxscore + play-by-play for every finished game (regular season and playoffs)."""
    ids = set()
    for name in (f"schedule-{label}.json", f"playoffs-{label}.json"):
        p = os.path.join(DATA, name)
        if os.path.exists(p):
            for gs in json.load(open(p)).values():
                ids.update(g["id"] for g in gs if g["res"])
    os.makedirs(GAMES, exist_ok=True)
    todo = [g for g in sorted(ids)
            if not os.path.exists(os.path.join(GAMES, f"{g}.json.gz"))]
    print(f"  {len(ids)} finished games, {len(todo)} to fetch")

    def one(gid):
        box = get(f"{WEB}/gamecenter/{gid}/boxscore")
        pbp = get(f"{WEB}/gamecenter/{gid}/play-by-play")
        # Drop the bulky bits nothing downstream reads.
        for p in pbp.get("plays", []):
            d = p.get("details") or {}
            for k in list(d):
                if k.startswith(("highlightClipSharingUrlFr", "highlightClipFr", "discreteClip")):
                    d.pop(k)
            p.pop("pptReplayUrl", None)
        box.pop("tvBroadcasts", None)
        pbp.pop("tvBroadcasts", None)
        tmp = os.path.join(GAMES, f"{gid}.json.gz.part")
        with gzip.open(tmp, "wt") as f:
            json.dump({"box": box, "pbp": pbp}, f, separators=(",", ":"))
        os.replace(tmp, os.path.join(GAMES, f"{gid}.json.gz"))
        return gid

    t0, done = time.time(), 0
    with ThreadPoolExecutor(8) as ex:
        for _ in ex.map(one, todo):
            done += 1
            if done % 100 == 0:
                print(f"  {done}/{len(todo)}  {time.time() - t0:.0f}s", flush=True)
    print(f"  done {len(todo)} in {time.time() - t0:.0f}s")


def rest_all(kind, report, label, gtype):
    rows, start = [], 0
    exp = urllib.parse.quote(f"seasonId={sid(label)} and gameTypeId={gtype}")
    while True:
        d = get(f"{STATS}/{kind}/{report}?isAggregate=false&isGame=false&start={start}"
                f"&limit=100&sort=playerId&cayenneExp={exp}")
        rows += d["data"]
        start += 100
        if start >= d["total"]:
            return rows


def stats(label):
    out = {}
    for kind, reports in (("skater", ("summary", "realtime", "timeonice", "bios")),
                          ("goalie", ("summary", "advanced", "bios"))):
        merged = {}
        for rep in reports:
            for r in rest_all(kind, rep, label, 2):
                merged.setdefault(r["playerId"], {}).update(r)
        po = {}
        for r in rest_all(kind, "summary", label, 3):
            po[r["playerId"]] = r
        out[kind] = {"rs": merged, "po": po}
        print(f"  {kind}: {len(merged)} regular-season lines, {len(po)} playoff lines")
    save(os.path.join(RAW, f"stats-{label}.json"), out)


if __name__ == "__main__":
    os.makedirs(RAW, exist_ok=True)
    os.makedirs(DATA, exist_ok=True)
    cmd, label = sys.argv[1], sys.argv[2]
    {"teams": teams, "standings": standings, "schedule": schedule, "games": games,
     "stats": stats}[cmd](label)
