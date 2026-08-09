# dulo.cx — Unofficial API Documentation

2026-08-09. The streaming frontend lives at
**https://dulo.gd** (dulo.tv / dulo.cx are mirror hosts). The API is fronted by
Cloudflare and runs Express.

---

## 1. Overview

| Item | Value |
|---|---|
| Base URL (API) | `https://dulo.cx` | 
| Frontend origin | `https://dulo.gd` |
| Content type | `application/json` |
| Auth model | Anonymous session cookie (no signup / no Supabase auth) |
| Session lifetime | 8 hours (`Max-Age=28800`) |
| Rate limiting | Revealed via `X-RateLimit-Remaining` response header |

**Session flow (in short):**

1. `GET /api/session` → server sets the `__Host-amri_session` cookie.
2. Send that cookie (plus `Origin: https://dulo.gd`) with the source request.
3. If the cookie is missing/expired → `session_required`; mint a new one and retry.

---

## 2. Authentication & Sessions

### 2.1 Mint a session

```
GET /api/session
```

No body, no origin requirement.

**Response: `200 OK`**

```json
{ "ok": true }
```

**Set-Cookie:**

```
__Host-amri_session=<token>; Path=/; SameSite=Lax; Secure; Max-Age=28800; HttpOnly
```

- Cookie is **HttpOnly** — only usable via HTTP requests, not readable by client JS.
- Once minted, a session is good for ~8 hours. Sessions do **not** invalidate each
  other — you may safely cache one cookie and reuse it until expiry.
- The `/api/session` endpoint responds `200` even when called repeatedly; each call
  just issues a fresh cookie.

### 2.2 Required request details

| Requirement | Value |
|---|---|
| Cookie header | `Cookie: __Host-amri_session=<token>` |
| Origin header | `Origin: https://dulo.gd` (missing/wrong → `forbidden_origin`) |
| Referer header | `Referer: https://dulo.gd/` (frontend sends it; harmless to include) |
| Content-Type | `application/json` |
| User-Agent | Any browser-like UA works |

> ⚠️ Without the `Origin` header the API answers `{"error":"forbidden_origin"}` even
> with a valid session cookie.

---

## 3. Endpoints

### 3.1 POST /api/source — resolve streaming sources

Primary endpoint. Returns the playable `.m3u8` / stream URLs for a title.

```
POST /api/source
```

**Request headers:**

```
Content-Type: application/json
Origin: https://dulo.gd
Referer: https://dulo.gd/
Cookie: __Host-amri_session=<token>
```

**Request bodies:**

| `type` | `tmdbId` | `season` | `episode` | Notes |
|---|---|---|---|---|
| `movie` | required | — | — | Single film. |
| `tv` | required | required | required | Episode-level HLS urls. |
| `anime` | required | optional | optional | Behavior mirrors `tv` when season/episode supplied. |

Examples:

```json
{ "type": "movie", "tmdbId": 969681 }

{ "type": "tv", "tmdbId": 1396, "season": 1, "episode": 1 }
```

Any other `type` value → `{"error":"invalid_source_request"}`.

**Response: `200 OK`**

```json
{
  "requestId": "4c5aca02-7ef2-445a-942a-7b44a9905f42",
  "sources": [
    {
      "url": "https://pub-...r2.dev/spider-man-brand-new-day-969681-1080p/index-v2.m3u8",
      "title": "Source 1",
      "type": "hls",
      "quality": "auto"
    },
    {
      "url": "https://dulo.gd/api/artemis-hls/39521080/playlist.m3u8",
      "title": "Source 2",
      "type": "hls",
      "quality": "4K"
    }
  ],
  "count": 2,
  "attempts": [
    { "position": 2, "outcome": "disabled", "sourceCount": 0, "durationMs": 0 }
  ],
  "cacheHit": false,
  "durationMs": 80
}
```

**Response fields:**

| Field | Type | Description |
|---|---|---|
| `requestId` | string | Echo of the server-side request id. |
| `sources[].url` | string | Full stream url (`type: "hls"` → m3u8 playlist). |
| `sources[].title` | string | `"Source N"` label. |
| `sources[].type` | string | `hls`. |
| `sources[].quality` | string | `auto`, `1080p`, `4K`, ... |
| `count` | int | Number of usable sources. |
| `attempts[]` | array | Per-provider outcome: `success`, `disabled`, `empty` (+ `durationMs`). |
| `cacheHit` | bool | Whether a cached response was served. |
| `durationMs` | int | Server-side resolution time. |

**Streaming notes:**

- `dulo.gd/api/artemis-hls/<id>/playlist.m3u8` playlists are **public** — no session
  cookie needed once you have the url.
- R2 (Cloudflare) mirror playlists are likewise public.
- `vidapi-*-proxy.welikewater.workers.dev` urls are direct, long-lived 1080p sources.

### 3.2 GET /api/session — see §2.1

---

## 4. Errors

| HTTP | `error` / `reason` | Meaning | Handling |
|---|---|---|---|
| 403 | `session_required` (± `X-Dulo-Refresh-Required: 1`) | Session missing/expired. | Mint a new session, retry. |
| 403 | `forbidden_origin` | Missing or wrong `Origin` header. | Send `Origin: https://dulo.gd`. |
| 400 | `invalid_source_request` | Bad `type` / missing `season`+`episode`. | Fix payload. |
| 404 | — | Unknown route. | — |

Error bodies look like:

```json
{ "error": "session_required", "reason": "session_required",
  "message": "Your session expired. Refresh the page to continue.",
  "refreshRequired": true, "sources": [], "count": 0 }
```

---

## 5. Rate limiting

- Response header `X-RateLimit-Remaining` reports remaining budget (≈270 observed on a
  fresh session).
- Reusing one cached session keeps you far below the limit; mint a new session only
  when you get `session_required`.
- All traffic passes through Cloudflare — abuse can trigger CF challenges (403
  `cf-mitigated`). Keep volumes modest and use a stable egress IP.

---

## 6. Reference: bash + Python

### cURL

```bash
# 1) Mint session (saves cookie jar)
curl -c cookies.txt https://dulo.cx/api/session

# 2) Resolve movie sources
curl -X POST https://dulo.cx/api/source \
  -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "Origin: https://dulo.gd" \
  -d '{"type":"movie","tmdbId":969681}'

# 3) Resolve a TV episode
curl -X POST https://dulo.cx/api/source \
  -b cookies.txt \
  -H "Content-Type: application/json" \
  -H "Origin: https://dulo.gd" \
  -d '{"type":"tv","tmdbId":1396,"season":1,"episode":1}'
```

### Python (stdlib only — no pip installs)

```python
import json, time, urllib.request

SESSION_URL = "https://dulo.cx/api/session"
SOURCE_URL  = "https://dulo.cx/api/source"
ORIGIN      = "https://dulo.gd"
UA          = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"

def mint_cookie():
    req = urllib.request.Request(SESSION_URL, headers={"User-Agent": UA})
    hdr = urllib.request.urlopen(req, timeout=30).headers.get("Set-Cookie", "")
    for part in hdr.split(","):
        part = part.strip()
        if part.startswith("__Host-amri_session="):
            return part.split("=", 1)[1].split(";", 1)[0]
    raise RuntimeError("no session cookie")

def get_sources(payload, token):
    body = json.dumps(payload).encode()
    req = urllib.request.Request(SOURCE_URL, data=body, method="POST", headers={
        "User-Agent": UA,
        "Content-Type": "application/json",
        "Origin": ORIGIN,
        "Referer": ORIGIN + "/",
        "Cookie": f"__Host-amri_session={token}",
    })
    return json.loads(urllib.request.urlopen(req, timeout=30).read())

token = mint_cookie()
print(get_sources({"type": "movie", "tmdbId": 969681}, token))
```

---

## 7. Appendix — observed server behaviour (notes for API consumers)

- `X-Powered-By: Express` → Node/Express backend behind Cloudflare.
- Cache headers: session responses `no-store`, source responses `private, no-store`.
- Same `__Host-` prefix means the cookie is bound to the host (sent only to
  `dulo.cx`), path-locked to `/`.
- The site is bilingual (i18n bundles) — API takes no `Accept-Language`-dependent
  parameters.
- tvdb-style `season`/`episode` are 1-indexed; `S01 E01` == `1,1`.
