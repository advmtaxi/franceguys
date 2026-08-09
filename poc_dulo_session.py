#!/usr/bin/env python3
"""
dulo.cx session-aware client for POST /api/source.

Flow (verified against the live API, 2026-08-09):
  1. GET  https://dulo.cx/api/session
     -> Set-Cookie: __Host-amri_session=<token>; Max-Age=28800 (8 h)
  2. POST https://dulo.cx/api/source  (Cookie + Origin: https://dulo.gd)
     -> {"sources":[{"url","title","type","quality"}],"count":N}

The session cookie is cached on disk and reused until ~8 h old. If the API
answers session_required (session missing/expired) the client re-mints a
fresh session and retries transparently.

Stdlib only (urllib) -> runs on a bare Ubuntu/Amazon-Linux EC2 box with no
pip installs. Python 3.7+.
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.error

SESSION_URL = "https://dulo.cx/api/session"
SOURCE_URL = "https://dulo.cx/api/source"
ORIGIN = "https://dulo.gd"
SESSION_TTL = 28800  # seconds, matches Max-Age in Set-Cookie
DEFAULT_CACHE = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "dulo_session.json",
)
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def _open(method, url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("User-Agent", UA)
    req.add_header("Accept", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    return urllib.request.urlopen(req, timeout=30)


def mint_session():
    """GET /api/session and return (cookie_string, issued_at)."""
    resp = _open("GET", SESSION_URL)
    cookie_hdr = resp.headers.get("Set-Cookie", "")
    for part in cookie_hdr.split(","):
        part = part.strip()
        if part.startswith("__Host-amri_session="):
            token = part.split("=", 1)[1].split(";", 1)[0]
            return f"__Host-amri_session={token}", time.time()
    raise RuntimeError("no __Host-amri_session cookie in /api/session response")


def load_cookie():
    """Return cached cookie if still inside its 8 h window, else None."""
    try:
        with open(DEFAULT_CACHE, "r", encoding="utf-8") as fh:
            cached = json.load(fh)
        if time.time() - cached["issued_at"] < SESSION_TTL - 60:
            return cached["cookie"]
    except (OSError, KeyError, ValueError):
        pass
    return None


def save_cookie(cookie):
    os.makedirs(os.path.dirname(DEFAULT_CACHE), exist_ok=True)
    with open(DEFAULT_CACHE, "w", encoding="utf-8") as fh:
        json.dump({"cookie": cookie, "issued_at": time.time()}, fh)


def get_sources(payload):
    """POST /api/source, re-minting the session on session_required."""
    cookie = load_cookie()
    if cookie is None:
        cookie, _ = mint_session()
        save_cookie(cookie)

    body = json.dumps(payload).encode("utf-8")

    for attempt in range(4):
        headers = {
            "Content-Type": "application/json",
            "Origin": ORIGIN,
            "Referer": ORIGIN + "/",
            "Cookie": cookie,
        }
        try:
            resp = _open("POST", SOURCE_URL, data=body, headers=headers)
            return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                err = json.loads(raw)
            except ValueError:
                err = {"raw": raw[:200]}
            reason = err.get("error") or err.get("reason") or ""
            if reason == "session_required" or exc.code in (401, 403):
                print(f"[*] session_required (HTTP {exc.code}) -> re-minting session", file=sys.stderr)
                cookie, _ = mint_session()
                save_cookie(cookie)
                time.sleep(1)
                continue
            print(f"[!] API error (HTTP {exc.code}): {json.dumps(err)[:400]}", file=sys.stderr)
            return err
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"[!] network error: {exc}", file=sys.stderr)
            time.sleep(2)
    raise RuntimeError("gave up after repeated session_required responses")


def main():
    ap = argparse.ArgumentParser(description="dulo.cx session-aware /api/source client")
    ap.add_argument("--type", choices=["movie", "tv", "anime"], help="required unless --mint-only")
    ap.add_argument("--tmdb-id", type=int, help="required unless --mint-only")
    ap.add_argument("--season", type=int, help="required when --type tv")
    ap.add_argument("--episode", type=int, help="required when --type tv")
    ap.add_argument("--json", action="store_true", help="print raw API JSON")
    ap.add_argument("--mint-only", action="store_true", help="just mint/cache a session and exit")
    args = ap.parse_args()

    if args.mint_only:
        cookie, _ = mint_session()
        save_cookie(cookie)
        print(f"session minted, cached in {DEFAULT_CACHE} (valid ~8 h)")
        return

    if not args.type or args.tmdb_id is None:
        ap.error("--type and --tmdb-id are required (unless --mint-only)")
    payload = {"type": args.type, "tmdbId": args.tmdb_id}
    if args.type == "tv":
        if args.season is None or args.episode is None:
            ap.error("--season and --episode are required for --type tv")
        payload["season"] = args.season
        payload["episode"] = args.episode
    elif args.type == "anime":
        if args.season is not None and args.episode is not None:
            payload["season"] = args.season
            payload["episode"] = args.episode

    data = get_sources(payload)
    if args.json:
        print(json.dumps(data, indent=2))
        return

    if data.get("error"):
        print(f"error: {data.get('error')} - {data.get('message', '')}")
        sys.exit(1)
    print(f"{data.get('count', 0)} source(s):")
    for s in data.get("sources", []):
        print(f"  [{s.get('quality', '?')}] {s.get('title', '?')}")
        print(f"    {s.get('url')}")


if __name__ == "__main__":
    main()
