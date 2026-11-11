#!/usr/bin/env python3
"""Make every text shown in Typownik Polish.

English headlines (news, rumours, automatic coach notes) and English absence reasons are translated
offline with Argos Translate (open-source, runs on the GitHub runner, nothing is sent anywhere).
Translations are cached in ~/.typownik/cache/translations.json. Anything that is still English after this
step (no model available, translation failed) is removed, so the app never shows English text.
"""
import json
import os
import re
import sys

H = os.path.expanduser("~/.typownik")
CACHE = os.path.join(H, "cache", "translations.json")
PL_CHARS = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")
EN_WORDS = re.compile(r"\b(the|to|of|and|for|in|on|with|after|is|are|was|set|says|his|her|their|over|from|as|at|be|"
                      r"will|could|would|new|win|wins|loss|vs|against|into|out|back|first|how|what|why|who|it|this|"
                      r"that|has|have|not|but|by|an|a|up|off|injury|transfer|boss|manager|star|deal|report|news)\b", re.I)


def is_en(t):
    t = t or ""
    return bool(t) and not PL_CHARS.search(t) and len(EN_WORDS.findall(t)) >= 2


def load_cache():
    try:
        return json.load(open(CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def translator():
    try:
        import argostranslate.package as pkg
        import argostranslate.translate as tr
    except Exception as e:
        print("Argos niedostępny:", e)
        return None
    try:
        if not any(p.from_code == "en" and p.to_code == "pl" for p in pkg.get_installed_packages()):
            pkg.update_package_index()
            cand = [p for p in pkg.get_available_packages() if p.from_code == "en" and p.to_code == "pl"]
            if not cand:
                print("Brak modelu en→pl w indeksie Argos")
                return None
            pkg.install_from_path(cand[0].download())
        t = tr.get_translation_from_codes("en", "pl")
        return t.translate
    except Exception as e:
        print("Nie udało się przygotować modelu:", e)
        return None


def main():
    tp, mp = os.path.join(H, "data", "teams.json"), os.path.join(H, "data", "matches.json")
    T = json.load(open(tp, encoding="utf-8"))
    M = json.load(open(mp, encoding="utf-8"))
    cache = load_cache()
    todo = set()
    for kb in T.get("teams", {}).values():
        for key in ("news", "rumours"):
            for n in kb.get(key) or []:
                if is_en(n.get("text")) and n["text"] not in cache:
                    todo.add(n["text"])
        note = (kb.get("coach") or {}).get("note")
        if is_en(note) and note not in cache:
            todo.add(note)
    for m in M.get("matches", []):
        for side in ("home", "away"):
            for a in m.get(side, {}).get("absences") or []:
                if is_en(a.get("reason")) and a["reason"] not in cache:
                    todo.add(a["reason"])
    fn = translator() if todo else None
    done = 0
    for t in sorted(todo):
        if fn is None:
            break
        try:
            out = fn(t).strip()
            if out and out != t:
                cache[t] = out
                done += 1
        except Exception:
            pass
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    json.dump(cache, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)
    dropped = 0

    def pl(t):
        return cache.get(t, t)

    for kb in T.get("teams", {}).values():
        for key in ("news", "rumours"):
            keep = []
            for n in kb.get(key) or []:
                n["text"] = pl(n.get("text"))
                if is_en(n["text"]):
                    dropped += 1
                    continue
                keep.append(n)
            if key in kb:
                kb[key] = keep
        co = kb.get("coach") or {}
        if co.get("note"):
            co["note"] = pl(co["note"])
            if is_en(co["note"]):
                co.pop("note")
    for m in M.get("matches", []):
        for side in ("home", "away"):
            for a in m.get(side, {}).get("absences") or []:
                if a.get("reason"):
                    a["reason"] = pl(a["reason"])
                    if is_en(a["reason"]):
                        a["reason"] = "nieobecny"
    for path, obj in ((tp, T), (mp, M)):
        tmp = path + ".tmp"
        json.dump(obj, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
        os.replace(tmp, path)
    print(json.dumps({"do_tlumaczenia": len(todo), "przetlumaczono": done, "usunieto_angielskie": dropped,
                      "w_pamieci": len(cache)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
