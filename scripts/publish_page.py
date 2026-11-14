#!/usr/bin/env python3
"""Put the app page into docs/index.html with a fresh version stamp and write docs/version.txt.

The page checks version.txt when the home-screen app comes to the front and reloads itself when the stamp is
newer, so phones never keep showing an old version. Usage: python3 scripts/publish_page.py [page.html]
"""
import datetime as dt
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = sys.argv[1] if len(sys.argv) > 1 else os.path.expanduser("~/.typownik/app/typownik.html")
page = open(src, encoding="utf-8").read()
assert 'const BUILD="__BUILD__"' in page, "page has no BUILD placeholder"
stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M%S")
out = os.path.join(REPO, "docs", "index.html")
old = open(out, encoding="utf-8").read()
head = old[:old.index("</head><body>\n") + len("</head><body>\n")]  # meta tags, icons and manifest stay as they are
open(out, "w", encoding="utf-8").write(head + page.replace('const BUILD="__BUILD__"', f'const BUILD="{stamp}"', 1) + "\n</body></html>\n")
open(os.path.join(REPO, "docs", "version.txt"), "w").write(stamp + "\n")
print("opublikowano wersję", stamp)
