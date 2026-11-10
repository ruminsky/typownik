#!/usr/bin/env python3
"""Typownik daily builder.

Keeps the public data files of the Typownik app in ~/.typownik/data/ up to date:
  matches.json  upcoming + recently finished matches (finished > 90 days are compacted into history.json)
  tables.json   league tables (display names)
  teams.json    club knowledge base (coach, news, morale) — edited by the daily Claude task
  players.json  player leaderboards (players_parse.py)
  meta.json     update time, which API keys are present, news sources
Sources: Sky Sports (sky_parse.py), 90minut/other (pl1_parse.py), eloratings.net (elo_parse.py),
The Odds API + API-Football + RSS news (typownik_fetch.py). Pure Python, no LLM.

Usage: python3 -I ~/.typownik/typownik_build.py [--no-api] [--no-players] [--days 14]
Then copy ~/.typownik/run/pub/ next to the page and publish (the daily task does this).
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import shutil
import sys

H = os.path.expanduser("~/.typownik")
D = os.path.join(H, "data")
RUN = os.path.join(H, "run")
PUB = os.path.join(RUN, "pub")


def load_mod(name):
    p = os.path.join(H, name + ".py")
    if not os.path.exists(p):
        return None
    spec = importlib.util.spec_from_file_location(name, p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


tf = load_mod("typownik_fetch")
sky = load_mod("sky_parse")
pl1 = load_mod("pl1_parse")
elo_mod = load_mod("elo_parse")

CLUB_LEAGUES = ["pl", "laliga", "seriea", "bundesliga", "ligue1", "championship", "pl1"]
CUPS = ["ucl", "uel", "uecl"]
NATIONS = ["nl", "friendly", "wc", "euro"]
DOMESTIC = ["pl", "laliga", "seriea", "bundesliga", "ligue1", "championship"]
LEAGUE_AVG = {"pl": (1.55, 1.25), "laliga": (1.45, 1.10), "seriea": (1.45, 1.15), "bundesliga": (1.70, 1.40),
              "ligue1": (1.50, 1.20), "championship": (1.40, 1.15), "pl1": (1.45, 1.10)}
# Sky name -> display name used in the app (crests, colours and news matching are keyed by these)
DISPLAY = {"Brighton and Hove Albion": "Brighton", "Tottenham Hotspur": "Tottenham", "Newcastle United": "Newcastle",
           "Leeds United": "Leeds", "Ipswich Town": "Ipswich", "Coventry City": "Coventry", "Atletico Madrid": "Atlético Madryt",
           "Real Madrid": "Real Madryt", "Deportivo La Coruna": "Deportivo La Coruña", "Alaves": "Alavés", "Malaga": "Málaga",
           "Inter Milan": "Inter", "AC Milan": "Milan", "Bayern Munich": "Bayern Monachium", "Cologne": "1. FC Köln",
           "Monchengladbach": "Borussia M'gladbach", "1. FC Union Berlin": "Union Berlin", "SC Paderborn 07": "Paderborn",
           "SV 07 Elversberg": "Elversberg", "TSG Hoffenheim": "Hoffenheim", "SC Freiburg": "Freiburg", "FC Augsburg": "Augsburg",
           "Hamburg": "Hamburger SV", "Paris Saint-Germain": "PSG", "RC Lens": "Lens"}
PRESTIGE = {"Manchester United": 10, "Liverpool": 10, "Real Madryt": 10, "Barcelona": 10, "Bayern Monachium": 10, "PSG": 10,
            "Arsenal": 9, "Manchester City": 9, "Chelsea": 9, "Juventus": 9, "Inter": 9, "Milan": 9, "Borussia Dortmund": 9,
            "Tottenham": 8, "Atlético Madryt": 8, "Napoli": 8, "Roma": 8, "Marseille": 8,
            "Newcastle": 7, "Lazio": 7, "Bayer Leverkusen": 7, "Lyon": 7,
            "Aston Villa": 6, "Everton": 6, "Leeds": 6, "Sevilla": 6, "Real Betis": 6, "Athletic Club": 6, "Valencia": 6,
            "Atalanta": 6, "Fiorentina": 6, "RB Leipzig": 6, "Schalke": 6, "Borussia M'gladbach": 6, "Eintracht Frankfurt": 6,
            "Hamburger SV": 6, "Lille": 6, "Monaco": 6, "Real Sociedad": 5, "Villarreal": 5, "Deportivo La Coruña": 5,
            "Werder Bremen": 5, "1. FC Köln": 5, "Stuttgart": 5, "Nice": 5, "Lens": 5, "Rennes": 5,
            "Legia Warszawa": 6, "Lech Poznań": 5, "Wisła Kraków": 6, "Ruch Chorzów": 5}
DERBIES = {("Sevilla", "Real Betis"), ("Werder Bremen", "Hamburger SV"), ("Liverpool", "Manchester United"),
           ("Borussia M'gladbach", "Borussia Dortmund"), ("Nice", "PSG"), ("Lille", "Lyon"), ("Atalanta", "Inter"),
           ("Getafe", "Atlético Madryt"), ("Newcastle", "Arsenal"), ("Milan", "Inter"), ("Inter", "Milan"),
           ("Real Madryt", "Barcelona"), ("Barcelona", "Real Madryt"), ("Arsenal", "Tottenham"), ("Tottenham", "Arsenal"),
           ("Manchester City", "Manchester United"), ("Manchester United", "Manchester City"), ("Roma", "Lazio"), ("Lazio", "Roma"),
           ("Marseille", "PSG"), ("PSG", "Marseille"), ("Borussia Dortmund", "Bayern Monachium"), ("Bayern Monachium", "Borussia Dortmund")}


def now():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(s):
    return tf.parse_iso(s)


# Sky Sports country names -> eloratings.net English names
SKY_NATION = {"Czech Republic": "Czechia", "Football Union of Russia": "Russia", "Korea Republic": "South Korea",
              "Republic of Ireland": "Ireland", "Swaziland": "Eswatini", "U.A.E.": "United Arab Emirates",
              "USA": "United States", "Bosnia-Herzegovina": "Bosnia and Herzegovina", "Cote d'Ivoire": "Ivory Coast",
              "Côte d'Ivoire": "Ivory Coast", "Turkiye": "Turkey", "Türkiye": "Turkey", "IR Iran": "Iran",
              "Korea DPR": "North Korea", "FYR Macedonia": "North Macedonia", "China PR": "China", "Congo DR": "DR Congo",
              "Cabo Verde": "Cape Verde", "Kyrgyz Republic": "Kyrgyzstan"}


def disp(name):
    return DISPLAY.get(name, SKY_NATION.get(name, name))


def rjson(name, default):
    try:
        with open(os.path.join(D, name), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def wjson(name, obj):
    os.makedirs(D, exist_ok=True)
    tmp = os.path.join(D, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, os.path.join(D, name))


def source_for(comp):
    return pl1 if comp == "pl1" else sky


def safe(fn, comp, report, what):
    mod = source_for(comp)
    if mod is None or not hasattr(mod, fn):
        report["errors"].append(f"{what} {comp}: brak parsera")
        return []
    if mod is sky and comp not in getattr(sky, "COMPS", {}):
        return []  # no Sky page for this competition (e.g. World Cup / Euro outside tournament years)
    try:
        return getattr(mod, fn)(comp) or []
    except Exception as e:  # network/parse problems must not stop the whole build
        report["errors"].append(f"{what} {comp}: {e}")
        return []


def form_of(team, results):
    rs = sorted((r for r in results if r["home"] == team or r["away"] == team), key=lambda r: r["date"])[-5:]
    out = ""
    for r in rs:
        gf, ga = (r["hg"], r["ag"]) if r["home"] == team else (r["ag"], r["hg"])
        out += "W" if gf > ga else "L" if gf < ga else "D"
    return out or None


def last_played(team, all_results):
    ds = [r["date"] for r in all_results if r["home"] == team or r["away"] == team]
    return max(ds) if ds else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--no-api", action="store_true")
    ap.add_argument("--no-players", action="store_true")
    ap.add_argument("--pub-only", action="store_true", help="only copy data/*.json into run/pub/data (after manual edits)")
    a = ap.parse_args()
    if a.pub_only:
        os.makedirs(os.path.join(PUB, "data"), exist_ok=True)
        for f in ("matches.json", "teams.json", "tables.json", "meta.json", "players.json", "crests.json"):
            if os.path.exists(os.path.join(D, f)):
                shutil.copy(os.path.join(D, f), os.path.join(PUB, "data", f))
        print("skopiowano do", os.path.join(PUB, "data"))
        return 0
    report = {"at": iso(now()), "errors": [], "created": 0, "updated": 0, "settled": 0, "compacted": 0}
    state = rjson("matches.json", {"matches": []})
    docs = {m["id"]: m for m in state.get("matches", [])}
    hist = rjson("history.json", {"matches": []})
    teams_kb = rjson("teams.json", {"teams": {}}).get("teams", {})

    # 1) tables and results for every competition we can parse
    comps = CLUB_LEAGUES + CUPS + NATIONS
    tables, results = {}, {}
    for c in comps:
        tables[c] = [dict(r, team=disp(r["team"])) for r in safe("table", c, report, "tabela")]
        results[c] = [dict(r, home=disp(r["home"]), away=disp(r["away"])) for r in safe("results", c, report, "wyniki")]
    all_results = [r for c in comps for r in results[c]]
    team_row, team_dom = {}, {}
    for c in DOMESTIC + ["pl1"]:
        for r in tables[c]:
            team_row[(c, r["team"])] = r
            team_dom.setdefault(r["team"], c)
    lavg = {}
    for c in CLUB_LEAGUES:
        rows = [r for r in tables[c] if r.get("p")]
        if rows:
            mean = sum(r["gf"] for r in rows) / max(1, sum(r["p"] for r in rows))
            lavg[c] = {"home": round(mean * 1.12, 3), "away": round(mean * 0.88, 3)}
        elif c in LEAGUE_AVG:
            lavg[c] = {"home": LEAGUE_AVG[c][0], "away": LEAGUE_AVG[c][1]}
    elo = {}
    if elo_mod is not None:
        try:
            ej = rjson("elo.json", None)
            if not ej or (now() - parse(ej.get("updatedAt", "2000-01-01T00:00:00Z"))).days >= 1:
                os.system(f"{sys.executable} -I {os.path.join(H, 'elo_parse.py')} --out {os.path.join(D, 'elo.json')} >/dev/null 2>&1")
                ej = rjson("elo.json", {})
            elo = (ej or {}).get("teams", {})
        except Exception as e:
            report["errors"].append(f"elo: {e}")

    def team_obj(name, comp, kickoff):
        t = {"name": name}
        if comp in NATIONS:
            e = elo.get(name) or next((v for k, v in elo.items() if v.get("pl") == name), None)
            if e:
                t["elo"] = int(round(e["rating"]))
                t["name"] = e.get("pl") or name
            return t
        dom = comp if comp in CLUB_LEAGUES else team_dom.get(name)
        if dom:
            row = team_row.get((dom, name))
            if row:
                t["pos"] = row["pos"]
                t["overall"] = {k: row[k] for k in ("p", "w", "d", "l", "gf", "ga", "pts")}
            f = form_of(name, results.get(dom, []))
            if f:
                t["form"] = f
            if comp in CUPS:
                t["dom"] = dom
                if dom in lavg:
                    t["domAvg"] = lavg[dom]
        lp = last_played(name, all_results)
        if lp:
            t["restDays"] = max(0, (kickoff - parse(lp + "T12:00:00Z")).days)
        return t

    # 2) fixtures -> create / update documents
    horizon = now() + dt.timedelta(days=a.days)
    for c in comps:
        for fx in safe("fixtures", c, report, "terminarz"):
            try:
                ko = parse(fx["kickoff_utc"])
            except (KeyError, ValueError):
                continue
            if ko > horizon or ko < now() - dt.timedelta(hours=3):
                continue
            h, aw = disp(fx["home"]), disp(fx["away"])
            existing = next((i for i, m in docs.items() if m.get("league") == c and m["home"]["name"] in (h, (elo.get(h) or {}).get("pl"))
                             and m["away"]["name"] in (aw, (elo.get(aw) or {}).get("pl"))
                             and abs((parse(m["kickoff"]) - ko).total_seconds()) <= 4 * 86400), None)
            doc_id = existing or f"{c}-{ko.strftime('%Y-%m-%d')}-{tf.slug(fx['home'])}-{tf.slug(fx['away'])}"
            m = docs.get(doc_id, {"id": doc_id, "league": c, "status": "scheduled", "sources": []})
            hn, an = team_obj(h, c, ko), team_obj(aw, c, ko)
            for side, new in (("home", hn), ("away", an)):
                old = m.get(side, {})
                keep = {k: v for k, v in old.items() if k in ("absences", "keyPlayers", "news", "morale", "transfers", "transfersNote",
                                                           "motivation", "motivationNote", "venue", "coach", "europeMidweek")}
                m[side] = {**new, **keep}
            m["kickoff"] = iso(ko)
            if fx.get("tbc"):
                m["tbc"] = True
            if c in lavg:
                m["leagueAvg"] = lavg[c]
            pop = PRESTIGE.get(m["home"]["name"], 3) + PRESTIGE.get(m["away"]["name"], 3)
            if m["home"].get("pos") and m["away"].get("pos") and m["home"]["pos"] <= 6 and m["away"]["pos"] <= 6:
                pop += 3
            if (m["home"]["name"], m["away"]["name"]) in DERBIES:
                pop += 3
            if c in CUPS:
                pop += 3
            if c in NATIONS:
                pop = 6 + (3 if min(m["home"].get("elo", 0), m["away"].get("elo", 0)) >= 1850 else 0) + \
                      (8 if "Polska" in (m["home"]["name"], m["away"]["name"]) else 0)
            m["popularity"] = pop
            m["sources"] = sorted(set(m.get("sources") or []) | {"skysports.com" if c != "pl1" else "pl1"})
            m["updatedAt"] = iso(now())
            report["updated" if doc_id in docs else "created"] += 1
            docs[doc_id] = m

    # 3) settle finished matches
    for m in docs.values():
        if m.get("status") == "finished" or parse(m["kickoff"]) > now() - dt.timedelta(hours=2, minutes=30):
            continue
        day = m["kickoff"][:10]
        names = {m["home"]["name"], m["away"]["name"]}
        for r in results.get(m["league"], []):
            if abs((parse(r["date"] + "T12:00:00Z") - parse(day + "T12:00:00Z")).days) <= 1:
                rh, ra = r["home"], r["away"]
                rh = (elo.get(rh) or {}).get("pl", rh) if m["league"] in NATIONS else rh
                ra = (elo.get(ra) or {}).get("pl", ra) if m["league"] in NATIONS else ra
                if rh == m["home"]["name"] and ra == m["away"]["name"]:
                    m["status"], m["result"] = "finished", {"h": r["hg"], "a": r["ag"]}
                    report["settled"] += 1
                    break

    # 4) APIs + news (odds, injuries, h2h, coaches)
    up = {i: m for i, m in docs.items() if m.get("status") != "finished" and parse(m["kickoff"]) <= horizon}
    if tf is not None and not a.no_api:
        fr = {"at": iso(now()), "errors": [], "unmatched": [], "news": {}, "odds_api": {"key": False, "matched": 0},
              "api_football": {"key": False, "injury_docs": 0, "h2h_docs": 0}}
        patches, team_patches = {}, {}
        ok, fk = tf.read_key("odds-api.key"), tf.read_key("api-football.key")
        fr["odds_api"]["key"], fr["api_football"]["key"] = bool(ok), bool(fk)
        try:
            if ok:
                tf.fetch_odds({i: m for i, m in up.items() if m["league"] in tf.ODDS_SPORT
                               and parse(m["kickoff"]) <= now() + dt.timedelta(days=4)}, ok, fr, patches)  # saves credits
            if fk:
                tf.fetch_af({i: m for i, m in up.items() if m["league"] in tf.AF_LEAGUE}, fk, 90, fr, patches, team_patches)
            os.makedirs(RUN, exist_ok=True)
            tf.collect_news(up, teams_kb, fr, RUN)
        except Exception as e:
            fr["errors"].append(f"fetch: {e}")
        for i, p in patches.items():
            m = docs.get(i)
            if not m:
                continue
            if "odds" in p:
                m["odds"] = p["odds"]
            if "h2h" in p and not m.get("h2h"):
                m["h2h"] = p["h2h"]
            for side in ("home", "away"):
                if side in p and "absences" in p[side]:
                    m[side]["absences"] = p[side]["absences"]
            m["sources"] = sorted(set(m.get("sources") or []) | set(p.get("sources") or []))
        for name, tp in team_patches.items():
            s = tf.slug(name)
            cur = teams_kb.get(s, {"name": name, "league": tp.get("league"), "news": []})
            oc = (cur.get("coach") or {})
            nc = tp.get("coach") or {}
            if nc.get("name") and nc["name"] != oc.get("name"):
                cur["coach"] = {**nc, "status": "nowy" if oc.get("name") else "ok", "updatedAt": iso(now())}
            teams_kb[s] = cur
        report["fetch"] = {k: fr[k] for k in ("odds_api", "api_football", "news", "errors", "unmatched")}
        report["keys"] = {"odds": bool(ok), "apiFootball": bool(fk)}

    # 5) players
    if not a.no_players and os.path.exists(os.path.join(H, "players_parse.py")):
        pj = rjson("players.json", {})
        if not pj or (now() - parse(pj.get("updatedAt", "2000-01-01T00:00:00Z"))).total_seconds() > 20 * 3600:
            rc = os.system(f"{sys.executable} -I {os.path.join(H, 'players_parse.py')} --out {os.path.join(D, 'players.json')} >/dev/null 2>&1")
            if rc:
                report["errors"].append(f"players_parse exit {rc}")
        pj = rjson("players.json", {})
        for lg in (pj.get("leagues") or {}).values():
            for board in ("goals", "assists", "yellow", "red"):
                for r in lg.get(board) or []:
                    if r.get("team"):
                        r["team"] = disp(tf.to_display(r["team"], None) or r["team"])
        if pj:
            wjson("players.json", pj)

    # 6) retention: finished matches older than 90 days -> compact history (kept 365 days)
    cut = now() - dt.timedelta(days=90)
    for i in [i for i, m in docs.items() if m.get("status") == "finished" and parse(m["kickoff"]) < cut]:
        m = docs.pop(i)
        hist["matches"].append({"id": i, "league": m["league"], "kickoff": m["kickoff"], "home": m["home"]["name"],
                                "away": m["away"]["name"], "result": m.get("result")})
        report["compacted"] += 1
    hcut = now() - dt.timedelta(days=365)
    hist["matches"] = [h for h in hist["matches"] if parse(h["kickoff"]) >= hcut]
    for s, t in teams_kb.items():  # club news older than 90 days are dropped
        t["news"] = [n for n in (t.get("news") or []) if not n.get("date") or parse(n["date"]) >= cut]
    # stale scheduled matches without result after 7 days (postponed/abandoned) are dropped
    for i in [i for i, m in docs.items() if m.get("status") != "finished" and parse(m["kickoff"]) < now() - dt.timedelta(days=7)]:
        docs.pop(i)

    # 7) write data + publish folder
    stamp = iso(now())
    wjson("matches.json", {"updatedAt": stamp, "matches": sorted(docs.values(), key=lambda m: m["kickoff"])})
    wjson("history.json", hist)
    wjson("teams.json", {"updatedAt": stamp, "teams": teams_kb})
    wjson("tables.json", {"updatedAt": stamp, "tables": {c: r for c, r in tables.items() if r}})
    keys = report.get("keys") or {"odds": bool(tf and tf.read_key("odds-api.key")), "apiFootball": bool(tf and tf.read_key("api-football.key"))}
    wjson("meta.json", {"updatedAt": stamp, "keys": keys, "news": [s for s, _ in (tf.FEEDS if tf else [])],
                        "counts": {"matches": len(docs), "upcoming": len(up)}})
    # crests for teams that are new to the app (FotMob logos / national flags)
    if os.path.exists(os.path.join(H, "crests_more.py")):
        os.system(f"{sys.executable} -I {os.path.join(H, 'crests_more.py')} >/dev/null 2>&1")
    shutil.rmtree(os.path.join(PUB, "crests"), ignore_errors=True)
    os.makedirs(os.path.join(PUB, "crests"), exist_ok=True)
    added = []
    try:
        added = json.load(open(os.path.join(RUN, "crests_added.json"), encoding="utf-8"))
    except (OSError, ValueError):
        pass
    for name in added:
        src = os.path.join(H, "app", "crests", tf.slug(name) + ".png")
        if os.path.exists(src):
            shutil.copy(src, os.path.join(PUB, "crests"))
    report["new_crests"] = len(added)
    os.makedirs(os.path.join(PUB, "data"), exist_ok=True)
    for f in ("matches.json", "teams.json", "tables.json", "meta.json", "players.json", "crests.json"):
        if os.path.exists(os.path.join(D, f)):
            shutil.copy(os.path.join(D, f), os.path.join(PUB, "data", f))
    with open(os.path.join(RUN, "build_report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in report.items() if k != "fetch"}, ensure_ascii=False, indent=1))
    if "fetch" in report:
        print("fetch:", json.dumps({k: report["fetch"][k] for k in ("odds_api", "api_football", "news")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
