"""
Bob Music - backend.

API local (FastAPI) que usa o spotDL como biblioteca:
- busca metadados no Spotify (músicas, playlists, álbuns, artistas)
- baixa o áudio (YouTube Music / YouTube) via spotDL, com capa, tags e letra
- mantém uma fila com progresso por música
- lista e toca a biblioteca baixada
"""

from __future__ import annotations

import functools
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from spotdl.download.downloader import Downloader
from spotdl.download.progress_handler import ProgressHandler
from spotdl.types.album import Album
from spotdl.types.artist import Artist
from spotdl.types.playlist import Playlist
from spotdl.types.song import Song
from spotdl.utils.config import DEFAULT_CONFIG
from spotdl.utils.spotify import SpotifyClient

from app import apple, youtube

logger = logging.getLogger("bobmusic")

APP_DIR = Path(__file__).resolve().parent
WEB_DIR = APP_DIR / "web"


def data_dir() -> Path:
    """Pasta de configurações/log: %APPDATA%\\BobMusic ou ~/Library/Application Support/BobMusic."""
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "BobMusic"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "BobMusic"
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "BobMusic"


DATA_DIR = data_dir()
DATA_DIR.mkdir(parents=True, exist_ok=True)


@functools.lru_cache(maxsize=1)
def find_ffmpeg() -> str:
    """ffmpeg embutido no instalador > ffmpeg do sistema > ffmpeg do spotDL (baixa se faltar)."""
    import shutil

    exe = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
    bases = [Path(getattr(sys, "_MEIPASS", APP_DIR.parent)), Path(sys.executable).parent]
    for base in bases:
        candidate = base / "ffmpeg" / exe
        if candidate.is_file():
            return str(candidate)
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    from spotdl.utils.ffmpeg import download_ffmpeg, get_ffmpeg_path

    found = get_ffmpeg_path()
    if found is None:
        logger.info("ffmpeg não encontrado, baixando...")
        found = download_ffmpeg()
    return str(found)
SETTINGS_FILE = DATA_DIR / "settings.json"

AUDIO_FORMATS = ["mp3", "m4a", "flac", "opus", "ogg", "wav"]
BITRATES = ["auto", "128k", "192k", "256k", "320k"]

VIDEO_FORMATS = ["mp4", "webm", "mkv", "mov"]
_VIDEOS_HOME = Path.home() / ("Movies" if sys.platform == "darwin" else "Videos")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "output_dir": str(Path.home() / "Music" / "Bob Music"),
    "video_dir": str(_VIDEOS_HOME / "Bob Music"),
    "format": "mp3",
    "bitrate": "320k",
    "lyrics": True,
    "subfolders": False,  # Artista/Álbum/Música
}

SPOTIFY_URL = re.compile(
    r"open\.spotify\.com/(?:intl-\w+/)?(track|album|playlist|artist)/([A-Za-z0-9]+)"
)


# --------------------------------------------------------------------------- #
# Configurações
# --------------------------------------------------------------------------- #
def load_settings() -> Dict[str, Any]:
    settings = dict(DEFAULT_SETTINGS)
    if SETTINGS_FILE.exists():
        try:
            settings.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            logger.warning("settings.json inválido, usando padrões")
    return settings


def save_settings(settings: Dict[str, Any]) -> None:
    SETTINGS_FILE.write_text(json.dumps(settings, indent=2), encoding="utf-8")


SETTINGS = load_settings()


def output_template(settings: Dict[str, Any]) -> str:
    base = Path(settings["output_dir"])
    if settings.get("subfolders"):
        return str(base / "{album-artist}" / "{album}" / "{artists} - {title}.{output-ext}")
    return str(base / "{artists} - {title}.{output-ext}")


def downloader_options(settings: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "output": output_template(settings),
        "format": settings["format"],
        "bitrate": None if settings["bitrate"] == "auto" else settings["bitrate"],
        "lyrics_providers": ["musixmatch", "azlyrics"] if settings.get("lyrics") else [],
        "threads": 1,
        "simple_tui": True,
        # se o arquivo já existe, só regrava capa/tags (sem baixar de novo)
        "overwrite": "metadata",
        "audio_providers": ["youtube-music", "youtube"],
        "ffmpeg": find_ffmpeg(),
        "log_level": "INFO",
    }


# --------------------------------------------------------------------------- #
# Fila de downloads
# --------------------------------------------------------------------------- #
class Job:
    def __init__(self, track: Dict[str, Any]):
        self.id = uuid.uuid4().hex[:12]
        self.url: str = track["url"]
        self.title: str = track.get("title", "")
        self.artist: str = track.get("artist", "")
        self.cover: Optional[str] = track.get("cover")
        # YouTube: "video" (vídeo + áudio) ou "audio"; Spotify/Apple: sempre áudio (None)
        self.mode: Optional[str] = None
        self.quality: str = "best"
        if youtube.is_youtube_url(self.url):
            self.mode = "video" if track.get("mode") == "video" else "audio"
            q = str(track.get("quality") or "best")
            self.quality = q if q in youtube.VIDEO_QUALITIES else "best"
        self.status = "queued"  # queued | running | done | skipped | error | canceled
        self.progress = 0
        self.message = "Na fila"
        self.path: Optional[str] = None
        self.error: Optional[str] = None
        self.created = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "artist": self.artist,
            "cover": self.cover,
            "mode": self.mode,
            "quality": self.quality,
            "status": self.status,
            "progress": int(self.progress),
            "message": self.message,
            "path": self.path,
            "error": self.error,
        }


MESSAGES_PT = {
    "Getting lyrics": "Buscando letra",
    "Searching for song": "Procurando áudio",
    "Getting audio meta": "Lendo áudio",
    "Downloading": "Baixando",
    "Converting": "Convertendo",
    "Embedding metadata": "Gravando capa e tags",
    "Done": "Concluído",
    "Skipped": "Já existia",
    "Error": "Erro",
}


class DownloadWorker(threading.Thread):
    """Thread única dona do Downloader do spotDL (ele usa seu próprio event loop)."""

    def __init__(self) -> None:
        super().__init__(daemon=True, name="spotdl-worker")
        self.jobs: Dict[str, Job] = {}
        self.order: List[str] = []
        self.pending: "queue.Queue[str]" = queue.Queue()
        self.lock = threading.Lock()
        self.current: Optional[Job] = None
        self.downloader: Optional[Downloader] = None
        self.settings_dirty = True

    # ---- API usada pelo servidor -------------------------------------------
    def enqueue(self, tracks: List[Dict[str, Any]]) -> List[Job]:
        created = []
        with self.lock:
            active = {
                (j.url, j.mode) for j in self.jobs.values() if j.status in ("queued", "running")
            }
            for track in tracks:
                job = Job(track)
                if (job.url, job.mode) in active:
                    continue
                self.jobs[job.id] = job
                self.order.append(job.id)
                created.append(job)
                active.add((job.url, job.mode))
        for job in created:
            self.pending.put(job.id)
        return created

    def snapshot(self) -> List[Dict[str, Any]]:
        with self.lock:
            return [self.jobs[i].to_dict() for i in reversed(self.order)]

    def cancel(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if job and job.status == "queued":
                job.status = "canceled"
                job.message = "Cancelado"

    def retry(self, job_id: str) -> None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or job.status not in ("error", "canceled"):
                return
            job.status, job.progress, job.message, job.error = "queued", 0, "Na fila", None
        self.pending.put(job_id)

    def clear_finished(self) -> None:
        with self.lock:
            keep = [
                i for i in self.order if self.jobs[i].status in ("queued", "running")
            ]
            self.jobs = {i: self.jobs[i] for i in keep}
            self.order = keep

    # ---- Loop da thread ----------------------------------------------------
    def _on_progress(self, tracker, message: str) -> None:
        job = self.current
        if job is None:
            return
        job.progress = max(job.progress, min(99, int(tracker.progress)))
        job.message = MESSAGES_PT.get(message, message)

    def _ensure_downloader(self) -> Downloader:
        if self.downloader is None or self.settings_dirty:
            Path(SETTINGS["output_dir"]).mkdir(parents=True, exist_ok=True)
            self.downloader = Downloader(downloader_options(SETTINGS))
            self.downloader.progress_handler = ProgressHandler(
                simple_tui=True, update_callback=self._on_progress, web_ui=True
            )
            self.settings_dirty = False
        return self.downloader

    def run(self) -> None:
        while True:
            job_id = self.pending.get()
            with self.lock:
                job = self.jobs.get(job_id)
                if job is None or job.status != "queued":
                    continue
                job.status, job.message, job.progress = "running", "Lendo metadados", 5
                self.current = job
            try:
                if job.mode:
                    self._download_youtube(job)
                    continue
                downloader = self._ensure_downloader()
                if apple.is_apple_url(job.url):
                    song = apple.song_from_url(job.url)
                else:
                    song = Song.from_url(job.url)
                job.title, job.artist = song.name, ", ".join(song.artists)
                job.cover = job.cover or song.cover_url
                downloader.progress_handler.set_song_count(1)
                _, path = downloader.download_song(song)
                if path is None:
                    err = downloader.errors[-1] if downloader.errors else "Áudio não encontrado"
                    raise RuntimeError(err)
                job.path = str(Path(path).resolve())
                skipped = job.message == MESSAGES_PT["Skipped"]
                job.status = "skipped" if skipped else "done"
                job.message = MESSAGES_PT["Skipped"] if skipped else "Concluído"
                job.progress = 100
            except Exception as exc:  # noqa: BLE001
                logger.exception("Falha ao baixar %s", job.url)
                job.status, job.message, job.error = "error", "Erro", str(exc)[:300]
            finally:
                self.current = None

    def _download_youtube(self, job: Job) -> None:
        def on_progress(progress: int, message: str) -> None:
            job.progress = max(job.progress, min(99, progress))
            job.message = message

        ffmpeg = find_ffmpeg()
        result = youtube.download(
            job.url, job.mode or "audio", job.quality, SETTINGS,
            None if ffmpeg == "ffmpeg" else ffmpeg, on_progress,
        )
        job.path = result["path"]
        job.title = job.title or result["title"]
        job.artist = job.artist or result["artist"]
        job.cover = job.cover or result["cover"]
        job.status, job.message, job.progress = "done", "Concluído", 100


WORKER = DownloadWorker()


# --------------------------------------------------------------------------- #
# Spotify helpers
# --------------------------------------------------------------------------- #
def init_spotify() -> None:
    try:
        SpotifyClient.init(
            client_id=DEFAULT_CONFIG["client_id"],
            client_secret=DEFAULT_CONFIG["client_secret"],
            headless=True,
        )
    except Exception as exc:  # já inicializado
        logger.debug("SpotifyClient: %s", exc)


def _best_image(images: List[Dict[str, Any]], prefer: int = 300) -> Optional[str]:
    if not images:
        return None
    images = [i for i in images if i.get("url")]
    if not images:
        return None
    return min(images, key=lambda i: abs((i.get("width") or prefer) - prefer))["url"]


def track_from_raw(item: Dict[str, Any]) -> Dict[str, Any]:
    album = item.get("album") or {}
    return {
        "url": item["external_urls"]["spotify"],
        "title": item["name"],
        "artist": ", ".join(a["name"] for a in item.get("artists", [])),
        "album": album.get("name", ""),
        "cover": _best_image(album.get("images", [])),
        "duration": int((item.get("duration_ms") or 0) / 1000),
        "explicit": bool(item.get("explicit")),
    }


def track_from_song(song: Song, fallback_cover: Optional[str] = None) -> Dict[str, Any]:
    return {
        "url": song.url,
        "title": song.name or "",
        "artist": ", ".join(song.artists or []) or (song.artist or ""),
        "album": song.album_name or "",
        "cover": song.cover_url or fallback_cover,
        "duration": int(song.duration or 0),
        "explicit": bool(song.explicit),
    }


def resolve_link(kind: str, url: str) -> Dict[str, Any]:
    if kind == "track":
        song = Song.from_url(url)
        return {"type": "track", "name": song.name, "cover": song.cover_url,
                "tracks": [track_from_song(song)]}

    cls = {"album": Album, "playlist": Playlist, "artist": Artist}[kind]
    metadata, songs = cls.get_metadata(url)
    cover = None
    client = SpotifyClient()
    try:
        raw = getattr(client, kind)(url)
        cover = _best_image((raw or {}).get("images", []))
    except Exception:  # noqa: BLE001
        pass
    seen, tracks = set(), []
    for s in songs:
        if s.url and s.url not in seen:
            seen.add(s.url)
            tracks.append(track_from_song(s, cover if kind == "album" else None))
    return {
        "type": kind,
        "name": metadata.get("name", ""),
        "cover": cover,
        "tracks": tracks,
    }


# --------------------------------------------------------------------------- #
# FastAPI
# --------------------------------------------------------------------------- #
app = FastAPI(title="Bob Music")


@app.on_event("startup")
def _startup() -> None:
    init_spotify()
    if not WORKER.is_alive():
        WORKER.start()


class SearchResult(BaseModel):
    type: str
    source: str = "spotify"
    name: str = ""
    cover: Optional[str] = None
    tracks: List[Dict[str, Any]]


@app.get("/api/search", response_model=SearchResult)
def api_search(q: str, source: str = "spotify") -> Dict[str, Any]:
    q = q.strip()
    if not q:
        raise HTTPException(400, "Digite algo para buscar")
    if youtube.is_youtube_url(q) or (source == "youtube" and not SPOTIFY_URL.search(q)
                                     and not apple.is_apple_url(q)):
        try:
            if youtube.is_youtube_url(q):
                return youtube.resolve(q)
            return {"type": "search", "source": "youtube", "name": q, "cover": None,
                    "tracks": youtube.search(q)}
        except youtube.YouTubeError as exc:
            raise HTTPException(502, str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro no YouTube")
            raise HTTPException(502, f"Não consegui falar com o YouTube: {exc}") from exc
    if apple.is_apple_url(q):
        try:
            return apple.resolve(q)
        except apple.AppleMusicError as exc:
            raise HTTPException(502, str(exc)) from exc
    if source == "apple" and not SPOTIFY_URL.search(q):
        try:
            return {"type": "search", "source": "apple", "name": q, "cover": None,
                    "tracks": apple.search(q)}
        except apple.AppleMusicError as exc:
            raise HTTPException(502, str(exc)) from exc
    match = SPOTIFY_URL.search(q)
    try:
        if match:
            kind, item_id = match.groups()
            return resolve_link(kind, f"https://open.spotify.com/{kind}/{item_id}")
        raw = SpotifyClient().search(q, type="track", limit=30)
        items = (raw or {}).get("tracks", {}).get("items", [])[:30]
        return {"type": "search", "name": q, "cover": None,
                "tracks": [track_from_raw(i) for i in items if i]}
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("Erro na busca")
        raise HTTPException(502, f"Não consegui falar com o Spotify: {exc}") from exc


class DownloadRequest(BaseModel):
    tracks: List[Dict[str, Any]]


@app.post("/api/download")
def api_download(req: DownloadRequest) -> Dict[str, Any]:
    tracks = [
        t for t in req.tracks
        if SPOTIFY_URL.search(t.get("url", "")) or apple.is_apple_url(t.get("url", ""))
        or youtube.is_youtube_url(t.get("url", ""))
    ]
    if not tracks:
        raise HTTPException(400, "Nenhum item válido para baixar")
    jobs = WORKER.enqueue(tracks)
    return {"queued": len(jobs)}


@app.get("/api/jobs")
def api_jobs() -> List[Dict[str, Any]]:
    return WORKER.snapshot()


@app.post("/api/jobs/{job_id}/cancel")
def api_cancel(job_id: str) -> Dict[str, bool]:
    WORKER.cancel(job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/retry")
def api_retry(job_id: str) -> Dict[str, bool]:
    WORKER.retry(job_id)
    return {"ok": True}


@app.post("/api/jobs/clear")
def api_clear() -> Dict[str, bool]:
    WORKER.clear_finished()
    return {"ok": True}


@app.get("/api/settings")
def api_get_settings() -> Dict[str, Any]:
    return {**SETTINGS, "formats": AUDIO_FORMATS, "bitrates": BITRATES}


class SettingsUpdate(BaseModel):
    output_dir: Optional[str] = None
    video_dir: Optional[str] = None
    format: Optional[str] = None
    bitrate: Optional[str] = None
    lyrics: Optional[bool] = None
    subfolders: Optional[bool] = None


@app.post("/api/settings")
def api_set_settings(update: SettingsUpdate) -> Dict[str, Any]:
    changes = {k: v for k, v in update.model_dump().items() if v is not None}
    if "format" in changes and changes["format"] not in AUDIO_FORMATS:
        raise HTTPException(400, "Formato inválido")
    if "bitrate" in changes and changes["bitrate"] not in BITRATES:
        raise HTTPException(400, "Bitrate inválido")
    for key in ("output_dir", "video_dir"):
        if key in changes:
            try:
                Path(changes[key]).expanduser().mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                raise HTTPException(400, f"Pasta inválida: {exc}") from exc
            changes[key] = str(Path(changes[key]).expanduser().resolve())
    SETTINGS.update(changes)
    save_settings(SETTINGS)
    WORKER.settings_dirty = True
    return api_get_settings()


def _library_root() -> Path:
    return Path(SETTINGS["output_dir"]).resolve()


def _video_root() -> Path:
    return Path(SETTINGS.get("video_dir") or DEFAULT_SETTINGS["video_dir"]).resolve()


@app.get("/api/library")
def api_library() -> List[Dict[str, Any]]:
    files, seen = [], set()
    for root, exts, kind in ((_library_root(), AUDIO_FORMATS, "audio"),
                             (_video_root(), VIDEO_FORMATS, "video")):
        if not root.exists():
            continue
        for path in root.rglob("*"):
            ext = path.suffix[1:].lower()
            if ext not in exts or path in seen or not path.is_file():
                continue
            seen.add(path)
            stat = path.stat()
            stem = path.stem
            artist, _, title = stem.partition(" - ")
            files.append({
                "path": str(path),
                "rel": str(path.relative_to(root)),
                "title": title or stem,
                "artist": artist if title else "",
                "size": stat.st_size,
                "mtime": stat.st_mtime,
                "ext": ext,
                "kind": kind,
            })
    files.sort(key=lambda f: f["mtime"], reverse=True)
    return files


def _safe_library_path(file: str) -> Path:
    path = Path(file).resolve()
    inside = path.is_relative_to(_library_root()) or path.is_relative_to(_video_root())
    if not inside or not path.is_file():
        raise HTTPException(404, "Arquivo não encontrado")
    return path


MIME = {"mp3": "audio/mpeg", "m4a": "audio/mp4", "flac": "audio/flac",
        "opus": "audio/ogg", "ogg": "audio/ogg", "wav": "audio/wav",
        "mp4": "video/mp4", "webm": "video/webm", "mkv": "video/x-matroska",
        "mov": "video/quicktime"}


@app.get("/api/stream")
def api_stream(file: str) -> FileResponse:
    path = _safe_library_path(file)
    return FileResponse(path, media_type=MIME.get(path.suffix[1:].lower(), "audio/mpeg"))


@app.get("/api/cover")
def api_cover(file: str):
    """Extrai a capa embutida no arquivo (mutagen vem junto com o spotDL)."""
    path = _safe_library_path(file)
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path)
        data, mime = None, "image/jpeg"
        if audio is not None and audio.tags is not None:
            for key in audio.tags.keys():
                if str(key).startswith("APIC"):
                    data, mime = audio.tags[key].data, audio.tags[key].mime
                    break
            if data is None and "covr" in audio.tags:
                data = bytes(audio.tags["covr"][0])
        if data is None and hasattr(audio, "pictures") and audio.pictures:
            data, mime = audio.pictures[0].data, audio.pictures[0].mime
        if data is None:
            raise HTTPException(404)
        from fastapi.responses import Response
        return Response(content=data, media_type=mime,
                        headers={"Cache-Control": "max-age=86400"})
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(404) from exc


class OpenRequest(BaseModel):
    file: Optional[str] = None
    videos: bool = False


@app.post("/api/open-folder")
def api_open_folder(req: OpenRequest) -> Dict[str, bool]:
    root = _video_root() if req.videos else _library_root()
    root.mkdir(parents=True, exist_ok=True)
    import subprocess

    path = _safe_library_path(req.file) if req.file else None
    if sys.platform == "win32":
        if path:
            subprocess.Popen(["explorer", "/select,", str(path)])
        else:
            os.startfile(root)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)] if path else ["open", str(root)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent if path else root)])
    return {"ok": True}


@app.delete("/api/library")
def api_delete(file: str) -> Dict[str, bool]:
    path = _safe_library_path(file)
    path.unlink()
    return {"ok": True}


app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
