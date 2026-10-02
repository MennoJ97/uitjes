#!/usr/bin/env python3
"""UITradar build script — fetch every enabled source and write web/data/events.json.

  python3 build.py                 build everything that is enabled
  python3 build.py --list          show the source registry and its status
  python3 build.py --only tivoli   build a subset (repeatable / comma-separated)
  python3 build.py --probe tivoli  dump what one source returns, no writing
  python3 build.py --refresh       bypass the HTTP cache
  python3 build.py --serve         build, then serve web/ on http://localhost:8765
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from uitradar.net import Http  # noqa: E402
from uitradar.pipeline import assemble, load_config, run_source  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
GREEN, RED, DIM, YEL, OFF = "\033[32m", "\033[31m", "\033[2m", "\033[33m", "\033[0m"


def write_json(dataset: dict, path: str) -> None:
    """Write via a temp file in the same directory and rename, so nginx never
    serves a half-written JSON to someone loading the page mid-build."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(dataset, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, path)


def record_refusal(out_path: str, dataset: dict, problems: list[str]) -> None:
    """Stamp a refused build onto the live file, leaving its events alone.

    The page's Bronnen panel reads `sources` from the published file, so after a
    refusal it went on showing the last good build: Kleine Komedie read "ok" for
    four days (2026-09-18..22) while every run was rejected over it. Only
    `last_attempt` changes here - `sources` stays the published build's, because
    guard() compares the next run against it.
    """
    try:
        with open(out_path, encoding="utf-8") as fh:
            live = json.load(fh)
    except (OSError, ValueError):
        return
    live["last_attempt"] = {
        "at": dataset["generated_at"],
        "published": False,
        "problems": problems,
        "sources": {r["id"]: {"kept": r.get("kept", 0), "error": r.get("error")}
                    for r in dataset.get("sources", [])},
    }
    write_json(live, out_path)


def guard(dataset: dict, out_path: str, cfg: dict) -> list[str]:
    """Reasons this build should not replace the one already published.

    Only ever compares against the live file, so the first run, a moved output
    path or an unreadable previous build all publish without argument. A source
    that is newly added, or one that is now disabled on purpose, is skipped too:
    the guard is looking for a source that used to work and has gone quiet.
    """
    rules = cfg.get("publish", {})
    floor = int(rules.get("min_events_to_guard", 20))
    source_drop = float(rules.get("source_drop_fraction", 0.5))
    total_drop = float(rules.get("total_drop_fraction", 0.2))

    try:
        with open(out_path, encoding="utf-8") as fh:
            previous = json.load(fh)
    except (OSError, ValueError):
        return []

    was = {r["id"]: r.get("kept", 0) for r in previous.get("sources", [])}
    now = {r["id"]: r for r in dataset.get("sources", [])}
    problems = []

    for sid, before in was.items():
        report = now.get(sid)
        # Below the floor a source is too small to be worth blocking a publish
        # over: Ajax announcing five home games instead of twelve is not a bug.
        if report is None or before < floor:
            continue
        if report.get("error") == "disabled":
            continue
        after = report.get("kept", 0)
        if after < before * source_drop:
            why = report.get("error") or "no error reported, so check the source"
            problems.append(f"{sid}: {before} events last build, {after} now ({why})")

    before_total = (previous.get("counts") or {}).get("total", 0)
    after_total = (dataset.get("counts") or {}).get("total", 0)
    if before_total >= floor and after_total < before_total * (1 - total_drop):
        problems.append(f"total: {before_total} events last build, {after_total} now")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=os.path.join(ROOT, "sources.toml"))
    ap.add_argument("--only", action="append", default=[],
                    help="source id(s) to run; comma-separated or repeated")
    ap.add_argument("--probe", help="run one source and print its raw output")
    ap.add_argument("--list", action="store_true", help="list sources and exit")
    ap.add_argument("--refresh", action="store_true", help="ignore the HTTP cache")
    ap.add_argument("--serve", action="store_true", help="serve web/ after building")
    ap.add_argument("--force", action="store_true",
                    help="publish even if the guard says this build looks broken")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--out", help="where to write events.json "
                                  "(default: web/data/events.json)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    sources = cfg.get("source", [])

    if args.list:
        print(f"{'id':22} {'adapter':14} {'status':9} label")
        for spec in sources:
            state = "on" if spec.get("enabled", True) else "off"
            colour = GREEN if state == "on" else DIM
            print(f"{colour}{spec['id']:22} {spec.get('adapter',''):14} "
                  f"{state:9} {spec.get('label','')}{OFF}")
        return 0

    wanted = {s.strip() for item in args.only for s in item.split(",") if s.strip()}
    if args.probe:
        wanted = {args.probe}

    http = Http(
        cache_dir=os.path.join(ROOT, "data", "cache"),
        ttl=int(cfg.get("http", {}).get("cache_ttl_seconds", 3600)),
        refresh=args.refresh or bool(args.probe),
        timeout=int(cfg.get("http", {}).get("timeout_seconds", 25)),
        delay=float(cfg.get("http", {}).get("delay_seconds", 0.4)),
        retries=int(cfg.get("http", {}).get("retries", 2)),
        retry_backoff=float(cfg.get("http", {}).get("retry_backoff_seconds", 1.0)),
        host_failure_limit=int(cfg.get("http", {}).get("host_failure_limit", 4)),
    )

    defaults = cfg.get("defaults", {})
    all_events, reports = [], []
    for spec in sources:
        if wanted and spec["id"] not in wanted:
            continue
        merged = {**defaults, **spec}
        if args.probe:
            merged["enabled"] = True
        events, report = run_source(merged, http, ROOT)
        all_events.extend(events)
        reports.append(report)

        if report["error"] == "disabled":
            continue
        mark = f"{RED}FAIL{OFF}" if report["error"] else f"{GREEN} ok {OFF}"
        detail = f" {DIM}{report['error']}{OFF}" if report["error"] else ""
        print(f"[{mark}] {report['id']:22} {report['count']:5d} raw  "
              f"{report['seconds']:5.1f}s{detail}")

    if args.probe:
        print(f"\n{YEL}--- first 8 normalised events from {args.probe} ---{OFF}")
        for ev in all_events[:8]:
            print(json.dumps(ev.to_dict(), ensure_ascii=False, indent=2))
        print(f"\n{len(all_events)} events total (unfiltered).")
        return 0

    dataset = assemble(all_events, cfg, reports)
    out_path = args.out or os.path.join(ROOT, "web", "data", "events.json")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    counts = dataset["counts"]
    print(f"\n{GREEN}{counts['total']} events{OFF} after filtering "
          f"({dataset['horizon_days']}-day horizon)")
    print("  by city:     " + ", ".join(f"{k} {v}" for k, v in
                                        sorted(counts["by_city"].items(),
                                               key=lambda kv: -kv[1])))
    print("  by category: " + ", ".join(f"{k} {v}" for k, v in
                                        sorted(counts["by_category"].items(),
                                               key=lambda kv: -kv[1])))
    # A retry that worked leaves no other trace, and a run that needed a lot of
    # them is a slow host worth knowing about before it becomes a failed source.
    if http.retried:
        print(f"  {DIM}{http.retried} request(s) retried{OFF}")

    kept_lines = [r for r in reports if r["kept"] or (r["error"] and r["error"] != "disabled")]
    if kept_lines:
        print("  kept per source: " + ", ".join(f"{r['id']} {r['kept']}" for r in kept_lines))

    # Printed in full rather than counted: a merge takes an event off the site,
    # so every one of them should be readable in the log that made it. The
    # `extra` words are the tell — if a satellite event ("+[nazit]") ever shows
    # up here, that is a rule to add, not a mystery to debug.
    merges = dataset.get("merges") or []
    if merges:
        print(f"\n{YEL}{len(merges)} fuzzy title merges{OFF}")
        for row in merges:
            how = "" if row.get("rule", "title") == "title" else f"[{row['rule']}] "
            print(f"  {DIM}{row['day']}{OFF} {how}+{row['extra']}  "
                  f"{row['kept']} {DIM}({row['kept_source']}){OFF}"
                  f"  <-  {row['dropped']} {DIM}({row['dropped_source']}){OFF}")
    # Compare against what is already published before replacing it. A source
    # that breaks does not fail the build - by design, one bad source degrades
    # the dataset rather than killing it - which on 2026-08-27 quietly published
    # a site missing all 227 Melkweg events. Retries make that rarer; this makes
    # it visible. Refusing to publish is safe: the previous events.json stays,
    # and the next scheduled run tries again in a few hours.
    problems = [] if args.force else guard(dataset, out_path, cfg)
    if problems:
        rejected = out_path + ".rejected"
        write_json(dataset, rejected)
        print(f"\n{RED}NOT PUBLISHED{OFF} - this build looks broken:")
        for line in problems:
            print(f"  - {line}")
        print(f"\n  {out_path} is unchanged and still serving the last good build.")
        print(f"  This one is at {rejected} if you want to look at it.")
        print(f"  {DIM}Re-run with --force to publish it anyway.{OFF}")
        record_refusal(out_path, dataset, problems)
        return 1

    dataset["last_attempt"] = {"at": dataset["generated_at"], "published": True}
    write_json(dataset, out_path)
    print(f"\nwrote {out_path}")

    if args.serve:
        serve(os.path.join(ROOT, "web"), args.port)
    return 0


def serve(directory: str, port: int) -> None:
    import functools
    import http.server
    import socketserver

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", port), handler) as httpd:
        print(f"\nserving {directory} on http://localhost:{port}  (ctrl-c to stop)")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nbye")


if __name__ == "__main__":
    raise SystemExit(main())
