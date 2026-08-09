import asyncio
import json
import logging
import time
import urllib.parse
import re
from typing import Optional, Any, Dict

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import httpx
import uvicorn

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SESSION_URL = "https://dulo.cx/api/session"
SOURCE_URL = "https://dulo.cx/api/source"
ORIGIN = "https://dulo.gd"
SESSION_TTL = 28800  # 8 hours

PROXY_BASE_URL = "https://apidulo.b-cdn.net/api/proxy?url="

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

app = FastAPI(title="Dulo API Wrapper", description="Lag-resistant API for Dulo.cx with Proxy")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state for session management
session_cookie: Optional[str] = None
session_issued_at: float = 0
session_lock = asyncio.Lock()


class SourceRequest(BaseModel):
    type: str = Field(..., description="movie, tv, or anime")
    tmdbId: int = Field(..., description="TMDB ID of the title")
    season: Optional[int] = Field(None, description="Season number (required for tv)")
    episode: Optional[int] = Field(None, description="Episode number (required for tv)")


async def mint_session(client: httpx.AsyncClient) -> str:
    """Fetch a new session cookie."""
    logger.info("Minting new session...")
    headers = {
        "User-Agent": UA,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": ORIGIN,
        "Referer": f"{ORIGIN}/"
    }
    try:
        resp = await client.get(SESSION_URL, headers=headers, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error(f"Failed to mint session (HTTP {e.response.status_code}): {e.response.text}")
        raise
    
    cookie_hdr = resp.headers.get("Set-Cookie", "")
    for part in cookie_hdr.split(","):
        part = part.strip()
        if part.startswith("__Host-amri_session="):
            token = part.split("=", 1)[1].split(";", 1)[0]
            return f"__Host-amri_session={token}"
    
    logger.error(f"No cookie in response. Headers: {resp.headers}")
    raise RuntimeError("No __Host-amri_session cookie in /api/session response")


async def get_valid_cookie(client: httpx.AsyncClient) -> str:
    """Get the current session cookie, minting a new one if expired or missing. Uses async lock."""
    global session_cookie, session_issued_at

    # Fast path if cookie is valid
    if session_cookie and (time.time() - session_issued_at < SESSION_TTL - 60):
        return session_cookie

    async with session_lock:
        # Check again in case another task updated it while we were waiting for the lock
        if session_cookie and (time.time() - session_issued_at < SESSION_TTL - 60):
            return session_cookie

        cookie = await mint_session(client)
        session_cookie = cookie
        session_issued_at = time.time()
        return session_cookie


@app.post("/api/source")
async def get_sources(req: SourceRequest):
    payload = {"type": req.type, "tmdbId": req.tmdbId}
    if req.type == "tv":
        if req.season is None or req.episode is None:
            raise HTTPException(status_code=400, detail="season and episode are required for tv")
        payload["season"] = req.season
        payload["episode"] = req.episode
    elif req.type == "anime":
        if req.season is not None and req.episode is not None:
            payload["season"] = req.season
            payload["episode"] = req.episode
    elif req.type != "movie":
        raise HTTPException(status_code=400, detail="invalid type. Must be movie, tv, or anime")

    async with httpx.AsyncClient() as client:
        try:
            cookie = await get_valid_cookie(client)
        except Exception as exc:
            logger.error(f"Failed to get valid cookie: {exc}")
            raise HTTPException(status_code=502, detail=f"Failed to obtain session from upstream: {str(exc)}")

        for attempt in range(4):
            headers = {
                "User-Agent": UA,
                "Content-Type": "application/json",
                "Origin": ORIGIN,
                "Referer": f"{ORIGIN}/",
                "Cookie": cookie,
                "Accept": "application/json",
            }
            
            try:
                resp = await client.post(SOURCE_URL, json=payload, headers=headers, timeout=30)
                
                # If OK, just return the JSON
                if resp.status_code == 200:
                    data = resp.json()
                    # Just in case API sends 200 with an error object
                    if data.get("error") == "session_required":
                        logger.warning("Got session_required inside a 200 response")
                        # force remint
                        async with session_lock:
                            cookie = await mint_session(client)
                            global session_cookie, session_issued_at
                            session_cookie = cookie
                            session_issued_at = time.time()
                        await asyncio.sleep(1)
                        continue
                        
                    # Rewrite URLs for the proxy
                    for source in data.get("sources", []):
                        if "url" in source:
                            encoded_url = urllib.parse.quote(source["url"], safe="")
                            source["url"] = f"{PROXY_BASE_URL}{encoded_url}"
                            
                    return data
                
                # Check for 403 / 401 session required
                if resp.status_code in (401, 403):
                    try:
                        err = resp.json()
                    except Exception:
                        err = {}
                    reason = err.get("error") or err.get("reason") or ""
                    
                    if reason == "session_required" or resp.status_code in (401, 403):
                        logger.info(f"session_required (HTTP {resp.status_code}) -> re-minting session")
                        async with session_lock:
                            cookie = await mint_session(client)
                            session_cookie = cookie
                            session_issued_at = time.time()
                        await asyncio.sleep(1)
                        continue
                
                # Other errors
                resp.raise_for_status()
                
            except httpx.HTTPStatusError as exc:
                try:
                    err_detail = exc.response.json()
                except Exception:
                    err_detail = exc.response.text
                logger.error(f"API Error HTTP {exc.response.status_code}: {err_detail}")
                if attempt == 3:
                    raise HTTPException(status_code=exc.response.status_code, detail=err_detail)
                await asyncio.sleep(1)
                
            except (httpx.RequestError, httpx.TimeoutException) as exc:
                logger.error(f"Network error: {exc}")
                if attempt == 3:
                    raise HTTPException(status_code=502, detail=f"Upstream API network error: {str(exc)}")
                await asyncio.sleep(2)
                
        raise HTTPException(status_code=500, detail="Gave up after repeated session_required or network errors")


@app.get("/api/proxy")
async def proxy_m3u8(url: str, request: Request):
    """Proxy M3U8 and TS files to bypass CORS."""
    if not url:
        raise HTTPException(status_code=400, detail="Missing url parameter")
    
    client = httpx.AsyncClient(timeout=30.0)
    headers = {
        "User-Agent": UA,
        "Origin": ORIGIN,
        "Referer": f"{ORIGIN}/"
    }
    
    try:
        req = client.build_request("GET", url, headers=headers)
        r = await client.send(req, stream=True)
        r.raise_for_status()
    except Exception as exc:
        logger.error(f"Upstream fetch failed: {repr(exc)}")
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"Failed to fetch upstream: {repr(exc)}")
        
    content_type = r.headers.get("Content-Type", "")
    is_m3u8 = "mpegurl" in content_type.lower() or url.split("?")[0].endswith(".m3u8")
    
    if is_m3u8:
        await r.aread()
        text = r.text
        await client.aclose()
        
        lines = text.splitlines()
        rewritten_lines = []
        base_url = str(r.url)
        
        for line in lines:
            line = line.strip()
            if not line:
                rewritten_lines.append(line)
                continue
            
            if line.startswith("#"):
                # Rewrite URI="..." inside tags (e.g. #EXT-X-MEDIA)
                def replacer(match):
                    uri = match.group(1)
                    absolute_uri = urllib.parse.urljoin(base_url, uri)
                    encoded_uri = urllib.parse.quote(absolute_uri, safe="")
                    proxied_uri = f"{PROXY_BASE_URL}{encoded_uri}"
                    return f'URI="{proxied_uri}"'
                
                new_line = re.sub(r'URI="([^"]+)"', replacer, line)
                rewritten_lines.append(new_line)
            else:
                absolute_uri = urllib.parse.urljoin(base_url, line)
                encoded_uri = urllib.parse.quote(absolute_uri, safe="")
                proxied_uri = f"{PROXY_BASE_URL}{encoded_uri}"
                rewritten_lines.append(proxied_uri)
                
        return Response(
            content="\n".join(rewritten_lines) + "\n",
            media_type="application/vnd.apple.mpegurl",
            headers={
                "Cache-Control": "no-cache",
                "Access-Control-Allow-Origin": "*"
            }
        )
    else:
        # Download the segment fully and return a standard response with Content-Length
        await r.aread()
        content = r.content
        await client.aclose()
        
        headers = {
            "Cache-Control": r.headers.get("Cache-Control", "public, max-age=31536000"),
            "Access-Control-Allow-Origin": "*"
        }
        if "Content-Length" in r.headers:
            headers["Content-Length"] = r.headers["Content-Length"]
        else:
            headers["Content-Length"] = str(len(content))
            
        return Response(
            content=content,
            media_type=content_type or "application/octet-stream",
            headers=headers
        )


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=6767, reload=True)
