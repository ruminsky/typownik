#!/usr/bin/env python3
"""Typownik player leaderboards (stdlib only, Python 3.9+).

Collects current-season player leaderboards - goals, assists, yellow cards, red cards -
for the leagues used by Typownik, from FotMob's public league stats pages:

  1. https://www.fotmob.com/leagues/<id>/stats/<slug>   (HTML, embedded __NEXT_DATA__ JSON)
     -> confirms the selected season (e.g. "2026/2027") and gives the "see all" URLs
  2. https://data.fotmob.com/stats/<id>/season/<seasonId>/<stat>.json
     -> the full stat lists the page loads (player, team, value, matches, minutes)

No login, cookies or API keys. Nothing is invented: a row that fails validation is
skipped, a board that cannot be fetched is left out, and every problem is listed in
"errors". Secondary fields (e.g. assists on the goals board) are only filled when the
source gives them; otherwise the key is omitted.

Usage:
  python3 -I ~/.typownik/players_parse.py --out ~/.typownik/data/players.json
  python3 -I ~/.typownik/players_parse.py --out FILE --leagues pl,laliga --limit 25

Exit code: 0 = every requested league has all four boards, 3 = partial (see "errors"),
1 = nothing usable (the output file is then left untouched).
"""
import argparse
import datetime as dt
import gzip
import http.client
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "Mozilla/5.0 (Typownik)"
SOURCE = "FotMob (www.fotmob.com league stats pages + data.fotmob.com stat lists)"

# key -> (display name, FotMob league id, FotMob URL slug)
LEAGUES = [
    ("pl", "Premier League", 47, "premier-league"),
    ("laliga", "LaLiga", 87, "laliga"),
    ("seriea", "Serie A", 55, "serie"),
    ("bundesliga", "Bundesliga", 54, "bundesliga"),
    ("ligue1", "Ligue 1", 53, "ligue-1"),
    ("championship", "EFL Championship", 48, "championship"),
    ("ucl", "Champions League", 42, "champions-league"),
]

# board -> FotMob stat list name, our value key, and the secondary value FotMob puts in
# SubStatValue (only used when the list's Subtitle confirms what it is).
BOARDS = {
    "goals": {"stat": "goals", "key": "goals", "sub_key": "penGoals", "sub_title": "penalty goals"},
    "assists": {"stat": "goal_assist", "key": "assists"},
    "yellow": {"stat": "yellow_card", "key": "yellow", "sub_key": "red", "sub_title": "red cards"},
    "red": {"stat": "red_card", "key": "red", "sub_key": "yellow", "sub_title": "yellow cards"},
}
GA_STAT = "_goals_and_goal_assist"  # used only to fill the optional goals<->assists fields

NEXT_RX = re.compile(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S)


class Blocked(Exception):
    pass


CONNECT_TIMEOUT = 4.0


def _connect(address, timeout=None, source_address=None):
    """Like socket.create_connection, but with a short connect timeout per resolved address.
    FotMob's CloudFront IPs occasionally drop a SYN; the stdlib would then wait the whole read
    timeout on that one address before trying the next one."""
    host, port = address
    infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    err = OSError("no addresses for %s" % host)
    for _ in range(2):
        for fam, typ, proto, _canon, sa in infos:
            s = socket.socket(fam, typ, proto)
            try:
                s.settimeout(CONNECT_TIMEOUT)
                if source_address:
                    s.bind(source_address)
                s.connect(sa)
                s.settimeout(timeout if isinstance(timeout, (int, float)) else None)
                return s
            except OSError as e:
                err = e
                s.close()
    raise err


class _HTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect


class _HTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        kw = {"context": getattr(self, "_context", None)}
        if getattr(self, "_check_hostname", None) is not None:
            kw["check_hostname"] = self._check_hostname
        return self.do_open(_HTTPSConnection, req, **kw)


class Http:
    """Polite GET client: fixed UA, timeout, pause between requests, no retry on 403/429."""

    def __init__(self, pause, timeout, verbose=False):
        self.pause = pause
        self.timeout = timeout
        self.verbose = verbose
        self.blocked = {}  # host -> reason
        self.last = 0.0
        self.count = 0
        self.opener = urllib.request.build_opener(_HTTPSHandler())

    def get(self, url):
        host = urllib.parse.urlsplit(url).netloc
        if host in self.blocked:
            raise Blocked("%s skipped: host refused earlier (%s)" % (url, self.blocked[host]))
        err = None
        for attempt in (1, 2):
            wait = self.pause - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            t0 = self.last = time.time()
            self.count += 1
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept-Encoding": "gzip",
                "Accept": "text/html,application/json;q=0.9,*/*;q=0.8"})
            try:
                with self.opener.open(req, timeout=self.timeout) as r:
                    body = r.read()
                if body[:2] == b"\x1f\x8b":  # data.fotmob.com gzips even when not asked to
                    body = gzip.decompress(body)
                if self.verbose:
                    print("GET %s %d bytes %.1fs" % (url, len(body), time.time() - t0), file=sys.stderr)
                return body.decode("utf-8")
            except urllib.error.HTTPError as e:
                head = e.read(4096) if hasattr(e, "read") else b""
                if e.code == 403 and b"<Code>AccessDenied</Code>" in head:
                    # data.fotmob.com is an S3 bucket: a missing file answers 403 AccessDenied.
                    raise IOError("%s -> no such file (S3 403 AccessDenied)" % url)
                if e.code in (401, 403, 429):
                    self.blocked[host] = "HTTP %d" % e.code
                    raise Blocked("%s -> HTTP %d (host refused; not retrying)" % (url, e.code))
                err = "%s -> HTTP %d" % (url, e.code)
                if e.code < 500:
                    break
            except (urllib.error.URLError, OSError, ValueError) as e:  # timeouts, DNS, bad gzip
                err = "%s -> %s" % (url, getattr(e, "reason", e))
            if attempt == 1:
                print("retrying after: %s" % err, file=sys.stderr)
                time.sleep(max(self.pause, 3.0))
        raise IOError(err)


def expected_season(today):
    """European season label for a date: Jul-Dec -> 'Y/Y+1', Jan-Jun -> 'Y-1/Y'."""
    y = today.year if today.month >= 7 else today.year - 1
    return "%d/%d" % (y, y + 1)


def as_count(v):
    """Non-negative whole number from a JSON number, else None (never guesses)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v < 0 or abs(v - round(v)) > 1e-9:
        return None
    return int(round(v))


def read_league_page(http, lid, slug, season):
    """Return (stat urls by stat name, set of season ids) after checking the page's season."""
    url = "https://www.fotmob.com/leagues/%d/stats/%s" % (lid, slug)
    m = NEXT_RX.search(http.get(url))
    if not m:
        raise ValueError("%s: no __NEXT_DATA__ JSON in page" % url)
    pp = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
    details = pp.get("details") or {}
    if details.get("id") not in (lid, str(lid)):
        raise ValueError("%s: page is for league id %r, expected %d" % (url, details.get("id"), lid))
    sel = details.get("selectedSeason")
    if sel != season:
        raise ValueError("%s: page shows season %r, expected %r (not using other seasons)" % (url, sel, season))
    stats = pp.get("stats") or {}
    sids = {str(x.get("TournamentId")) for x in stats.get("seasonStatLinks") or []
            if x.get("Name") == season and x.get("TournamentId") is not None}
    if not sids:
        raise ValueError("%s: no stat links for season %s" % (url, season))
    urls = {}
    for cat in stats.get("players") or []:
        name, u = cat.get("name"), cat.get("fetchAllUrl")
        if isinstance(name, str) and isinstance(u, str):
            urls[name] = u
    return url, urls, sids


def stat_url(lid, urls, sids, stat):
    """Page-provided URL for a stat list (must point at the expected season), else built from the season id."""
    u = urls.get(stat)
    if u:
        m = re.search(r"/stats/(\d+)/season/(\d+)/", u)
        if not m or m.group(1) != str(lid) or m.group(2) not in sids:
            raise ValueError("%s: link does not point at the current season (%s)" % (u, "/".join(sorted(sids))))
        return u
    if len(sids) != 1:
        raise ValueError("stat %s not on page and season id ambiguous (%s)" % (stat, sorted(sids)))
    return "https://data.fotmob.com/stats/%d/season/%s/%s.json" % (lid, next(iter(sids)), stat)


def fetch_list(http, url, stat):
    d = json.loads(http.get(url))
    lists = [t for t in d.get("TopLists") or [] if t.get("StatName") == stat]
    if not lists:
        raise ValueError("%s: no TopList named %r" % (url, stat))
    t = lists[0]
    rows = t.get("StatList")
    if not isinstance(rows, list):
        raise ValueError("%s: StatList missing" % url)
    return t, rows


def clean_rows(rows, stat, errs, where):
    """Validate raw FotMob rows; returns dicts with id/player/team/teamId/value/sub/apps/minutes."""
    out = []
    for i, r in enumerate(rows):
        name = (r.get("ParticipantName") or "").strip() if isinstance(r.get("ParticipantName"), str) else ""
        team = (r.get("TeamName") or "").strip() if isinstance(r.get("TeamName"), str) else ""
        val = as_count(r.get("StatValue"))
        apps = as_count(r.get("MatchesPlayed"))
        mins = as_count(r.get("MinutesPlayed"))
        pid = r.get("ParticiantId", r.get("ParticipantId"))  # FotMob's key really is misspelled
        problem = None
        if not name or not team:
            problem = "missing player/team name"
        elif val is None:
            problem = "bad %s value %r" % (stat, r.get("StatValue"))
        elif apps is None or apps < 1:
            problem = "bad matches played %r" % r.get("MatchesPlayed")
        elif stat in ("red_card",) and val > apps:
            problem = "%d red cards in %d matches" % (val, apps)
        elif stat in ("yellow_card",) and val > 2 * apps:
            problem = "%d yellow cards in %d matches" % (val, apps)
        if problem:
            errs.append("%s: row %d (%s) skipped: %s" % (where, i + 1, name or "?", problem))
            continue
        out.append({"id": pid, "player": name, "team": team, "teamId": r.get("TeamId"),
                    "value": val, "sub": r.get("SubStatValue"), "apps": apps, "minutes": mins})
    return out


def top(rows, limit, cap):
    """Highest values first (stable: keeps FotMob's tie order); ties at the cut-off kept up to cap.
    Returns (rows, note) where note says how many tied players were left out, if any."""
    rows = sorted([r for r in rows if r["value"] > 0], key=lambda r: -r["value"])
    if len(rows) <= limit:
        return rows, None
    cut = rows[limit - 1]["value"]
    keep = [r for i, r in enumerate(rows) if i < limit or r["value"] == cut]
    if len(keep) <= cap:
        return keep, None
    return keep[:cap], ("%d shown; %d players have %d or more (players tied on %d listed in FotMob's order)"
                        % (cap, len(keep), cut, cut))


def collect_league(http, key, name, lid, slug, season, limit, errors):
    where = key
    page_url, urls, sids = read_league_page(http, lid, slug, season)
    league = {"name": name, "season": season, "sourceUrls": [page_url]}
    raw = {}
    for board, spec in BOARDS.items():
        try:
            u = stat_url(lid, urls, sids, spec["stat"])
            meta, rows = fetch_list(http, u, spec["stat"])
            league["sourceUrls"].append(u)
            raw[board] = (meta, clean_rows(rows, spec["stat"], errors, "%s/%s" % (where, board)))
        except (Blocked, IOError, ValueError) as e:
            errors.append("%s/%s: %s" % (where, board, e))
    if "goals" not in raw and "assists" not in raw and "yellow" not in raw and "red" not in raw:
        raise ValueError("no boards could be fetched")

    # goals + assists list: lets us state assists for scorers who have none (G+A == G) and vice versa.
    ga = None
    if "goals" in raw and "assists" in raw:
        try:
            u = stat_url(lid, urls, sids, GA_STAT)
            _, rows = fetch_list(http, u, GA_STAT)
            ga = {r["id"]: r["value"] for r in clean_rows(rows, GA_STAT, [], where) if r["id"] is not None}
            gmap = {r["id"]: r["value"] for r in raw["goals"][1] if r["id"] is not None}
            amap = {r["id"]: r["value"] for r in raw["assists"][1] if r["id"] is not None}
            bad = [pid for pid in set(ga) if ga[pid] != gmap.get(pid, 0) + amap.get(pid, 0)]
            if bad or any(pid not in ga for pid in set(gmap) | set(amap)):
                errors.append("%s: goals/assists/G+A lists are not from one snapshot (%d mismatches);"
                              " cross-filled goals/assists omitted" % (where, len(bad)))
                ga = None
            else:
                league["sourceUrls"].append(u)
        except (Blocked, IOError, ValueError) as e:
            errors.append("%s/goals+assists: %s (cross-filled goals/assists omitted)" % (where, e))
            ga = None
    gmap = {r["id"]: r["value"] for r in raw["goals"][1] if r["id"] is not None} if "goals" in raw else {}
    amap = {r["id"]: r["value"] for r in raw["assists"][1] if r["id"] is not None} if "assists" in raw else {}

    cap = max(limit * 2, limit + 10)
    for board, spec in BOARDS.items():
        if board not in raw:
            continue
        meta, rows = raw[board]
        sub_ok = bool(spec.get("sub_key")) and str(meta.get("Subtitle", "")).strip().lower() == spec["sub_title"]
        if spec.get("sub_key") and not sub_ok:
            errors.append("%s/%s: secondary column is %r, not %r - omitted" %
                          (where, board, meta.get("Subtitle"), spec["sub_title"]))
        chosen, note = top(rows, limit, cap)
        if note:
            league.setdefault("cutoff", {})[board] = note
        out = []
        for r in chosen:
            row = {"player": r["player"], "team": r["team"], "apps": r["apps"], spec["key"]: r["value"]}
            if r["minutes"] is not None:
                row["minutes"] = r["minutes"]
            if sub_ok and as_count(r["sub"]) is not None:
                row[spec["sub_key"]] = as_count(r["sub"])
            if board == "goals" and r["id"] in amap:
                row["assists"] = amap[r["id"]]
            elif board == "goals" and ga is not None and r["id"] in ga:
                row["assists"] = ga[r["id"]] - r["value"]
            if board == "assists" and r["id"] in gmap:
                row["goals"] = gmap[r["id"]]
            elif board == "assists" and ga is not None and r["id"] in ga:
                row["goals"] = ga[r["id"]] - r["value"]
            row["playerId"], row["teamId"] = r["id"], r["teamId"]
            out.append(row)
        league[board] = out
    return league


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=os.path.expanduser("~/.typownik/data/players.json"))
    ap.add_argument("--leagues", default=",".join(k for k, *_ in LEAGUES),
                    help="comma-separated keys: " + ",".join(k for k, *_ in LEAGUES))
    ap.add_argument("--limit", type=int, default=20, help="rows per board (ties at the cut-off are kept)")
    ap.add_argument("--season", default=None, help="expected season label, default from today's date, e.g. 2026/2027")
    ap.add_argument("--pause", type=float, default=1.5, help="seconds between requests")
    ap.add_argument("--timeout", type=float, default=25.0)
    ap.add_argument("-v", "--verbose", action="store_true", help="log every request to stderr")
    a = ap.parse_args(argv)

    now = dt.datetime.now(dt.timezone.utc)
    season = a.season or expected_season(now.date())
    wanted = [k.strip() for k in a.leagues.split(",") if k.strip()]
    known = {k: (n, lid, slug) for k, n, lid, slug in LEAGUES}
    errors = ["unknown league key %r" % k for k in wanted if k not in known]
    http = Http(a.pause, a.timeout, a.verbose)
    leagues = {}
    for key in wanted:
        if key not in known:
            continue
        name, lid, slug = known[key]
        try:
            leagues[key] = collect_league(http, key, name, lid, slug, season, max(1, a.limit), errors)
        except (Blocked, IOError, ValueError) as e:
            errors.append("%s: league skipped: %s" % (key, e))

    complete = all(k in leagues and all(b in leagues[k] for b in BOARDS) for k in wanted)
    for k, lg in leagues.items():
        print("%-12s %s" % (k, "  ".join("%s=%d" % (b, len(lg[b])) if b in lg else "%s=MISSING" % b
                                          for b in BOARDS)), file=sys.stderr)
    for e in errors:
        print("ERROR " + e, file=sys.stderr)
    print("%d requests" % http.count, file=sys.stderr)
    if not leagues:
        print("nothing parsed; %s left untouched" % a.out, file=sys.stderr)
        return 1

    doc = {
        "updatedAt": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "season": season,
        "source": [SOURCE],
        "notes": {
            "apps": "matches played in this competition this season, substitute appearances included",
            "minutes": "minutes played in this competition this season",
            "team": "FotMob team name (not mapped to Typownik display names)",
            "penGoals": "penalty goals included in goals",
            "ucl": "FotMob's Champions League stats (checked 2026-11-09: league phase only, no qualifying rounds)",
            "omitted": "a missing optional key means the source did not give it, not zero",
            "cutoff": "per league: boards where players tied at the last place were cut off",
        },
        "leagues": leagues,
        "errors": errors,
    }
    out = os.path.abspath(os.path.expanduser(a.out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, out)
    print("wrote %s" % out, file=sys.stderr)
    return 0 if complete and not errors else 3


if __name__ == "__main__":
    sys.exit(main())
