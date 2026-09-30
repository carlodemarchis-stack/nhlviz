#!/usr/bin/env python3
"""Derive everything the cards render, into one compact payload.

    python3 tools/build_cards.py 2025-26

Reads data/{teams,schedule-*,playoffs-*}.json, scratch/raw/{standings,stats}-*.json and
the per-game boxscore + play-by-play cache in scratch/games/, writes data/cards-<label>.json
and the per-team files behind the unlisted cards in data/roster/. Nothing here fetches.

Every season total on a card is re-derived from the games and checked against the
league's own season summary -- a card whose game chart does not add up to the number
printed above it is worse than no card.
"""
import gzip
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
DATA = os.path.join(ROOT, "data")
RAW = os.path.join(ROOT, "scratch", "raw")
GAMES = os.path.join(ROOT, "scratch", "games")

SKATER_CARDS = 100
GOALIE_CARDS = 15
BIG_NIGHTS = 100
ROUND_ORDER = ["1st Round", "2nd Round", "Conference Final", "Stanley Cup Final"]
B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def load(path, gz=False):
    if not os.path.exists(path):
        return None
    return json.load(gzip.open(path, "rt") if gz else open(path))


def r1(x):
    return round(x + 1e-9, 1)


def r3(x):
    return round(x + 1e-12, 3)


def mins(s):
    """'19:15' -> 19.25"""
    if not s:
        return 0.0
    m, sec = s.split(":")
    return int(m) + int(sec) / 60


def pack(shots):
    """[(x, y, kind)] -> 3 base-62 chars each. x is feet from centre ice towards the net
    he was attacking (0..100), y is feet across (-43..43), kind 0 saved / 1 goal / 2 missed.
    A shot from his own half (an empty-net try, a clearance that counted) is pinned to the
    red line rather than dropped, so the count still reconciles."""
    out = []
    for x, y, k in shots:
        x = max(0, min(100, int(round(x))))
        y = max(-43, min(43, int(round(y))))
        n = (x * 87 + (y + 43)) * 3 + k
        out.append(B62[n // 3844] + B62[(n // 62) % 62] + B62[n % 62])
    return "".join(out)


def round_of(gid):
    return ROUND_ORDER[int(str(gid)[-3]) - 1]


def playoff_run(games):
    series, order = {}, []
    for g in games:
        rn = round_of(g["id"])
        if rn not in series:
            series[rn] = {"round": rn, "w": 0, "l": 0, "opp": g["opp"], "games": []}
            order.append(rn)
        s = series[rn]
        s["w" if g["res"] == "W" else "l"] += 1
        s["games"].append({"res": g["res"], "us": g["us"], "them": g["them"], "ha": g["ha"],
                           "date": g["date"], "end": g.get("end")})
    if not order:
        return None
    rounds = [series[r] for r in sorted(order, key=ROUND_ORDER.index)]
    last = rounds[-1]
    if last["w"] > last["l"]:
        outcome = "CHAMPIONS" if last["round"] == "Stanley Cup Final" else f"Won the {last['round']}"
    else:
        outcome = f"Lost the {last['round']}"
    return {"rounds": rounds, "outcome": outcome,
            "w": sum(r["w"] for r in rounds), "l": sum(r["l"] for r in rounds)}


def team_run_label(run):
    if not run:
        return None
    last = run["rounds"][-1]
    if run["outcome"] == "CHAMPIONS":
        return "won the Stanley Cup"
    if last["round"] == "Stanley Cup Final":
        return "lost the Stanley Cup Final"
    return "went out in the " + last["round"]


def streaks(games):
    """Longest run of wins, and longest run without one (regulation and OT losses both)."""
    best = {"W": 0, "L": 0}
    cur, kind = 0, None
    for g in games:
        if not g["res"]:
            continue
        k = "W" if g["res"] == "W" else "L"
        cur = cur + 1 if k == kind else 1
        kind = k
        best[k] = max(best[k], cur)
    return best


def rec(games):
    w = sum(1 for g in games if g["res"] == "W")
    l = sum(1 for g in games if g["res"] == "L")
    o = sum(1 for g in games if g["res"] == "OTL")
    return w, l, o


def age_at(bd, label):
    if not bd:
        return None
    y, m, d = map(int, bd.split("-"))
    start = date(int(label[:4]), 10, 1)
    return start.year - y - ((start.month, start.day) < (m, d))


def height(inches):
    return f"{inches // 12}'{inches % 12}\"" if inches else None


def main(label):
    teams = load(os.path.join(DATA, "teams.json"))
    sched = load(os.path.join(DATA, f"schedule-{label}.json"))
    po = load(os.path.join(DATA, f"playoffs-{label}.json")) or {}
    stand = load(os.path.join(RAW, f"standings-{label}.json")) or []
    stats = load(os.path.join(RAW, f"stats-{label}.json")) or {
        "skater": {"rs": {}, "po": {}}, "goalie": {"rs": {}, "po": {}}}
    SK, GK = stats["skater"]["rs"], stats["goalie"]["rs"]
    SKPO, GKPO = stats["skater"]["po"], stats["goalie"]["po"]
    st_of = {s["teamAbbrev"]["default"]: s for s in stand}

    gidx = {}
    for ab, gs in sched.items():
        for i, g in enumerate(gs):
            gidx[(ab, g["id"])] = i
    po_meta = {}
    for ab, gs in po.items():
        for g in gs:
            po_meta[(ab, g["id"])] = g

    # ---- walk every finished game once -------------------------------------------------
    sk_log = defaultdict(list)      # pid -> [(date, ab, gi, toi, g, a1, a2, sog, hits, blk, pm, pim, a)]
    gk_log = defaultdict(list)      # pid -> [(date, ab, gi, toi, sa, ga, dec, start)]
    sk_po = defaultdict(list)       # pid -> [(date, ab, gid, toi, g, a1, a2, sog, hits, blk, pm)]
    gk_po = defaultdict(list)
    shots = defaultdict(list)       # skater -> [(x, y, kind)]  regular season
    faced = defaultdict(list)       # goalie -> [(x, y, kind)]  regular season
    jersey = defaultdict(Counter)   # (pid, team) -> number worn
    pos_of, nm_box = {}, {}
    empty_net = Counter()

    ids = sorted({g["id"] for gs in sched.values() for g in gs if g["res"]} |
                 {g["id"] for gs in po.values() for g in gs if g["res"]})
    missing = [i for i in ids if not os.path.exists(os.path.join(GAMES, f"{i}.json.gz"))]
    if missing:
        sys.exit(f"FAIL {len(missing)} finished games not fetched -- run fetch_nhl.py games {label}")

    for gid in ids:
        d = load(os.path.join(GAMES, f"{gid}.json.gz"), gz=True)
        box, pbp = d["box"], d["pbp"]
        playoff = box["gameType"] == 3
        home_id, away_id = pbp["homeTeam"]["id"], pbp["awayTeam"]["id"]
        ab_of = {home_id: pbp["homeTeam"]["abbrev"], away_id: pbp["awayTeam"]["abbrev"]}
        gdate = box["gameDate"]

        # primary / secondary assists, and shot locations, from the play-by-play.
        a1, a2 = Counter(), Counter()
        for pl in pbp["plays"]:
            k = pl["typeDescKey"]
            if k not in ("goal", "shot-on-goal", "missed-shot"):
                continue
            if (pl.get("periodDescriptor") or {}).get("periodType") == "SO":
                continue                    # a shootout goal is not a goal in anyone's stats
            det = pl.get("details") or {}
            if k == "goal":
                if det.get("assist1PlayerId"):
                    a1[det["assist1PlayerId"]] += 1
                if det.get("assist2PlayerId"):
                    a2[det["assist2PlayerId"]] += 1
            if playoff:
                continue
            shooter = det.get("scoringPlayerId") if k == "goal" else det.get("shootingPlayerId")
            x, y = det.get("xCoord"), det.get("yCoord")
            if shooter is None or x is None or y is None:
                continue
            # Which end was he attacking? The side the home team defends flips every
            # period; the shooting team attacks the other one. Rotate so every shot
            # travels towards +x, the net on the right of the drawing.
            side = pl.get("homeTeamDefendingSide")
            owner = det.get("eventOwnerTeamId")
            if side in ("left", "right"):
                home_attacks_right = side == "left"
                attacks_right = home_attacks_right if owner == home_id else not home_attacks_right
            else:
                attacks_right = x >= 0 if det.get("zoneCode") == "O" else x < 0
            if not attacks_right:
                x, y = -x, -y
            kind = 1 if k == "goal" else 0 if k == "shot-on-goal" else 2
            shots[shooter].append((x, y, kind))
            gk = det.get("goalieInNetId")
            if kind in (0, 1):
                if gk:
                    faced[gk].append((x, y, kind))
                elif kind == 1:
                    empty_net[shooter] += 1

        for side_key, tid in (("homeTeam", home_id), ("awayTeam", away_id)):
            ab = ab_of[tid]
            blk = box["playerByGameStats"][side_key]
            for grp in ("forwards", "defense"):
                for r in blk.get(grp, []):
                    pid = r["playerId"]
                    # Listed but never took a shift: Kucherov is in one box score at 00:00,
                    # and the league does not count that game as played.
                    if mins(r.get("toi")) <= 0:
                        continue
                    pos_of.setdefault(pid, r["position"])
                    nm_box.setdefault(pid, r["name"]["default"])
                    jersey[(pid, ab)][r.get("sweaterNumber")] += 1
                    row = (gdate, ab, mins(r.get("toi")), r["goals"], a1[pid], a2[pid],
                           r.get("sog", 0), r.get("hits", 0), r.get("blockedShots", 0),
                           r.get("plusMinus", 0), r.get("pim", 0), r["assists"])
                    if playoff:
                        sk_po[pid].append(row[:1] + (ab, gid) + row[2:])
                    else:
                        sk_log[pid].append(row[:2] + (gidx[(ab, gid)],) + row[2:])
            for r in blk.get("goalies", []):
                t = mins(r.get("toi"))
                if t <= 0:
                    continue                # dressed as the backup, never went in
                pid = r["playerId"]
                pos_of.setdefault(pid, "G")
                nm_box.setdefault(pid, r["name"]["default"])
                jersey[(pid, ab)][r.get("sweaterNumber")] += 1
                row = (gdate, ab, t, r.get("shotsAgainst", 0), r.get("goalsAgainst", 0),
                       r.get("decision"), 1 if r.get("starter") else 0)
                if playoff:
                    gk_po[pid].append(row[:1] + (ab, gid) + row[2:])
                else:
                    gk_log[pid].append(row[:2] + (gidx[(ab, gid)],) + row[2:])

    # ---- teams -------------------------------------------------------------------------
    out_teams = []
    runs = {ab: playoff_run(po.get(ab, [])) for ab in teams}
    for ab, t in teams.items():
        games = sched.get(ab, [])
        done = [g for g in games if g["res"]]
        w, l, o = rec(done)
        gf = sum(g["us"] for g in done)
        ga = sum(g["them"] for g in done)
        home = [g for g in done if g["ha"] == "H"]
        away = [g for g in done if g["ha"] == "A"]
        # The biggest WIN among the wins and the heaviest DEFEAT among the defeats: sorting
        # every game by margin made one early-season win both at once.
        wins = sorted((g for g in done if g["res"] == "W"), key=lambda g: (g["us"] - g["them"], g["us"]))
        losses = sorted((g for g in done if g["res"] != "W"), key=lambda g: (g["us"] - g["them"], g["us"]))
        s = st_of.get(ab, {})
        n = max(1, len(done))

        roster = []
        for pid, log in sk_log.items():
            mine = [x for x in log if x[1] == ab]
            if not mine:
                continue
            gp = len(mine)
            gl = sum(x[4] for x in mine)
            a = sum(x[12] for x in mine)
            roster.append({"id": pid, "g": gp, "gl": gl, "a": a, "pts": gl + a,
                           "ppg": r1((gl + a) / gp), "gpg": r1(gl / gp), "apg": r1(a / gp),
                           "toi": r1(sum(x[3] for x in mine) / gp)})
        roster.sort(key=lambda r: (-r["pts"], -r["gl"], r["g"], r["id"]))

        goalies = []
        for pid, log in gk_log.items():
            mine = [x for x in log if x[1] == ab]
            if not mine:
                continue
            sa = sum(x[4] for x in mine)
            gaa_ = sum(x[5] for x in mine)
            toi = sum(x[3] for x in mine)
            dec = Counter(x[6] for x in mine)
            goalies.append({"id": pid, "g": len(mine), "w": dec["W"], "l": dec["L"],
                            "o": dec["O"], "svp": r3((sa - gaa_) / sa) if sa else None,
                            "gaa": round(gaa_ * 60 / toi, 2) if toi else None})
        goalies.sort(key=lambda r: (-r["g"], r["id"]))

        out_teams.append({
            "abbr": ab, "name": t["name"], "city": t["city"], "nick": t["nick"],
            "conf": t["conf"], "div": t["div"],
            "seed": s.get("conferenceSequence") if done else None,
            "divRank": s.get("divisionSequence") if done else None,
            "wc": s.get("wildcardSequence") if done else None,
            "clinch": s.get("clinchIndicator"),
            "primary": t["primary"], "secondary": t["secondary"],
            "w": w, "l": l, "otl": o, "pts": 2 * w + o,
            "pct": r1(100 * (2 * w + o) / max(1, 2 * len(done))),
            "rw": s.get("regulationWins") if done else 0,
            "gf": gf, "ga": ga, "gfpg": r1(gf / n), "gapg": r1(ga / n),
            "diff": gf - ga,
            "home": "-".join(map(str, rec(home))), "away": "-".join(map(str, rec(away))),
            "streak": streaks(games),
            "ot": {"w": sum(1 for g in done if g["res"] == "W" and g["end"]),
                   "l": o, "so": sum(1 for g in done if g["end"] == "SO")},
            "best": ({"opp": wins[-1]["opp"], "us": wins[-1]["us"],
                      "them": wins[-1]["them"], "date": wins[-1]["date"]}
                     if wins else None),
            "worst": ({"opp": losses[0]["opp"], "us": losses[0]["us"],
                       "them": losses[0]["them"], "date": losses[0]["date"]}
                      if losses else None),
            "games": [{"g": g["g"], "opp": g["opp"], "ha": g["ha"], "res": g["res"],
                       "us": g["us"], "them": g["them"], "date": g["date"],
                       **({"end": g["end"]} if g.get("end") else {}),
                       **({"venue": g["venue"]} if g.get("neutral") else {})}
                      for g in games],
            "po": runs[ab],
            "roster": roster[:18],
            "goalies": goalies,
        })

    if any(t["w"] or t["l"] or t["otl"] for t in out_teams):
        out_teams.sort(key=lambda t: (-t["pts"], -t["rw"], -t["w"], -t["diff"], t["abbr"]))
    else:
        out_teams.sort(key=lambda t: (t["conf"], t["div"], t["city"]))
    by_ab = {t["abbr"]: t for t in out_teams}

    # ---- players -----------------------------------------------------------------------
    def teams_of(log):
        order, cnt = [], Counter()
        for x in sorted(log):
            if x[1] not in cnt:
                order.append(x[1])
            cnt[x[1]] += 1
        return [{"t": ab, "g": cnt[ab]} for ab in order]

    def jersey_of(pid, tlist):
        for tm in reversed(tlist):
            c = jersey.get((pid, tm["t"]))
            if c:
                return c.most_common(1)[0][0]
        return None

    def bio(src, pid, label):
        return {"age": age_at(src.get("birthDate"), label),
                "height": height(src.get("height")),
                "weight": f"{src['weight']} lb" if src.get("weight") else None,
                "nat": src.get("nationalityCode") or src.get("birthCountryCode"),
                "hand": src.get("shootsCatches"),
                "draft": (f"{src['draftYear']} #{src['draftOverall']}"
                          if src.get("draftYear") else "undrafted")}

    def wl_in(log):
        w = l = o = 0
        for x in log:
            gm = by_ab[x[1]]["games"][x[2]]
            if gm["res"] == "W":
                w += 1
            elif gm["res"] == "L":
                l += 1
            elif gm["res"] == "OTL":
                o += 1
        return [w, l, o]

    def skater(pid, rank):
        s = SK.get(str(pid)) or {}
        log = sorted(sk_log.get(pid, []))
        tl = teams_of(log)
        tix = {t["t"]: i for i, t in enumerate(tl)}
        team = tl[-1]["t"] if tl else s.get("teamAbbrevs", "").split(",")[-1]
        pts_log = [x[4] + x[12] for x in log]
        best = max(range(len(log)), key=lambda i: (pts_log[i], log[i][4]), default=None)
        bg = by_ab[log[best][1]]["games"][log[best][2]] if best is not None else None
        pp = SKPO.get(str(pid))
        pol = []
        for r in sorted(sk_po.get(pid, [])):
            g = po_meta[(r[1], r[2])]
            pol.append([r[4] + r[12], r[4], r[5], r[6], r[7], r[8], r1(r[3]),
                        ROUND_ORDER.index(round_of(r[2])), g["res"], g["opp"], g["us"], g["them"],
                        g["ha"], g["date"], g.get("end")])
        name = s.get("skaterFullName") or nm_box.get(pid)
        toi = s.get("timeOnIcePerGame") or 0
        return {
            "kind": "s", "rank": rank, "id": pid, "name": name,
            "team": team, "teams": tl, "pos": s.get("positionCode") or pos_of.get(pid),
            "jersey": jersey_of(pid, tl), **bio(s, pid, label),
            "gp": s.get("gamesPlayed", len(log)), "g": s.get("goals", 0),
            "a": s.get("assists", 0), "pts": s.get("points", 0),
            "pm": s.get("plusMinus", 0), "pim": s.get("penaltyMinutes", 0),
            "ppp": s.get("ppPoints", 0), "ppg_": s.get("ppGoals", 0),
            "shp": s.get("shPoints", 0), "gwg": s.get("gameWinningGoals", 0),
            "otg": s.get("otGoals", 0), "sog": s.get("shots", 0),
            "shpct": r1(100 * (s.get("shootingPct") or 0)),
            "toi": r1(toi / 60), "ppm": round(s.get("points", 0) / max(1, s.get("gamesPlayed", 1)), 2),
            "hits": s.get("hits", 0), "blk": s.get("blockedShots", 0),
            "fo": r1(100 * s["faceoffWinPct"]) if s.get("faceoffWinPct") else None,
            "take": s.get("takeaways", 0), "give": s.get("giveaways", 0),
            "a1": sum(x[5] for x in log), "a2": sum(x[6] for x in log),
            "en": empty_net.get(pid, 0),
            "log": pts_log,
            # [team index, game index, toi, goals, a1, a2, shots, hits, blocks, +/-, pim]
            "glog": [[tix[x[1]], x[2], r1(x[3]), x[4], x[5], x[6], x[7], x[8], x[9],
                      x[10], x[11]] for x in log],
            "wl": wl_in(log),
            "highs": {"pts": max(pts_log or [0]), "g": max((x[4] for x in log), default=0),
                      "a": max((x[12] for x in log), default=0),
                      "sog": max((x[7] for x in log), default=0),
                      "hits": max((x[8] for x in log), default=0),
                      "toi": r1(max((x[3] for x in log), default=0)),
                      **({"opp": bg["opp"], "ha": bg["ha"]} if bg else {})},
            "shots": pack(shots.get(pid, [])),
            "po": ({"gp": pp.get("gamesPlayed", 0), "g": pp.get("goals", 0),
                    "a": pp.get("assists", 0), "pts": pp.get("points", 0),
                    "pm": pp.get("plusMinus", 0),
                    "toi": r1((pp.get("timeOnIcePerGame") or 0) / 60),
                    "ppm": round(pp.get("points", 0) / max(1, pp.get("gamesPlayed", 1)), 2),
                    "wl": [sum(1 for x in pol if x[8] == "W"), sum(1 for x in pol if x[8] == "L")]}
                   if pp else None),
            # [pts, g, a1, a2, shots, hits, toi, round, W/L, opp, us, them, ha, date, end]
            "polog": pol or None,
            "poMiss": (team_run_label(runs.get(team)) if not pp and runs.get(team) else None),
        }

    def goalie(pid, rank):
        s = GK.get(str(pid)) or {}
        log = sorted(gk_log.get(pid, []))
        tl = teams_of(log)
        tix = {t["t"]: i for i, t in enumerate(tl)}
        team = tl[-1]["t"] if tl else s.get("teamAbbrevs", "").split(",")[-1]
        pp = GKPO.get(str(pid))
        pol = []
        for r in sorted(gk_po.get(pid, [])):
            g = po_meta[(r[1], r[2])]
            pol.append([r[4] - r[5], r[5], r1(r[3]), r[6],
                        ROUND_ORDER.index(round_of(r[2])), g["res"], g["opp"], g["us"],
                        g["them"], g["ha"], g["date"], g.get("end")])
        saves_log = [x[4] - x[5] for x in log]
        best = max(range(len(log)), key=lambda i: (saves_log[i], -log[i][5]), default=None)
        bg = by_ab[log[best][1]]["games"][log[best][2]] if best is not None else None
        name = s.get("goalieFullName") or nm_box.get(pid)
        return {
            "kind": "g", "rank": rank, "id": pid, "name": name,
            "team": team, "teams": tl, "pos": "G", "jersey": jersey_of(pid, tl),
            **bio(s, pid, label),
            "gp": s.get("gamesPlayed", len(log)), "gs": s.get("gamesStarted", 0),
            "w": s.get("wins", 0), "l": s.get("losses", 0), "otl": s.get("otLosses", 0),
            "svp": r3(s.get("savePct") or 0), "gaa": round(s.get("goalsAgainstAverage") or 0, 2),
            "so": s.get("shutouts", 0), "sv": s.get("saves", 0), "sa": s.get("shotsAgainst", 0),
            "ga": s.get("goalsAgainst", 0), "qs": s.get("qualityStart", 0),
            "toi": r1((s.get("timeOnIce") or 0) / 60),
            "log": saves_log,
            # [team index, game index, toi, shots against, goals against, decision, started]
            "glog": [[tix[x[1]], x[2], r1(x[3]), x[4], x[5], x[6] or "", x[7]] for x in log],
            "wl": wl_in(log),
            "highs": {"sv": max(saves_log or [0]), "sa": max((x[4] for x in log), default=0),
                      "ga": max((x[5] for x in log), default=0),
                      **({"opp": bg["opp"], "ha": bg["ha"]} if bg else {})},
            "shots": pack(faced.get(pid, [])),
            "po": ({"gp": pp.get("gamesPlayed", 0), "w": pp.get("wins", 0),
                    "l": pp.get("losses", 0), "svp": r3(pp.get("savePct") or 0),
                    "gaa": round(pp.get("goalsAgainstAverage") or 0, 2),
                    "so": pp.get("shutouts", 0)} if pp else None),
            # [saves, goals against, toi, decision, round, W/L, opp, us, them, ha, date, end]
            "polog": pol or None,
            "poMiss": (team_run_label(runs.get(team)) if not pp and runs.get(team) else None),
        }

    # The order: points, then goals (the Art Ross tiebreak), then fewer games.
    sk_pool = sorted((int(k), v) for k, v in SK.items() if int(k) in sk_log)
    sk_pool.sort(key=lambda kv: (-kv[1]["points"], -kv[1]["goals"], kv[1]["gamesPlayed"],
                                 kv[1]["skaterFullName"]))
    gk_pool = sorted((int(k), v) for k, v in GK.items() if int(k) in gk_log)
    gk_pool.sort(key=lambda kv: (-kv[1]["wins"], -(kv[1]["savePct"] or 0), kv[1]["goalieFullName"]))
    sk_rank = {pid: i + 1 for i, (pid, _) in enumerate(sk_pool)}
    gk_rank = {pid: i + 1 for i, (pid, _) in enumerate(gk_pool)}

    players = [skater(pid, sk_rank[pid]) for pid, _ in sk_pool[:SKATER_CARDS]]
    goalies = [goalie(pid, gk_rank[pid]) for pid, _ in gk_pool[:GOALIE_CARDS]]

    # ---- the unlisted cards: everyone else who played, fetched per team on demand ------
    carded = {p["id"] for p in players} | {g["id"] for g in goalies}
    home = {}
    for pid, _ in sk_pool + gk_pool:
        lg = sk_log.get(pid) or gk_log.get(pid)
        home[pid] = sorted(lg)[-1][1]
    tails = defaultdict(dict)
    tail_order = []
    for pid, _ in sk_pool[SKATER_CARDS:]:
        tails[home[pid]][pid] = skater(pid, sk_rank[pid])
        tail_order.append([pid, home[pid]])
    for pid, _ in gk_pool[GOALIE_CARDS:]:
        tails[home[pid]][pid] = goalie(pid, gk_rank[pid])
        tail_order.append([pid, home[pid]])

    # ---- leaders over the whole pool -------------------------------------------------------
    def board(rows, key_tot, key_avg):
        return {"tot": sorted(rows, key=key_tot)[:12], "avg": sorted(rows, key=key_avg)[:12]}

    rows = lambda f: [[pid, home[pid], v[f], r1(v[f] / max(1, v["gamesPlayed"])), v["gamesPlayed"]]
                      for pid, v in sk_pool]
    leaders = {}
    for key, f in (("pts", "points"), ("g", "goals"), ("a", "assists")):
        rs = rows(f)
        # A per-game table with no minimum is led by a man with one game and one point,
        # so the average view needs a floor: a quarter of the schedule.
        floor = max(1, max((v["gamesPlayed"] for _, v in sk_pool), default=0) // 4)
        leaders[key] = {"tot": sorted(rs, key=lambda r: (-r[2], -r[3], r[0]))[:12],
                        "avg": sorted([r for r in rs if r[4] >= floor],
                                      key=lambda r: (-r[3], -r[2], r[0]))[:12]}
    # Goalies: [id, team, wins, save pct, games played, shutouts]
    grow = [[pid, home[pid], v["wins"], r3(v["savePct"] or 0), v["gamesPlayed"], v["shutouts"]]
            for pid, v in gk_pool]
    gfloor = max(1, max((v["gamesPlayed"] for _, v in gk_pool), default=0) // 2)
    leaders["gk"] = {"w": sorted(grow, key=lambda r: (-r[2], -r[3], r[0]))[:12],
                     "sv": sorted([r for r in grow if r[4] >= gfloor],
                                  key=lambda r: (-r[3], -r[2], r[0]))[:12],
                     "so": sorted(grow, key=lambda r: (-r[5], -r[3], r[0]))[:12]}

    # ---- the biggest nights: most points in a game, league-wide -----------------------
    perf = []
    for pid, log in sk_log.items():
        for x in log:
            p = x[4] + x[12]
            if p < 3:
                continue
            gm = by_ab[x[1]]["games"][x[2]]
            perf.append({"pts": p, "g": x[4], "a": x[12], "id": pid,
                         "name": (SK.get(str(pid)) or {}).get("skaterFullName") or nm_box.get(pid),
                         "team": x[1], "opp": gm["opp"], "h": 1 if gm["ha"] == "H" else 0,
                         "w": 1 if gm["res"] == "W" else 0, "res": gm["res"],
                         "us": gm["us"], "them": gm["them"], "date": gm["date"],
                         "sog": x[7], "toi": r1(x[3]), "a1": x[5], "a2": x[6]})
    perf.sort(key=lambda o: (-o["pts"], -o["g"], o["date"], o["name"]))
    nights = perf[:BIG_NIGHTS]
    if nights:
        cut = nights[-1]["pts"]
        nights[-1] = dict(nights[-1], tie=sum(1 for o in perf if o["pts"] == cut))

    names = {}
    for pid, v in sk_pool:
        names[pid] = [v["skaterFullName"], v["positionCode"],
                      jersey_of(pid, teams_of(sk_log[pid])) or ""]
    for pid, v in gk_pool:
        names[pid] = [v["goalieFullName"], "G", jersey_of(pid, teams_of(gk_log[pid])) or ""]

    champ = next((t["abbr"] for t in out_teams if t["po"] and t["po"]["outcome"] == "CHAMPIONS"), None)
    payload = {"season": label, "champion": champ, "teams": out_teams,
               "players": players, "goalies": goalies, "names": names,
               "nights": nights, "tail": tail_order, "leaders": leaders,
               "counts": {"teams": len(out_teams), "players": len(players),
                          "goalies": len(goalies), "skaterPool": len(sk_pool),
                          "goaliePool": len(gk_pool)}}
    p = os.path.join(DATA, f"cards-{label}.json")
    with open(p, "w") as f:
        json.dump(payload, f, separators=(",", ":"), ensure_ascii=False)

    rdir = os.path.join(DATA, "roster")
    os.makedirs(rdir, exist_ok=True)
    for old in os.listdir(rdir):
        if old.startswith(f"{label}-"):
            os.remove(os.path.join(rdir, old))
    tot = 0
    for ab, recs in tails.items():
        rp = os.path.join(rdir, f"{label}-{ab}.json")
        with open(rp, "w") as f:
            json.dump(recs, f, separators=(",", ":"), ensure_ascii=False)
        tot += os.path.getsize(rp)

    # ---- report + reconciliation ----------------------------------------------------------
    print(f"  {len(out_teams)} teams, {len(players)} skater cards, {len(goalies)} goalie cards "
          f"(pools {len(sk_pool)} / {len(gk_pool)}); {len(tail_order)} unlisted, "
          f"{tot / 1024:.0f} KB of roster files")
    print(f"  champion: {champ}")
    if out_teams and out_teams[0]["w"]:
        t = out_teams[0]
        print(f"  best record: {t['name']} {t['w']}-{t['l']}-{t['otl']} {t['pts']} pts")
    bad = []
    for x in players + [r for d_ in tails.values() for r in d_.values() if r["kind"] == "s"]:
        gl = sum(g[3] for g in x["glog"])
        a = sum(g[4] + g[5] for g in x["glog"])
        s = SK.get(str(x["id"])) or {}
        if (len(x["glog"]), gl, sum(x["log"])) != (s.get("gamesPlayed"), s.get("goals"), s.get("points")) \
                or a != s.get("assists"):
            bad.append((x["name"], len(x["glog"]), s.get("gamesPlayed"), gl, s.get("goals"),
                        a, s.get("assists")))
    print(f"  skaters: {'OK all' if not bad else f'{len(bad)} MISMATCHED of'} "
          f"{len(sk_pool)} reconcile gp/goals/A1+A2/points to the league's season summary"
          + (f" -- {bad[:4]}" if bad else ""))
    shot_bad = []
    for x in players:
        s = SK.get(str(x["id"])) or {}
        on = sum(1 for i in range(0, len(x["shots"]), 3)
                 if (B62.index(x["shots"][i]) * 3844 + B62.index(x["shots"][i + 1]) * 62
                     + B62.index(x["shots"][i + 2])) % 3 in (0, 1))
        if on != s.get("shots"):
            shot_bad.append((x["name"], on, s.get("shots")))
    # The play-by-play and the box score disagree by a shot here and there -- McDavid has
    # three located shots on goal in a game whose box score credits him two. The upstream
    # feed contradicts itself, so the map shows what was located and the card prints the
    # official total; anything beyond a couple of shots would mean OUR bug, not theirs.
    worst = max((abs(a - b) for _, a, b in shot_bad), default=0)
    print(f"  shot maps: {len(players) - len(shot_bad)}/{len(players)} located shots on goal "
          f"match official SOG exactly, the rest within {worst}"
          + (" -- FAIL, too far off" if worst > 3 else " (upstream feed inconsistency)"))
    gbad = []
    for x in goalies:
        s = GK.get(str(x["id"])) or {}
        sa = sum(g[3] for g in x["glog"])
        ga = sum(g[4] for g in x["glog"])
        w = sum(1 for g in x["glog"] if g[5] == "W")
        if (len(x["glog"]), sa, ga, w) != (s.get("gamesPlayed"), s.get("shotsAgainst"),
                                          s.get("goalsAgainst"), s.get("wins")):
            gbad.append((x["name"], len(x["glog"]), s.get("gamesPlayed"), sa, s.get("shotsAgainst"),
                         ga, s.get("goalsAgainst"), w, s.get("wins")))
    print(f"  goalies: {'OK all' if not gbad else f'{len(gbad)} MISMATCHED of'} {len(goalies)} "
          f"reconcile gp/SA/GA/wins" + (f" -- {gbad[:4]}" if gbad else ""))
    drift = 0
    for x in players + goalies:
        for (ti, gi, *_), when in zip(x["glog"], sorted(sk_log.get(x["id"]) or gk_log.get(x["id"]))):
            gm = by_ab[x["teams"][ti]["t"]]["games"][gi]
            if gm["date"] != when[0]:
                drift += 1
    print(f"  game-log pointers: {'all resolve to the right night' if not drift else f'{drift} WRONG'}")
    if nights:
        print(f"  biggest night: {nights[0]['name']} {nights[0]['pts']} pts "
              f"({nights[0]['g']}G {nights[0]['a']}A) v {nights[0]['opp']} {nights[0]['date']}")
    print(f"  wrote data/cards-{label}.json  {os.path.getsize(p) / 1024:.0f} KB")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2025-26")
