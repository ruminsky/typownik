#!/usr/bin/env python3
"""National-team Elo ratings from eloratings.net (World Football Elo Ratings).

Data files (the same ones the site's own scripts/ratings.js loads):
  https://www.eloratings.net/World.tsv     one row per team, tab-separated:
      local rank, global rank, team code, rating, highest rank, highest rating, ...
  https://www.eloratings.net/en.teams.tsv  code<TAB>English name[<TAB>short forms...]
Codes are the site's own (EN = England, SQ = Scotland, WA = Wales, EI = Northern Ireland,
KO = Kosovo, NM = North Macedonia ...), so names are always taken from en.teams.tsv and
Polish names are looked up by that English name.

Usage (stdlib only, Python 3.9+):
  python3 -I elo_parse.py --out ~/.typownik/data/elo.json   write the JSON file
  python3 -I elo_parse.py                                    print the JSON to stdout
  python3 -I elo_parse.py --selftest                         counts, samples and checks

Output: {"updatedAt","source","namesSource","sourceLastModified","count",
         "teams": {"<English name>": {"code","rating","rank","pl"}}}
"pl" is null for a team without a reliable Polish name. Rows that cannot be parsed are
skipped and listed on stderr; if the download looks incomplete nothing is written (exit 2).
"""
import datetime as dt
import email.utils
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (Typownik)"
TIMEOUT = 25
PAUSE = 1.5
WORLD_URL = "https://www.eloratings.net/World.tsv"
NAMES_URL = "https://www.eloratings.net/en.teams.tsv"
MIN_TEAMS = 150  # World.tsv lists ~244 teams; far fewer means a broken/partial download

# Polish names keyed by the English name used in en.teams.tsv.
PL = {
    "Spain": "Hiszpania", "Argentina": "Argentyna", "England": "Anglia", "France": "Francja",
    "Portugal": "Portugalia", "Brazil": "Brazylia", "Colombia": "Kolumbia", "Switzerland": "Szwajcaria",
    "Netherlands": "Holandia", "Belgium": "Belgia", "Italy": "Włochy", "Norway": "Norwegia",
    "Japan": "Japonia", "Morocco": "Maroko", "Germany": "Niemcy", "Mexico": "Meksyk", "Denmark": "Dania",
    "Croatia": "Chorwacja", "Uruguay": "Urugwaj", "Ecuador": "Ekwador", "Austria": "Austria",
    "Paraguay": "Paragwaj", "Senegal": "Senegal", "Greece": "Grecja", "United States": "USA",
    "Australia": "Australia", "Russia": "Rosja", "Sweden": "Szwecja", "Turkey": "Turcja",
    "Algeria": "Algieria", "Canada": "Kanada", "Iran": "Iran", "Venezuela": "Wenezuela",
    "South Korea": "Korea Południowa", "Hungary": "Węgry", "Ukraine": "Ukraina",
    "Ivory Coast": "Wybrzeże Kości Słoniowej", "Chile": "Chile", "Scotland": "Szkocja",
    "Slovenia": "Słowenia", "Egypt": "Egipt", "Nigeria": "Nigeria", "Ireland": "Irlandia",
    "Kosovo": "Kosowo", "DR Congo": "DR Konga", "Poland": "Polska", "Serbia": "Serbia", "Peru": "Peru",
    "Wales": "Walia", "Slovakia": "Słowacja", "Northern Ireland": "Irlandia Północna",
    "Bosnia and Herzegovina": "Bośnia i Hercegowina", "Czechia": "Czechy", "Panama": "Panama",
    "Uzbekistan": "Uzbekistan", "Saudi Arabia": "Arabia Saudyjska", "Mali": "Mali", "Georgia": "Gruzja",
    "Cameroon": "Kamerun", "Jordan": "Jordania", "Israel": "Izrael", "Bolivia": "Boliwia",
    "Albania": "Albania", "Iceland": "Islandia", "South Africa": "RPA", "Costa Rica": "Kostaryka",
    "Haiti": "Haiti", "Angola": "Angola", "United Arab Emirates": "Zjednoczone Emiraty Arabskie",
    "Cape Verde": "Republika Zielonego Przylądka", "New Zealand": "Nowa Zelandia", "Romania": "Rumunia",
    "Iraq": "Irak", "Jamaica": "Jamajka", "Belarus": "Białoruś", "Tunisia": "Tunezja",
    "North Macedonia": "Macedonia Północna", "Finland": "Finlandia", "Honduras": "Honduras",
    "Guatemala": "Gwatemala", "Burkina Faso": "Burkina Faso", "Ghana": "Ghana", "Palestine": "Palestyna",
    "Curaçao": "Curaçao", "Montenegro": "Czarnogóra", "Syria": "Syria", "Qatar": "Katar",
    "Gambia": "Gambia", "Oman": "Oman", "Guinea": "Gwinea", "Suriname": "Surinam", "Uganda": "Uganda",
    "Bulgaria": "Bułgaria", "Northern Cyprus": "Cypr Północny", "Benin": "Benin",
    "Kurdistan": "Kurdystan", "Gabon": "Gabon", "Estonia": "Estonia", "Vietnam": "Wietnam",
    "Faroe Islands": "Wyspy Owcze", "Libya": "Libia", "Equatorial Guinea": "Gwinea Równikowa",
    "Zambia": "Zambia", "Kazakhstan": "Kazachstan", "Rwanda": "Rwanda", "Mozambique": "Mozambik",
    "Luxembourg": "Luksemburg", "Zimbabwe": "Zimbabwe", "Thailand": "Tajlandia", "Comoros": "Komory",
    "North Korea": "Korea Północna", "Madagascar": "Madagaskar", "Niger": "Niger", "Togo": "Togo",
    "Armenia": "Armenia", "Guinea-Bissau": "Gwinea Bissau", "Cyprus": "Cypr", "Kenya": "Kenia",
    "Reunion": "Reunion", "China": "Chiny", "Sudan": "Sudan", "Trinidad and Tobago": "Trynidad i Tobago",
    "Mauritania": "Mauretania", "Malaysia": "Malezja", "Dominican Republic": "Dominikana",
    "Indonesia": "Indonezja", "Azerbaijan": "Azerbejdżan", "Guyana": "Gujana", "El Salvador": "Salwador",
    "Zanzibar": "Zanzibar", "Bahrain": "Bahrajn", "Kyrgyzstan": "Kirgistan", "Martinique": "Martynika",
    "Sierra Leone": "Sierra Leone", "Namibia": "Namibia", "Botswana": "Botswana", "Burundi": "Burundi",
    "Tajikistan": "Tadżykistan", "Kuwait": "Kuwejt", "Moldova": "Mołdawia", "Lithuania": "Litwa",
    "Lebanon": "Liban", "Liberia": "Liberia", "Tanzania": "Tanzania", "Ethiopia": "Etiopia",
    "Malta": "Malta", "Nicaragua": "Nikaragua", "French Guiana": "Gujana Francuska", "Latvia": "Łotwa",
    "Cuba": "Kuba", "Malawi": "Malawi", "Yemen": "Jemen", "Guadeloupe": "Gwadelupa", "Mayotte": "Majotta",
    "Saint Vincent and the Grenadines": "Saint Vincent i Grenadyny", "Turkmenistan": "Turkmenistan",
    "Central African Republic": "Republika Środkowoafrykańska", "Congo": "Kongo", "Eritrea": "Erytrea",
    "Lesotho": "Lesotho", "New Caledonia": "Nowa Kaledonia", "Tahiti": "Tahiti",
    "Puerto Rico": "Portoryko", "Vanuatu": "Vanuatu", "Singapore": "Singapur", "Eswatini": "Eswatini",
    "India": "Indie", "Fiji": "Fidżi", "Philippines": "Filipiny", "Hong Kong": "Hongkong",
    "Papua New Guinea": "Papua-Nowa Gwinea", "Solomon Islands": "Wyspy Salomona",
    "South Sudan": "Sudan Południowy", "Saint Lucia": "Saint Lucia",
    "Saint Kitts and Nevis": "Saint Kitts i Nevis", "Chad": "Czad", "Mauritius": "Mauritius",
    "Andorra": "Andora", "Sao Tome and Principe": "Wyspy Świętego Tomasza i Książęca",
    "Gibraltar": "Gibraltar", "Saint Martin": "Saint-Martin", "Montserrat": "Montserrat",
    "Belize": "Belize", "Bermuda": "Bermudy", "Myanmar": "Mjanma", "Barbados": "Barbados",
    "Somaliland": "Somaliland", "Grenada": "Grenada", "Western Sahara": "Sahara Zachodnia",
    "Aruba": "Aruba", "Somalia": "Somalia", "Afghanistan": "Afganistan", "Greenland": "Grenlandia",
    "Djibouti": "Dżibuti", "Sint Maarten": "Sint Maarten", "Pakistan": "Pakistan",
    "Bangladesh": "Bangladesz", "Antigua and Barbuda": "Antigua i Barbuda", "Monaco": "Monako",
    "Nepal": "Nepal", "Liechtenstein": "Liechtenstein", "Dominica": "Dominika", "Cambodia": "Kambodża",
    "Sri Lanka": "Sri Lanka", "Bonaire": "Bonaire", "Taiwan": "Tajwan", "San Marino": "San Marino",
    "Seychelles": "Seszele", "Cayman Islands": "Kajmany", "Chagos Islands": "Archipelag Czagos",
    "Maldives": "Malediwy", "Tuvalu": "Tuvalu", "Sint Eustatius": "Sint Eustatius", "Samoa": "Samoa",
    "Mongolia": "Mongolia", "Laos": "Laos", "Saint Barthelemy": "Saint-Barthélemy",
    "East Timor": "Timor Wschodni", "Guam": "Guam", "Wallis and Futuna": "Wallis i Futuna",
    "US Virgin Islands": "Wyspy Dziewicze Stanów Zjednoczonych", "Vatican": "Watykan", "Saba": "Saba",
    "Turks and Caicos Islands": "Turks i Caicos", "Saint Pierre and Miquelon": "Saint-Pierre i Miquelon",
    "British Virgin Islands": "Brytyjskie Wyspy Dziewicze", "Cook Islands": "Wyspy Cooka",
    "Bahamas": "Bahamy", "Bhutan": "Bhutan", "Tibet": "Tybet", "Anguilla": "Anguilla", "Macao": "Makau",
    "Christmas Island": "Wyspa Bożego Narodzenia", "Falkland Islands": "Falklandy",
    "FS Micronesia": "Mikronezja", "Marshall Islands": "Wyspy Marshalla", "Brunei": "Brunei",
    "Kiribati": "Kiribati", "Tonga": "Tonga", "Niue": "Niue", "Northern Mariana Islands": "Mariany Północne",
    "Cocos Islands": "Wyspy Kokosowe", "Palau": "Palau", "Eastern Samoa": "Samoa Amerykańskie",
}

_last_request = [0.0]


def _get(url):
    """Return (status, text, headers, error). 403/429 are not retried."""
    for attempt in (1, 2):
        wait = PAUSE - (time.time() - _last_request[0])
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                raw = r.read(5_000_000)
                return r.status, raw.decode(r.headers.get_content_charset() or "utf-8"), dict(r.headers), None
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 2:
                return e.code, "", {}, "HTTP %d" % e.code
        except Exception as e:
            if attempt == 2:
                return 0, "", {}, "%s: %s" % (type(e).__name__, e)
    return 0, "", {}, "unreachable"


def _iso(t):
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_names(text):
    names = {}
    for line in text.splitlines():
        v = line.rstrip("\r").split("\t")
        if len(v) < 2 or v[0].endswith("_loc") or not v[1].strip():
            continue
        names[v[0].strip()] = v[1].strip()
    return names


def parse_world(text, names, report):
    teams, prev = {}, None
    for n, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        f = line.rstrip("\r").split("\t")
        if len(f) < 4 or not all(re.fullmatch(r"\d+", x) for x in (f[0], f[1], f[3])) \
                or not re.fullmatch(r"[A-Z]{2}", f[2]):
            report["skipped"].append({"line": n, "raw": line[:80], "why": "unparseable row"})
            continue
        local, rank, code, rating = int(f[0]), int(f[1]), f[2], int(f[3])
        if local != rank:
            report["warnings"].append("line %d %s: local rank %d != global rank %d (global used)" % (n, code, local, rank))
        if not 100 <= rating <= 3000:
            report["skipped"].append({"line": n, "raw": line[:80], "why": "rating out of range"})
            continue
        name = names.get(code)
        if not name:
            report["skipped"].append({"line": n, "raw": line[:80], "why": "code %s not in en.teams.tsv" % code})
            continue
        if name in teams:
            report["skipped"].append({"line": n, "raw": line[:80], "why": "duplicate team name %s" % name})
            continue
        if prev and (rank < prev[0] or rating > prev[1]):
            report["warnings"].append("line %d %s: order not monotonic (rank %d rating %d after rank %d rating %d)"
                                      % (n, code, rank, rating, prev[0], prev[1]))
        prev = (rank, rating)
        pl = PL.get(name)
        if pl is None:
            report["no_polish_name"].append("%s (%s)" % (name, code))
        teams[name] = {"code": code, "rating": rating, "rank": rank, "pl": pl}
    return teams


def build():
    report = {"skipped": [], "warnings": [], "no_polish_name": []}
    now = dt.datetime.now(dt.timezone.utc)
    st, world, headers, err = _get(WORLD_URL)
    if st != 200 or not world:
        raise RuntimeError("World.tsv: %s" % (err or st))
    st, names_txt, _, err = _get(NAMES_URL)
    if st != 200 or not names_txt:
        raise RuntimeError("en.teams.tsv: %s" % (err or st))
    names = parse_names(names_txt)
    if len(names) < MIN_TEAMS:
        raise RuntimeError("en.teams.tsv has only %d names - refusing" % len(names))
    teams = parse_world(world, names, report)
    if len(teams) < MIN_TEAMS:
        raise RuntimeError("only %d teams parsed from World.tsv - refusing" % len(teams))
    lm = None
    hdr = {k.lower(): v for k, v in headers.items()}.get("last-modified")
    if hdr:
        try:
            lm = _iso(email.utils.parsedate_to_datetime(hdr))
        except (TypeError, ValueError):
            report["warnings"].append("unparseable Last-Modified header %r" % hdr)
    data = {"updatedAt": _iso(now), "source": WORLD_URL, "namesSource": NAMES_URL,
            "sourceLastModified": lm, "count": len(teams), "teams": teams}
    return data, report


def write(data, path):
    path = os.path.abspath(os.path.expanduser(path))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def selftest(data, report):
    t = data["teams"]
    by_rank = sorted(t.items(), key=lambda kv: kv[1]["rank"])
    print("source: %s (Last-Modified %s), names: %s" % (data["source"], data["sourceLastModified"], data["namesSource"]))
    print("counts: teams=%d with_polish_name=%d without=%d skipped_rows=%d"
          % (len(t), sum(1 for v in t.values() if v["pl"]), len(report["no_polish_name"]), len(report["skipped"])))
    must = ["Hiszpania", "Anglia", "Niemcy", "Francja", "Polska", "Holandia", "Włochy", "Szkocja", "Walia",
            "Irlandia Północna", "Kosowo", "Macedonia Północna", "Czechy", "USA", "Korea Południowa"]
    have = {v["pl"] for v in t.values()}
    checks = [("rank 1 present", by_rank[0][1]["rank"] == 1),
              ("ratings non-increasing by rank", all(by_rank[i][1]["rating"] >= by_rank[i + 1][1]["rating"]
                                                     for i in range(len(by_rank) - 1))),
              ("key Polish names present", all(n in have for n in must)),
              ("Polish names unique", len([v["pl"] for v in t.values() if v["pl"]]) == len(have - {None}))]
    for name, ok in checks:
        print("  [%s] %s" % ("OK" if ok else "FAIL", name))
    for name, v in by_rank[:5] + [kv for kv in by_rank if kv[0] == "Poland"]:
        print("  ", name, json.dumps(v, ensure_ascii=False))
    print("report:", json.dumps(report, ensure_ascii=False))
    return all(ok for _, ok in checks)


def main(argv):
    out = None
    if "--out" in argv:
        i = argv.index("--out")
        if i + 1 >= len(argv):
            print(__doc__, file=sys.stderr)
            return 64
        out = argv[i + 1]
    try:
        data, report = build()
    except Exception as e:
        print("error: %s (nothing written)" % e, file=sys.stderr)
        return 2
    if "--selftest" in argv:
        return 0 if selftest(data, report) else 1
    if out:
        path = write(data, out)
        print("wrote %s: %d teams (%d with Polish name), source Last-Modified %s"
              % (path, data["count"], sum(1 for v in data["teams"].values() if v["pl"]), data["sourceLastModified"]))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    if report["skipped"] or report["warnings"] or report["no_polish_name"]:
        print("# report " + json.dumps(report, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
