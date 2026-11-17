"""
Suporte ao YouTube (yt-dlp, que já vem com o spotDL).

- busca por texto e links de vídeo, Shorts, playlist e YouTube Music
- download em dois modos:
    video -> MP4 com vídeo + áudio, na qualidade escolhida
    audio -> só o áudio, no formato/bitrate das Configurações, com capa e tags
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import yt_dlp
from yt_dlp.utils import sanitize_filename

logger = logging.getLogger("bobmusic.youtube")

YT_URL = re.compile(
    r"(?:https?://)?(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com/(?:watch\?|shorts/|playlist\?|live/|embed/)|youtu\.be/)",
    re.I,
)
VIDEO_QUALITIES = ["best", "2160", "1440", "1080", "720", "480", "360"]
# formato das Configurações -> codec do yt-dlp
AUDIO_CODEC = {"mp3": "mp3", "m4a": "m4a", "flac": "flac", "opus": "opus", "ogg": "vorbis", "wav": "wav"}


class YouTubeError(Exception):
    pass


def is_youtube_url(text: str) -> bool:
    return bool(YT_URL.search(text or ""))


def _base_opts() -> Dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": logging.getLogger("yt-dlp"),
    }


def _views(n: int) -> str:
    for size, suffix in ((1_000_000_000, " bi"), (1_000_000, " mi"), (1_000, " mil")):
        if n >= size:
            value = n / size
            return (f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")) + suffix
    return str(n)


def _thumb(video_id: str) -> str:
    return f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg"


def _entry_dict(e: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    vid = e.get("id")
    # ignora canais e playlists que aparecem no meio da busca
    if not vid or e.get("ie_key") not in (None, "Youtube"):
        return None
    title = e.get("track") or e.get("title") or ""
    artist = e.get("artist") or e.get("channel") or e.get("uploader") or ""
    if not artist and " - " in title:
        artist = title.split(" - ", 1)[0]
    views = e.get("view_count")
    return {
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": title,
        "artist": re.sub(r"\s*-\s*Topic$", "", artist),
        "album": f"{_views(views)} visualizações" if views else "",
        "cover": _thumb(vid),
        "duration": int(e.get("duration") or 0),
        "explicit": False,
        "source": "youtube",
    }


def _extract(url: str, **extra: Any) -> Dict[str, Any]:
    opts = {**_base_opts(), "extract_flat": "in_playlist", "skip_download": True, **extra}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False) or {}
    except yt_dlp.utils.DownloadError as exc:
        raise YouTubeError(_clean_error(exc)) from exc


def _clean_error(exc: Exception) -> str:
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
    msg = msg.replace("ERROR: ", "").strip()
    if "Private video" in msg:
        return "Vídeo privado"
    if "Video unavailable" in msg:
        return "Vídeo indisponível"
    if "Sign in to confirm your age" in msg:
        return "Vídeo com restrição de idade (precisa de login no YouTube)"
    return msg[:300]


def search(term: str, limit: int = 25) -> List[Dict[str, Any]]:
    info = _extract(f"ytsearch{limit}:{term}")
    return [d for d in (_entry_dict(e) for e in info.get("entries") or [] if e) if d]


def resolve(url: str) -> Dict[str, Any]:
    is_playlist_page = "/playlist?" in url
    info = _extract(url, noplaylist=not is_playlist_page)
    if info.get("_type") == "playlist":
        tracks = [d for d in (_entry_dict(e) for e in info.get("entries") or [] if e) if d]
        if not tracks:
            raise YouTubeError("Playlist vazia ou privada")
        return {"type": "playlist", "source": "youtube", "name": info.get("title", ""),
                "cover": tracks[0]["cover"].replace("mqdefault", "hqdefault"), "tracks": tracks}
    track = _entry_dict(info)
    if not track:
        raise YouTubeError("Vídeo não encontrado")
    return {"type": "video", "source": "youtube", "name": track["title"],
            "cover": _thumb(info["id"]).replace("mqdefault", "hqdefault"), "tracks": [track]}


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def _file_stem(info: Dict[str, Any]) -> str:
    """'Artista - Título' (mesmo padrão das músicas), sem repetir o artista."""
    title = info.get("track") or info.get("title") or info.get("id")
    artist = info.get("artist") or info.get("channel") or info.get("uploader") or ""
    artist = re.sub(r"\s*-\s*Topic$", "", artist)
    name = title if (" - " in title or not artist) else f"{artist} - {title}"
    return sanitize_filename(name, restricted=False).strip() or info["id"]


def download(
    url: str,
    mode: str,
    quality: str,
    settings: Dict[str, Any],
    ffmpeg: Optional[str],
    on_progress: Callable[[int, str], None],
) -> Dict[str, Any]:
    """Baixa e devolve {"path", "title", "artist", "cover"}."""
    if mode == "video":
        out_dir = Path(settings["video_dir"])
    else:
        out_dir = Path(settings["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)

    opts: Dict[str, Any] = {
        **_base_opts(),
        "noplaylist": True,
        "ffmpeg_location": ffmpeg,
        "writethumbnail": True,
        "overwrites": False,
        "retries": 5,
        "fragment_retries": 5,
    }
    if mode == "video":
        res = "res" if quality == "best" else f"res:{quality}"
        opts.update({
            "format": "bv*+ba/b",
            # maior resolução até o limite; empate -> H.264 + AAC (abre em qualquer player)
            "format_sort": [res, "vcodec:h264", "acodec:aac"],
            "merge_output_format": "mp4",
            "postprocessors": [
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                {"key": "FFmpegMetadata", "add_metadata": True},
                {"key": "EmbedThumbnail"},
            ],
        })
    else:
        fmt = settings.get("format", "mp3")
        bitrate = settings.get("bitrate", "320k")
        opts.update({
            "format": "bestaudio/best",
            "postprocessors": [
                {"key": "FFmpegThumbnailsConvertor", "format": "jpg", "when": "before_dl"},
                {"key": "FFmpegExtractAudio", "preferredcodec": AUDIO_CODEC.get(fmt, "mp3"),
                 "preferredquality": "0" if bitrate == "auto" else bitrate.rstrip("k")},
                {"key": "FFmpegMetadata", "add_metadata": True},
                *([] if fmt == "wav" else [{"key": "EmbedThumbnail"}]),
            ],
        })

    state = {"done_streams": 0, "streams": 1}

    def progress_hook(d: Dict[str, Any]) -> None:
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            frac = (d.get("downloaded_bytes") or 0) / total if total else 0
            overall = (state["done_streams"] + frac) / state["streams"]
            on_progress(5 + int(overall * 85), "Baixando")
        elif d["status"] == "finished":
            state["done_streams"] += 1

    PP_MSG = {
        "Merger": "Juntando vídeo e áudio",
        "FFmpegExtractAudio": "Convertendo",
        "ExtractAudio": "Convertendo",
        "FFmpegMetadata": "Gravando tags",
        "Metadata": "Gravando tags",
        "EmbedThumbnail": "Gravando capa",
    }

    def pp_hook(d: Dict[str, Any]) -> None:
        if d["status"] == "started" and d.get("postprocessor") in PP_MSG:
            on_progress(92, PP_MSG[d["postprocessor"]])

    opts["progress_hooks"] = [progress_hook]
    opts["postprocessor_hooks"] = [pp_hook]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            on_progress(3, "Lendo vídeo")
            info = ydl.extract_info(url, download=False)
            if info.get("_type") == "playlist":
                raise YouTubeError("Isso é uma playlist; baixe pelos itens da lista")
            if info.get("is_live"):
                raise YouTubeError("Transmissões ao vivo não podem ser baixadas")
            state["streams"] = len(info.get("requested_formats") or []) or 1
            stem = _file_stem(info).replace("%", "%%")
            ydl.params["outtmpl"] = {"default": str(out_dir / f"{stem}.%(ext)s")}
            info = ydl.process_ie_result(info, download=True)
    except yt_dlp.utils.DownloadError as exc:
        raise YouTubeError(_clean_error(exc)) from exc

    downloads = info.get("requested_downloads") or [{}]
    path = downloads[-1].get("filepath") or downloads[-1].get("_filename")
    if not path or not Path(path).exists():
        raise YouTubeError("O download terminou, mas o arquivo não foi encontrado")
    return {
        "path": str(Path(path).resolve()),
        "title": info.get("track") or info.get("title") or "",
        "artist": info.get("artist") or info.get("channel") or info.get("uploader") or "",
        "cover": _thumb(info["id"]),
    }
