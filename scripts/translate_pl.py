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


PL_WORDS = re.compile(r"\b(i|w|z|na|się|nie|po|do|od|za|jest|oraz|przez|dla|ze|we|o|co|jak|już|czy|ma|był|była|"
                      r"będzie|został|została|gdzie|ten|ta|tak|tylko|jeszcze|przed|mecz\w*|trener\w*|kontuzj\w*|gol\w*|"
                      r"wygra\w*|przegra\w*|remis\w*|zremis\w*|pokona\w*|klub\w*|piłkar\w*|pilkar\w*|reprezentac\w*|bramk\w*|"
                      r"meczu|druzyn\w*|drużyn\w*|sezon\w*|kibic\w*|zwyci\w*)\b", re.I)
EN_MORE = re.compile(r"\b(explains?|chief|delay|punishment|target|summer|news|rumours?|rejected|wait|continues|amid|appeal|"
                     r"charges?|theory|signs?|signing|loan|deal|clash|ahead|ready|insists?|confirms?|latest|update|"
                     r"return|ruled|should|might|plans?|eyes?|wants?|reveals?|admits?|hails?|slams?|blow|boost)\b", re.I)
BBC_RUMOURS = re.compile(r"^(.{2,40}?) transfer (rumours|news)\s*:\s*(.+)$", re.I)
BBC_PREFIX = re.compile(r"^(.{2,40}?) (news|gossip|latest)\s*:\s*", re.I)


def is_en(t, lang=None):
    t = t or ""
    if not t:
        return False
    if lang == "en":
        return True
    if lang == "pl":
        return False
    words = len(EN_WORDS.findall(t)) + len(EN_MORE.findall(t))
    return (not PL_CHARS.search(t) and not PL_WORDS.search(t)) or words >= 3


def prepare(t):
    """Turn BBC-style prefixes into something a reader understands before translating."""
    m = BBC_RUMOURS.match(t)
    if m:
        return f"Plotki transferowe wokół {m.group(1)}: {m.group(3)}", False
    return BBC_PREFIX.sub("", t), True


EN_KEEP_CATS = set()  # machine translations read badly: English-origin headlines are never shown
NAME_RX = re.compile(r"\b(?:[A-Z][\w'’.-]*)(?:\s+(?:de|da|van|von|di|del|la|le)?\s*[A-Z][\w'’.-]*)+\b")
KNOWN = ["Man City", "Man Utd", "Man United", "Spurs", "Barca", "PSG", "Inter", "AC Milan", "Juve", "Real Madrid",
         "Atletico", "Bayern", "Dortmund", "Leverkusen", "Liverpool", "Arsenal", "Chelsea", "Newcastle", "Everton",
         "Fulham", "Brentford", "Brighton", "Wolves", "Villa", "Leeds", "Sunderland", "Celtic", "Rangers"]


def protect(text):
    """Replace club and player names with numbered tokens so the translator cannot mangle them."""
    names = []

    def keep(m):
        names.append(m.group(0))
        return f"[{len(names)}]"
    for k in sorted(KNOWN, key=len, reverse=True):
        text = re.sub(r"\b" + re.escape(k) + r"\b", keep, text)
    text = NAME_RX.sub(lambda m: m.group(0) if m.group(0).startswith("[") else keep(m), text)
    return text, names


def restore(text, names):
    for i, n in enumerate(names, 1):
        tok = f"[{i}]"
        if tok not in text:
            return None  # the translator lost a name – do not show a garbled sentence
        text = text.replace(tok, n)
    return re.sub(r"\s+", " ", text).strip()


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
                if is_en(n.get("text"), n.get("lang")) and n["text"] not in cache:
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
        text, needs = prepare(t)
        if not needs:  # already rewritten into Polish (BBC transfer rumours)
            cache[t] = text
            done += 1
            continue
        if fn is None:
            continue
        try:
            prot, names = protect(text)
            out = restore(fn(prot).strip(), names)
            if out and out != text:
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
            seen = set()
            translated = set(cache.values())
            for n in kb.get(key) or []:
                txt = BBC_PREFIX.sub("", n.get("text") or "") if not (n.get("text") or "").startswith("Plotki transferowe") else n.get("text")
                n["text"] = txt
                if not txt or txt in seen:
                    dropped += 1
                    continue
                english_origin = is_en(txt, n.get("lang")) or txt in translated
                if english_origin and n.get("cat") not in EN_KEEP_CATS and not txt.startswith("Plotki transferowe"):
                    dropped += 1  # transfer/form/squad items come from Polish sources, which read naturally
                    continue
                if "Miasto" in txt and txt in translated:
                    dropped += 1  # old translation that mangled "City"
                    continue
                seen.add(txt)
                if is_en(n.get("text"), n.get("lang")):
                    if n.get("text") not in cache:
                        dropped += 1
                        continue
                    n["text"] = cache[n["text"]]
                    n["lang"] = "pl"
                    if is_en(n["text"]) and not n["text"].startswith("Plotki transferowe"):
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
