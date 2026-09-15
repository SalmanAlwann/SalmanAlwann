#!/usr/bin/env python3
"""Render the profile README card: ASCII portrait + neofetch-style panel with live GitHub stats.

    GITHUB_TOKEN=... python generate.py    # refresh stats.json from the GitHub API, then render
    python generate.py --offline           # render from the committed stats.json

Writes dark_mode.svg and light_mode.svg. Standard library only.
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent
API = "https://api.github.com"

WIDTH = 985
FONT_SIZE = 16
CHAR_W = 9.6  # Consolas at 109% size-adjust, Menlo, DejaVu Sans Mono are all ~9.6px wide at 16px
LINE_H = 20
ART_X, PANEL_X, TOP = 15, 390, 30
PANEL_CHARS = 60
STATS_LEFT = 35  # left column width of the two-column stat lines

THEMES = {
    "dark": {"bg": "#161b22", "text": "#c9d1d9", "key": "#ffa657", "value": "#a5d6ff",
             "add": "#3fb950", "del": "#f85149", "cc": "#616e7f"},
    "light": {"bg": "#f6f8fa", "text": "#24292f", "key": "#953800", "value": "#0a3069",
              "add": "#1a7f37", "del": "#cf222e", "cc": "#c2cfde"},
}


# ---------------------------------------------------------------- GitHub stats

def api(path, token, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "profile-card-generator")
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read()
        return resp.status, json.loads(raw) if raw else None


REPO_QUERY = """
query($login: String!, $cursor: String, $privacy: RepositoryPrivacy) {
  user(login: $login) {
    followers { totalCount }
    contributed: repositories(first: 1, ownerAffiliations: [OWNER, COLLABORATOR, ORGANIZATION_MEMBER], privacy: $privacy) { totalCount }
    repositories(first: 100, after: $cursor, ownerAffiliations: OWNER, privacy: $privacy) {
      totalCount
      pageInfo { hasNextPage endCursor }
      nodes { name isFork stargazerCount owner { login } }
    }
  }
}"""


def contributor_stats(login, owner, name, token):
    """Commits/additions/deletions by `login` in one repo, or None while GitHub is still computing."""
    for attempt in range(8):
        status, data = api(f"/repos/{owner}/{name}/stats/contributors", token)
        if status == 202:
            time.sleep(min(3 + 2 * attempt, 12))
            continue
        totals = {"commits": 0, "additions": 0, "deletions": 0}
        for entry in data or []:
            if (entry.get("author") or {}).get("login", "").lower() == login.lower():
                totals["commits"] = entry["total"]
                totals["additions"] = sum(w["a"] for w in entry["weeks"])
                totals["deletions"] = sum(w["d"] for w in entry["weeks"])
        return totals
    return None


def fetch_stats(login, token, include_private, cached):
    privacy = None if include_private else "PUBLIC"
    repos, cursor = [], None
    while True:
        _, res = api("/graphql", token, {"query": REPO_QUERY, "variables": {"login": login, "cursor": cursor, "privacy": privacy}})
        if res.get("errors"):
            raise RuntimeError(res["errors"])
        user = res["data"]["user"]
        repos += user["repositories"]["nodes"]
        page = user["repositories"]["pageInfo"]
        if not page["hasNextPage"]:
            break
        cursor = page["endCursor"]

    per_repo = {}
    for repo in repos:
        if repo["isFork"]:
            continue
        stats = contributor_stats(login, repo["owner"]["login"], repo["name"], token)
        if stats is None:  # GitHub is still computing: keep the last known numbers for this repo
            stats = cached.get("per_repo", {}).get(repo["name"], {"commits": 0, "additions": 0, "deletions": 0})
            print(f"  {repo['name']}: stats not ready, using cached values", file=sys.stderr)
        per_repo[repo["name"]] = stats

    return {
        "repos": user["repositories"]["totalCount"],
        "contributed": user["contributed"]["totalCount"],
        "stars": sum(r["stargazerCount"] for r in repos),
        "followers": user["followers"]["totalCount"],
        "commits": sum(s["commits"] for s in per_repo.values()),
        "additions": sum(s["additions"] for s in per_repo.values()),
        "deletions": sum(s["deletions"] for s in per_repo.values()),
        "per_repo": dict(sorted(per_repo.items())),
    }


# ---------------------------------------------------------------- panel text

def field(key, value_segs, width, lead=True):
    """'. Key.Sub: ...... value' padded with dots to exactly `width` characters."""
    segs = [(". ", "cc")] if lead else []
    for i, part in enumerate(key.split(".")):
        if i:
            segs.append((".", None))
        segs.append((part, "key"))
    segs.append((":", None))
    used = sum(len(t) for t, _ in segs) + sum(len(t) for t, _ in value_segs) + 2
    if used >= width:
        raise ValueError(f"'{key}' does not fit in {width} characters")
    return segs + [(" " + "." * (width - used) + " ", "cc")] + value_segs


def header(label, title=False):
    head = f"{label} -" if title else f"- {label} -"
    return [(head + "—" * (PANEL_CHARS - len(head) - 3) + "-—-", None)]


def panel_lines(profile, stats):
    n = lambda v: f"{v:,}"
    lines = [header(profile["title"], title=True)]
    for item in profile["info"]:
        lines.append([(". ", "cc")] if item == "." else field(item[0], [(item[1], "value")], PANEL_CHARS))
    lines += [[], header("Contact")]
    lines += [field(k, [(v, "value")], PANEL_CHARS) for k, v in profile["contact"]]
    lines += [[], header("GitHub Stats")]

    right = PANEL_CHARS - STATS_LEFT - 3
    lines.append(
        field("Repos", [(n(stats["repos"]), "value"), (" {", None), ("Contributed", "key"), (": ", None),
                        (n(stats["contributed"]), "value"), ("}", None)], STATS_LEFT)
        + [(" | ", None)] + field("Stars", [(n(stats["stars"]), "value")], right, lead=False)
    )
    lines.append(
        field("Commits", [(n(stats["commits"]), "value")], STATS_LEFT)
        + [(" | ", None)] + field("Followers", [(n(stats["followers"]), "value")], right, lead=False)
    )
    loc = [(n(stats["additions"] - stats["deletions"]), "value"), (" ( ", None), (n(stats["additions"]), "add"),
           ("++", "add"), (", ", None), (n(stats["deletions"]), "del"), ("--", "del"), (" )", None)]
    try:
        lines.append(field("Lines of Code on GitHub", loc, PANEL_CHARS))
    except ValueError:  # numbers outgrew the long label
        lines.append(field("Lines of Code", loc, PANEL_CHARS))
    return lines


# ---------------------------------------------------------------- SVG

def tspans(segs):
    merged = []
    for text, cls in segs:
        if merged and merged[-1][1] == cls:
            merged[-1][0] += text
        else:
            merged.append([text, cls])
    return "".join(escape(t) if cls is None else f'<tspan class="{cls}">{escape(t)}</tspan>' for t, cls in merged)


def text_row(x, y, content, length, cls, delay):
    return (f'<text x="{x:g}" y="{y}" textLength="{length:g}" class="{cls}" '
            f'style="animation-delay:{delay:.2f}s">{content}</text>')


def render(theme, profile, stats):
    c = THEMES[theme]
    art = (ROOT / "ascii" / f"{theme}.txt").read_text().rstrip("\n").split("\n")
    lines = panel_lines(profile, stats)
    rows = max(len(art), len(lines))
    height = TOP + (rows - 1) * LINE_H + LINE_H

    body = []
    for r, row in enumerate(art):
        stripped = row.rstrip()
        lead = len(stripped) - len(stripped.lstrip())
        if stripped.strip():
            body.append(text_row(ART_X + lead * CHAR_W, TOP + r * LINE_H, escape(stripped[lead:]),
                                 (len(stripped) - lead) * CHAR_W, "ascii", 0.15 + r * 0.06))
    for i, segs in enumerate(lines):
        length = sum(len(t) for t, _ in segs)
        if length:
            body.append(text_row(PANEL_X, TOP + i * LINE_H, tspans(segs), length * CHAR_W, "line", 0.5 + i * 0.07))

    return f"""<?xml version='1.0' encoding='UTF-8'?>
<svg xmlns="http://www.w3.org/2000/svg" font-family="ConsolasFallback,Consolas,monospace" width="{WIDTH}px" height="{height}px" font-size="{FONT_SIZE}px">
<style>
@font-face {{
src: local('Consolas'), local('Consolas Bold');
font-family: 'ConsolasFallback';
font-display: swap;
-webkit-size-adjust: 109%;
size-adjust: 109%;
}}
.key {{fill: {c["key"]};}}
.value {{fill: {c["value"]};}}
.add {{fill: {c["add"]};}}
.del {{fill: {c["del"]};}}
.cc {{fill: {c["cc"]};}}
text, tspan {{white-space: pre;}}
text {{fill: {c["text"]};}}
@keyframes fade {{ from {{opacity: 0;}} to {{opacity: 1;}} }}
@keyframes type {{ 0% {{opacity: 0; clip-path: inset(0 100% 0 0);}} 1% {{opacity: 1;}} 100% {{opacity: 1; clip-path: inset(0 0 0 0);}} }}
.ascii {{animation: fade .4s ease-out both;}}
.line {{animation: type .5s steps(30, end) both;}}
@media (prefers-reduced-motion: reduce) {{ .ascii, .line {{animation: none;}} }}
</style>
<rect width="{WIDTH}px" height="{height}px" fill="{c["bg"]}" rx="15"/>
{chr(10).join(body)}
</svg>
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true", help="render from stats.json without calling the API")
    args = ap.parse_args()

    profile = json.loads((ROOT / "profile.json").read_text())
    stats_path = ROOT / "stats.json"
    cached = json.loads(stats_path.read_text()) if stats_path.exists() else {}

    if args.offline:
        stats = cached
    else:
        token = os.environ.get("GITHUB_TOKEN")
        if not token:
            sys.exit("GITHUB_TOKEN is not set (use --offline to render from stats.json)")
        include_private = os.environ.get("INCLUDE_PRIVATE", "").lower() == "true"
        stats = fetch_stats(profile["login"], token, include_private, cached)
        stats_path.write_text(json.dumps(stats, indent=2) + "\n")

    for theme in THEMES:
        (ROOT / f"{theme}_mode.svg").write_text(render(theme, profile, stats))
    print(f"rendered: repos={stats['repos']} stars={stats['stars']} commits={stats['commits']} "
          f"followers={stats['followers']} loc=+{stats['additions']}/-{stats['deletions']}")


if __name__ == "__main__":
    main()
