#!/usr/bin/env python3
"""Rule-based club news and football gossip for Typownik (runs without any LLM).

Reads ~/.typownik/run/news_candidates.json (headlines collected by typownik_fetch.collect_news) and updates
~/.typownik/data/teams.json:
  * coach status from explicit patterns (sacked / new / suspended / under pressure), expires after 10 days;
    a sacking that is only rumoured sets "pod presją", never "zwolniony"
  * "news"     – confirmed headlines, impact -1/0/+1 from keywords (full weight in the app)
  * "rumours"  – gossip headlines (media reports, "podobno", "gossip", "linked" …), impact -1/0/+1 (half weight)
A headline counts for a team only when the team's name appears in the headline itself (not just the summary).
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
                    r"latest transfer|\?$)", re.I)
PRIVATE = re.compile(r"(żon\w*|mąż|męża|rozw\w*|dziewczyn\w*|partnerk\w*|ślub\w*|dzieck\w*|syn\w*|córk\w*|matk\w*|ojc\w*|"
                     r"pogrzeb|zmarł\w*|śmier\w*|wife|girlfriend|divorce|wedding|funeral|died|son |daughter)", re.I)
COACH = r"(trener\w*|szkoleniow\w*|menedżer\w*|manager|head coach|coach|boss)"
RULES = [
    ("zwolniony", re.compile(r"(zwolni\w*|dymisj\w*|sacked|fired|parted company|rozsta\w* z trener\w*)", re.I)),
    ("pod presją", re.compile(r"(sack|under pressure|pod presją|traci posad\w*|może stracić|na wylocie|ostatnia szansa|"
                              r"zagrożon\w*|future in doubt|dni \w+ policzone|zwolnienie\?)", re.I)),
    ("nowy", re.compile(r"(nowym trener\w*|nowy trener|new (head )?coach|new manager|appointed|zatrudni\w*|objął|obejmie)", re.I)),
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
    ("Trener", re.compile(COACH + r".{0,60}(zwolni\w*|dymisj\w*|sack\w*|presj\w*|pressure|nowy|new|appoint\w*|zawiesz\w*|ban\w*|"
                          r"chor\w*|\bill\b|nieobecn\w*|absent|odejdzie|odejść|leave|resign\w*|rezygn\w*|przyszłoś\w*|future|"
                          r"ultimatum|ostatni\w* szans\w*|na wylocie|kandydat\w*|candidate|replace\w*|zastąpi\w*|następc\w*)|"
                          r"(zwolni\w*|dymisj\w*|sack\w*|appointed|nowym trenerem|new (head )?coach|new manager|replace\w*|zastąpi\w*).{0,60}" + COACH, re.I)),
    ("Sytuacja w klubie", re.compile(r"(konflikt\w*|bunt\w*|w szatni|dressing room|zaległ\w* (pensj|wypłat)\w*|nie otrzymal\w* (pensji|wypłat)|"
                                     r"unpaid wages|protest\w* kibic\w*|kibic\w* protest\w*|fans? protest\w*|boycott|bojkot\w*|"
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
                       r"visit\w*|odwiedzi\w*|charity|charytat\w*|students|anniversary|rocznic\w*)", re.I)


LIVE = re.compile(r"(live|na żywo|aktualizacj\w* na żywo|minute by minute|as it happened|relacja)", re.I)
SEO = re.compile(r"(kto wygrał|wynik meczu|wynik, kto|\d{1,2}\.\d{1,2}\.\d{4}|kim jest|who is|przewidywan\w*|zapowied\w*|"
                 r"preview|gdzie obejrze\w*|\btypy\b|prediction\w*|predicted|przewidywany skład|player ratings|oceny)", re.I)


def LOWVALUE(t):
    letters = [ch for ch in t if ch.isalpha()]
    caps = sum(ch.isupper() for ch in letters) / max(1, len(letters))
    return caps > 0.6 and len(letters) > 15 or bool(LIVE.search(t)) or bool(SEO.search(t))


def category(text):
    for name, rx in CATEGORIES:
        if rx.search(text or ""):
            return name
    return None



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
    for c in cands:
        title, summ = (c.get("title") or "").strip(), c.get("summary") or ""
        if not title or PRIVATE.search(title) or CLICKBAIT.search(title) or LOWVALUE(title):
            continue
        tnorm = tf.norm(title)
        in_title = [t for t in c.get("teams", []) if any(rx.search(tnorm) for rx in (tf.news_rx().get(t) or [tf._stem_rx(t)]))]
        if len(in_title) != 1:  # ambiguous, or the club is only mentioned in the summary
            continue
        team = in_title[0]
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
            if co.get("status") != status and not (co.get("status") and not co.get("auto") and co.get("updatedAt", "") > cut):
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
        cat = "Trener" if status else category(title + " " + summ[:160])
        if not cat:
            continue  # not about anything that can affect a match
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
    for kb in teams.values():
        for key in ("news", "rumours"):
            if key in kb:  # older automatic items get the same filters and a category
                fresh = []
                for n in kb[key]:
                    if n.get("auto"):
                        if CLICKBAIT.search(n.get("text") or "") or LOWVALUE(n.get("text") or ""):
                            continue
                        n["cat"] = "Trener" if n.get("cat") == "Trener" and (kb.get("coach") or {}).get("note") == n.get("text") \
                            else category(n.get("text"))  # stricter rules apply to stored items too
                        if not n["cat"]:
                            continue
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
    print(json.dumps({"zmiany": len(changes), "przyklady": changes[:12]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
