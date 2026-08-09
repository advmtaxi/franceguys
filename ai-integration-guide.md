# AI Integration Guide: Fetching Streaming Sources via Dulo API

This guide provides instructions for an AI agent on how to integrate and use the Dulo wrapper API to fetch HLS (`.m3u8`) streaming sources for movies, TV shows, and anime.

## 1. Get the TMDB ID
First, you need to search The Movie Database (TMDB) API to find the TMDB ID of the title you want to stream.

- For **Movies**: Use the TMDB `/search/movie` endpoint.
- For **TV Shows** and **Anime**: Use the TMDB `/search/tv` endpoint.

You will extract the `id` field from the top relevant search result.

## 2. Request Sources from Our API

The wrapper API exposes a POST endpoint: `/api/source`

Make a `POST` request to this endpoint with a JSON body.

### Request Payloads

**For a Movie:**
```json
{
  "type": "movie",
  "tmdbId": 12345
}
```

**For a TV Show or Anime:**
```json
{
  "type": "tv",  // Use "tv" or "anime"
  "tmdbId": 67890,
  "season": 1,
  "episode": 1
}
```

### Example Request using Python
```python
import httpx

API_URL = "https://apidulo.b-cdn.net/api/source"

payload = {
    "type": "movie",
    "tmdbId": 969681
}

response = httpx.post(API_URL, json=payload)
data = response.json()
```

## 3. Extracting the HLS m3u8 Link

A successful response (HTTP 200) will return a JSON object with a `sources` array.

### Example Response
```json
{
  "requestId": "4c5aca02-...",
  "sources": [
    {
      "url": "https://apidulo.b-cdn.net/api/proxy/https://pub-...r2.dev/spider-man-brand-new-day-969681-1080p/index-v2.m3u8",
      "title": "Source 1",
      "type": "hls",
      "quality": "auto"
    }
  ],
  "count": 1
}
```

### Parsing the Response
Iterate through the `sources` array and extract the `url`. These URLs point directly to an `.m3u8` playlist which you can pass to any HLS-compatible video player (like Video.js, HLS.js, etc.) on your website. 

**Note on CORS and Proxying**: The returned URLs are automatically wrapped in our Bunny CDN proxy (`https://apidulo.b-cdn.net/api/proxy?url=...`). This perfectly resolves all browser CORS issues and ensures that the video chunks are cached globally by Bunny CDN.

```javascript
// Example JS parsing for the website integration
const sources = data.sources;
if (sources.length > 0) {
    const streamUrl = sources[0].url; // Best quality is usually first or "auto"
    console.log("Play this in your video player:", streamUrl);
} else {
    console.log("No sources found.");
}
```

## Key Considerations for AI Implementers
- **Lag Resistance**: This API server handles all the session management and caching internally. You do not need to manage Dulo session cookies on the client side. Just make standard POST requests, and the server safely resolves rate-limiting via global async locks.
- **TV vs Anime**: Our API accepts both `"tv"` and `"anime"` types, but both require the `season` and `episode` fields to be provided.
