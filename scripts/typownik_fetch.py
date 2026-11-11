#!/usr/bin/env python3
"""Typownik data fetcher.

Pulls market odds (The Odds API) and injuries / head-to-head (API-Football) for the
matches that are already in the Typownik database, and writes merge patches that the
daily Claude task applies with the ArtifactData tool ("update" per document).

Usage:
  python3 ~/.typownik/typownik_fetch.py --docs DIR --out DIR [--af-budget 90]
  python3 ~/.typownik/typownik_fetch.py --docs DIR --selftest

--docs is the ArtifactData "list" out_dir (it contains matches/<doc_id>.json) or the
matches folder itself. API keys are read from ~/.typownik/odds-api.key and
~/.typownik/api-football.key and are never printed or written anywhere else.
"""
import argparse
import datetime as dt
import email.utils
import html
import json
import os
import re
import statistics
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

HOME = os.path.expanduser("~/.typownik")
CACHE = os.path.join(HOME, "cache")
UA = "Typownik/1.0 (personal football model)"
ODDS_BASE = "https://api.the-odds-api.com/v4"
AF_BASE = "https://v3.football.api-sports.io"
ODDS_SPORT = {"pl": "soccer_epl", "laliga": "soccer_spain_la_liga", "seriea": "soccer_italy_serie_a",
              "bundesliga": "soccer_germany_bundesliga", "ligue1": "soccer_france_ligue_one",
              "championship": "soccer_efl_champ", "ucl": "soccer_uefa_champs_league", "uel": "soccer_uefa_europa_league",
              "uecl": "soccer_uefa_europa_conference_league", "nl": "soccer_uefa_nations_league",
              "wc": "soccer_fifa_world_cup", "euro": "soccer_uefa_european_championship",
              "friendly": "soccer_international_friendlies", "ekstraklasa": "soccer_poland_ekstraklasa"}
AF_LEAGUE = {"pl": 39, "laliga": 140, "seriea": 135, "bundesliga": 78, "ligue1": 61, "championship": 40,
             "ucl": 2, "uel": 3, "uecl": 848, "nl": 5, "wc": 1, "euro": 4, "friendly": 10, "ekstraklasa": 106, "pl1": 107}
AF_LEAGUE_REV = {v: k for k, v in AF_LEAGUE.items()}

# Our display names (as stored in the database) -> other spellings used by data providers.
ALIASES = {
    # Premier League
    "Arsenal": ["arsenal fc"], "Brighton": ["brighton and hove albion", "brighton hove albion", "brighton & hove albion"],
    "Manchester City": ["man city"], "Liverpool": [], "Chelsea": [], "Everton": [], "Bournemouth": ["afc bournemouth"],
    "Brentford": [], "Leeds": ["leeds united"], "Aston Villa": [], "Tottenham": ["tottenham hotspur", "spurs"],
    "Newcastle": ["newcastle united"], "Hull City": ["hull"], "Ipswich": ["ipswich town"], "Fulham": [],
    "Nottingham Forest": ["nottm forest", "nottingham"], "Coventry": ["coventry city"],
    "Manchester United": ["man united", "man utd", "manchester utd"], "Sunderland": [], "Crystal Palace": [],
    # LaLiga
    "Barcelona": ["fc barcelona"], "Real Madryt": ["real madrid"],
    "Atlético Madryt": ["atletico madrid", "atletico de madrid", "atl madrid", "club atletico de madrid"],
    "Real Betis": ["betis", "real betis balompie"], "Real Sociedad": [], "Sevilla": ["sevilla fc"],
    "Athletic Club": ["athletic bilbao", "ath bilbao"], "Villarreal": [],
    "Deportivo La Coruña": ["deportivo la coruna", "deportivo", "dep la coruna", "deportivo coruna", "rc deportivo"],
    "Racing Santander": ["racing", "real racing club", "racing club de santander"], "Celta Vigo": ["celta", "rc celta"],
    "Alavés": ["alaves", "deportivo alaves"], "Espanyol": ["rcd espanyol"], "Rayo Vallecano": ["rayo"],
    "Getafe": [], "Osasuna": ["ca osasuna"], "Levante": [], "Elche": [], "Valencia": [], "Málaga": ["malaga"],
    # Serie A
    "Lazio": ["ss lazio"], "Inter": ["inter milan", "internazionale", "fc internazionale milano"], "Roma": ["as roma"],
    "Milan": ["ac milan"], "Como": ["como 1907"], "Frosinone": [], "Juventus": [], "Bologna": [], "Cagliari": [],
    "Napoli": ["ssc napoli"], "Torino": [], "Atalanta": ["atalanta bc"], "Genoa": [], "Lecce": ["us lecce"],
    "Fiorentina": ["acf fiorentina"], "Parma": [], "Sassuolo": [], "Monza": ["ac monza"], "Venezia": [], "Udinese": [],
    # Bundesliga
    "Bayern Monachium": ["bayern munich", "bayern munchen", "fc bayern munchen", "bayern"],
    "Borussia Dortmund": ["dortmund"], "Freiburg": ["sc freiburg"], "RB Leipzig": ["leipzig", "rasenballsport leipzig"],
    "Bayer Leverkusen": ["leverkusen", "bayer 04 leverkusen"], "Werder Bremen": ["bremen", "sv werder bremen"],
    "Elversberg": ["sv elversberg", "sv 07 elversberg"], "Stuttgart": ["vfb stuttgart"],
    "Hoffenheim": ["tsg hoffenheim", "1899 hoffenheim", "tsg 1899 hoffenheim"], "Eintracht Frankfurt": ["frankfurt"],
    "Hamburger SV": ["hamburg", "hamburg sv"], "Mainz": ["mainz 05", "fsv mainz 05", "1 fsv mainz 05"],
    "Paderborn": ["sc paderborn", "sc paderborn 07"], "Augsburg": ["fc augsburg"],
    "1. FC Köln": ["fc koln", "koln", "cologne", "1 fc cologne", "fc cologne"],
    "Union Berlin": ["1 fc union berlin", "fc union berlin"], "Schalke": ["schalke 04", "fc schalke 04"],
    "Borussia M'gladbach": ["borussia monchengladbach", "monchengladbach", "m gladbach", "gladbach", "b monchengladbach"],
    # Ligue 1
    "PSG": ["paris saint germain", "paris sg", "paris saint-germain"], "Lille": ["losc", "losc lille"], "Paris FC": [],
    "Marseille": ["olympique marseille", "olympique de marseille"], "Lyon": ["olympique lyonnais", "olympique lyon"],
    "Nice": ["ogc nice"], "Monaco": ["as monaco"], "Rennes": ["stade rennais"], "Brest": ["stade brestois", "stade brestois 29"],
    "Lorient": [], "Lens": ["rc lens"], "Toulouse": [], "Strasbourg": ["rc strasbourg", "rc strasbourg alsace"],
    "Auxerre": ["aj auxerre"], "Le Mans": [], "Angers": ["angers sco", "sco angers"], "Troyes": ["estac troyes"],
    "Le Havre": ["havre ac", "le havre ac"],
}
STOP = {"fc", "cf", "afc", "ac", "as", "sc", "sv", "ss", "ssc", "us", "acf", "cfc", "rc", "rcd", "cd", "ud", "ca", "ogc",
        "tsg", "vfb", "vfl", "fsv", "losc", "aj", "sco", "estac", "stade", "club", "calcio", "de", "bc",
        "1", "04", "05", "07", "29", "1899", "1900", "1904", "1907", "1909", "1910", "1913"}
INJ_PL = {"injury": "kontuzja", "knee injury": "uraz kolana", "hamstring injury": "uraz mięśnia dwugłowego",
          "muscle injury": "uraz mięśnia", "ankle injury": "uraz kostki", "calf injury": "uraz łydki",
          "groin injury": "uraz pachwiny", "thigh injury": "uraz uda", "back injury": "uraz pleców",
          "foot injury": "uraz stopy", "shoulder injury": "uraz barku", "hip injury": "uraz biodra",
          "achilles tendon injury": "uraz ścięgna Achillesa", "knock": "stłuczenie", "illness": "choroba",
          "suspended": "zawieszenie", "red card": "zawieszenie (czerwona kartka)", "yellow cards": "zawieszenie za kartki",
          "rest": "odpoczynek", "coach's decision": "decyzja trenera", "lack of fitness": "brak formy fizycznej",
          "international duty": "zgrupowanie reprezentacji", "personal reasons": "sprawy osobiste",
          "concussion": "wstrząśnienie mózgu", "virus": "infekcja", "broken leg": "złamanie nogi",
          "broken ankle": "złamanie kostki", "cruciate ligament injury": "zerwane więzadło krzyżowe",
          "adductor injury": "uraz przywodziciela", "meniscus injury": "uraz łąkotki"}
POS = {"Goalkeeper": "gk", "Defender": "def", "Midfielder": "mid", "Attacker": "atk"}

# Polish (and English) sports news feeds that answered with valid RSS on 2026-11-09.
FEEDS = [("Przegląd Sportowy", "https://przegladsportowy.onet.pl/.feed"),
         ("Onet Sport", "https://sport.onet.pl/.feed"),
         ("WP SportoweFakty", "https://sportowefakty.wp.pl/pilka-nozna/rss.xml"),
         ("Weszło", "https://weszlo.com/feed/"),
         ("Interia Sport", "https://sport.interia.pl/pilka-nozna/feed"),
         ("TVP Sport", "https://sport.tvp.pl/rss"),
         ("Polsat Sport", "https://www.polsatsport.pl/rss/pilkanozna.xml"),
         ("pilkanozna.pl", "https://pilkanozna.pl/feed"),
         ]  # BBC (English) removed: the app shows Polish sources only
# Names Polish media use for clubs (matched with Polish case endings, e.g. "Bayernu", "Realem").
NEWS_ALIASES = {
    "Real Madryt": ["real madryt", "realu madryt", "realem madryt"], "Barcelona": ["fc barcelona", "barcelona", "barca"],
    "Atlético Madryt": ["atletico madryt", "atletico"], "Real Betis": ["betis"], "Sevilla": ["sevilla"],
    "Athletic Club": ["athletic bilbao", "athletic club"], "Valencia": ["valencia"], "Villarreal": ["villarreal"],
    "Real Sociedad": ["real sociedad"], "Deportivo La Coruña": ["deportivo la coruna"], "Celta Vigo": ["celta vigo", "celta"],
    "Inter": ["inter mediolan", "inter"], "Milan": ["ac milan"], "Juventus": ["juventus"], "Napoli": ["napoli", "ssc napoli"],
    "Roma": ["as roma"], "Lazio": ["lazio"], "Atalanta": ["atalanta"], "Fiorentina": ["fiorentina"], "Bologna": ["bologna"],
    "Torino": ["torino"], "Como": ["como 1907"], "Genoa": ["genoa"], "Udinese": ["udinese"], "Cagliari": ["cagliari"],
    "Bayern Monachium": ["bayern monachium", "bayern"], "Borussia Dortmund": ["borussia dortmund", "bvb"],
    "Bayer Leverkusen": ["bayer leverkusen", "leverkusen"], "RB Leipzig": ["rb lipsk", "rb leipzig", "lipsk"],
    "Eintracht Frankfurt": ["eintracht frankfurt", "eintracht"], "Werder Bremen": ["werder brema", "werder bremen", "werder"],
    "Hamburger SV": ["hamburger sv", "hsv"], "1. FC Köln": ["fc koln", "1 fc koln", "kolonia"], "Union Berlin": ["union berlin"],
    "Schalke": ["schalke"], "Borussia M'gladbach": ["borussia monchengladbach", "monchengladbach", "gladbach"],
    "Stuttgart": ["vfb stuttgart"], "Hoffenheim": ["hoffenheim"], "Freiburg": ["sc freiburg"], "Mainz": ["mainz"],
    "PSG": ["psg", "paris saint germain"], "Marseille": ["olympique marsylia", "marsylia", "marseille"],
    "Lyon": ["olympique lyon", "lyon"], "Lille": ["lille"], "Monaco": ["as monaco"], "Nice": ["ogc nicea", "nicea"],
    "Lens": ["rc lens"], "Rennes": ["stade rennes", "rennes"], "Brest": ["stade brest"], "Strasbourg": ["strasburg", "rc strasbourg"],
    "Toulouse": ["tuluza", "toulouse"], "Arsenal": ["arsenal"], "Chelsea": ["chelsea"], "Liverpool": ["liverpool"],
    "Manchester United": ["manchester united", "man utd"], "Manchester City": ["manchester city", "man city"],
    "Tottenham": ["tottenham"], "Newcastle": ["newcastle"], "Aston Villa": ["aston villa"], "Everton": ["everton"],
    "Brighton": ["brighton"], "Brentford": ["brentford"], "Fulham": ["fulham"], "Bournemouth": ["bournemouth"],
    "Nottingham Forest": ["nottingham forest"], "Crystal Palace": ["crystal palace"], "Leeds": ["leeds"],
    "Sunderland": ["sunderland"], "Ipswich": ["ipswich"], "Coventry": ["coventry"], "Hull City": ["hull city"],
}
NEWS_KEYWORDS = ["trener", "szkoleniow", "menedzer", "zwolni", "dymisj", "posad", "zawiesz", "dyskwalifik", "kontuzj", "uraz",
                 "nie zagra", "pauz", "absencj", "kartk", "czerwon", "chor", "nieobecn", "afer", "korupc", "zarzut", "prokurat",
                 "sledztw", "kryzys", "konflikt", "bunt", "szatni", "zaleglosc", "wyplat", "protest", "kara ", "zakaz", "sztab",
                 "manager", "coach", "sacked", "injur", "ruled out", "suspen", "ban ", "illness", "doubt", "crisis", "charged",
                 "investigation", "unrest",
                 # football gossip / rumours (shown in the app as "plotka", counted with half weight)
                 "plotk", "media:", "podobno", "wedlug", "spekul", "transfer", "interesuje", "chce kupic", "oferta",
                 "negocjac", "gossip", "rumour", "linked", "target", "bid ", "set to", "expected to", "could leave",
                 "talks", "interest in", "reportedly", "forma", "seria", "kryzys", "wraca", "powrot"]


def slug(display):
    return norm(display).replace(" ", "-")


def _stem_rx(alias):
    toks = norm(alias).split()
    parts = []
    for t in toks:
        st = t[:-1] if len(t) >= 5 and t[-1] in "aeiouy" else t
        parts.append(re.escape(st) + r"(?:a|o|u|y|e|i|em|om|ie|owi|ach|ami|ow)?")  # Polish case endings only
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b")


NEWS_RX = {}


def news_rx():
    if not NEWS_RX:
        for d in ALIASES:
            NEWS_RX[d] = [_stem_rx(a) for a in (NEWS_ALIASES.get(d) or [d])]
    return NEWS_RX


def _tag(block, name):
    m = re.search(r"<" + name + r"(?:\s[^>]*)?>(.*?)</" + name + r">", block, re.S | re.I)
    if not m:
        return ""
    v = m.group(1).strip()
    v = re.sub(r"^<!\[CDATA\[(.*)\]\]>$", r"\1", v, flags=re.S)
    v = re.sub(r"<[^>]+>", " ", v)
    return re.sub(r"\s+", " ", html.unescape(v)).strip()


def collect_news(docs, teams_kb, report, out_dir, hours=72):
    """Prefilter recent RSS headlines that mention a team (or its coach) from upcoming matches."""
    wanted = {}
    for m in docs.values():
        for side in ("home", "away"):
            wanted[m[side]["name"]] = m["league"]
    coach_rx = {}
    for name, kb in (teams_kb or {}).items():
        cn = ((kb or {}).get("coach") or {}).get("name")
        if cn and name in wanted and len(norm(cn).split()[-1]) >= 4:
            coach_rx[name] = re.compile(r"\b" + re.escape(norm(cn).split()[-1]) + r"[a-z]{0,4}\b")
    since = now_utc() - dt.timedelta(hours=hours)
    items, seen = [], set()
    for src, url in FEEDS:
        st, hd, body = http_get_text(url)
        if st != 200 or not body:
            report["errors"].append(f"RSS {src}: HTTP {st}")
            continue
        n = 0
        for block in re.findall(r"<item[\s>].*?</item>", body, re.S | re.I):
            title, desc, link = _tag(block, "title"), _tag(block, "description"), _tag(block, "link")
            try:
                pub = email.utils.parsedate_to_datetime(_tag(block, "pubDate"))
                pub = pub if pub.tzinfo else pub.replace(tzinfo=dt.timezone.utc)
            except (TypeError, ValueError):
                pub = None
            if pub and pub < since:
                continue
            text = norm(title + " " + desc)
            teams = [d for d in wanted if any(rx.search(text) for rx in (news_rx().get(d) or [_stem_rx(d)]))
                     or (d in coach_rx and coach_rx[d].search(text))]
            teams = [d for d in teams if not (d == "Inter" and "inter miami" in text)]
            if not teams or not any(k in text + " " for k in NEWS_KEYWORDS):
                continue
            key = norm(title)[:80]
            if key in seen:
                continue
            seen.add(key)
            n += 1
            items.append({"teams": teams, "leagues": sorted({wanted[t] for t in teams}), "source": src, "title": title,
                          "summary": desc[:280], "url": link, "published": iso(pub) if pub else None,
                          "lang": "en" if src == "BBC Sport" else "pl"})
        report["news"][src] = n
    try:
        items += team_news(docs, report)
    except Exception as e:  # never let the extra source break the build
        report["errors"].append(f"Google News: {e}")
    items.sort(key=lambda x: x["published"] or "", reverse=True)
    with open(os.path.join(out_dir, "news_candidates.json"), "w", encoding="utf-8") as fh:
        json.dump(items[:2500], fh, ensure_ascii=False, indent=1)
    report["news"]["candidates"] = min(len(items), 2500)


# ---------------------------------------------------------------- Google News per team
GN_NOISE = re.compile(r"(gdzie ogl[aą]da|transmisj|na żywo|live stream|wyniki na żywo|sk[lł]ady|line-?ups?|team stats|"
                      r"statystyki|head-to-head|h2h|highlights|skr[oó]t meczu|bramki|u-?1[0-9]|u-?2[0-3]|kobiet|women|"
                      r"official (club )?website|tickets|bilety|fantasy|kurs[y]? bukmacher|typy|zak[lł]ad|betting|odds|"
                      r"predictions?|przewidywan|prognoz|player ratings|oceny pi[lł]karzy|wallpaper|quiz|^watch |volleyball|siatk|"
                      r"koszyk|basket|tenis|tennis|handball|piłk\w* ręczn|futsal|esport|award hono|kobiec|mistrzy[nń]|"
                      r"live commentary|relacja live|minuta po minucie|film|serial|reżyser|\bwcl\b|\bwsl\b|lionesses|youtuber)", re.I)
GN_FIXTURE = re.compile(r"^[^:|!?]{2,40} (-|–|vs\.?|v) [^|!?]{2,40}?( \d+[:-]\d+)?$")  # bare "Team - Team" match pages
NATION_LEAGUES = ("nl", "friendly", "wc", "euro")


def _gn_fetch(q, hl, gl, ceid, timeout=20):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": q, "hl": hl, "gl": gl, "ceid": ceid})
    st, _, body = http_get_text(url, timeout=timeout)
    if st != 200 or not body:
        return None
    out = []
    for block in re.findall(r"<item>(.*?)</item>", body, re.S):
        title = _tag(block, "title")
        src = _tag(block, "source")
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        try:
            pub = email.utils.parsedate_to_datetime(_tag(block, "pubDate"))
            pub = pub if pub.tzinfo else pub.replace(tzinfo=dt.timezone.utc)
        except (TypeError, ValueError):
            pub = None
        out.append({"title": title.strip(), "source": src or "Google News", "url": _tag(block, "link"), "published": pub})
    return out


def team_news(docs, report, days_ahead=14, per_team=8, pause=0.6):
    """Recent articles about each team of the coming matches (Google News RSS, Polish + English editions)."""
    import time
    soon = now_utc() + dt.timedelta(days=days_ahead)
    order, league_of = [], {}
    for m in sorted(docs.values(), key=lambda m: -(m.get("popularity") or 0)):
        if parse_iso(m["kickoff"]) > soon:
            continue
        for side in ("home", "away"):
            n = m[side]["name"]
            if n not in league_of:
                league_of[n] = m["league"]
                order.append(n)
    en_of = {}
    try:
        with open(os.path.join(HOME, "data", "elo.json"), encoding="utf-8") as f:
            for en, v in (json.load(f).get("teams") or {}).items():
                if v.get("pl"):
                    en_of[v["pl"]] = en
    except (OSError, ValueError):
        pass
    since = now_utc() - dt.timedelta(days=7)
    items, fails, done = [], 0, 0
    for team in order:
        nation = league_of[team] in NATION_LEAGUES
        # Polish sources only: machine-translated English headlines read badly
        if nation:
            queries = [(f'"{team}" (reprezentacja OR kadra OR selekcjoner OR kontuzja OR powołania) when:5d', "pl", "PL", "PL:pl")]
        else:
            pl_q = (NEWS_ALIASES.get(team) or [team])[0]
            queries = [(f'"{pl_q}" (trener OR kontuzja OR uraz OR zawieszenie OR kartka OR kadra OR szatnia OR zwolniony) when:5d',
                        "pl", "PL", "PL:pl")]
        rxs = news_rx().get(team) or [_stem_rx(team)]
        if nation and team in en_of:
            rxs = rxs + [_stem_rx(en_of[team])]
        for q, hl, gl, ceid in queries:
            res = _gn_fetch(q, hl, gl, ceid)
            time.sleep(pause)
            if res is None:
                fails += 1
                if fails >= 6:
                    report["errors"].append("Google News: za dużo błędów, przerywam")
                    break
                continue
            fails, kept = 0, 0
            for it in res:
                if kept >= per_team or not it["title"]:
                    continue
                if it["published"] and it["published"] < since:
                    continue
                t = norm(it["title"])
                if GN_NOISE.search(it["title"]) or (GN_FIXTURE.match(it["title"]) and len(it["title"].split()) <= 7) \
                        or not any(rx.search(t) for rx in rxs):
                    continue
                items.append({"teams": [team], "leagues": [league_of[team]], "source": it["source"], "title": it["title"],
                              "summary": "", "url": it["url"], "published": iso(it["published"]) if it["published"] else None,
                              "lang": "pl" if hl == "pl" else "en"})
                kept += 1
        if fails >= 6:
            break
        done += 1
    report.setdefault("news", {})["google_news_teams"] = done
    report["news"]["google_news_items"] = len(items)
    return items


def http_get_text(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Typownik RSS reader)"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(3_000_000)
            enc = r.headers.get_content_charset() or "utf-8"
            return r.status, {}, raw.decode(enc, "replace")
    except urllib.error.HTTPError as e:
        return e.code, {}, ""
    except Exception as e:
        return 0, {"error": str(e)}, ""


def norm(s):
    s = unicodedata.normalize("NFKD", str(s))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower().replace("ß", "ss").replace("ø", "o")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", s)).strip()


def core(s):
    return " ".join(t for t in norm(s).split() if t not in STOP)


INDEX = {}
for _disp, _al in ALIASES.items():
    for _a in [_disp] + _al:
        INDEX.setdefault(norm(_a), _disp)
        INDEX.setdefault(core(_a), _disp)


def to_display(name, candidates=None):
    """Map a provider's team name to one of our display names (restricted to candidates)."""
    for key in (norm(name), core(name)):
        d = INDEX.get(key)
        if d and (not candidates or d in candidates):
            return d
    toks = set(core(name).split()) or set(norm(name).split())
    best, score = None, 0.0
    for d in (candidates or ALIASES.keys()):
        for a in [d] + ALIASES.get(d, []):
            at = set(core(a).split()) or set(norm(a).split())
            if at and toks:
                j = len(toks & at) / len(toks | at)
                if j > score:
                    best, score = d, j
    return best if score >= 0.5 else None


_EN2PL = None


def nation_pl(name):
    """English country name (odds/API providers) -> Polish display name used in the app (from elo.json)."""
    global _EN2PL
    if _EN2PL is None:
        _EN2PL = {}
        try:
            with open(os.path.join(HOME, "data", "elo.json"), encoding="utf-8") as f:
                for en, v in (json.load(f).get("teams") or {}).items():
                    if v.get("pl"):
                        _EN2PL[en] = v["pl"]
            _EN2PL.update({"Czech Republic": "Czechy", "Republic of Ireland": "Irlandia", "Korea Republic": "Korea Południowa",
                           "USA": "USA", "Turkiye": "Turcja", "Bosnia & Herzegovina": "Bośnia i Hercegowina"})
        except (OSError, ValueError):
            pass
    return _EN2PL.get(name, name)


def parse_iso(s):
    s = re.sub(r"\.\d+", "", str(s).strip().replace("Z", "+00:00"))
    t = dt.datetime.fromisoformat(s)
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_key(name):
    p = os.path.join(HOME, name)
    try:
        with open(p, encoding="utf-8") as f:
            k = f.read().strip()
        return k or None
    except OSError:
        return None


def http_get(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            body = json.loads(body)
        except ValueError:
            body = body[:300]
        return e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, body
    except Exception as e:  # network errors, timeouts
        return 0, {}, {"error": str(e)}


def load_docs(path):
    d = os.path.join(path, "matches") if os.path.isdir(os.path.join(path, "matches")) else path
    docs = {}
    for f in sorted(os.listdir(d)):
        if f.endswith(".json"):
            with open(os.path.join(d, f), encoding="utf-8") as fh:
                j = json.load(fh)
            j = j.get("data", j) if isinstance(j, dict) and "data" in j and "home" not in j else j
            docs[f[:-5]] = j
    return docs


def upcoming(docs, days=14):
    lo, hi = now_utc() - dt.timedelta(hours=3), now_utc() + dt.timedelta(days=days)
    out = {}
    for i, m in docs.items():
        try:
            k = parse_iso(m["kickoff"])
        except (KeyError, ValueError):
            continue
        if m.get("status") != "finished" and lo <= k <= hi and m.get("league") in AF_LEAGUE:
            out[i] = m
    return out


def find_doc(docs, league, home, away, kickoff):
    best, gap = None, None
    for i, m in docs.items():
        if m.get("league") != league or m["home"]["name"] != home or m["away"]["name"] != away:
            continue
        g = abs((parse_iso(m["kickoff"]) - kickoff).total_seconds())
        if g <= 36 * 3600 and (gap is None or g < gap):
            best, gap = i, g
    return best


def league_teams(docs, league):
    s = set()
    for m in docs.values():
        if m.get("league") == league:
            s.add(m["home"]["name"])
            s.add(m["away"]["name"])
    return s


# ---------------------------------------------------------------- The Odds API
def fetch_odds(docs, key, report, patches):
    for league in sorted({m["league"] for m in docs.values() if m["league"] in ODDS_SPORT}):
        params = {"apiKey": key, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal", "dateFormat": "iso"}
        st, hd, js = http_get(f"{ODDS_BASE}/sports/{ODDS_SPORT[league]}/odds/?" + urllib.parse.urlencode(params))
        report["odds_api"]["remaining"] = hd.get("x-requests-remaining", report["odds_api"].get("remaining"))
        if st != 200 or not isinstance(js, list):
            report["errors"].append(f"The Odds API {league}: HTTP {st} {js if not isinstance(js, list) else ''}")
            continue
        cands = league_teams(docs, league)
        for ev in js:
            hn, an = ev.get("home_team", ""), ev.get("away_team", "")
            if league in ("nl", "friendly", "wc", "euro"):
                hn, an = nation_pl(hn), nation_pl(an)
            h, a = to_display(hn, cands), to_display(an, cands)
            doc = find_doc(docs, league, h, a, parse_iso(ev["commence_time"])) if h and a else None
            if not doc:
                report["unmatched"].append(f"kursy {league}: {ev.get('home_team')} – {ev.get('away_team')} ({ev.get('commence_time')})")
                continue
            probs, betclic, pinnacle = [], None, None
            for bk in ev.get("bookmakers", []):
                for mk in bk.get("markets", []):
                    if mk.get("key") != "h2h":
                        continue
                    o = {x.get("name"): x.get("price") for x in mk.get("outcomes", [])}
                    oh, od, oa = o.get(ev["home_team"]), o.get("Draw"), o.get(ev["away_team"])
                    if not (oh and od and oa):
                        continue
                    s = 1 / oh + 1 / od + 1 / oa
                    probs.append((1 / oh / s, 1 / od / s, 1 / oa / s, s - 1))
                    if "betclic" in bk.get("key", ""):
                        betclic = {"h": oh, "d": od, "a": oa}
                    if bk.get("key") == "pinnacle":
                        pinnacle = {"h": oh, "d": od, "a": oa}
            if not probs:
                continue
            ph, pd_, pa = (statistics.median(p[i] for p in probs) for i in range(3))
            tot = ph + pd_ + pa
            odds = {"h": round(tot / ph, 3), "d": round(tot / pd_, 3), "a": round(tot / pa, 3), "n": len(probs),
                    "margin": round(statistics.median(p[3] for p in probs), 4), "src": "the-odds-api.com", "at": iso(now_utc())}
            if betclic:
                odds["betclic"] = betclic
            if pinnacle:
                odds["pinnacle"] = pinnacle
            patches.setdefault(doc, {})["odds"] = odds
            report["odds_api"]["matched"] += 1


# ---------------------------------------------------------------- API-Football
class AF:
    def __init__(self, key, budget, report):
        self.key, self.left, self.report, self.used = key, budget, report, 0

    def get(self, path, params, count=True):
        if count and self.left <= 0:
            return None, "skończył się dzienny budżet zapytań"
        if count:
            self.left -= 1
            self.used += 1
            # free plan: max 10 requests per minute
            import time
            wait = getattr(self, "_last", 0) + 6.5 - time.time()
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
        import time
        for attempt in range(3):
            st, hd, js = http_get(AF_BASE + path + "?" + urllib.parse.urlencode(params), {"x-apisports-key": self.key})
            errs = js.get("errors") if isinstance(js, dict) else None
            limited = st == 429 or (isinstance(errs, dict) and "rateLimit" in errs)
            if not limited:
                break
            time.sleep(62)  # per-minute limit hit: wait for the window to reset, then retry
        self._last = time.time()
        if st != 200 or errs:
            return None, f"HTTP {st}: {errs or js}"
        return js.get("response"), None


def season_of(t):
    return t.year if t.month >= 7 else t.year - 1


def cache_get(name, max_age_days):
    p = os.path.join(CACHE, name)
    try:
        if (now_utc().timestamp() - os.path.getmtime(p)) < max_age_days * 86400:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    return None


def cache_put(name, data):
    os.makedirs(CACHE, exist_ok=True)
    with open(os.path.join(CACHE, name), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def fetch_af(docs, key, budget, report, patches, team_patches=None, h2h_cap=25):
    team_patches = {} if team_patches is None else team_patches
    af = AF(key, budget, report)
    rep = report["api_football"]
    status, err = af.get("/status", {}, count=False)
    if err:
        report["errors"].append("API-Football /status: " + err)
        return
    if isinstance(status, dict):
        sub, req = status.get("subscription") or {}, status.get("requests") or {}
        rep["plan"] = sub.get("plan")
        if req.get("limit_day") is not None:
            af.left = min(af.left, max(0, int(req["limit_day"]) - int(req.get("current") or 0) - 3))
    # 1) fixtures -> fixture ids and team ids for our documents
    fx = {}  # doc_id -> (fixture_id, home_team_id, away_team_id)
    kos = [parse_iso(m["kickoff"]) for m in docs.values()]
    d1, d2 = min(kos).strftime("%Y-%m-%d"), max(kos).strftime("%Y-%m-%d")
    blocked = False
    raw = []
    for league in sorted({m["league"] for m in docs.values()}):
        r, err = af.get("/fixtures", {"league": AF_LEAGUE[league], "season": season_of(min(kos)), "from": d1, "to": d2, "timezone": "UTC"})
        if err:
            report["errors"].append(f"API-Football fixtures {league}: {err}")
            if any(w in err.lower() for w in ("plan", "season", "access")):
                blocked = True
                break
            continue
        raw += r or []
    if blocked:  # free plans: fall back to date queries for today / tomorrow
        rep["note"] = "Plan nie obejmuje całego sezonu – używam zapytań po dacie (dziś/jutro)."
        for day in sorted({k.strftime("%Y-%m-%d") for k in kos if k.date() <= (now_utc() + dt.timedelta(days=1)).date()}):
            r, err = af.get("/fixtures", {"date": day, "timezone": "UTC"})
            if err:
                report["errors"].append(f"API-Football fixtures {day}: {err}")
                continue
            raw += [x for x in (r or []) if (x.get("league") or {}).get("id") in AF_LEAGUE_REV]
    for x in raw:
        league = AF_LEAGUE_REV.get((x.get("league") or {}).get("id"))
        if not league:
            continue
        cands = league_teams(docs, league)
        th, ta = x["teams"]["home"], x["teams"]["away"]
        hn, an = th["name"], ta["name"]
        if league in ("nl", "friendly", "wc", "euro"):
            hn, an = nation_pl(hn), nation_pl(an)
        h, a = to_display(hn, cands), to_display(an, cands)
        doc = find_doc(docs, league, h, a, parse_iso(x["fixture"]["date"])) if h and a else None
        if doc:
            fx[doc] = (x["fixture"]["id"], th["id"], ta["id"])
        else:
            report["unmatched"].append(f"API-Football {league}: {th['name']} – {ta['name']} ({x['fixture']['date']})")
    rep["fixtures_matched"] = len(fx)
    # team ids for matches outside the plan's date window: /teams?search (no season needed), cached for good
    ids_cache = cache_get("team_ids.json", 3650) or {}
    SEARCH = {"Bayern Monachium": "Bayern", "Real Madryt": "Real Madrid", "Atlético Madryt": "Atletico Madrid",
              "1. FC Köln": "Koln", "Borussia M'gladbach": "Monchengladbach", "Milan": "AC Milan", "PSG": "Paris Saint Germain",
              "Deportivo La Coruña": "Deportivo La Coruna", "Alavés": "Alaves", "Málaga": "Malaga", "Hamburger SV": "Hamburger",
              "Atalanta": "Atalanta", "Inter": "Inter"}
    searched = 0
    tid_of = {}
    for doc in sorted(docs, key=lambda d: -(docs[d].get("popularity") or 0)):
        m = docs[doc]
        if m["league"] in ("nl", "friendly", "wc", "euro"):
            continue
        for side in ("home", "away"):
            nm = m[side]["name"]
            if nm in ids_cache:
                tid_of[nm] = ids_cache[nm]
                continue
            if searched >= 30:
                continue
            q = norm(SEARCH.get(nm, nm))  # API-Football search accepts only plain letters, digits and spaces
            if len(q) < 3:
                continue
            r, err = af.get("/teams", {"search": q})
            searched += 1
            if err:
                report["errors"].append(f"API-Football team {nm}: {err}")
                if "budżet" in err:
                    break
                continue
            pick = next((t["team"]["id"] for t in (r or []) if not t["team"].get("national")
                         and to_display(t["team"]["name"], {nm}) == nm), None)
            ids_cache[nm] = pick
            if pick:
                tid_of[nm] = pick
    cache_put("team_ids.json", ids_cache)
    for doc, m in docs.items():
        if doc not in fx and tid_of.get(m["home"]["name"]) and tid_of.get(m["away"]["name"]):
            fx[doc] = (None, tid_of[m["home"]["name"]], tid_of[m["away"]["name"]])
    if not fx:
        rep["requests_used"] = af.used
        return
    # 2) injuries for matched fixtures (20 ids per request)
    ids = [v[0] for v in fx.values() if v[0]]
    by_fixture = {}
    for i in range(0, len(ids), 20):
        r, err = af.get("/injuries", {"ids": "-".join(str(x) for x in ids[i:i + 20])})
        if err:
            report["errors"].append("API-Football injuries: " + err)
            continue
        for it in r or []:
            by_fixture.setdefault((it.get("fixture") or {}).get("id"), []).append(it)
        for fid in ids[i:i + 20]:
            by_fixture.setdefault(fid, [])
    # 3) squads (positions) for teams with injuries, cached for 7 days
    pos = {}
    teams_inj = sorted({it["team"]["id"] for its in by_fixture.values() for it in its if it.get("team")})
    for tid in teams_inj:
        sq = cache_get(f"squad_{tid}.json", 7)
        if sq is None:
            r, err = af.get("/players/squads", {"team": tid})
            if err:
                report["errors"].append(f"API-Football squad {tid}: {err}")
                continue
            sq = {str(p["id"]): p.get("position") for blk in (r or []) for p in blk.get("players", [])}
            cache_put(f"squad_{tid}.json", sq)
        pos.update({int(k): v for k, v in sq.items()})
    for doc, (fid, hid, aid) in fx.items():
        if not fid or fid not in by_fixture:
            continue
        side = {hid: "home", aid: "away"}
        out = {"home": [], "away": []}
        for it in by_fixture[fid]:
            pl, s = it.get("player") or {}, side.get((it.get("team") or {}).get("id"))
            if not s or not pl.get("name"):
                continue
            q = (pl.get("type") or "").lower().startswith("questionable")
            reason = INJ_PL.get((pl.get("reason") or "").lower(), pl.get("reason") or "")
            ab = {"player": pl["name"], "weight": 1 if q else 2, "reason": ("niepewny: " if q else "") + reason}
            if POS.get(pos.get(pl.get("id"))):
                ab["role"] = POS[pos[pl["id"]]]
            if all(x["player"] != ab["player"] for x in out[s]):
                out[s].append(ab)
        p = patches.setdefault(doc, {})
        p["home"] = {"absences": out["home"]}
        p["away"] = {"absences": out["away"]}
        rep["injury_docs"] += 1
    # 4) head-to-head for documents that have none yet (most popular first)
    order = sorted(fx, key=lambda d: -(docs[d].get("popularity") or 0))
    done_h2h = 0
    for doc in order:
        if docs[doc].get("h2h") or done_h2h >= h2h_cap:
            continue
        done_h2h += 1
        fid, hid, aid = fx[doc]
        r, err = af.get("/fixtures/headtohead", {"h2h": f"{hid}-{aid}"})  # free plan: no "last" parameter
        if err:
            report["errors"].append(f"API-Football h2h {doc}: {err}")
            if "budżet" in err:
                break
            continue
        names = {hid: docs[doc]["home"]["name"], aid: docs[doc]["away"]["name"]}
        ms = []
        for x in r or []:
            if ((x.get("fixture") or {}).get("status") or {}).get("short") not in ("FT", "AET", "PEN"):
                continue
            th, ta, g = x["teams"]["home"], x["teams"]["away"], x.get("goals") or {}
            if th["id"] in names and ta["id"] in names and g.get("home") is not None:
                ms.append({"date": x["fixture"]["date"][:10], "home": names[th["id"]], "away": names[ta["id"]],
                           "hg": g["home"], "ag": g["away"]})
        if ms:
            ms.sort(key=lambda m: m["date"], reverse=True)
            patches.setdefault(doc, {})["h2h"] = {"matches": ms[:10], "src": "api-football.com"}
            rep["h2h_docs"] += 1
    # 5) current head coach of every team in upcoming matches (cached 14 days, popular matches first)
    for doc in order:
        m = docs[doc]
        for tid, side in ((fx[doc][1], "home"), (fx[doc][2], "away")):
            disp = m[side]["name"]
            if disp in team_patches:
                continue
            c = cache_get(f"coach_{tid}.json", 14)
            if c is None:
                r, err = af.get("/coachs", {"team": tid})
                if err:
                    if "budżet" not in err:
                        report["errors"].append(f"API-Football coach {disp}: {err}")
                    continue
                c = {}
                for co in r or []:
                    for car in co.get("career") or []:
                        if (car.get("team") or {}).get("id") == tid and not car.get("end"):
                            if not c or (car.get("start") or "") > (c.get("since") or ""):
                                c = {"name": co.get("name"), "since": car.get("start")}
                cache_put(f"coach_{tid}.json", c)
            if c and c.get("name"):
                team_patches[disp] = {"name": disp, "league": m["league"],
                                      "coach": {"name": c["name"], "since": c.get("since"), "src": "api-football.com"}}
                rep["coaches"] = rep.get("coaches", 0) + 1
    rep["requests_used"] = af.used
    rep["requests_left_today"] = af.left


def selftest(docs):
    bad = []
    for m in docs.values():
        cands = league_teams(docs, m.get("league"))
        for t in (m["home"]["name"], m["away"]["name"]):
            if to_display(t, cands) != t:
                bad.append(t)
    return sorted(set(bad))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--docs", required=True)
    ap.add_argument("--out")
    ap.add_argument("--teams", help="ArtifactData list out_dir of the 'teams' collection (optional)")
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--af-budget", type=int, default=90)
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    docs = load_docs(a.docs)
    if a.selftest:
        bad = selftest(docs)
        print(json.dumps({"docs": len(docs), "teams_not_matching_themselves": bad}, ensure_ascii=False))
        return 0 if not bad else 1
    if not a.out:
        ap.error("--out is required")
    up = upcoming(docs, a.days)
    teams_kb = {}
    if a.teams and os.path.isdir(a.teams):
        tdir = os.path.join(a.teams, "teams") if os.path.isdir(os.path.join(a.teams, "teams")) else a.teams
        for f in os.listdir(tdir):
            if f.endswith(".json"):
                with open(os.path.join(tdir, f), encoding="utf-8") as fh:
                    t = json.load(fh)
                if t.get("name"):
                    teams_kb[t["name"]] = t
    report = {"at": iso(now_utc()), "docs_upcoming": len(up), "errors": [], "unmatched": [], "news": {},
              "odds_api": {"key": False, "matched": 0}, "api_football": {"key": False, "injury_docs": 0, "h2h_docs": 0}}
    patches, team_patches = {}, {}
    os.makedirs(a.out, exist_ok=True)
    ok, fk = read_key("odds-api.key"), read_key("api-football.key")
    report["odds_api"]["key"], report["api_football"]["key"] = bool(ok), bool(fk)
    if up and ok:
        fetch_odds(up, ok, report, patches)
    if up and fk:
        fetch_af(up, fk, a.af_budget, report, patches, team_patches)
    if up and not a.no_news:
        collect_news(up, teams_kb, report, a.out)
    tdir = os.path.join(a.out, "team_patches")
    os.makedirs(tdir, exist_ok=True)
    for f in os.listdir(tdir):
        os.remove(os.path.join(tdir, f))
    for disp, tp in team_patches.items():
        tp["updatedAt"] = iso(now_utc())
        with open(os.path.join(tdir, slug(disp) + ".json"), "w", encoding="utf-8") as fh:
            json.dump(tp, fh, ensure_ascii=False)
    report["team_patches"] = sorted(slug(d) for d in team_patches)
    pdir = os.path.join(a.out, "patches")
    os.makedirs(pdir, exist_ok=True)
    for f in os.listdir(pdir):
        os.remove(os.path.join(pdir, f))
    for doc, p in patches.items():
        src = set(up[doc].get("sources") or [])
        if "odds" in p:
            src.add("the-odds-api.com")
        if "h2h" in p or "home" in p:
            src.add("api-football.com")
        p["sources"] = sorted(src)
        p["updatedAt"] = iso(now_utc())
        with open(os.path.join(pdir, doc + ".json"), "w", encoding="utf-8") as fh:
            json.dump(p, fh, ensure_ascii=False)
    report["patched_docs"] = sorted(patches)
    with open(os.path.join(a.out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)
    print(json.dumps({k: v for k, v in report.items() if k != "patched_docs"}, ensure_ascii=False, indent=1))
    print(f"patches: {len(patches)} -> {pdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
