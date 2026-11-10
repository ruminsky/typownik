#!/usr/bin/env python3
"""Betclic I liga (Polish second tier) 2026/27 - fixtures, results and league table.

Source: 90minut.pl season page (plain HTTP, ISO-8859-2). One page holds the table, the
cross-table of results and every round ("Kolejka N") with dates, kick-off times and scores.

Usage (stdlib only, Python 3.9+):
  python3 -I pl1_parse.py fixtures pl1   -> JSON list of upcoming matches
  python3 -I pl1_parse.py results pl1    -> JSON list of played matches
  python3 -I pl1_parse.py table pl1      -> JSON league table
  python3 -I pl1_parse.py selftest       -> counts, samples and consistency checks

JSON goes to stdout. Everything that was skipped or looked inconsistent goes to stderr as a
"# report" line; nothing is guessed. On a fetch/parse failure nothing is printed to stdout
and the exit code is 2.

Shapes:
  fixtures -> {"comp","date","time","kickoff_utc","home","away"} (+ "tbc": true when the
              kick-off time is not published: kickoff_utc is then 12:00 UTC of that date)
  results  -> {"comp","date","home","away","hg","ag"}
  table    -> {"comp","group","pos","team","p","w","d","l","gf","ga","pts"}
Dates are the local (Europe/Warsaw) match dates printed by the source.
"""
import datetime as dt
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

COMP = "pl1"
SEASON = "2026/2027"
UA = "Mozilla/5.0 (Typownik)"
TIMEOUT = 25
PAUSE = 2.0  # seconds between requests to the same process
BASE = "http://www.90minut.pl"  # the site does not answer over HTTPS
PRIMARY_URL = BASE + "/liga/1/liga14676.html"  # Betclic I liga 2026/2027 (id_sezon=109)
WARSAW = ZoneInfo("Europe/Warsaw")

MONTHS = {
    "stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6,
    "lipca": 7, "sierpnia": 8, "września": 9, "października": 10, "listopada": 11, "grudnia": 12,
    "styczeń": 1, "luty": 2, "marzec": 3, "kwiecień": 4, "maj": 5, "czerwiec": 6,
    "lipiec": 7, "sierpień": 8, "wrzesień": 9, "październik": 10, "listopad": 11, "grudzień": 12,
}

_last_request = [0.0]
_cache = {}


# ----------------------------------------------------------------------------- fetching
def _http_get(url):
    """Return (status, text, error). 403/429 are returned at once (caller moves on)."""
    for attempt in (1, 2):
        wait = PAUSE - (time.time() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pl,en;q=0.5"})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read(6_000_000)
                return r.status, _decode(raw, r.headers.get_content_charset()), None
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) or e.code < 500 or attempt == 2:
                return e.code, "", "HTTP %d" % e.code
        except Exception as e:  # timeout, DNS, connection reset
            if attempt == 2:
                return 0, "", "%s: %s" % (type(e).__name__, e)
    return 0, "", "unreachable"


def _decode(raw, declared):
    """90minut declares ISO-8859-2; accept UTF-8 if the bytes are valid UTF-8 with non-ASCII."""
    try:
        text = raw.decode("utf-8")
        if re.search(r"[^\x00-\x7f]", text):
            return text
    except UnicodeDecodeError:
        pass
    if not declared:
        m = re.search(rb'charset=["\']?([A-Za-z0-9_-]+)', raw[:4000])
        declared = m.group(1).decode("ascii") if m else "iso-8859-2"
    try:
        return raw.decode(declared)
    except (LookupError, UnicodeDecodeError):
        return raw.decode("iso-8859-2", "replace")


def _title(page):
    m = re.search(r"<title>(.*?)</title>", page, re.S | re.I)
    return _text(m.group(1)) if m else ""


def _is_season_page(page):
    return bool(re.search(r"(?<![I])\bI liga\s+" + re.escape(SEASON) + r"\b", _title(page)))


def _discover(report):
    """Find the I liga 2026/2027 page through the site's menus (homepage -> season archive)."""
    st, home, err = _http_get(BASE + "/")
    if st != 200:
        report["errors"].append("90minut homepage: %s" % (err or st))
        return None
    want = SEASON.replace("/", " / ")
    arch = None
    for href, label in re.findall(r'<a[^>]+href="([^"]*archsezon\.php\?id_sezon=\d+)"[^>]*>(.*?)</a>', home, re.S | re.I):
        if _text(label) == want:
            arch = href
            break
    if not arch:
        report["errors"].append("90minut: no season archive link for %s" % want)
        return None
    st, page, err = _http_get(BASE + "/" + arch.lstrip("/"))
    if st != 200:
        report["errors"].append("90minut season archive: %s" % (err or st))
        return None
    for href, label in re.findall(r'<a[^>]+href="(/liga/\d+/liga\d+\.html)"[^>]*>(.*?)</a>', page, re.S | re.I):
        if re.search(r"(?<![I])\bI liga\s+" + re.escape(SEASON) + r"\b", _text(label)):
            return BASE + href
    report["errors"].append("90minut: no 'I liga %s' link in season archive" % SEASON)
    return None


def _fetch(report):
    st, page, err = _http_get(PRIMARY_URL)
    if st == 200 and _is_season_page(page):
        return PRIMARY_URL, page
    report["errors"].append("primary %s: %s" % (PRIMARY_URL, err or ("unexpected title %r" % _title(page))))
    if st in (403, 429):  # blocked / rate-limited: do not keep hitting the site
        raise RuntimeError("90minut.pl answered HTTP %d - giving up for now" % st)
    url = _discover(report)
    if url and url != PRIMARY_URL:
        st, page, err = _http_get(url)
        if st == 200 and _is_season_page(page):
            return url, page
        report["errors"].append("%s: %s" % (url, err or ("unexpected title %r" % _title(page))))
    raise RuntimeError("could not fetch Betclic I liga %s from 90minut.pl: %s" % (SEASON, "; ".join(report["errors"])))


# ----------------------------------------------------------------------------- parsing helpers
def _text(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def _rows(table_html):
    return re.findall(r"<tr\b([^>]*)>(.*?)</tr>", table_html, re.S | re.I)


def _cells(row_html):
    return re.findall(r"<td\b([^>]*)>(.*?)</td>", row_html, re.S | re.I)


def _season_years():
    a, b = SEASON.split("/")
    return int(a), int(b)


_DATE_RX = re.compile(r"^(\d{1,2})\s+([^\s,\d()]+)(?:\s+(\d{4}))?\s*(?:,\s*(\d{1,2})[:.](\d{2}))?\s*(?:\([^)]*\))?$")


def _parse_when(cell):
    """'26 lipca, 14:30 (4595)' -> (date, 'HH:MM' or None). Raises ValueError if not understood."""
    m = _DATE_RX.match(cell)
    if not m:
        raise ValueError("unrecognised date cell %r" % cell)
    day, mon, year, hh, mm = m.groups()
    month = MONTHS.get(mon.lower())
    if not month:
        raise ValueError("unknown month %r in %r" % (mon, cell))
    y1, y2 = _season_years()
    inferred = y1 if month >= 7 else y2
    if year and int(year) != inferred:
        raise ValueError("year %s outside season %s in %r" % (year, SEASON, cell))
    d = dt.date(inferred, month, int(day))  # raises ValueError for impossible dates
    tm = None
    if hh is not None:
        h, mi = int(hh), int(mm)
        if not (0 <= h <= 23 and 0 <= mi <= 59):
            raise ValueError("bad time in %r" % cell)
        tm = "%02d:%02d" % (h, mi)
    return d, tm


def _iso_utc(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------- page parsing
def _parse_table(page, report):
    tbl = None
    for t in re.findall(r"<table\b[^>]*>(.*?)</table>", page, re.S | re.I):
        if re.search(r">\s*RAZEM\s*<", t) and re.search(r">\s*Nazwa\s*<", t) and "Pkt." in t:
            tbl = t
            break
    if tbl is None:
        raise RuntimeError("league table not found on page")
    out, ids = [], {}
    for attrs, row in _rows(tbl):
        cells = _cells(row)
        if len(cells) < 8:
            continue
        pos_txt = _text(cells[0][1])
        if not re.fullmatch(r"\d+\.", pos_txt):
            continue
        team = _text(cells[1][1])
        mid = re.search(r"id_klub=(\d+)", cells[1][1])
        vals = [_text(c[1]) for c in cells[2:8]]
        try:
            p, pts, w, d, l = (int(v) for v in vals[:5])
            gm = re.fullmatch(r"(\d+)\s*-\s*(\d+)", vals[5])
            if not gm or not team:
                raise ValueError
            gf, ga = int(gm.group(1)), int(gm.group(2))
        except ValueError:
            report["skipped"].append({"what": "table row", "raw": [pos_txt, team] + vals})
            continue
        if p != w + d + l:
            report["skipped"].append({"what": "table row (P != W+D+L)", "raw": [pos_txt, team] + vals})
            continue
        if pts != 3 * w + d:
            report["warnings"].append("%s: Pkt %d != 3*W+D=%d (point deduction/bonus?) - kept source value" % (team, pts, 3 * w + d))
        out.append({"comp": COMP, "group": None, "pos": int(pos_txt[:-1]), "team": team,
                    "p": p, "w": w, "d": d, "l": l, "gf": gf, "ga": ga, "pts": pts})
        if mid:
            ids[mid.group(1)] = team
    return out, ids


def _parse_grid(page):
    """Cross-table cells: id_mecz -> 'Home H-A Away' (title attribute)."""
    grid = {}
    for mid, title in re.findall(r'<a[^>]+href="/mecz\.php\?id_mecz=(\d+)"[^>]*title="([^"]+)"', page, re.I):
        grid[mid] = _text(title)
    return grid


def _parse_rounds(page, report):
    """Yield dicts for every match row in every 'Kolejka N' section."""
    heads = list(re.finditer(r"<u>(.*?)</u>", page, re.S | re.I))
    matches = []
    for i, h in enumerate(heads):
        label = _text(h.group(1))
        start = page.find("</table>", h.end())
        stop = heads[i + 1].start() if i + 1 < len(heads) else len(page)
        if start < 0:
            continue
        m = re.search(r"<table\b[^>]*>(.*?)</table>", page[start + 8:stop], re.S | re.I)
        rm = re.fullmatch(r"Kolejka (\d+)(?: - (.*))?", label)
        if not rm:
            if m and re.search(r"id_mecz=|>\s*-\s*</td>", m.group(1)):
                report["skipped"].append({"what": "section not parsed (not a league round)", "raw": label})
            continue
        if not m:
            report["skipped"].append({"what": "round without match table", "raw": label})
            continue
        rnd, rnd_dates = int(rm.group(1)), (rm.group(2) or "").strip()
        last = None
        for attrs, row in _rows(m.group(1)):
            if 'class="odds"' in attrs:
                dd = re.search(r'data-date="(\d{4}-\d{2}-\d{2})"', attrs)
                hid = re.search(r'data-home="(\d+)"', attrs)
                aid = re.search(r'data-away="(\d+)"', attrs)
                if last is not None and dd:
                    last["odds_date"] = dd.group(1)
                    last["odds_ids"] = (hid.group(1) if hid else None, aid.group(1) if aid else None)
                continue
            cells = _cells(row)
            if len(cells) != 4 or "colspan" in cells[0][0].lower():
                continue  # scorers / notes rows
            home, score, away, when = (_text(c[1]) for c in cells)
            mid = re.search(r"id_mecz=(\d+)", cells[1][1])
            last = {"round": rnd, "round_dates": rnd_dates, "home": home, "away": away,
                    "score": score, "when": when, "id": mid.group(1) if mid else None}
            matches.append(last)
    return matches


def _check_vs_table(results, table, report):
    """Recompute P/W/D/L/GF/GA from the parsed results and compare with the source table."""
    agg = {r["team"]: dict(p=0, w=0, d=0, l=0, gf=0, ga=0) for r in table}
    for r in results:
        for team, f, a in ((r["home"], r["hg"], r["ag"]), (r["away"], r["ag"], r["hg"])):
            s = agg.get(team)
            if s is None:
                continue
            s["p"] += 1
            s["gf"] += f
            s["ga"] += a
            s["w" if f > a else "d" if f == a else "l"] += 1
    bad = []
    for r in table:
        s = agg[r["team"]]
        diff = {k: (r[k], s[k]) for k in s if r[k] != s[k]}
        if diff:
            bad.append({"team": r["team"], "table_vs_results": diff})
    return bad


def parse(comp=COMP, now=None):
    """Fetch and parse everything once per process. Returns dict with fixtures/results/table/report."""
    if comp != COMP:
        raise ValueError("unsupported comp %r (only %r)" % (comp, COMP))
    if "data" in _cache:
        return _cache["data"]
    now = now or dt.datetime.now(dt.timezone.utc)
    report = {"errors": [], "warnings": [], "skipped": []}
    url, raw = _fetch(report)
    page = re.sub(r"<!--.*?-->", "", raw, flags=re.S)

    table, club_ids = _parse_table(page, report)
    teams = {r["team"] for r in table}
    grid = _parse_grid(page)
    rows = _parse_rounds(page, report)

    results, fixtures, seen = [], [], {}
    cross_checked = date_checked = 0
    today_pl = now.astimezone(WARSAW).date()
    undated, stale = [], []
    for m in rows:
        tag = "R%d %s - %s" % (m["round"], m["home"], m["away"])
        if m["home"] not in teams or m["away"] not in teams or m["home"] == m["away"]:
            report["skipped"].append({"what": "match with unknown team", "raw": tag})
            continue
        key = (m["home"], m["away"])
        if key in seen:
            report["skipped"].append({"what": "duplicate pairing (also in R%d)" % seen[key], "raw": tag})
            continue
        seen[key] = m["round"]
        sm = re.fullmatch(r"(\d+)\s*-\s*(\d+)", m["score"])
        played = bool(sm)
        if not played and m["score"] != "-":
            report["skipped"].append({"what": "unrecognised score cell", "raw": tag + " [%s]" % m["score"]})
            continue
        if not m["when"]:
            if played:
                report["skipped"].append({"what": "result without date", "raw": tag})
            else:
                undated.append(m)
            continue
        try:
            day, hhmm = _parse_when(m["when"])
        except ValueError as e:
            report["skipped"].append({"what": "unparsed date", "raw": tag, "error": str(e)})
            continue
        if m.get("odds_date") and m.get("odds_ids") == (_id_of(club_ids, m["home"]), _id_of(club_ids, m["away"])):
            if m["odds_date"] != day.isoformat():
                report["skipped"].append({"what": "date disagrees with source data-date",
                                          "raw": tag, "error": "%s vs %s" % (day, m["odds_date"])})
                continue
            date_checked += 1
        if played:
            hg, ag = int(sm.group(1)), int(sm.group(2))
            if m["id"] and m["id"] in grid:
                want = "%s %d-%d %s" % (m["home"], hg, ag, m["away"])
                if grid[m["id"]] != want:
                    report["skipped"].append({"what": "match list and cross-table disagree", "raw": want,
                                              "error": "cross-table: %s" % grid[m["id"]]})
                    continue
                cross_checked += 1
            else:
                report["warnings"].append("%s: not found in cross-table (not cross-checked)" % tag)
            results.append({"comp": COMP, "date": day.isoformat(), "home": m["home"], "away": m["away"],
                            "hg": hg, "ag": ag})
            continue
        if hhmm:
            local = dt.datetime.combine(day, dt.time(int(hhmm[:2]), int(hhmm[3:])), tzinfo=WARSAW)
            ko = local.astimezone(dt.timezone.utc)
            upcoming = ko > now
        else:
            ko = dt.datetime.combine(day, dt.time(12, 0), tzinfo=dt.timezone.utc)
            upcoming = day >= today_pl
        if not upcoming:
            stale.append(tag + " (%s %s)" % (day, hhmm or "--:--"))
            continue
        fx = {"comp": COMP, "date": day.isoformat(), "time": hhmm, "kickoff_utc": _iso_utc(ko),
              "home": m["home"], "away": m["away"]}
        if not hhmm:
            fx["tbc"] = True
        fixtures.append(fx)

    if undated:
        by_round = {}
        for m in undated:
            by_round.setdefault("R%d (%s)" % (m["round"], m["round_dates"]), 0)
            by_round["R%d (%s)" % (m["round"], m["round_dates"])] += 1
        report["skipped"].append({"what": "unplayed matches without a published date (not output)",
                                  "count": len(undated), "rounds": by_round})
    if stale:
        report["warnings"].append("unplayed but kick-off already passed (postponed or result not yet entered): "
                                  + "; ".join(stale))
    mism = _check_vs_table(results, table, report)
    if mism:
        report["warnings"].append({"table/results mismatch": mism})

    results.sort(key=lambda r: (r["date"], r["home"]))
    fixtures.sort(key=lambda f: (f["kickoff_utc"], f["home"]))
    table.sort(key=lambda r: r["pos"])
    data = {"source": url, "title": _title(raw), "fetched_at": _iso_utc(now), "fixtures": fixtures,
            "results": results, "table": table, "report": report,
            "_meta": {"rows": len(rows), "grid": len(grid), "teams": len(teams), "table_mismatch": mism,
                      "cross_checked": cross_checked, "year_checked": date_checked}}
    _cache["data"] = data
    return data


def _id_of(ids, team):
    for k, v in ids.items():
        if v == team:
            return k
    return None


# ----------------------------------------------------------------------------- public API
def fixtures(comp=COMP):
    return parse(comp)["fixtures"]


def results(comp=COMP):
    return parse(comp)["results"]


def table(comp=COMP):
    return parse(comp)["table"]


def last_report():
    return _cache["data"]["report"] if "data" in _cache else None


def selftest():
    d = parse(COMP)
    fx, rs, tb, rep = d["fixtures"], d["results"], d["table"], d["report"]
    print("source: %s  (%s)" % (d["source"], d["title"]))
    print("fetched_at: %s" % d["fetched_at"])
    print("counts: table=%d results=%d fixtures=%d match_rows=%d cross_table_cells=%d dates_with_source_year=%d"
          % (len(tb), len(rs), len(fx), d["_meta"]["rows"], d["_meta"]["grid"], d["_meta"]["year_checked"]))
    checks = []
    n = len(tb)
    checks.append(("table has 18 teams", n == 18))
    checks.append(("positions are 1..N", [r["pos"] for r in tb] == list(range(1, n + 1))))
    checks.append(("sum W == sum L", sum(r["w"] for r in tb) == sum(r["l"] for r in tb)))
    checks.append(("sum GF == sum GA", sum(r["gf"] for r in tb) == sum(r["ga"] for r in tb)))
    checks.append(("sum P == 2 x results", sum(r["p"] for r in tb) == 2 * len(rs)))
    checks.append(("table equals table recomputed from results", not d["_meta"]["table_mismatch"]))
    checks.append(("every result matches the cross-table (%d/%d)" % (d["_meta"]["cross_checked"], len(rs)),
                   d["_meta"]["cross_checked"] == len(rs) == d["_meta"]["grid"]))
    checks.append(("no fixture also has a result",
                   not ({(f["home"], f["away"]) for f in fx} & {(r["home"], r["away"]) for r in rs})))
    checks.append(("table sorted by pts", all(tb[i]["pts"] >= tb[i + 1]["pts"] for i in range(n - 1))))
    for name, ok in checks:
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))
    print("table top 3 / bottom 1:")
    for r in tb[:3] + tb[-1:]:
        print("  ", json.dumps(r, ensure_ascii=False))
    print("latest results:")
    for r in rs[-3:]:
        print("  ", json.dumps(r, ensure_ascii=False))
    print("next fixtures:")
    for f in fx[:3]:
        print("  ", json.dumps(f, ensure_ascii=False))
    print("report:", json.dumps(rep, ensure_ascii=False, indent=1))
    return all(ok for _, ok in checks)


def main(argv):
    if len(argv) >= 1 and argv[0] == "selftest":
        try:
            return 0 if selftest() else 1
        except Exception as e:
            print("selftest failed: %s" % e, file=sys.stderr)
            return 2
    if len(argv) != 2 or argv[0] not in ("fixtures", "results", "table") or argv[1] != COMP:
        print(__doc__, file=sys.stderr)
        return 64
    try:
        data = {"fixtures": fixtures, "results": results, "table": table}[argv[0]](argv[1])
    except Exception as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    print(json.dumps(data, ensure_ascii=False, indent=1))
    print("# report " + json.dumps(last_report(), ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
