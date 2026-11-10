#!/usr/bin/env python3
"""sky_parse - direct (no-LLM) parser for Sky Sports football pages.

Stdlib-only, Python 3.9+.  Parses fixtures, full-time results and league
tables straight from the HTML that www.skysports.com serves.

Accuracy policy (real money depends on this data):
  * Nothing is ever guessed or invented.  Every match row is taken from Sky's
    own embedded match JSON (``data-state``) AND cross-checked against the
    visible markup (date header, team names, kick-off <time>, scores, status).
    If the two disagree, or anything is missing/unparseable, the row is
    skipped and an issue is reported.
  * Results: only matches Sky marks as finished with status "FT" and a numeric
    score are returned.  AET, postponed, cancelled, abandoned, suspended and
    in-play matches are skipped (and reported).
  * Table rows must satisfy P = W + D + L and GD = GF - GA (when a GD column
    is shown), otherwise they are skipped.  Points that differ from 3W + D
    (points deductions) are kept as shown and reported as "info".

Page layout (verified 2026-11-09):
  * ``/<slug>-fixtures`` and ``/<slug>-results`` both 301-redirect to
    ``/<slug>-scores-fixtures``: ONE calendar-month view containing results
    and upcoming fixtures for the current month.  Other months live at
    ``/<slug>-scores-fixtures/YYYY-MM-01`` (linked from the month picker).
    Date headers ("Saturday 21st November") carry no year: the year comes
    from the month picker's selected month and is verified against the
    weekday.
  * ``/<slug>-table``: one <table class="sdc-site-table"> per table/group.
  * Times are UK local (Europe/London) and are converted to UTC with zoneinfo.

Public API (all return plain lists of dicts):
  fixtures(comp, months_ahead=1, issues=None)
      -> [{"comp","date","time","kickoff_utc","home","away","tbc"}]
  results(comp, months_back=1, issues=None)
      -> [{"comp","date","home","away","hg","ag"}]
  table(comp, issues=None)
      -> [{"comp","group","pos","team","p","w","d","l","gf","ga","pts"}]

Skipped/odd items are appended to ``issues`` (if a list is passed) as dicts
{"level","comp","kind","url","what","detail"}; the most recent call's issues
are also available via ``last_issues()``.  When no ``issues`` list is passed,
warnings are emitted through the "sky_parse" logger.

CLI:
  python3 -I sky_parse.py fixtures|results|table <comp> [--months N]
  python3 -I sky_parse.py selftest
JSON goes to stdout, issues to stderr.
"""

import argparse
import datetime as _dt
import json
import logging
import re
import sys
import time as _time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from urllib.parse import urljoin

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    raise SystemExit("sky_parse requires Python 3.9+ (zoneinfo)")

__all__ = [
    "COMPS", "SkyParseError", "fixtures", "results", "table", "last_issues",
    "fetch_html", "parse_match_page", "parse_table_page",
]

BASE_URL = "https://www.skysports.com"
USER_AGENT = "Mozilla/5.0 (Typownik)"
TIMEOUT = 25
LONDON = ZoneInfo("Europe/London")
UTC = _dt.timezone.utc

# Competition key -> Sky slug + identity checks.  ``ids``/``names`` are the
# competition ids/names Sky embeds in each match; a match is accepted only if
# its id OR its name matches (names vary, e.g. "Sky Bet Championship" vs
# "EFL Championship", ids have been stable).
COMPS = {
    "pl": {"slug": "premier-league", "ids": ("8486969423712956130",),
           "names": ("Premier League",), "table": True},
    "laliga": {"slug": "la-liga", "ids": ("4995233845642010130",),
               "names": ("Spanish La Liga", "La Liga"), "table": True},
    "seriea": {"slug": "serie-a", "ids": ("7746543763139168130",),
               "names": ("Italian Serie A", "Serie A"), "table": True},
    "bundesliga": {"slug": "bundesliga", "ids": ("4909707171777062130",),
                   "names": ("German Bundesliga", "Bundesliga"), "table": True},
    "ligue1": {"slug": "ligue-1", "ids": ("5719643862672366130",),
               "names": ("French Ligue 1", "Ligue 1"), "table": True},
    "championship": {"slug": "championship", "ids": ("8789991840994107130",),
                     "names": ("EFL Championship", "Sky Bet Championship"),
                     "table": True},
    "ucl": {"slug": "champions-league", "ids": ("7955915977057233130",),
            "names": ("UEFA Champions League",), "table": True},
    "uel": {"slug": "europa-league", "ids": ("7732030873957203130",),
            "names": ("UEFA Europa League",), "table": True},
    "uecl": {"slug": "europa-conference-league", "ids": ("6293274262073826130",),
             "names": ("UEFA Conference League", "UEFA Europa Conference League"),
             "table": True},
    "nl": {"slug": "uefa-nations-league", "ids": ("4619052772841123130",),
           "names": ("UEFA Nations League",), "table": True},
    # Men's senior international friendlies: Sky files them under the
    # competition "International Match" with slug "internationals"
    # (/international-friendlies-fixtures is a 404).  There is no friendlies
    # table: /internationals-table redirects to a hub of unrelated
    # World Cup / Nations League group tables, so table() refuses it.
    "friendly": {"slug": "internationals", "ids": ("8032400728296815130",),
                 "names": ("International Match",), "table": False},
}

_log = logging.getLogger("sky_parse")
_LAST_ISSUES = []

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
             "Saturday", "Sunday")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")
_DATE_RE = re.compile(
    r"^(%s)\s+(\d{1,2})(?:st|nd|rd|th)?\s+(%s)(?:\s+(\d{4}))?$"
    % ("|".join(_WEEKDAYS), "|".join(_MONTHS)), re.IGNORECASE)
_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_TIME12_RE = re.compile(r"^(1[0-2]|[1-9])[.:]([0-5]\d)\s*([ap])\.?m\.?$", re.I)
_PICKER_RE = re.compile(r"^(?:(\d{1,2})\.)?(\d{1,2})\.(\d{4})$")
_URL_DATE_RE = re.compile(r"/(\d{4})-(\d{2})-(\d{2})/?$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_TBC_RE = re.compile(r"\b(tbc|tba|to be confirmed|to be arranged)\b", re.I)


class SkyParseError(Exception):
    """Fatal problem: page could not be fetched or its structure is unknown."""


# --------------------------------------------------------------------------
# issue reporting
# --------------------------------------------------------------------------
class _Reporter:
    def __init__(self, comp, kind, sink):
        self.comp, self.kind, self.sink = comp, kind, sink
        self.items = []

    def add(self, level, what, detail="", url=None):
        item = {"level": level, "comp": self.comp, "kind": self.kind,
                "url": url, "what": what, "detail": detail}
        self.items.append(item)
        if self.sink is not None:
            self.sink.append(item)
        elif level in ("warning", "error"):
            _log.warning("[%s %s] %s: %s (%s)", self.comp, self.kind, what,
                         detail, url)

    def finish(self):
        global _LAST_ISSUES
        _LAST_ISSUES = list(self.items)


def last_issues():
    """Issues (skipped rows, warnings) recorded by the most recent call."""
    return list(_LAST_ISSUES)


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------
def fetch_html(url, timeout=TIMEOUT, retries=2):
    """GET ``url`` (following redirects).  Returns (html_text, final_url).

    Raises SkyParseError on HTTP errors / network failure."""
    last_err = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
        })
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                status = getattr(resp, "status", 200)
                if status != 200:
                    raise SkyParseError("HTTP %s for %s" % (status, url))
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                final_url = resp.geturl()
            try:
                text = raw.decode(charset)
            except (UnicodeDecodeError, LookupError) as exc:
                raise SkyParseError("cannot decode %s as %s: %s"
                                    % (url, charset, exc))
            return text, final_url
        except urllib.error.HTTPError as exc:
            last_err = exc
            if exc.code < 500:  # 4xx: retrying will not help
                break
        except (urllib.error.URLError, OSError) as exc:
            last_err = exc
        if attempt < retries:
            _time.sleep(2)
    raise SkyParseError("fetch failed for %s: %s" % (url, last_err))


# --------------------------------------------------------------------------
# minimal DOM built with html.parser
# --------------------------------------------------------------------------
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link",
         "meta", "param", "source", "track", "wbr"}
_SKIP_TEXT = {"script", "style", "template", "noscript"}


class _Node(object):
    __slots__ = ("tag", "attrs", "children", "parent", "in_template")

    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.attrs = attrs
        self.children = []
        self.parent = parent
        self.in_template = tag == "template" or bool(
            parent is not None and parent.in_template)

    def get(self, name, default=None):
        return self.attrs.get(name, default)

    def has_class(self, cls):
        return cls in (self.attrs.get("class") or "").split()

    def iter(self):
        """Pre-order (document order) traversal of element nodes."""
        stack = [self]
        while stack:
            node = stack.pop()
            yield node
            kids = [c for c in node.children if isinstance(c, _Node)]
            stack.extend(reversed(kids))

    def find_all(self, pred, include_template=False):
        return [n for n in self.iter() if n is not self and pred(n)
                and (include_template or not n.in_template)]

    def find(self, pred, include_template=False):
        for n in self.iter():
            if n is not self and pred(n) and (include_template
                                             or not n.in_template):
                return n
        return None

    def text(self):
        parts = []
        stack = [self]
        while stack:
            node = stack.pop()
            if isinstance(node, str):
                parts.append(node)
                continue
            if node.tag in _SKIP_TEXT and node is not self:
                continue
            stack.extend(reversed(node.children))
        return " ".join(" ".join(parts).split())


class _TreeBuilder(HTMLParser):
    def __init__(self):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.stack = [self.root]

    def _make(self, tag, attrs):
        node = _Node(tag, {k: (v if v is not None else "") for k, v in attrs},
                     self.stack[-1])
        self.stack[-1].children.append(node)
        return node

    def handle_starttag(self, tag, attrs):
        node = self._make(tag, attrs)
        if tag not in _VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self._make(tag, attrs)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return
        # stray end tag: ignore

    def handle_data(self, data):
        if data:
            self.stack[-1].children.append(data)


def _parse_dom(text):
    builder = _TreeBuilder()
    builder.feed(text)
    builder.close()
    return builder.root


def _norm(s):
    return " ".join((s or "").split())


# --------------------------------------------------------------------------
# date helpers
# --------------------------------------------------------------------------
def _add_months(y, m, k):
    idx = y * 12 + (m - 1) + k
    return idx // 12, idx % 12 + 1


def _parse_date_header(text):
    """'Saturday 21st November' -> (weekday_idx, day, month, year|None)."""
    m = _DATE_RE.match(_norm(text))
    if not m:
        return None
    wd = [w.lower() for w in _WEEKDAYS].index(m.group(1).lower())
    mon = [x.lower() for x in _MONTHS].index(m.group(3).lower()) + 1
    year = int(m.group(4)) if m.group(4) else None
    return wd, int(m.group(2)), mon, year


def _resolve_date(header, page_ym):
    """Turn a parsed header into a date, using the page month for the year
    and verifying the weekday.  Returns (date, None) or (None, reason)."""
    wd, day, mon, year = header
    if year is not None:
        candidates = [year]
    elif page_ym is None:
        return None, "year unknown (page month not found)"
    else:
        py, pm = page_ym
        candidates = []
        for y in (py - 1, py, py + 1):
            diff = abs((y * 12 + mon) - (py * 12 + pm))
            if diff <= 1:
                candidates.append(y)
    good = []
    for y in candidates:
        try:
            d = _dt.date(y, mon, day)
        except ValueError:
            continue
        if d.weekday() == wd:
            good.append(d)
    if len(good) == 1:
        return good[0], None
    if not good:
        return None, "no year near the page month makes this weekday valid"
    return None, "ambiguous year"


def _uk_to_utc(d, hh, mm):
    """UK wall-clock -> aware UTC datetime, or (None, reason) for DST gaps
    and overlaps (never guessed)."""
    naive = _dt.datetime(d.year, d.month, d.day, hh, mm)
    a = naive.replace(tzinfo=LONDON, fold=0)
    b = naive.replace(tzinfo=LONDON, fold=1)
    if a.utcoffset() != b.utcoffset():
        # either non-existent (spring gap) or ambiguous (autumn overlap)
        back = a.astimezone(UTC).astimezone(LONDON)
        if back.replace(tzinfo=None) != naive:
            return None, "non-existent UK local time (DST gap)"
        return None, "ambiguous UK local time (DST overlap)"
    return a.astimezone(UTC), None


def _today_uk():
    return _dt.datetime.now(UTC).astimezone(LONDON).date()


# --------------------------------------------------------------------------
# scores & fixtures page
# --------------------------------------------------------------------------
def _picker(root):
    """Month picker -> (selected (y, m) or None, {(y, m): href}, granularity)."""
    nav = root.find(lambda n: n.get("data-component-name") ==
                    "ui-sport-date-picker")
    gran = nav.get("data-component-granularity") if nav is not None else None
    links, selected = {}, None
    scope = nav if nav is not None else root
    for a in scope.find_all(lambda n: n.tag == "a" and
                            n.get("data-role") == "date-link"):
        m = _PICKER_RE.match((a.get("data-date-string") or "").strip())
        if not m:
            continue
        ym = (int(m.group(3)), int(m.group(2)))
        if a.get("href"):
            links[ym] = a.get("href")
        if a.get("aria-current") == "true":
            selected = ym
    return selected, links, gran


def _side_text(match_node, side, cls_inner):
    """Visible text of a home/away sub-element (outside <template>)."""
    teams = match_node.find_all(
        lambda n: n.has_class("ui-sport-match-score__team")
        and n.get("data-team-id") == side)
    if len(teams) != 1:
        return None
    names = teams[0].find_all(lambda n: n.has_class(cls_inner))
    if len(names) != 1:
        return None
    return names[0].text()


def _score_text(match_node, side):
    wrappers = match_node.find_all(
        lambda n: n.has_class("ui-sport-match-score__score-wrapper")
        and n.get("data-role") == "score-" + side)
    if not wrappers:
        return None
    if len(wrappers) > 1:
        return "<ambiguous>"
    disp = wrappers[0].find(lambda n: n.has_class("ui-sport-match-score__score"))
    return (disp or wrappers[0]).text()


def _extract_match(node):
    """Raw facts about one match tile (no validation yet)."""
    info = {"id": node.get("data-match-id"),
            "dom_state": node.get("data-match-state"),
            "state": None, "state_error": None}
    raw = node.get("data-state")
    if raw is None:
        info["state_error"] = "no data-state attribute"
    else:
        try:
            st = json.loads(raw)
            if not isinstance(st, dict):
                raise ValueError("not an object")
            info["state"] = st
        except ValueError as exc:
            info["state_error"] = "bad data-state JSON: %s" % exc
    info["home"] = _side_text(node, "home", "ui-sport-match-score__team-name")
    info["away"] = _side_text(node, "away", "ui-sport-match-score__team-name")
    info["score_home"] = _score_text(node, "home")
    info["score_away"] = _score_text(node, "away")
    st_nodes = node.find_all(lambda n: n.get("data-role") ==
                             "status-description")
    short = None
    if len(st_nodes) == 1:
        p = st_nodes[0].find(lambda n: n.get("data-role") == "short")
        short = p.text() if p is not None else ""
    info["status_short"] = short
    tnode = node.find(lambda n: n.has_class("ui-sport-match-score__start-time"))
    if tnode is not None:
        t = tnode.find(lambda n: n.tag == "time")
        info["time_attr"] = (t.get("datetime") if t is not None else None)
        info["time_text"] = (t.text() if t is not None else tnode.text())
    else:
        info["time_attr"] = info["time_text"] = None
    summ = node.find(lambda n: n.has_class("ui-sport-match-score__summary"))
    info["summary"] = summ.text().rstrip(" .") if summ is not None else ""
    return info


def parse_match_page(text, url=None):
    """Parse one scores-&-fixtures month page.

    Returns dict(page_month=(y, m)|None, month_links={...}, granularity=str,
    matches=[raw match dicts with 'header', 'header_date', 'header_error'],
    tiles_in_source=int)."""
    root = _parse_dom(text)
    selected, links, gran = _picker(root)
    if selected is None and url:
        m = _URL_DATE_RE.search(url)
        if m:
            selected = (int(m.group(1)), int(m.group(2)))
    matches = []
    header = None
    for node in root.iter():
        if node.in_template:
            continue
        if node.tag in ("h2", "h3") and node.has_class(
                "ui-sitewide-component-header--h3"):
            tgt = node.find(lambda n: n.get("data-role") == "short-text-target")
            htext = (tgt or node).text()
            parsed = _parse_date_header(htext)
            if parsed is None:
                header = {"text": htext, "date": None,
                          "error": "not a date header"}
            else:
                d, err = _resolve_date(parsed, selected)
                header = {"text": htext, "date": d, "error": err}
            continue
        if node.tag == "div" and node.get("data-component-name") == \
                "ui-sport-match-score":
            info = _extract_match(node)
            info["header"] = header["text"] if header else None
            info["header_date"] = header["date"] if header else None
            info["header_error"] = (header["error"] if header
                                    else "no date header before match")
            matches.append(info)
    tiles = len(re.findall(r'data-component-name="ui-sport-match-score"', text))
    return {"page_month": selected, "month_links": links, "granularity": gran,
            "matches": matches, "tiles_in_source": tiles}


def _comp_cfg(comp):
    if comp not in COMPS:
        raise SkyParseError("unknown competition %r (known: %s)"
                            % (comp, ", ".join(COMPS)))
    return COMPS[comp]


def _load_months(comp, kind, offsets, rep):
    """Fetch the base page (spec URL) plus neighbouring months.

    ``offsets``: month offsets relative to the base page's month (0 = base).
    Returns list of (url, parsed_page)."""
    cfg = _comp_cfg(comp)
    base_url = "%s/%s-%s" % (BASE_URL, cfg["slug"], kind)
    text, final_url = fetch_html(base_url)
    page = parse_match_page(text, final_url)
    if page["page_month"] is None:
        raise SkyParseError("%s: cannot determine which month the page shows "
                            "(page structure changed?)" % final_url)
    if page["granularity"] not in (None, "month"):
        rep.add("warning", "unexpected calendar granularity",
                repr(page["granularity"]), final_url)
    if page["tiles_in_source"] and not page["matches"]:
        raise SkyParseError("%s: match tiles present but none parsed "
                            "(page structure changed?)" % final_url)
    pages = []
    by, bm = page["page_month"]
    for off in offsets:
        if off == 0:
            pages.append((final_url, page))
            continue
        ym = _add_months(by, bm, off)
        href = page["month_links"].get(ym)
        if href is None:
            # build from the selected month's link pattern
            cur = page["month_links"].get((by, bm))
            if cur and _URL_DATE_RE.search(cur):
                href = _URL_DATE_RE.sub("/%04d-%02d-01" % ym, cur)
            else:
                href = "/%s-scores-fixtures/%04d-%02d-01" % (cfg["slug"], ym[0],
                                                             ym[1])
            rep.add("info", "month not in picker; built URL", href, final_url)
        url = urljoin(final_url, href)
        try:
            t2, f2 = fetch_html(url)
        except SkyParseError as exc:
            rep.add("warning", "month page not fetched, its matches are missing",
                    str(exc), url)
            continue
        p2 = parse_match_page(t2, f2)
        if p2["page_month"] != ym:
            rep.add("warning", "month page shows a different month; ignored",
                    "wanted %s got %s" % (ym, p2["page_month"]), f2)
            continue
        if p2["tiles_in_source"] and not p2["matches"]:
            rep.add("warning", "match tiles present but none parsed", "", f2)
            continue
        pages.append((f2, p2))
    for url, p in pages:
        if p["tiles_in_source"] != len(p["matches"]):
            rep.add("warning", "tile count mismatch",
                    "source has %d tiles, parsed %d" % (p["tiles_in_source"],
                                                       len(p["matches"])), url)
    return pages


def _validate_common(comp, m, url, rep):
    """Checks shared by fixtures/results.  Returns (date, home, away, state)
    or None (after reporting) if the match must be skipped."""
    cfg = COMPS[comp]
    label = "%s v %s (id %s)" % (m.get("home"), m.get("away"), m.get("id"))
    st = m["state"]
    if st is None:
        rep.add("warning", "skipped: " + label, m["state_error"], url)
        return None
    c = st.get("competition") or {}
    cid = str(c.get("id") or "")
    cname = ((c.get("name") or {}).get("full") or "")
    if cid not in cfg["ids"] and cname not in cfg["names"]:
        rep.add("warning", "skipped: " + label,
                "competition %r (id %s) is not %s" % (cname, cid, comp), url)
        return None
    teams = st.get("teams") or {}
    names = []
    for side in ("home", "away"):
        js = _norm(((teams.get(side) or {}).get("name") or {}).get("full"))
        vis = _norm(m.get(side))
        if not vis or not js:
            rep.add("warning", "skipped: " + label,
                    "%s team name missing (visible=%r, json=%r)" % (side, vis, js),
                    url)
            return None
        if vis != js:
            rep.add("warning", "skipped: " + label,
                    "%s team name mismatch visible=%r json=%r" % (side, vis, js),
                    url)
            return None
        names.append(vis)
    if m["header_date"] is None:
        rep.add("warning", "skipped: " + label,
                "date header %r unusable: %s" % (m["header"], m["header_error"]),
                url)
        return None
    js_date = _norm((st.get("start") or {}).get("date"))
    if js_date and js_date.lower() != _norm(m["header"]).lower():
        rep.add("warning", "skipped: " + label,
                "date header %r != match date %r" % (m["header"], js_date), url)
        return None
    if m["dom_state"] and st.get("matchState") and \
            m["dom_state"] != st.get("matchState"):
        rep.add("warning", "skipped: " + label,
                "match state mismatch %r vs %r" % (m["dom_state"],
                                                   st.get("matchState")), url)
        return None
    return m["header_date"], names[0], names[1], st


_BAD_FLAGS = ("isPostponed", "isCancelled", "isAbandoned", "isSuspended")


def _flag_reason(st):
    flags = [f[2:].lower() for f in _BAD_FLAGS if st.get(f) is True]
    if st.get("customScore"):
        flags.append("customScore=%s" % st.get("customScore"))
    return ", ".join(flags)


def _dedupe(rows, key, rep, url_hint=None):
    seen, out, bad = {}, [], set()
    for mid, row in rows:
        if mid in seen:
            if seen[mid] != row:
                bad.add(mid)
                rep.add("warning", "skipped: match id %s listed twice with "
                        "different data" % mid, "", url_hint)
            continue
        seen[mid] = row
        out.append((mid, row))
    return sorted([r for mid, r in out if mid not in bad], key=key)


def fixtures(comp, months_ahead=1, issues=None):
    """Upcoming fixtures for ``comp``: the current month page plus
    ``months_ahead`` following month pages."""
    rep = _Reporter(comp, "fixtures", issues)
    try:
        cfg = _comp_cfg(comp)
        pages = _load_months(comp, "fixtures", range(0, months_ahead + 1), rep)
        now = _dt.datetime.now(UTC)
        today = _today_uk()
        rows = []
        for url, page in pages:
            for m in page["matches"]:
                label = "%s v %s (id %s)" % (m.get("home"), m.get("away"),
                                             m.get("id"))
                if m["state"] is None:
                    rep.add("warning", "skipped: " + label, m["state_error"], url)
                    continue
                st = m["state"]
                ms = st.get("matchState") or m["dom_state"]
                if ms == "post" and st.get("isResult") is True:
                    continue  # a result, not a fixture
                if ms == "live" or st.get("isInPlay") is True:
                    rep.add("info", "skipped (in play): " + label, "", url)
                    continue
                if _flag_reason(st):
                    rep.add("info", "skipped (postponed/cancelled/abandoned): "
                            + label, "date=%s flags=[%s] summary=%r" % (
                                m["header"], _flag_reason(st), m.get("summary")),
                            url)
                    continue
                if ms != "pre" or st.get("isFixture") is not True:
                    rep.add("warning", "skipped (unexpected state): " + label,
                            "state=%s isFixture=%r status=%r summary=%r" % (
                                ms, st.get("isFixture"), st.get("status"),
                                m.get("summary")), url)
                    continue
                common = _validate_common(comp, m, url, rep)
                if common is None:
                    continue
                d, home, away, st = common
                start = st.get("start")
                if not isinstance(start, dict):
                    rep.add("warning", "skipped: " + label,
                            "no start info in match JSON", url)
                    continue
                js_time = start.get("time")
                js_time = js_time.strip() if isinstance(js_time, str) else js_time
                attr = (m.get("time_attr") or "").strip() or None
                vis = _norm(m.get("time_text")) or None
                tbc = False
                hhmm = None
                if js_time and _HHMM_RE.match(js_time):
                    hhmm = js_time
                    if attr is not None and attr != js_time:
                        rep.add("warning", "skipped: " + label,
                                "kick-off mismatch json=%s <time>=%s"
                                % (js_time, attr), url)
                        continue
                    if vis and not _TBC_RE.search(vis):
                        t12 = _TIME12_RE.match(vis)
                        if t12:
                            h = int(t12.group(1)) % 12 + (
                                12 if t12.group(3).lower() == "p" else 0)
                            if "%02d:%s" % (h, t12.group(2)) != js_time:
                                rep.add("warning", "skipped: " + label,
                                        "kick-off mismatch json=%s shown=%r"
                                        % (js_time, vis), url)
                                continue
                    if vis and _TBC_RE.search(vis):
                        rep.add("warning", "skipped: " + label,
                                "json time %s but page shows %r" % (js_time, vis),
                                url)
                        continue
                elif (js_time in (None, "") or (isinstance(js_time, str) and
                                                _TBC_RE.search(js_time))) and \
                        "time" in start and (attr is None or _TBC_RE.search(attr)) \
                        and (vis is None or _TBC_RE.search(vis)):
                    tbc = True
                else:
                    rep.add("warning", "skipped: " + label,
                            "unparseable kick-off json=%r <time>=%r shown=%r"
                            % (js_time, attr, vis), url)
                    continue
                if tbc:
                    if d < today:
                        rep.add("warning", "skipped: " + label,
                                "TBC fixture dated in the past (%s)" % d, url)
                        continue
                    rows.append((m["id"], {
                        "comp": comp, "date": d.isoformat(), "time": None,
                        "kickoff_utc": "%sT12:00:00Z" % d.isoformat(),
                        "home": home, "away": away, "tbc": True}))
                    continue
                hh, mm = int(hhmm[:2]), int(hhmm[3:])
                ko, err = _uk_to_utc(d, hh, mm)
                if ko is None:
                    rep.add("warning", "skipped: " + label,
                            "%s %s: %s" % (d, hhmm, err), url)
                    continue
                if ko < now - _dt.timedelta(hours=3):
                    rep.add("warning", "skipped: " + label,
                            "still marked pre-match but kick-off %s was over 3h "
                            "ago (postponed/abandoned?)" % ko.isoformat(), url)
                    continue
                rows.append((m["id"], {
                    "comp": comp, "date": d.isoformat(), "time": hhmm,
                    "kickoff_utc": ko.strftime("%Y-%m-%dT%H:%M:00Z"),
                    "home": home, "away": away, "tbc": False}))
        return _dedupe(rows, lambda r: (r["kickoff_utc"], r["home"]), rep)
    finally:
        rep.finish()


def results(comp, months_back=1, issues=None):
    """Full-time results for ``comp``: the current month page plus
    ``months_back`` previous month pages."""
    rep = _Reporter(comp, "results", issues)
    try:
        _comp_cfg(comp)
        pages = _load_months(comp, "results", range(-months_back, 1), rep)
        today = _today_uk()
        rows = []
        for url, page in pages:
            for m in page["matches"]:
                label = "%s v %s (id %s)" % (m.get("home"), m.get("away"),
                                             m.get("id"))
                if m["state"] is None:
                    rep.add("warning", "skipped: " + label, m["state_error"], url)
                    continue
                st = m["state"]
                ms = st.get("matchState") or m["dom_state"]
                if ms == "pre" and not _flag_reason(st):
                    continue  # ordinary fixture
                if ms == "live" or st.get("isInPlay") is True:
                    rep.add("info", "skipped (in play): " + label, "", url)
                    continue
                if _flag_reason(st):
                    rep.add("info", "skipped (postponed/cancelled/abandoned): "
                            + label, "date=%s flags=[%s] summary=%r" % (
                                m["header"], _flag_reason(st), m.get("summary")),
                            url)
                    continue
                if st.get("isResult") is not True:
                    rep.add("warning", "skipped (unexpected state): " + label,
                            "state=%s isResult=%r status=%r summary=%r" % (
                                ms, st.get("isResult"), st.get("status"),
                                m.get("summary")), url)
                    continue
                status = st.get("status")
                sdesc = (st.get("statusDescription") or {}).get("full")
                if status != "FT" or sdesc not in (None, "FT"):
                    rep.add("warning", "skipped (not a plain full-time result): "
                            + label, "status=%r (%r) summary=%r" % (
                                status, sdesc, m.get("summary")), url)
                    continue
                if ms != "post":
                    rep.add("warning", "skipped: " + label,
                            "status FT but state %r" % ms, url)
                    continue
                common = _validate_common(comp, m, url, rep)
                if common is None:
                    continue
                d, home, away, st = common
                if m.get("status_short") != "FT":
                    rep.add("warning", "skipped: " + label,
                            "visible status %r is not 'FT'" % m.get("status_short"),
                            url)
                    continue
                goals = []
                for side in ("home", "away"):
                    sc = ((st.get("teams") or {}).get(side) or {}).get("score") or {}
                    js = sc.get("current")
                    vis = _norm(m.get("score_" + side))
                    if not isinstance(js, int) or isinstance(js, bool) or js < 0:
                        goals = None
                        rep.add("warning", "skipped: " + label,
                                "%s score in JSON not an int: %r" % (side, js), url)
                        break
                    if not vis.isdigit() or int(vis) != js:
                        goals = None
                        rep.add("warning", "skipped: " + label,
                                "%s score mismatch shown=%r json=%r" % (
                                    side, vis, js), url)
                        break
                    goals.append(js)
                if goals is None:
                    continue
                if d > today:
                    rep.add("warning", "skipped: " + label,
                            "result dated in the future (%s)" % d, url)
                    continue
                rows.append((m["id"], {
                    "comp": comp, "date": d.isoformat(), "home": home,
                    "away": away, "hg": goals[0], "ag": goals[1]}))
        return _dedupe(rows, lambda r: (r["date"], r["home"]), rep)
    finally:
        rep.finish()


# --------------------------------------------------------------------------
# tables
# --------------------------------------------------------------------------
_HEADER_FIELDS = {
    "position": "pos", "pos": "pos", "#": "pos",
    "team": "team", "club": "team",
    "played": "p", "pl": "p", "p": "p", "mp": "p",
    "won": "w", "w": "w", "drawn": "d", "d": "d", "lost": "l", "l": "l",
    "goals for": "gf", "f": "gf", "gf": "gf",
    "goals against": "ga", "a": "ga", "ga": "ga",
    "goal difference": "gd", "gd": "gd",
    "points": "pts", "pts": "pts",
}
_LIVE_KEYS = {"pos": "pos", "team": "team", "pld": "p", "w": "w", "d": "d",
              "l": "l", "f": "gf", "a": "ga", "gd": "gd", "pts": "pts"}
_REQUIRED = ("pos", "team", "p", "w", "d", "l", "gf", "ga", "pts")


def _th_field(th):
    cands = []
    abbr = th.find(lambda n: n.tag == "abbr")
    if abbr is not None:
        cands.append(abbr.get("title") or "")
        cands.append(abbr.text())
    hidden = th.find(lambda n: n.has_class("u-hide-visually"))
    if hidden is not None:
        cands.append(hidden.text())
    cands.append(th.text())
    fields = set()
    for c in cands:
        f = _HEADER_FIELDS.get(_norm(c).lower())
        if f:
            fields.add(f)
    if len(fields) == 1:
        return fields.pop()
    return None if not fields else "<conflict>"


def _to_int(s):
    s = _norm(s).replace("−", "-").replace("–", "-")
    return int(s) if _INT_RE.match(s) else None


def parse_table_page(text, comp, url=None, rep=None):
    """Parse every league table on a Sky table page."""
    rep = rep or _Reporter(comp, "table", None)
    cfg = _comp_cfg(comp)
    root = _parse_dom(text)
    tables = root.find_all(lambda n: n.tag == "table" and
                           n.has_class("sdc-site-table"))
    if not tables:
        raise SkyParseError("%s: no sdc-site-table found (page structure "
                            "changed?)" % url)
    multi = len(tables) > 1
    out = []
    for tbl in tables:
        # heading lives in the enclosing sdc-site-table__body
        anc = tbl.parent
        while anc is not None and not anc.has_class("sdc-site-table__body"):
            anc = anc.parent
        h = None
        if anc is not None:
            h = anc.find(lambda n: n.has_class("sdc-site-table__table-heading"))
        if h is None:
            h = tbl.find(lambda n: n.tag == "caption")
        heading = h.text() if h is not None else None
        round_id = tbl.get("data-round") or ""
        if not any(round_id.startswith(i) for i in cfg["ids"]) and not (
                heading and any(heading.startswith(nm) for nm in cfg["names"])):
            rep.add("warning", "table identity unconfirmed",
                    "heading=%r data-round=%r" % (heading, round_id), url)
        group = None
        if multi:
            if not heading:
                rep.add("warning", "skipped table without heading",
                        "data-round=%r" % round_id, url)
                continue
            group = heading
            for nm in cfg["names"]:
                if heading.startswith(nm + " - "):
                    group = heading[len(nm) + 3:].strip()
                    break
        tname = group or heading or comp
        thead = tbl.find(lambda n: n.tag == "thead")
        ths = (thead or tbl).find_all(lambda n: n.tag == "th")
        if thead is None:
            rep.add("warning", "skipped table: no <thead>", tname, url)
            continue
        cols = [_th_field(th) for th in ths]
        if "<conflict>" in cols or any(cols.count(f) > 1 for f in _REQUIRED):
            rep.add("warning", "skipped table: ambiguous header", repr(cols), url)
            continue
        missing = [f for f in _REQUIRED if f not in cols]
        if missing:
            rep.add("warning", "skipped table: missing columns",
                    "%s; header=%r" % (missing, cols), url)
            continue
        by_id = {th.get("id"): f for th, f in zip(ths, cols) if th.get("id")}
        tbody = tbl.find(lambda n: n.tag == "tbody")
        if tbody is None:
            rep.add("warning", "skipped table: no <tbody>", tname, url)
            continue
        trs = tbody.find_all(lambda n: n.tag == "tr")
        rows = []
        table_ok = True
        for ri, tr in enumerate(trs, 1):
            tds = [c for c in tr.children if isinstance(c, _Node)
                   and c.tag in ("td", "th")]
            if not tds:
                continue
            vals = {}
            row_err = None
            for ci, td in enumerate(tds):
                f = by_id.get(td.get("headers")) if td.get("headers") else None
                if f is None:
                    f = cols[ci] if ci < len(cols) else None
                lk = td.get("data-live-key")
                if lk and lk in _LIVE_KEYS and f is not None and \
                        _LIVE_KEYS[lk] != f:
                    row_err = ("column mismatch: header says %s, cell key %s"
                               % (f, lk))
                    break
                if f is None:
                    continue
                if f in vals:
                    row_err = "duplicate column %s" % f
                    break
                if f == "team":
                    nt = td.find(lambda n: n.has_class("sdc-site-table__name-target"))
                    vis = _norm((nt or td).text()).strip("*").strip()
                    a = td.find(lambda n: n.tag == "a" and n.get("aria-label"))
                    lab = _norm(a.get("aria-label")) if a is not None else None
                    if lab and vis and lab != vis:
                        row_err = "team name mismatch shown=%r label=%r" % (vis, lab)
                        break
                    vals["team"] = vis or lab
                    vals["_marked"] = "*" in (nt or td).text()
                else:
                    vals[f] = _to_int(td.text())
                    if vals[f] is None:
                        row_err = "%s not an integer: %r" % (f, td.text())
                        break
            if row_err is not None and row_err.startswith("column mismatch"):
                rep.add("warning", "skipped table: " + row_err, tname, url)
                table_ok = False
                break
            label = "%s row %d (%s)" % (tname, ri, vals.get("team"))
            if row_err is None:
                miss = [f for f in _REQUIRED if vals.get(f) in (None, "")]
                if miss:
                    row_err = "missing %s" % miss
            if row_err is None and vals["p"] != vals["w"] + vals["d"] + vals["l"]:
                row_err = "P != W+D+L (%s)" % vals
            if row_err is None and "gd" in vals and \
                    vals["gd"] != vals["gf"] - vals["ga"]:
                row_err = "GD != GF-GA (%s)" % vals
            if row_err is not None:
                rep.add("warning", "skipped: " + label, row_err, url)
                continue
            if vals["pts"] != 3 * vals["w"] + vals["d"]:
                rep.add("info", "points differ from 3W+D: " + label,
                        "pts=%d, 3W+D=%d%s" % (
                            vals["pts"], 3 * vals["w"] + vals["d"],
                            " (team marked * on Sky - see table key)"
                            if vals.get("_marked") else ""), url)
            rows.append({"comp": comp, "group": group, "pos": vals["pos"],
                         "team": vals["team"], "p": vals["p"], "w": vals["w"],
                         "d": vals["d"], "l": vals["l"], "gf": vals["gf"],
                         "ga": vals["ga"], "pts": vals["pts"]})
        if not table_ok:
            continue
        if not rows:
            rep.add("warning", "table has no parseable rows", tname, url)
            continue
        # A team listed twice (seen on Sky: a phantom all-zero row) cannot be
        # resolved without guessing which row is real -> drop every row of
        # that team and report it; the other rows are kept.
        counts = {}
        for r in rows:
            counts[r["team"]] = counts.get(r["team"], 0) + 1
        dups = sorted(t for t, c in counts.items() if c > 1)
        for t in dups:
            rep.add("warning", "skipped: %s listed %d times in %s" % (
                t, counts[t], tname), "; ".join(
                    "pos %d P%d W%d D%d L%d %d-%d %dpts" % (
                        r["pos"], r["p"], r["w"], r["d"], r["l"], r["gf"],
                        r["ga"], r["pts"]) for r in rows if r["team"] == t), url)
        positions = [r["pos"] for r in rows]
        if positions != list(range(1, len(rows) + 1)):
            rep.add("info", "positions not sequential as shown by Sky",
                    "%s: %s" % (tname, positions), url)
        out.extend(r for r in rows if r["team"] not in dups)
    return out


def table(comp, issues=None):
    """League table(s) for ``comp``."""
    rep = _Reporter(comp, "table", issues)
    try:
        cfg = _comp_cfg(comp)
        if not cfg["table"]:
            rep.add("warning", "no table for this competition",
                    "Sky has no league table for %s" % comp, None)
            return []
        url = "%s/%s-table" % (BASE_URL, cfg["slug"])
        text, final_url = fetch_html(url)
        return parse_table_page(text, comp, final_url, rep)
    finally:
        rep.finish()


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _print_issues(items, stream=sys.stderr, min_level="info"):
    order = {"info": 0, "warning": 1, "error": 2}
    for it in items:
        if order.get(it["level"], 1) >= order[min_level]:
            stream.write("[%s] %s/%s: %s%s\n" % (
                it["level"], it["comp"], it["kind"], it["what"],
                (" - " + it["detail"]) if it["detail"] else ""))


def _selftest():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass
    failures = 0
    summary = []
    for comp in COMPS:
        for kind, fn in (("fixtures", fixtures), ("results", results),
                         ("table", table)):
            issues = []
            try:
                rows = fn(comp, issues=issues)
            except SkyParseError as exc:
                failures += 1
                print("%-12s %-8s ERROR %s" % (comp, kind, exc))
                summary.append((comp, kind, "ERROR"))
                continue
            warn = sum(1 for i in issues if i["level"] != "info")
            info = len(issues) - warn
            note = ""
            if kind == "table" and not COMPS[comp]["table"]:
                note = " (n/a: Sky has no table for this competition)"
            print("%-12s %-8s %4d rows  %d warnings  %d info%s"
                  % (comp, kind, len(rows), warn, info, note))
            for r in rows[:2]:
                print("    " + json.dumps(r, ensure_ascii=False))
            _print_issues(issues, sys.stdout, "warning")
            summary.append((comp, kind, len(rows)))
    print("\nSUMMARY")
    for comp in COMPS:
        cells = [str(n) for c, k, n in summary if c == comp]
        print("  %-12s fixtures=%s results=%s table=%s" % ((comp,) + tuple(cells)))
    return 1 if failures else 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="sky_parse.py", description="Parse Sky Sports football pages.")
    ap.add_argument("command", choices=("fixtures", "results", "table",
                                        "selftest"))
    ap.add_argument("comp", nargs="?", choices=tuple(COMPS))
    ap.add_argument("--months", type=int, default=1,
                    help="extra months to load (ahead for fixtures, back for "
                         "results; default 1)")
    args = ap.parse_args(argv)
    if args.command == "selftest":
        return _selftest()
    if not args.comp:
        ap.error("comp is required for %s" % args.command)
    if args.months < 0 or args.months > 12:
        ap.error("--months must be between 0 and 12")
    issues = []
    try:
        if args.command == "fixtures":
            rows = fixtures(args.comp, months_ahead=args.months, issues=issues)
        elif args.command == "results":
            rows = results(args.comp, months_back=args.months, issues=issues)
        else:
            rows = table(args.comp, issues=issues)
    except SkyParseError as exc:
        _print_issues(issues)
        sys.stderr.write("ERROR: %s\n" % exc)
        return 1
    json.dump(rows, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")
    _print_issues(issues)
    return 0


if __name__ == "__main__":
    sys.exit(main())
