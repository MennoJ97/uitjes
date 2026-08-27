"""Paradiso — and the rest of the venues Paradiso programmes.

The site is a Next.js app with no JSON-LD, no feed, and no /agenda page; the
programme arrives client-side from a GraphQL endpoint on AWS API Gateway. The
bearer token below is not a secret we were given: it is baked into their public
JS bundle (search the chunks for `execute-api`), so this asks for the programme
exactly the way a visitor's browser does. When it eventually rotates this source
starts failing loudly in the build report, and the fix is to pull the new one
out of the bundle — or to set it in the environment, see `token_env`.

Bespoke rather than `json_api` for three reasons:
  * it is a POST with a query body, and json_api only does GET,
  * paging is a cursor — `searchAfter` takes the previous page's last event's
    `sort` array — so page N+1 cannot be written as a URL up front,
  * venue and genre arrive as arrays of objects, not scalars.

Two things about the endpoint that cost an afternoon, so they are written down:
`site` MUST be null (passing "paradiso", or any other handle, 500s), and
`gteStartDateTime` must be a full ISO datetime — a bare date 500s too.

The feed is wider than the building: Paradiso books Tolhuistuin, Bitterzoet,
Zonnehuis, Cinetol and a dozen others, and hands them over in one list. Each
event names its own venue and that is what gets recorded — the source label
says who published the programme, `venue` says where to turn up.

Config keys: url, token, token_env, base_url, horizon_days, page_size,
max_pages, skip_statuses, image_type, default_venue, default_city,
category_map, fallback_categories
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta

from ..model import Event, classify, parse_dt

ENDPOINT = "https://knwxh8dmh1.execute-api.eu-central-1.amazonaws.com/graphql"

# Trimmed to the fields we record. Their own query also pulls twenty image
# transforms; one is enough, and `site` is left out entirely because the only
# value the resolver accepts is the null default.
QUERY = """
query programItemsQuery($size: Int, $gteStartDateTime: String,
                        $lteStartDateTime: String, $searchAfter: [String]) {
  program(size: $size, gteStartDateTime: $gteStartDateTime,
          lteStartDateTime: $lteStartDateTime, searchAfter: $searchAfter) {
    events {
      id
      uri
      title
      subtitle
      startDateTime
      sort
      eventStatus
      location { title }
      contentCategory { title }
      image { type desktopWebp }
    }
  }
}
"""


def fetch(cfg: dict, http) -> list[Event]:
    endpoint = cfg.get("url", ENDPOINT)
    token = os.environ.get(cfg.get("token_env", "PARADISO_API_TOKEN"), "").strip() \
        or str(cfg.get("token", "")).strip()
    if not token:
        raise RuntimeError("no Paradiso API token in config or environment")

    horizon = int(cfg.get("horizon_days", 120))
    size = int(cfg.get("page_size", 100))
    max_pages = int(cfg.get("max_pages", 20))
    # "canceled" is their spelling. A postponed show still carries its old date,
    # which is worse than absent: it reads as a night out that is not happening.
    skip = {s.lower() for s in cfg.get("skip_statuses") or ["canceled", "postponed"]}
    image_type = cfg.get("image_type", "relatedArtists")
    cmap = {k.lower(): v for k, v in (cfg.get("category_map") or {}).items()}
    base = cfg.get("base_url", "https://www.paradiso.nl/nl/")

    today = date.today()
    window = {
        "gteStartDateTime": datetime.combine(today, datetime.min.time()).isoformat() + "Z",
        "lteStartDateTime": (datetime.combine(today + timedelta(days=horizon),
                                              datetime.max.time())).isoformat() + "Z",
    }

    out: list[Event] = []
    after = None
    for _ in range(max_pages):
        variables = {"size": size, **window}
        if after:
            variables["searchAfter"] = after
        payload = http.get_json(
            endpoint,
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            ttl=cfg.get("ttl"),
            data=json.dumps({"query": QUERY, "variables": variables}).encode(),
        )
        # GraphQL answers 200 with an `errors` array, so a failure here would
        # otherwise look like an empty programme rather than a broken source.
        if payload.get("errors"):
            raise RuntimeError(f"GraphQL error: {payload['errors'][0].get('message')}")
        rows = ((payload.get("data") or {}).get("program") or {}).get("events") or []
        for row in rows:
            ev = _to_event(row, cfg, cmap, base, skip, image_type)
            if ev:
                out.append(ev)
        if len(rows) < size:
            break
        after = rows[-1].get("sort")
        if not after:
            break
    return out


def _first(rows, key: str) -> str | None:
    """Their one-to-many fields are one-to-one in practice — 431 of 432 events
    carry exactly one location — so take the first and keep the record flat."""
    for row in rows or []:
        value = (row or {}).get(key)
        if value:
            return value
    return None


def _image(rows, image_type: str) -> str | None:
    """Every event ships the same picture in five crops, and the array is
    ordered by their layout, not by ours. `relatedArtists` is the 103x70 one:
    the card renders it at 84x56, and the only other landscape crop on offer is
    a 1920x1080 hero — which, on a page that lists a thousand events and does
    not lazy-load, would be several megabytes of thumbnail."""
    for row in rows or []:
        if (row or {}).get("type") == image_type and row.get("desktopWebp"):
            return row["desktopWebp"]
    return _first(rows, "desktopWebp")


def _to_event(row: dict, cfg: dict, cmap: dict, base: str, skip: set[str],
              image_type: str) -> Event | None:
    if (row.get("eventStatus") or "").lower() in skip:
        return None
    start, all_day = parse_dt(row.get("startDateTime"))
    if not start:
        return None

    title = (row.get("title") or "").strip()
    if not title:
        return None

    genres = [g["title"] for g in row.get("contentCategory") or []
              if isinstance(g, dict) and g.get("title")]
    href = row.get("uri")
    return Event(
        title=title,
        start=start,
        all_day=all_day,
        # The venue is per event, not per source: this feed spans twenty rooms.
        venue=_first(row.get("location"), "title") or cfg.get("default_venue"),
        city=cfg.get("default_city"),
        categories=classify(title, row.get("subtitle"), mapping=cmap, given=genres),
        url=f"{base}{href}" if href else None,
        image=_image(row.get("image"), image_type),
        summary=row.get("subtitle"),
    )
