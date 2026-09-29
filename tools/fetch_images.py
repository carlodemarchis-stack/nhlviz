#!/usr/bin/env python3
"""Official crests and headshots from assets.nhle.com, headshots straight to webp.

    python3 tools/fetch_images.py crests
    python3 tools/fetch_images.py heads 2025-26

Crests are the league's `_dark` SVG variants -- drawn for a dark background, which is what
the cards are. Headshots are 336x336 transparent cutouts, and the URL carries the season
AND the team, so each player is fetched in the kit he wore for his last team that season.
Kept as RGBA webp: transparency has to survive or the portrait sits in a white box.
"""
import io
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data")
IMG = os.path.join(HERE, "..", "img")
UA = {"User-Agent": "agwas-nhl/1.0"}      # the NHL 403s urllib's default UA


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
        return r.read()


def crests():
    teams = json.load(open(os.path.join(DATA, "teams.json")))
    os.makedirs(os.path.join(IMG, "crest"), exist_ok=True)
    for ab in teams:
        b = fetch(f"https://assets.nhle.com/logos/nhl/svg/{ab}_dark.svg")
        with open(os.path.join(IMG, "crest", f"{ab}.svg"), "wb") as f:
            f.write(b)
    print(f"  {len(teams)} crests")


def heads(label):
    from PIL import Image
    season = f"{label[:4]}{int(label[:4]) + 1}"
    cards = json.load(open(os.path.join(DATA, f"cards-{label}.json")))
    want = {}
    for p in cards["players"] + cards["goalies"]:
        want[p["id"]] = p["team"]
    for pid, ab in cards["tail"]:
        want[pid] = ab
    out = os.path.join(IMG, "head")
    os.makedirs(out, exist_ok=True)

    def one(item):
        pid, ab = item
        path = os.path.join(out, f"{pid}.webp")
        if os.path.exists(path):
            return "cached"
        # The season-and-team URL first; a player who never got a photo for that kit
        # falls back to the league's current one for him.
        for url in (f"https://assets.nhle.com/mugs/nhl/{season}/{ab}/{pid}.png",
                    f"https://assets.nhle.com/mugs/nhl/latest/{pid}.png"):
            try:
                b = fetch(url)
            except Exception:
                continue
            im = Image.open(io.BytesIO(b)).convert("RGBA")
            im.save(path, "WEBP", quality=82, method=6)
            return "ok"
        return "missing"

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, want.items()))
    n = {k: res.count(k) for k in set(res)}
    size = sum(os.path.getsize(os.path.join(out, f)) for f in os.listdir(out))
    print(f"  heads: {n}  ({size / 1024 / 1024:.1f} MB on disk)")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "crests":
        crests()
    else:
        heads(sys.argv[2] if len(sys.argv) > 2 else "2025-26")
