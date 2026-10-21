"""Generic JSON endpoint reader — point it at any API and describe the shape.

Lots of venues have an undocumented JSON endpoint behind their agenda page.
Rather than a bespoke module each time, describe the response in sources.toml:

  [[source]]
  id = "somevenue"
  adapter = "json_api"
  url = "https://example.nl/api/events?page={page}"
  pages = [1, 2, 3]
  list_path = "data.items"            # dotted path to the array; "" = root
  [source.fields]                     # our field  <-  dotted path in their JSON
  title = "attributes.title"
  start = "attributes.startsAt"
  url   = "attributes.permalink"
  venue = "attributes.location.name"
  image = "attributes.image.src"

A date and a clock in two separate fields (the shape every ACF/WordPress
agenda has) is `fields.start_time` — it is folded into a date-only `start`:

  start      = "acf.datum"           # 20270518
  start_time = "acf.starttijd"       # 20:30:00

`stop_on_page_error = true` ends the page walk on the first page that fails
instead of failing the source, for an API that answers a page past the end with
an error rather than an empty list (WordPress returns HTTP 400). The first page
still has to work, so a dead host is still a failed source, not a silent zero.

A Next.js page is an API too: its server-rendered state sits in a
<script id="__NEXT_DATA__"> block. `embedded = "next_data"` reads the JSON out
of the page instead of asking for JSON. That state is a client cache, not a
list, and where the events sit in it depends on what the page happened to load
(Bash.social keeps them under the fourth query of a react-query dump), so
`list_path = "*"` takes every object anywhere in the tree that has both the
title and the start field - the shape is the address.

`build.py --probe <id>` prints what came back so you can iterate on the paths.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from ..model import Event, classify, parse_dt
from ..net import FetchError

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)

FIELDS = ("title", "start", "start_time", "end", "venue", "city", "url", "image",
          "summary", "price", "category")


def _dig(node, path: str):
    if not path:
        return node
    for part in path.split("."):
        if node is None:
            return None
        if part.endswith("]") and "[" in part:
            key, _, idx = part.partition("[")
            if key:
                node = node.get(key) if isinstance(node, dict) else None
            try:
                node = node[int(idx.rstrip("]"))]
            except (TypeError, IndexError, KeyError, ValueError):
                return None
        elif isinstance(node, dict):
            node = node.get(part)
        elif isinstance(node, list) and part.isdigit():
            node = node[int(part)] if int(part) < len(node) else None
        else:
            return None
    return node


def fetch(cfg: dict, http) -> list[Event]:
    templates = cfg.get("urls") or [cfg["url"]]
    pages = cfg.get("pages") or [None]
    fields = {k: v for k, v in (cfg.get("fields") or {}).items() if k in FIELDS}
    if "title" not in fields or "start" not in fields:
        raise ValueError("json_api needs at least `fields.title` and `fields.start`")

    cmap = {k.lower(): v for k, v in (cfg.get("category_map") or {}).items()}
    base = cfg.get("base_url") or templates[0]
    stop_on_page_error = bool(cfg.get("stop_on_page_error"))
    out: list[Event] = []

    for template in templates:
        for page in pages:
            url = template.replace("{page}", str(page)) if page is not None else template
            try:
                payload = _load(http, url, cfg)
            except FetchError:
                # Walking one page past the end is how you find the end on an
                # API that errors instead of answering with []. Only once
                # something has already been read: page one failing is the host
                # being down, and that is a failed source, not an empty one.
                if stop_on_page_error and out:
                    break
                raise
            if cfg.get("list_path") == "*":
                rows = list(_records(payload, fields["title"], fields["start"]))
            else:
                rows = _dig(payload, cfg.get("list_path", ""))
            if isinstance(rows, dict):
                rows = list(rows.values())
            for row in rows or []:
                ev = _to_event(row, fields, cfg, cmap, base)
                if ev:
                    out.append(ev)
            if not rows:
                break
    return out


def _load(http, url: str, cfg: dict):
    if cfg.get("embedded") != "next_data":
        return http.get_json(url, headers=cfg.get("headers"), ttl=cfg.get("ttl"))
    body = http.get(url, headers=cfg.get("headers"), ttl=cfg.get("ttl"))
    match = _NEXT_DATA.search(body)
    if not match:
        raise ValueError("no __NEXT_DATA__ on the page")
    return json.loads(match.group(1))


def _records(node, *paths: str):
    """Every object in the tree on which all of `paths` resolve, once each."""
    seen: set[str] = set()
    stack = [node]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            if all(_dig(node, p) not in (None, "") for p in paths):
                key = json.dumps([_dig(node, p) for p in paths], default=str)
                if key not in seen:
                    seen.add(key)
                    yield node
                continue
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)


def _to_event(row, fields: dict, cfg: dict, cmap: dict, base: str) -> Event | None:
    get = lambda name: _dig(row, fields[name]) if name in fields else None  # noqa: E731
    start, all_day = parse_dt(get("start"))
    if not start:
        return None
    # A date-only `start` plus a separate clock field is one timed event, not an
    # all-day one. Keyed on all_day rather than on the length of `start`:
    # parse_dt pads a bare date out to midnight, so a date-only value is
    # already 16 characters by the time it gets here. Only fills in a time that
    # is missing — a `start` that carries its own is the more specific field.
    clock = get("start_time")
    if all_day and clock:
        stamped, _ = parse_dt(f"{start[:10]} {str(clock)[:5]}")
        if stamped:
            start, all_day = stamped, False

    href, image = get("url"), get("image")
    category = get("category")
    if isinstance(category, str):
        category = [category]

    return Event(
        title=str(get("title") or ""),
        start=start,
        end=parse_dt(get("end"))[0],
        all_day=all_day,
        venue=get("venue") or cfg.get("default_venue"),
        city=get("city") or cfg.get("default_city"),
        categories=classify(get("title"), get("summary"), mapping=cmap,
                            given=[str(c) for c in (category or [])]),
        url=urljoin(base, href) if isinstance(href, str) else None,
        image=urljoin(base, image) if isinstance(image, str) else None,
        summary=get("summary"),
        price=str(get("price")) if get("price") not in (None, "") else None,
    )
