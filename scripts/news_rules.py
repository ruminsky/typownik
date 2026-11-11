#!/usr/bin/env python3
"""Rule-based club news and football gossip for Typownik (runs without any LLM).

Reads ~/.typownik/run/news_candidates.json (headlines collected by typownik_fetch.collect_news) and updates
~/.typownik/data/teams.json:
  * coach status from explicit patterns (sacked / new / suspended / under pressure), expires after 10 days;
    a sacking that is only rumoured sets "pod presją", never "zwolniony"
  * "news"     – confirmed headlines, impact -1/0/+1 from keywords (full weight in the app)
  * "rumours"  – gossip headlines (media reports, "podobno", "gossip", "linked" …), impact -1/0/+1 (half weight)
A headline counts for a team only when that team is its subject: the first team named in the headline that is not
the opponent ("przed Atalantą", "z Troyes"). Fixtures and match reports, youth/women's teams, other sports, former
coaches, interviews and machine translations of English headlines are dropped; near-duplicates are merged.
Private-life gossip (family, relationships, illness of relatives) is never stored.
Every item keeps its source and link; items older than 21 days are dropped; max 6 news + 6 rumours per club.
"""
import datetime as dt
import importlib.util
import json
import os
import re

H = os.path.expanduser("~/.typownik")
spec = importlib.util.spec_from_file_location("tf", os.path.join(H, "typownik_fetch.py"))
tf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tf)

RUMOUR = re.compile(r"(gossip|plotk\w*|podobno|spekul\w*|rumou?r\w*|według (mediów|dziennik\w*|portal\w*|\w+ ?\w*)|"
                    r"media:|media (podają|donoszą|informują)|ma (zostać|trafić)|może (trafić|odejść|zostać)|"
                    r"interesuje się|chce (kupić|sprowadzić)|negocjuj\w*|linked|target|reportedly|set to|"
                    r"expected to|could (leave|join|be)|in talks|interest in|monitor\w*|eye\w* move|eyeing|paper talk|would consider|"
                    r"considering|keen on|plot\w* (move|bid)|wants? to sign|wants? |transfer (news|rumou?rs?)|close tabs|"
                    r"latest transfer|jeśli|jeżeli|\?$)", re.I)
PRIVATE = re.compile(r"(żon\w*|mąż|męża|rozw\w*|dziewczyn\w*|partnerk\w*|ślub\w*|dzieck\w*|syn\w*|córk\w*|matk\w*|ojc\w*|"
                     r"pogrzeb|zmarł\w*|śmier\w*|wife|girlfriend|divorce|wedding|funeral|died|son |daughter)", re.I)
COACH = r"(trener\w*|szkoleniow\w*|menedżer\w*|manager|head coach|coach|boss)"
RULES = [
    ("zwolniony", re.compile(r"(zwolni\w*|dymisj\w*|sacked|fired|parted company|rozsta\w* z trener\w*)", re.I)),
    ("pod presją", re.compile(r"(sack|under pressure|pod presją|traci posad\w*|może stracić|na wylocie|ostatnia szansa|"
                              r"zagrożon\w*|future in doubt|dni \w+ policzone|zwolnienie\?)", re.I)),
    ("nowy", re.compile(r"(nowym trener\w*|nowy trener|nowego trener\w*|new (head )?coach|new manager|appointed|zatrudni\w*|"
                        r"objął|obejmie|zastąpił\w*)", re.I)),
    ("zawieszony", re.compile(r"(zawieszon\w*|dyskwalifik\w*|touchline ban|banned)", re.I)),
]
NEG = re.compile(r"(kontuzj\w*|uraz\w*|injur\w*|ruled out|wypada|nie zagra|opuści\w* zgrupowanie|pauzuj\w*|out for|"
                 r"kryzys\w*|konflikt\w*|kłopot\w*|problem\w*|zaległ\w*|protest\w*|bunt\w*|porażk\w*|klęsk\w*|"
                 r"kompromitacj\w*|rozpada|crisis|unrest|row|setback|blow|doubt|odejś\w*|leave|exit|kara\w*|fine|"
                 r"going wrong|struggl\w*|slump|worst|poor (form|run)|losing streak|seri\w* porażek|charged|zarzut\w*)", re.I)
TRANSFER = re.compile(r"(transfer\w*|sign\w*|bid|kupi\w*|sprowadz\w*|wypożycz\w*|loan|target|want\w*|"
                      r"interest\w*|close tabs|oferta|ofert\w*|negocj\w*)", re.I)
POS = re.compile(r"(wraca\w*|powr[óo]t\w*|seria zwycięstw|w formie|błyszczy|hat-trick|wzmocni\w*|return\w*|boost|"
                 r"fit again|back in training|zwycięsk\w* seri\w*|rekord\w*|lider\w*)", re.I)

# every stored item must be about something that can matter for a match; otherwise it is dropped
CATEGORIES = [  # only news that change who plays or the situation of a team before the match
    ("Kontuzja", re.compile(r"(kontuzj\w*|uraz\w*|injur\w*|ruled out|wypada|nie zagra|pauzuj\w*|out for|opuści\w* zgrupowanie|"
                            r"wraca po|powr[óo]t po|wraca do treningów|wznowił\w* trening\w*|fit again|back in training|"
                            r"back from injury|returns? from injury|available again|niedostępn\w*|unavailable|a doubt|doubt for|"
                            r"niepewn\w* (występ|start)|hamstring|knee|ankle|surgery|operacj\w*)", re.I)),
    ("Dyscyplina", re.compile(r"(kartk\w*|czerwon\w*|zawiesz\w*|dyskwalifik\w*|red card|suspen\w*|\bbanned|\bban\b|kara\w* dla)", re.I)),
    ("Trener", re.compile(COACH + r".{0,60}(zwolni\w*|dymisj\w*|sack\w*|presj\w*|pressure|\bnow(y|ego|ym)\b|\bnew\b|appoint\w*|"
                          r"zawiesz\w*|\bban(ned)?\b|\bchor(y|ego|ym|uje|ował\w*|ob\w*)\b|\bill\b|nieobecn\w*|absent|odejdzie|odejść|leave|resign\w*|rezygn\w*|przyszłoś\w*|future|"
                          r"ultimatum|ostatni\w* szans\w*|na wylocie|kandydat\w*|candidate|replace\w*|zastąpi\w*|następc\w*)|"
                          r"(zwolni\w*|dymisj\w*|sack\w*|appointed|nowym trenerem|new (head )?coach|new manager|replace\w*|zastąpi\w*).{0,60}" + COACH, re.I)),
    ("Sytuacja w klubie", re.compile(r"(konflikt\w*|bunt\w*|w szatni|dressing room|zaległ\w* (pensj|wypłat)\w*|nie otrzymal\w* (pensji|wypłat)|"
                                     r"unpaid wages|protest\w* kibic\w*|kibic\w* protest\w*|fans? protest\w*|boycott|bojkot\w*|gwizd\w*|"
                                     r"odsunięt\w*|odstawion\w*|frozen out|transfer request|chce odejść|wants out|"
                                     r"refus\w* to (train|play)|odmawia\w* (gry|trening)|strajk\w*|ujemn\w* punkt\w*|"
                                     r"points? deduct\w*|odjęci\w* punktów|zakaz transferow\w*|transfer ban|administration|"
                                     r"upadłoś\w*|takeover|nowy właściciel|sprzedaż klubu|charged|zarzut\w*)", re.I)),
    ("Kadra", re.compile(r"(powołan\w*|pominięt\w*|nie znalazł\w* się w kadrze|poza kadrą|left out|omitted|dropped from|"
                         r"\brested\b|odpocz\w*|rotacj\w*|oszczędzan\w*|squad (announced|named)|named in .{0,20}squad|call-?up|"
                         r"wyjściow\w* skład|w pierwszym składzie|na ławce)", re.I)),
]
CLICKBAIT = re.compile(r"(^(gotowi|zobacz|oto|to on|to ona|wiemy|sprawdź|tak wygląda|niesamowit\w*|szok\w*|wow|"
                       r"you won.t believe|watch|quiz|ranking|top \d+)\b|zarobk\w*|pensj\w*|ile zarabia|mógł trafić|"
                       r"nie uwierzysz|kulisy|wspomina|wspomnie\w*|jak dziś|lat temu|throwback|on this day|"
                       r"fryzur\w*|tatuaż\w*|samoch[oó]d|willa|dom za|wakacj\w*|instagram|tiktok|museum|muzeum|team photo|zdjęci\w*|"
                       r"visit\w*|odwiedzi\w*|charity|charytat\w*|students|anniversary|rocznic\w*|"
                       r"\boto (następc\w*|on|ona|powód|powody|szczegóły|kulisy|lista|nazwisk\w*)\b|\bto on\b|\bto ona\b|"
                       r"\bznamy (powołania|skład|nazwisk\w*)\b|ujawniono,? kto|wiadomo,? kto|tak zareagował\w*)", re.I)


LIVE = re.compile(r"(live|na żywo|aktualizacj\w* na żywo|minute by minute|as it happened|relacja)", re.I)
SEO = re.compile(r"(kto wygrał|wynik meczu|wynik, kto|\d{1,2}\.\d{1,2}\.\d{4}|kim jest|who is|przewidywan\w*|zapowied\w*|"
                 r"preview|gdzie obejrze\w*|\btypy\b|prediction\w*|predicted|przewidywany skład|player ratings|oceny)", re.I)


def LOWVALUE(t):
    letters = [ch for ch in t if ch.isalpha()]
    caps = sum(ch.isupper() for ch in letters) / max(1, len(letters))
    return caps > 0.6 and len(letters) > 15 or bool(LIVE.search(t)) or bool(SEO.search(t))


YOUTH = re.compile(r"(\bU-?(1[5-9]|2[0-3])\b|junior\w*|młodzieżow\w*|rezerw\w*|kobiet\w*|piłkark\w*|żeńsk\w*|"
                   r"\bwomen\b|\bWSL\b|femen\w*)", re.I)
OTHER_SPORT = re.compile(r"(krykiet\w*|cricket|\bTests?\b|rugby|siatk\w*|koszyk\w*|hokej\w*|\bNBA\b|\bNFL\b|tenis\w*|"
                         r"żużl\w*|piłk\w* ręczn\w*|szczypiorn\w*|futsal|e-?sport\w*|\bF1\b|formuł\w* 1|boks\w*|\bMMA\b|"
                         r"\bUFC\b|golf\w*|fina(ł|le)\w* MŚ)", re.I)
# match reports: a score, VAR / referee talk or "two goals and a red card" says nothing about the next match
REPORT = re.compile(r"(\b\d{1,2}\s?:\s?\d{1,2}\b|\b\d{1,2}-\d{1,2}\b|\bVAR\b|sędzi\w*|skrót\w*|highlights|"
                    r"\b(dwa|trzy|cztery|pięć) gol\w*|gol\w* i czerwon\w*)", re.I)
# former coaches/players, retirements, interviews and memories are not about the current team
FORMER = re.compile(r"(\bby[łl]\w* (trener|szkoleniow|menedżer|piłkarz|zawodnik|gracz|bramkarz|napastnik|obrońc|pomocnik|"
                    r"kapitan|prezes|selekcjoner)\w*|\bex-|\bformer\b|legend\w*|emeryt\w*|zakończ\w* karier\w*|"
                    r"wspomin\w*|wywiad\w*|podcast\w*|obudził\w*)", re.I)
# a team right after these words is the opponent, not the subject ("przed Atalantą", "nie zagra z Troyes")
OPPONENT_WORDS = {"przed", "przeciwko", "z", "ze", "vs", "v", "versus", "against", "kontra", "nad", "nie",
                  "rywal", "rywala", "rywalem", "rywalami"}
_KNOWN = []


def known_teams(teams):
    """Every team name the app knows (aliases, knowledge base, fixtures, tables) with its headline patterns."""
    if not _KNOWN:
        names = set(tf.ALIASES) | {kb.get("name") for kb in teams.values() if kb.get("name")}
        for fn, pick in (("matches.json", lambda d: [m[s]["name"] for m in d.get("matches", []) for s in ("home", "away")]),
                         ("tables.json", lambda d: [r["team"] for rows in d.get("tables", {}).values() for r in rows])):
            try:
                names |= set(pick(json.load(open(os.path.join(H, "data", fn), encoding="utf-8"))))
            except (OSError, ValueError, KeyError, TypeError):
                pass
        for n in sorted(x for x in names if x):
            _KNOWN.append((n, tf.news_rx().get(n) or [tf._stem_rx(n)]))
    return _KNOWN


def subject_of(title, teams):
    """The team a headline is about: the first team named that is not the opponent. None for fixtures/reports
    ("Valencia - Real Madryt") or when every team named is only the opponent."""
    t = tf.norm(title)
    hits = []
    for name, rxs in known_teams(teams):
        for rx in rxs:
            hits += [(m.start(), m.end(), name) for m in rx.finditer(t)]
    hits.sort(key=lambda h: (h[0], -(h[1] - h[0])))
    spans = []
    for h in hits:  # the longest name wins where two overlap ("Real Madryt" over "Real")
        if spans and h[0] < spans[-1][1]:
            continue
        spans.append(h)
    for a, b in zip(spans, spans[1:]):
        if a[2] != b[2] and t[a[1]:b[0]].strip() in ("", "vs", "v", "kontra"):
            return None  # two teams side by side = a fixture or a match report
    for start, end, name in spans:
        before = t[:start].split()
        while before and before[-1] in tf.STOP:  # "z SV Elversberg", "przed FC Barceloną"
            before.pop()
        if before and before[-1] in OPPONENT_WORDS:
            continue
        return name
    return None


ENGLISH_LEFT = re.compile(r"\b(the|and|of|for|with|after|sack\w*|manager|boss|strugglers?|injury|injured|update|deal|"
                          r"signs?|beat|clash|ahead|blow|boost|walking|wounded|huge|major|set to)\b", re.I)
ROLE = re.compile(r"\b(reprezentant|piłkarz|zawodnik|gwiazd|napastnik|obrońc|pomocnik|bramkarz|lider|kapitan|skrzydłow|"
                  r"snajper|gracz)\w*\s+(\S+)", re.I)
CAP = re.compile(r"^[A-ZĄĆĘŁŃÓŚŹŻ][a-ząćęłńóśźżéèáíúüöäçñ'’-]+")


def team_tokens(title, teams):
    t = tf.norm(title)
    out = set()
    for name, rxs in known_teams(teams):
        for rx in rxs:
            for m in rx.finditer(t):
                out |= set(m.group(0).split())
    return out


PERSONAL_CATS = ("Kontuzja", "Dyscyplina", "Kadra")


def clean(title):
    """Drop a trailing " — Der Spiegel" style source credit."""
    return re.sub(r"\s+[—–]\s+[^—–.!?]{2,40}$", "", title).strip()


def junk(title):
    return bool(PRIVATE.search(title) or CLICKBAIT.search(title) or LOWVALUE(title) or YOUTH.search(title)
                or OTHER_SPORT.search(title) or REPORT.search(title) or FORMER.search(title)
                or len(ENGLISH_LEFT.findall(title)) >= 2)


def _toks(text):
    return {w for w in tf.norm(text).split() if len(w) >= 4}


def near_duplicate(text, kept):
    a = _toks(text)
    for k in kept:
        b = _toks(k)
        if a and b and len(a & b) / len(a | b) >= 0.5:
            return True
    return False


def translated_texts():
    """Headlines that only exist as machine translations of English sources (they read badly, so they go)."""
    try:
        return set(json.load(open(os.path.join(H, "cache", "translations.json"), encoding="utf-8")).values())
    except (OSError, ValueError):
        return set()


COACH_EVENT = re.compile(r"(zwolni\w*|dymisj\w*|koniec|następc\w*|zastąp\w*|porozumieni\w*|zatrudni\w*|presj\w*|na wylocie|"
                         r"ultimatum|posad\w*|gwizd\w*|mają dość|rezygn\w*|zawiesz\w*)", re.I)


def coach_surname(kb):
    n = tf.norm(((kb or {}).get("coach") or {}).get("name") or "").split()
    return n[-1] if n and len(n[-1]) >= 4 else None


def category(text, kb=None):
    text = text or ""
    sur = coach_surname(kb)
    coach_named = bool(re.search(COACH, text, re.I)) or bool(sur and re.search(r"\b" + re.escape(sur[:-1] if len(sur) >= 5 else sur),
                                                                                tf.norm(text)))
    if coach_named and (COACH_EVENT.search(text) or any(rx.search(text) for _, rx in RULES)):
        return "Trener"
    for name, rx in CATEGORIES:
        if rx.search(text):
            return name
    return None


_PEOPLE = set()
COMMON = set("""nie już jest to oto media kolejne kolejny kolejna niepokojące wiadomo oficjalnie fatalne fatalna pech dramat cios
koniec problem problemy kłopoty złe dobre duże wielki wielka wielkie ogromny ogromna poważny poważna poważne kontuzja uraz trener
piłkarz piłkarze gwiazda gwiazdor kapitan napastnik obrońca bramkarz pomocnik reprezentacja reprezentant kadra klub szok sensacja
tak jak co kto dlaczego czy gdzie będzie był była było ma mają musi może chce nowy nowa nowe powrót wraca wracają przerwa operacja
zawieszenie kara czerwona kartka pauza absencja brak bez po przed w we z ze na do od dla i a ale oraz jeszcze też także również
tylko nagle wreszcie nareszcie jednak teraz dziś jutro wczoraj ostatni ostatnia pierwszy pierwsza znowu znów premier league liga
ligi lidze ligę serie bundesliga bundesligi ligue laliga ekstraklasa ekstraklasy mistrzów europy konferencji narodów euro mundial
mundialu champions puchar pucharu uefa fifa flash news oficjalnie wideo video""".split())


def people():
    """Player and coach names the app knows (scorers, cards, absences, coaches) – their stems, for "is someone named?"."""
    if not _PEOPLE:
        try:
            P = json.load(open(os.path.join(H, "data", "players.json"), encoding="utf-8"))
            for lg in (P.get("leagues") or {}).values():
                for board in lg.values():
                    if isinstance(board, list):
                        for r in board:
                            if isinstance(r, dict) and r.get("player"):
                                _PEOPLE.add(tf.norm(r["player"]).split()[-1])
        except (OSError, ValueError, AttributeError, IndexError):
            pass
        try:
            for kb in json.load(open(os.path.join(H, "data", "teams.json"), encoding="utf-8")).get("teams", {}).values():
                s = coach_surname(kb)
                if s:
                    _PEOPLE.add(s)
        except (OSError, ValueError):
            pass
        _PEOPLE.discard("")
    return {p[:-1] if len(p) >= 5 and p[-1] in "aeiouy" else p for p in _PEOPLE if len(p) >= 4}


def named(title, teams):
    """True when the headline names a person (player or coach), not just "a key player of X"."""
    tt = team_tokens(title, teams)
    stems = people()
    words = re.findall(r"\S+", title)
    for i, raw in enumerate(words):
        w = raw.strip("\"'„”«»()[],:;!?.-–—")
        if not CAP.match(w):
            continue  # lower-case words and all-caps tags (WIDEO, MŚ)
        nw = tf.norm(w)
        if not nw or set(nw.split()) <= tt:
            continue  # part of a team name
        if any(nw.startswith(st) for st in stems):
            return True
        lw = w.lower()
        if lw in COMMON:
            continue
        if i == 0 or words[i - 1][-1:] in ".!?:":
            continue  # a capital at a sentence start ("Alarm", "Kosztował") is a name only when it is a known player/coach
        return True
    return False



def now():
    return dt.datetime.now(dt.timezone.utc)


def impact_of(text):
    n, p = bool(NEG.search(text)), bool(POS.search(text))
    return -1 if n and not p else 1 if p and not n else 0


def main():
    cands = json.load(open(os.path.join(H, "run", "news_candidates.json"), encoding="utf-8"))
    tp = os.path.join(H, "data", "teams.json")
    T = json.load(open(tp, encoding="utf-8")) if os.path.exists(tp) else {"teams": {}}
    teams = T.setdefault("teams", {})
    stamp = now().strftime("%Y-%m-%dT%H:%M:%SZ")
    until = (now() + dt.timedelta(days=10)).strftime("%Y-%m-%d")
    cut = (now() - dt.timedelta(days=21)).strftime("%Y-%m-%dT%H:%M:%SZ")
    changes = []
    for c in sorted(cands, key=lambda c: c.get("published") or ""):
        title, summ = clean((c.get("title") or "").strip()), c.get("summary") or ""
        if not title or junk(title) or (c.get("lang") or "pl") != "pl":
            continue
        tnorm = tf.norm(title)
        team = subject_of(title, teams)
        if team not in (c.get("teams") or []):  # the club is only the opponent, a side mention or not named at all
            continue
        kb = teams.setdefault(tf.slug(team), {"name": team, "league": (c.get("leagues") or [None])[0], "news": []})
        text = title[:140]
        rumour = bool(RUMOUR.search(title) or RUMOUR.search(summ[:120]))
        coach_name = ((kb.get("coach") or {}).get("name") or "")
        coach_hit = re.search(COACH, title, re.I) or (coach_name and tf.norm(coach_name.split()[-1]) in tnorm)
        status = None
        if coach_hit:
            for st, rx in RULES:
                if rx.search(title):
                    status = st
                    break
            if status == "zwolniony" and rumour:
                status = "pod presją"  # a rumoured sacking is pressure, not a fact
        if status:
            co = kb.get("coach") or {}
            manual = co.get("status") not in (None, "ok") and not co.get("auto") and co.get("updatedAt", "") > cut
            if co.get("status") != status and not manual:
                kb["coach"] = {**co, "status": status, "note": text, "until": until, "source": c.get("source"),
                               "auto": True, "updatedAt": stamp}
                changes.append(f"{team}: trener → {status}")
        imp = impact_of(title + " " + summ[:160])
        if rumour and not status and TRANSFER.search(title):
            imp = 0  # interest in / from other clubs does not weaken a team before the match
        if status in ("zwolniony", "pod presją", "zawieszony"):
            imp = -1
        elif status == "nowy":
            imp = 1
        cat = "Trener" if status else category(title + " " + summ[:160], kb)
        if not cat:
            continue  # not about anything that can affect a match
        if cat in PERSONAL_CATS and not named(title, teams):
            continue  # "uraz kluczowego zawodnika", "znamy powołania" – nobody is named, nothing to use
        item = {"text": text, "impact": imp, "cat": cat, "date": c.get("published") or stamp, "source": c.get("source"),
                "url": c.get("url"), "auto": True, "lang": c.get("lang") or "pl"}
        key = "rumours" if rumour else "news"
        other = "news" if rumour else "rumours"
        # one headline lives in one list only: an automatic item re-classified today moves to the right list
        if other in kb:
            kb[other] = [n for n in kb[other] if not (n.get("auto") and (n.get("url") == item["url"] or n.get("text") == text))]
        lst = kb.setdefault(key, [])
        if all(n.get("url") != item["url"] and n.get("text") != text for n in lst):
            lst.insert(0, item)
            changes.append(f"{team}: {'plotka' if rumour else 'news'}")
        kb["updatedAt"] = stamp
    mt = translated_texts()
    dropped = 0
    for kb in teams.values():
        co = kb.get("coach") or {}
        note = co.get("note") or ""
        if co.get("auto") and note and (note in mt or junk(note) or subject_of(note, teams) != kb.get("name")):
            kb["coach"] = {**{k: v for k, v in co.items() if k not in ("note", "until", "auto", "source")}, "status": "ok"}
            dropped += 1  # the automatic coach status came from a headline that no longer passes the rules
        kept_texts = []
        for key in ("news", "rumours"):
            if key in kb:  # older automatic items get the same filters and a category
                fresh = []
                for n in kb[key]:
                    if n.get("auto"):
                        txt = n["text"] = clean(n.get("text") or "")
                        if (junk(txt) or txt in mt or n.get("lang") == "en" or subject_of(txt, teams) != kb.get("name")
                                or near_duplicate(txt, kept_texts)):
                            dropped += 1
                            continue
                        n["cat"] = "Trener" if n.get("cat") == "Trener" and (kb.get("coach") or {}).get("note") == n.get("text") \
                            else category(n.get("text"), kb)  # stricter rules apply to stored items too
                        if not n["cat"] or (n["cat"] in PERSONAL_CATS and not named(txt, teams)):
                            dropped += 1
                            continue
                    kept_texts.append(n.get("text") or "")
                    fresh.append(n)
                kb[key] = fresh
                kb[key] = sorted([n for n in kb[key] if (n.get("date") or stamp) >= cut],
                                 key=lambda n: n.get("date") or "", reverse=True)[:6]
        co = kb.get("coach") or {}
        if co.get("auto") and co.get("until") and co["until"] < now().strftime("%Y-%m-%d"):
            kb["coach"] = {**{k: v for k, v in co.items() if k not in ("note", "until", "auto")}, "status": "ok"}
    T["updatedAt"] = stamp
    tmp = tp + ".tmp"
    json.dump(T, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, tp)
    print(json.dumps({"zmiany": len(changes), "odrzucone_stare": dropped, "przyklady": changes[:12]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
