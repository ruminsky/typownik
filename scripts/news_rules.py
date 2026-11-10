#!/usr/bin/env python3
"""Rule-based club news for Typownik (runs without any LLM).

Reads ~/.typownik/run/news_candidates.json (headlines collected by typownik_fetch.collect_news) and updates
~/.typownik/data/teams.json conservatively:
  * coach status only from explicit patterns (sacked / new / suspended / under pressure), expires after 10 days
  * injury / absence headlines become team news items with impact -1
A headline counts for a team only when the team's name appears in the headline itself (not just the summary).
Rumour columns ("gossip", "plotki", "media:" / "podobno") are skipped. Every item keeps its source and link.
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

RUMOUR = re.compile(r"\b(gossip|plotk\w*|podobno|spekul\w*|rumou?r\w*|transfer news)\b|^media:", re.I)
COACH = r"(trener\w*|szkoleniow\w*|menedżer\w*|manager|head coach|coach|boss)"
RULES = [
    ("zwolniony", re.compile(r"(zwolni\w*|dymisj\w*|sacked|fired|parted company|rozsta\w* z trener\w*)", re.I), True),
    ("pod presją", re.compile(r"(expected to sack|set to sack|could be sacked|future in doubt|under pressure|pod presją|"
                              r"traci posad\w*|może stracić|na wylocie|ostatnia szansa|zagrożon\w*)", re.I), True),
    ("nowy", re.compile(r"(nowym trener\w*|nowy trener|new (head )?coach|new manager|appointed|zatrudni\w*|objął|obejmie)", re.I), True),
    ("zawieszony", re.compile(r"(zawieszon\w*|dyskwalifik\w*|touchline ban|banned)", re.I), True),
]
INJURY = re.compile(r"(kontuzj\w*|uraz\w*|injur\w*|ruled out|wypada|nie zagra|opuści\w* zgrupowanie|pauzuje|out for)", re.I)


def norm(s):
    return tf.norm(s)


def now():
    return dt.datetime.now(dt.timezone.utc)


def main():
    cands = json.load(open(os.path.join(H, "run", "news_candidates.json"), encoding="utf-8"))
    tp = os.path.join(H, "data", "teams.json")
    T = json.load(open(tp, encoding="utf-8")) if os.path.exists(tp) else {"teams": {}}
    teams = T.setdefault("teams", {})
    stamp = now().strftime("%Y-%m-%dT%H:%M:%SZ")
    until = (now() + dt.timedelta(days=10)).strftime("%Y-%m-%d")
    changes = []
    for c in cands:
        title, summ = c.get("title") or "", c.get("summary") or ""
        if RUMOUR.search(title) or RUMOUR.search(summ[:40]):
            continue
        tnorm = norm(title)
        in_title = [t for t in c.get("teams", []) if any(rx.search(tnorm) for rx in (tf.news_rx().get(t) or [tf._stem_rx(t)]))]
        if len(in_title) != 1:  # ambiguous or only mentioned in the summary
            continue
        team = in_title[0]
        slug = tf.slug(team)
        kb = teams.setdefault(slug, {"name": team, "league": (c.get("leagues") or [None])[0], "news": []})
        text = title.strip()[:140]
        coach_name = ((kb.get("coach") or {}).get("name") or "")
        coach_hit = re.search(COACH, title, re.I) or (coach_name and norm(coach_name.split()[-1]) in tnorm)
        status = None
        if coach_hit:
            for st, rx, _ in RULES:
                if rx.search(title) or (st != "pod presją" and rx.search(summ[:200])):
                    status = st
                    break
        if status:
            co = kb.get("coach") or {}
            if co.get("status") != status:
                kb["coach"] = {**co, "status": status, "note": text, "until": until, "source": c.get("source"),
                               "auto": True, "updatedAt": stamp}
                changes.append(f"{team}: trener → {status}")
        impact = -1 if (status in ("zwolniony", "pod presją", "zawieszony") or INJURY.search(title)) else (1 if status == "nowy" else 0)
        if status or INJURY.search(title):
            news = kb.setdefault("news", [])
            if all(n.get("url") != c.get("url") and n.get("text") != text for n in news):
                news.insert(0, {"text": text, "impact": impact, "date": c.get("published") or stamp,
                                "source": c.get("source"), "url": c.get("url"), "auto": True})
                del news[6:]
                changes.append(f"{team}: news")
        kb["updatedAt"] = stamp
    # expired automatic coach statuses go back to "ok"
    for kb in teams.values():
        co = kb.get("coach") or {}
        if co.get("auto") and co.get("until") and co["until"] < now().strftime("%Y-%m-%d"):
            kb["coach"] = {k: v for k, v in co.items() if k not in ("note", "until", "auto")} | {"status": "ok"}
    T["updatedAt"] = stamp
    tmp = tp + ".tmp"
    json.dump(T, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, tp)
    print(json.dumps({"zmiany": changes}, ensure_ascii=False))


if __name__ == "__main__":
    main()
