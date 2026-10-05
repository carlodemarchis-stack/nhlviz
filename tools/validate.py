#!/usr/bin/env python3
"""The gate: exits non-zero if the built season does not match the league.

    python3 tools/validate.py 2025-26

build_cards.py already reconciles every skater and goalie to the league's season
summary; this checks the things around it -- records against the official standings,
and that every image the pages ask for is on disk.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")


def main(label):
    fail = []
    d = json.load(open(os.path.join(ROOT, "data", f"cards-{label}.json")))
    st = json.load(open(os.path.join(ROOT, "scratch", "raw", f"standings-{label}.json")))
    st = {s["teamAbbrev"]["default"]: s for s in st}
    if len(d["teams"]) != 32:
        fail.append(f"{len(d['teams'])} teams")
    off = []
    for t in d["teams"]:
        s = st.get(t["abbr"])
        if not s:
            off.append(t["abbr"])
            continue
        if (t["w"], t["l"], t["otl"], t["pts"]) != (s["wins"], s["losses"], s["otLosses"], s["points"]):
            off.append(f"{t['abbr']} {t['w']}-{t['l']}-{t['otl']} v {s['wins']}-{s['losses']}-{s['otLosses']}")
    print(f"  records match the official standings: {32 - len(off)}/32" + (f" -- {off}" if off else ""))
    fail += off
    lens = {len(t["games"]) for t in d["teams"]}
    print(f"  games per team: {sorted(lens)}")
    if len(lens) != 1:
        fail.append(f"uneven schedules {lens}")
    miss = [ab for ab in (t["abbr"] for t in d["teams"])
            if not os.path.exists(os.path.join(ROOT, "img", "crest", f"{ab}.svg"))]
    ids = [p["id"] for p in d["players"] + d["goalies"]] + [r[0] for r in d["tail"]]
    hp = os.path.join(ROOT, "data", f"heads-{label}.json")
    alt = set(int(k) for k in json.load(open(hp))) if os.path.exists(hp) else set()
    nohead = [i for i in ids if not os.path.exists(
        os.path.join(ROOT, "img", "head", label, f"{i}.webp") if i in alt
        else os.path.join(ROOT, "img", "head", f"{i}.webp"))]
    print(f"  crests {32 - len(miss)}/32 · headshots {len(ids) - len(nohead)}/{len(ids)}")
    fail += miss + [f"head {i}" for i in nohead]
    if fail:
        sys.exit(f"FAIL {len(fail)}: {fail[:6]}")
    print("  PASS")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2025-26")
