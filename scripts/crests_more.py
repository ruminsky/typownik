#!/usr/bin/env python3
"""Fill missing club crests (FotMob team logos) and national-team flags (flagcdn.com) for Typownik.

Writes PNGs (max 128 px) to ~/.typownik/app/crests/<slug>.png and the list of available slugs to
~/.typownik/data/crests.json. Existing files are kept. Usage: python3 -I ~/.typownik/crests_more.py
"""
import gzip, importlib.util, json, os, re, subprocess, sys, time, urllib.error, urllib.request

H = os.path.expanduser("~/.typownik")
CRESTS = os.path.join(H, "app", "crests")
spec = importlib.util.spec_from_file_location("tf", os.path.join(H, "typownik_fetch.py"))
tf = importlib.util.module_from_spec(spec); spec.loader.exec_module(tf)
FOTMOB = {"pl": 47, "laliga": 87, "seriea": 55, "bundesliga": 54, "ligue1": 53, "championship": 48,
          "ucl": 42, "uel": 73, "uecl": 10216, "pl1": 197}
# FotMob team name -> Sky name where the two differ too much for automatic matching
FIX = {"Sporting CP": "Sporting Lizbona", "Olympiacos": "Olympiakos Pireus", "Olympiakos": "Olympiakos Pireus",
       "FC Salzburg": "Red Bull Salzburg", "RB Salzburg": "Red Bull Salzburg", "Red Bull Salzburg": "Red Bull Salzburg",
       "FC København": "FC Kopenhaga", "FC Kobenhavn": "FC Kopenhaga", "FC Nordsjælland": "FC Nordsjaelland",
       "Nordsjælland": "FC Nordsjaelland", "Heart of Midlothian": "Hearts", "Hearts": "Hearts", "LASK": "Linz ASK",
       "Sint-Truiden": "Sint-Truidense VV", "Sint-Truidense": "Sint-Truidense VV", "STVV": "Sint-Truidense VV",
       "Borac Banja Luka": "Borac", "Jagiellonia Białystok": "Jagiellonia Białystok", "Jagiellonia": "Jagiellonia Białystok"}
ISO = {"Singapore": "sg", "Albania": "al", "Andorra": "ad", "Armenia": "am", "Austria": "at", "Azerbaijan": "az", "Belarus": "by", "Belgium": "be",
       "Bosnia and Herzegovina": "ba", "Bulgaria": "bg", "Croatia": "hr", "Cyprus": "cy", "Czechia": "cz", "Denmark": "dk",
       "England": "gb-eng", "Estonia": "ee", "Faroe Islands": "fo", "Finland": "fi", "France": "fr", "Georgia": "ge", "Germany": "de",
       "Gibraltar": "gi", "Greece": "gr", "Hungary": "hu", "Iceland": "is", "Ireland": "ie", "Israel": "il", "Italy": "it",
       "Kazakhstan": "kz", "Kosovo": "xk", "Latvia": "lv", "Liechtenstein": "li", "Lithuania": "lt", "Luxembourg": "lu", "Malta": "mt",
       "Moldova": "md", "Montenegro": "me", "Netherlands": "nl", "North Macedonia": "mk", "Northern Ireland": "gb-nir", "Norway": "no",
       "Poland": "pl", "Portugal": "pt", "Romania": "ro", "Russia": "ru", "San Marino": "sm", "Scotland": "gb-sct", "Serbia": "rs",
       "Slovakia": "sk", "Slovenia": "si", "Spain": "es", "Sweden": "se", "Switzerland": "ch", "Turkey": "tr", "Ukraine": "ua",
       "Wales": "gb-wls", "Argentina": "ar", "Brazil": "br", "Uruguay": "uy", "Colombia": "co", "Chile": "cl", "Peru": "pe",
       "Ecuador": "ec", "Paraguay": "py", "Bolivia": "bo", "Venezuela": "ve", "Mexico": "mx", "United States": "us", "Canada": "ca",
       "Costa Rica": "cr", "Panama": "pa", "Honduras": "hn", "Jamaica": "jm", "El Salvador": "sv", "Guatemala": "gt", "Haiti": "ht",
       "Japan": "jp", "South Korea": "kr", "North Korea": "kp", "China": "cn", "Australia": "au", "New Zealand": "nz", "Iran": "ir",
       "Iraq": "iq", "Saudi Arabia": "sa", "Qatar": "qa", "United Arab Emirates": "ae", "Uzbekistan": "uz", "Jordan": "jo",
       "Oman": "om", "Bahrain": "bh", "Kuwait": "kw", "Syria": "sy", "Lebanon": "lb", "Palestine": "ps", "Thailand": "th",
       "Vietnam": "vn", "Indonesia": "id", "Malaysia": "my", "India": "in", "Kyrgyzstan": "kg", "Tajikistan": "tj",
       "Turkmenistan": "tm", "Morocco": "ma", "Algeria": "dz", "Tunisia": "tn", "Egypt": "eg", "Senegal": "sn", "Nigeria": "ng",
       "Ghana": "gh", "Cameroon": "cm", "Ivory Coast": "ci", "Mali": "ml", "Burkina Faso": "bf", "South Africa": "za",
       "DR Congo": "cd", "Congo": "cg", "Cape Verde": "cv", "Guinea": "gn", "Gabon": "ga", "Zambia": "zm", "Angola": "ao",
       "Kenya": "ke", "Uganda": "ug", "Tanzania": "tz", "Eswatini": "sz", "Libya": "ly", "Benin": "bj", "Togo": "tg",
       "Mauritania": "mr", "Madagascar": "mg", "Zimbabwe": "zw", "Equatorial Guinea": "gq", "Sudan": "sd", "Ethiopia": "et",
       "Curaçao": "cw", "Trinidad and Tobago": "tt", "Suriname": "sr", "Cuba": "cu", "Nicaragua": "ni", "Dominican Republic": "do"}


def get(url, binary=False):
    for _ in range(4):
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Typownik)", "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=25) as f:
                b = f.read(2_000_000)
            if b[:2] == b"\x1f\x8b":
                b = gzip.decompress(b)
            return b if binary else b.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            loc = e.headers.get("Location") if e.code in (301, 302, 307, 308) else None
            if not loc:
                raise
            url = loc if loc.startswith("http") else "https://www.fotmob.com" + loc
    raise RuntimeError("too many redirects")


def save_png(data, slug):
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    raw = os.path.join(H, "run", "crest_raw.png")
    os.makedirs(os.path.dirname(raw), exist_ok=True)
    open(raw, "wb").write(data)
    out = os.path.join(CRESTS, slug + ".png")
    try:  # macOS: shrink to 128 px; elsewhere (GitHub runner) keep the original small PNG
        subprocess.run(["sips", "-Z", "128", raw, "--out", out], check=True, capture_output=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        open(out, "wb").write(data)
    return True


def main():
    os.makedirs(CRESTS, exist_ok=True)
    have = {f[:-4] for f in os.listdir(CRESTS) if f.endswith(".png")}
    ms = json.load(open(os.path.join(H, "data", "matches.json")))["matches"]
    tbs = json.load(open(os.path.join(H, "data", "tables.json"))).get("tables", {})
    ours = {}
    for m in ms:
        for s in ("home", "away"):
            ours.setdefault(m["league"], set()).add(m[s]["name"])
    for c, rows in tbs.items():
        for r in rows:
            ours.setdefault(c, set()).add(r["team"])
    added, missing = [], []
    for comp, lid in FOTMOB.items():
        names = ours.get(comp, set())
        need = {n for n in names if tf.slug(n) not in have}
        if not need:
            continue
        try:
            h = get(f"https://www.fotmob.com/leagues/{lid}/table/x")
            s = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', h, re.S).group(1)
        except Exception as e:
            print("FotMob", comp, "błąd:", e)
            continue
        for name, tid in sorted(set(re.findall(r'\{"name":"([^"]+)","shortName":"[^"]*","id":(\d+)', s))):
            name = json.loads('"' + name + '"')
            d = FIX.get(name) if FIX.get(name) in need else tf.to_display(name, need)
            if not d or tf.slug(d) in have:
                continue
            try:
                ok = save_png(get(f"https://images.fotmob.com/image_resources/logo/teamlogo/{tid}.png", True), tf.slug(d))
            except Exception as e:
                ok = False
                print("logo", d, "błąd:", e)
            if ok:
                have.add(tf.slug(d)); added.append(d)
            time.sleep(0.4)
        missing += sorted(n for n in need if tf.slug(n) not in have)
        time.sleep(1.5)
    # national teams: flags by ISO code (Polish display names come from elo.json)
    elo = json.load(open(os.path.join(H, "data", "elo.json"))).get("teams", {})
    pl2en = {v.get("pl"): k for k, v in elo.items() if v.get("pl")}
    for comp in ("nl", "friendly", "wc", "euro"):
        for n in sorted(ours.get(comp, set())):
            if tf.slug(n) in have:
                continue
            iso = ISO.get(pl2en.get(n, n))
            if not iso:
                missing.append(n); continue
            try:
                if save_png(get(f"https://flagcdn.com/w160/{iso}.png", True), tf.slug(n)):
                    have.add(tf.slug(n)); added.append(n)
            except Exception as e:
                print("flaga", n, "błąd:", e); missing.append(n)
            time.sleep(0.2)
    with open(os.path.join(H, "data", "crests.json"), "w", encoding="utf-8") as f:
        json.dump(sorted(have), f, ensure_ascii=False)
    print(json.dumps({"dodane": len(added), "razem": len(have), "brak": sorted(set(missing))}, ensure_ascii=False))
    json.dump(added, open(os.path.join(H, "run", "crests_added.json"), "w"), ensure_ascii=False)


if __name__ == "__main__":
    sys.exit(main())
