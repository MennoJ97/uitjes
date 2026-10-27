# uitjes

An agenda of what's on in Amsterdam and Utrecht: concerts, theatre, comedy,
exhibitions, markets and pub quizzes, gathered from 25 venue and
city-agenda sources (plus hand-entered rules) into one filterable page. Live at
[uitjes.mhjansen.nl](https://uitjes.mhjansen.nl), in Dutch with an English
version under [/en](https://uitjes.mhjansen.nl/en).

Visitors can also save a filter and get a mail when a new matching event is
announced (double opt-in, one-click unsubscribe).

## Layout

| Path       | What it is |
|------------|------------|
| `updater/` | The builder (`build.py`, the `uitradar` package with one adapter per kind of source, `sources.toml`) and the mail-alerts service (`alerts.py`, `uitalerts/`). Python 3.12 standard library only. One Docker image runs both. |
| `web/`     | The hand-written page: `index.html`, `app.js`, `style.css`, the alerts page `meldingen.html`/`meldingen.js`, icons. |
| `nginx/`   | The nginx site config: cache headers, SSI for the per-city and per-language pages, redirects. |

The builder writes everything generated into one output directory, which is
mounted over `web/data/`:

- `events.json`, which `app.js` filters in the browser
- `pages/{all,amsterdam,utrecht}.html` (and `pages/en/`), the first days of the
  agenda as HTML plus Event JSON-LD, included by nginx SSI so crawlers see events
  without running scripts
- `sitemap.xml`

Nothing in `web/` is generated. `index.html` references `app.js?v=…` and
`style.css?v=…`. The hash is the first 8 hex characters of the file's sha256
(`shasum -a256 style.css | cut -c1-8`), and you update it by hand after editing
either file.

## Running it

Three containers:

| Container        | Image / command | Mounts |
|------------------|-----------------|--------|
| `uitjes`         | `nginx-unprivileged`, port 80 | `web/` → `/usr/share/nginx/html` (ro), output dir → `/usr/share/nginx/html/data` (ro), `nginx/` → `/etc/nginx/conf.d` (ro) |
| `uitjes-updater` | built from `updater/`; supercronic rebuilds at 06:20, 13:20 and 20:20 | output dir → `/site/data`, a cache dir → `/work/data/cache` |
| `uitjes-alerts`  | same image, `python alerts.py serve` on :8080 | output dir → `/site/data` (ro), a data dir → `/data` (sqlite, token key, outbox) |

Route `/api/alerts/` on the site's host to `uitjes-alerts`, and everything else
to `uitjes`.

Environment:

- both: `TZ=Europe/Amsterdam` (supercronic reads it; without it the schedule
  runs on UTC)
- updater: `TICKETMASTER_API_KEY` (optional; that source is disabled by default),
  `PARADISO_API_TOKEN` (optional override of the token in `sources.toml`)
- alerts: `ALERTS_BASE_URL`, `SMTP_HOST`/`SMTP_PORT`/`SMTP_USER`/`SMTP_PASSWORD`/
  `SMTP_FROM`, `ALERTS_CONTACT`, `ALERTS_ADMIN`, `ALERTS_STATS_FILE`,
  `ALERTS_UMAMI_WEBSITE`. Without `SMTP_HOST`, mails go to `<data>/outbox` and
  the sign-up form stays hidden. See the docstring of `updater/alerts.py`.

### A build without touching the live site

```sh
cd updater
python build.py --list                  # the source registry
python build.py --probe <source>        # fetch and parse one source
python build.py --out /tmp/events.json  # a full build
```

A full build compares itself against the `events.json` already at `--out` and
refuses to publish if a source loses half its events (`--force` overrides). A
source that cannot be reached keeps its last events for up to three days.

## Adding a source

Most sources are configuration only: an entry in `updater/sources.toml` using a
generic adapter (`json_api`, `jsonld`, `regex_cards`, `ics`, `rss`, `tribe`,
`weeztix`, …). The comments in that file record what each source needed and why.
Venues that publish nothing machine-readable can be entered as rules in
`updater/data/manual/`.
