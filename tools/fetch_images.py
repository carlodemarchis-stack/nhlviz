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


def base_season():
    """The earliest season with cards: its portraits are the base set, img/head/<id>.webp."""
    labels = sorted(f[6:-5] for f in os.listdir(DATA) if f.startswith("cards-") and f.endswith(".json"))
    return labels[0] if labels else None


def roster_of(label):
    """{player id: the team he last played for} over every card the season has."""
    cards = json.load(open(os.path.join(DATA, f"cards-{label}.json")))
    want = {p["id"]: p["team"] for p in cards["players"] + cards["goalies"]}
    for pid, ab in cards["tail"]:
        want[pid] = ab
    return want


def heads(label):
    """Every carded and unlisted player's portrait, in the kit of the team he plays for.

    The base season's portraits live at img/head/<id>.webp and are never overwritten --
    they are what that season's cards show. In a later season a player who has changed
    teams gets img/head/<season>/<id>.webp in his new kit, once the league publishes it
    (the league serves its newest photo for any team in the address, so these are asked
    for on every run and replaced only when the photo changes). data/heads-<season>.json
    records which players have one, and for which team, so a second move is caught too;
    render.py hands that list to the page.
    """
    from PIL import Image
    season = f"{label[:4]}{int(label[:4]) + 1}"
    want = roster_of(label)
    base = base_season()
    base_team = roster_of(base) if base and base != label else {}
    out = os.path.join(IMG, "head")
    alt = os.path.join(out, label)
    os.makedirs(out, exist_ok=True)
    man_p = os.path.join(DATA, f"heads-{label}.json")
    man = json.load(open(man_p)) if os.path.exists(man_p) else {}
    man = {int(k): v for k, v in man.items()}

    def save(b, path):
        """Write the webp, and say whether anything changed -- an identical photo must not
        rewrite the file, or every run would commit 116 unchanged images."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        buf = io.BytesIO()
        Image.open(io.BytesIO(b)).convert("RGBA").save(buf, "WEBP", quality=82, method=6)
        new = buf.getvalue()
        if os.path.exists(path) and open(path, "rb").read() == new:
            return False
        with open(path, "wb") as f:
            f.write(new)
        return True

    def one(item):
        pid, ab = item
        path = os.path.join(out, f"{pid}.webp")
        moved = base_team.get(pid) and base_team[pid] != ab
        if moved:
            # Asked again on every run: the league answers this address with the newest
            # photo it has, whatever the team in it, so until he is shot in the new kit
            # it is still the old one. When it changes, the file changes with it.
            try:
                changed = save(fetch(f"https://assets.nhle.com/mugs/nhl/{season}/{ab}/{pid}.png"),
                               os.path.join(alt, f"{pid}.webp"))
                man[pid] = ab
                return "new kit, updated" if changed else "new kit, unchanged"
            except Exception:
                return "no photo for the new team yet"
        if os.path.exists(path):
            return "cached"
        # The season-and-team URL first; a player who never got a photo for that kit
        # falls back to the league's current one for him.
        for url in (f"https://assets.nhle.com/mugs/nhl/{season}/{ab}/{pid}.png",
                    f"https://assets.nhle.com/mugs/nhl/latest/{pid}.png"):
            try:
                save(fetch(url), path)
                return "fetched"
            except Exception:
                continue
        return "missing"

    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(one, want.items()))
    # Only players still on this season's cards; a dropped one leaves the list.
    man = {pid: ab for pid, ab in man.items() if pid in want}
    if base and base != label:
        with open(man_p, "w") as f:
            json.dump({str(k): v for k, v in sorted(man.items())}, f, separators=(",", ":"))
    n = {k: res.count(k) for k in set(res)}
    size = sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(out) for f in fs)
    print(f"  heads: {n}  ({size / 1024 / 1024:.1f} MB on disk)")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "crests":
        crests()
    else:
        heads(sys.argv[2] if len(sys.argv) > 2 else "2025-26")
