"""
Suporte ao Apple Music.

Metadados vêm de fontes públicas da Apple (sem login):
- iTunes Search/Lookup API  -> músicas, álbuns, artistas e busca por texto
- página pública da playlist (JSON-LD) -> lista de faixas de playlists

O áudio continua vindo do YouTube Music via spotDL: montamos um objeto Song
do spotDL com os metadados da Apple e o downloader faz o resto (busca,
conversão, capa, tags e letra).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import requests
from spotdl.types.song import Song

APPLE_URL = re.compile(
    r"music\.apple\.com/(?P<cc>[a-z]{2})/(?P<kind>album|playlist|song|artist)"
    r"(?:/[^/?#]+)?/(?P<id>pl\.[\w-]+|\d+)(?:[^#]*?[?&]i=(?P<track>\d+))?",
    re.I,
)

LOOKUP = "https://itunes.apple.com/lookup"
SEARCH = "https://itunes.apple.com/search"
UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
}
TIMEOUT = 20


class AppleMusicError(Exception):
    pass


def is_apple_url(text: str) -> bool:
    return bool(APPLE_URL.search(text or ""))


def song_url(cc: str, track_id: Any) -> str:
    return f"https://music.apple.com/{cc}/song/{track_id}"


def _artwork(url: Optional[str], size: int) -> Optional[str]:
    if not url:
        return None
    return re.sub(r"/\d+x\d+(bb)?\.(jpg|png|webp)$", f"/{size}x{size}bb.jpg", url)


def _get_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    try:
        resp = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise AppleMusicError(f"Não consegui falar com a Apple Music: {exc}") from exc


def _lookup(ids: List[str], cc: str, **extra: Any) -> List[Dict[str, Any]]:
    """Lookup em lotes; devolve só as faixas de música, na ordem pedida."""
    results: Dict[str, Dict[str, Any]] = {}
    for start in range(0, len(ids), 150):
        chunk = ids[start:start + 150]
        data = _get_json(LOOKUP, {"id": ",".join(chunk), "country": cc, **extra})
        for item in data.get("results", []):
            if item.get("kind") == "song":
                results[str(item["trackId"])] = item
    return [results[i] for i in ids if i in results]


def _track_dict(item: Dict[str, Any], cc: str) -> Dict[str, Any]:
    return {
        "url": song_url(cc, item["trackId"]),
        "title": item.get("trackName", ""),
        "artist": item.get("artistName", ""),
        "album": item.get("collectionName", ""),
        "cover": _artwork(item.get("artworkUrl100"), 300),
        "duration": int((item.get("trackTimeMillis") or 0) / 1000),
        "explicit": item.get("trackExplicitness") == "explicit",
        "source": "apple",
    }


# --------------------------------------------------------------------------- #
# Resolver links e buscar
# --------------------------------------------------------------------------- #
def search(term: str, cc: str = "br", limit: int = 30) -> List[Dict[str, Any]]:
    data = _get_json(SEARCH, {"term": term, "entity": "song", "limit": limit, "country": cc})
    return [_track_dict(i, cc) for i in data.get("results", []) if i.get("kind") == "song"]


def _playlist_page(url: str) -> Dict[str, Any]:
    try:
        resp = requests.get(url, headers=UA, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise AppleMusicError(f"Não consegui abrir a playlist: {exc}") from exc
    html = resp.text
    match = re.search(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    )
    if not match:
        raise AppleMusicError("Playlist privada ou indisponível")
    data = json.loads(match.group(1))
    og_image = re.search(r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"', html)
    data["_cover"] = og_image.group(1) if og_image else None
    return data


def resolve(url: str) -> Dict[str, Any]:
    match = APPLE_URL.search(url)
    if not match:
        raise AppleMusicError("Link da Apple Music inválido")
    cc = match["cc"].lower()
    kind = match["kind"].lower()
    item_id = match["id"]

    # música avulsa: /song/123 ou /album/xxx/456?i=123
    if kind == "song" or match["track"]:
        track_id = match["track"] or item_id
        items = _lookup([track_id], cc)
        if not items:
            raise AppleMusicError("Música não encontrada na Apple Music")
        track = _track_dict(items[0], cc)
        return {"type": "track", "source": "apple", "name": track["title"],
                "cover": _artwork(items[0].get("artworkUrl100"), 600), "tracks": [track]}

    if kind == "album":
        data = _get_json(LOOKUP, {"id": item_id, "entity": "song", "country": cc, "limit": 200})
        results = data.get("results", [])
        album = next((r for r in results if r.get("wrapperType") == "collection"), {})
        songs = sorted(
            (r for r in results if r.get("kind") == "song"),
            key=lambda r: (r.get("discNumber", 1), r.get("trackNumber", 0)),
        )
        if not songs:
            raise AppleMusicError("Álbum não encontrado na Apple Music")
        return {"type": "album", "source": "apple", "name": album.get("collectionName", ""),
                "cover": _artwork(album.get("artworkUrl100"), 600),
                "tracks": [_track_dict(s, cc) for s in songs]}

    if kind == "artist":
        data = _get_json(LOOKUP, {"id": item_id, "entity": "song", "country": cc, "limit": 200})
        results = data.get("results", [])
        artist = next((r for r in results if r.get("wrapperType") == "artist"), {})
        songs = [r for r in results if r.get("kind") == "song"]
        if not songs:
            raise AppleMusicError("Artista não encontrado na Apple Music")
        return {"type": "artist", "source": "apple", "name": artist.get("artistName", ""),
                "cover": _artwork(songs[0].get("artworkUrl100"), 600),
                "tracks": [_track_dict(s, cc) for s in songs]}

    # playlist
    page = _playlist_page(f"https://music.apple.com/{cc}/playlist/x/{item_id}")
    ids: List[str] = []
    for t in page.get("track", []) or []:
        m = re.search(r"/song/(?:[^/]+/)?(\d+)", t.get("url", ""))
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    if not ids:
        raise AppleMusicError("Playlist vazia, privada ou indisponível")
    items = _lookup(ids, cc)
    return {"type": "playlist", "source": "apple", "name": page.get("name", ""),
            "cover": page.get("_cover"), "tracks": [_track_dict(i, cc) for i in items]}


# --------------------------------------------------------------------------- #
# Song do spotDL a partir da Apple
# --------------------------------------------------------------------------- #
def _split_artists(name: str) -> List[str]:
    parts = [p.strip() for p in re.split(r",\s*|\s+&\s+", name or "") if p.strip()]
    return parts or [name]


def song_from_url(url: str) -> Song:
    match = APPLE_URL.search(url)
    if not match:
        raise AppleMusicError("Link da Apple Music inválido")
    cc = match["cc"].lower()
    track_id = match["track"] or match["id"]
    items = _lookup([track_id], cc)
    if not items:
        raise AppleMusicError("Música não encontrada na Apple Music")
    it = items[0]

    copyright_text = None
    try:
        coll = _get_json(LOOKUP, {"id": it.get("collectionId"), "country": cc})
        copyright_text = (coll.get("results") or [{}])[0].get("copyright")
    except AppleMusicError:
        pass

    artists = _split_artists(it.get("artistName", ""))
    release = it.get("releaseDate") or ""
    return Song(
        name=it.get("trackName", ""),
        artists=artists,
        artist=artists[0],
        genres=[it["primaryGenreName"]] if it.get("primaryGenreName") else [],
        disc_number=int(it.get("discNumber") or 1),
        disc_count=int(it.get("discCount") or 1),
        album_name=it.get("collectionName", ""),
        album_artist=it.get("collectionArtistName") or artists[0],
        duration=int((it.get("trackTimeMillis") or 0) / 1000),
        year=int(release[:4]) if release[:4].isdigit() else 0,
        date=release[:10],
        track_number=int(it.get("trackNumber") or 1),
        tracks_count=int(it.get("trackCount") or 1),
        song_id=f"apple-{it['trackId']}",
        explicit=it.get("trackExplicitness") == "explicit",
        publisher="",
        url=song_url(cc, it["trackId"]),
        isrc="",  # a API pública da Apple não traz ISRC; None quebra a gravação das tags
        cover_url=_artwork(it.get("artworkUrl100"), 1000),
        copyright_text=copyright_text,
        album_id=str(it.get("collectionId", "")),
        artist_id=str(it.get("artistId", "")),
        album_type="album",
    )
