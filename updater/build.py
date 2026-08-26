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
    # Write via a temp file in the same directory and rename, so nginx never
    # serves a half-written JSON to someone loading the page mid-build.
    tmp_path = out_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(dataset, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp_path, out_path)

    counts = dataset["counts"]
    print(f"\n{GREEN}{counts['total']} events{OFF} after filtering "
          f"({dataset['horizon_days']}-day horizon)")
    print("  by city:     " + ", ".join(f"{k} {v}" for k, v in
                                        sorted(counts["by_city"].items(),
                                               key=lambda kv: -kv[1])))
    print("  by category: " + ", ".join(f"{k} {v}" for k, v in
                                        sorted(counts["by_category"].items(),
                                               key=lambda kv: -kv[1])))
    kept_lines = [r for r in reports if r["kept"] or (r["error"] and r["error"] != "disabled")]
    if kept_lines:
        print("  kept per source: " + ", ".join(f"{r['id']} {r['kept']}" for r in kept_lines))
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
