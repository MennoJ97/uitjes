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

`build.py --probe <id>` prints what came back so you can iterate on the paths.
"""
from __future__ import annotations

from urllib.parse import urljoin

from ..model import Event, classify, parse_dt

FIELDS = ("title", "start", "end", "venue", "city", "url", "image", "summary",
          "price", "category")


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
    out: list[Event] = []

    for template in templates:
        for page in pages:
            url = template.replace("{page}", str(page)) if page is not None else template
            payload = http.get_json(url, headers=cfg.get("headers"), ttl=cfg.get("ttl"))
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


def _to_event(row, fields: dict, cfg: dict, cmap: dict, base: str) -> Event | None:
    get = lambda name: _dig(row, fields[name]) if name in fields else None  # noqa: E731
    start, all_day = parse_dt(get("start"))
    if not start:
        return None

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
