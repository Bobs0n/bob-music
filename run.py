"""
Inicia o Bob Music.

    python run.py            -> abre em janela própria (desktop)
    python run.py --browser  -> abre no navegador padrão
    python run.py --server   -> só o servidor (http://127.0.0.1:8765)

Também é o ponto de entrada do executável gerado pelo PyInstaller (build/).
"""

import logging
import multiprocessing
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)
BASE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
sys.path.insert(0, str(BASE_DIR))


def _log_dir() -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", Path.home())) / "BobMusic"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "BobMusic"
    return Path.home() / ".config" / "BobMusic"


# Sem console (pythonw, .exe/.app empacotado) stdout/stderr são None e o
# uvicorn/spotDL quebram ao escrever neles. Nesse caso tudo vai para um log.
if sys.stdout is None or sys.stderr is None or (FROZEN and sys.platform == "darwin"):
    _dir = _log_dir()
    _dir.mkdir(parents=True, exist_ok=True)
    _log = open(_dir / "bobmusic.log", "w", encoding="utf-8", buffering=1)
    sys.stdout = sys.stdout or _log
    sys.stderr = sys.stderr or _log
    if FROZEN and sys.platform == "darwin":
        sys.stdout = sys.stderr = _log

# No Windows, sem console, cada chamada ao ffmpeg abriria uma janela preta.
if sys.platform == "win32" and (FROZEN or Path(sys.executable).name.lower() == "pythonw.exe"):
    import subprocess

    _popen_init = subprocess.Popen.__init__

    def _popen_no_window(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | 0x08000000  # CREATE_NO_WINDOW
        _popen_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = _popen_no_window  # type: ignore[method-assign]

import uvicorn  # noqa: E402

ICON = BASE_DIR / "app" / "assets" / "bobmusic.ico"
HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"


def port_in_use() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex((HOST, PORT)) == 0


def start_server() -> uvicorn.Server:
    from app.server import app

    config = uvicorn.Config(app, host=HOST, port=PORT, log_level="warning", log_config=None)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(150):
        if port_in_use():
            break
        time.sleep(0.1)
    return server


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
    mode = sys.argv[1] if len(sys.argv) > 1 else "--window"

    if port_in_use():
        print(f"Bob Music já está rodando em {URL}")
        webbrowser.open(URL)
        return

    server = start_server()
    print(f"Bob Music rodando em {URL}")

    if mode == "--server":
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return

    if mode == "--window":
        try:
            import webview

            # ID próprio para a barra de tarefas não agrupar com o Python
            # e mostrar o ícone do Bob Music.
            if sys.platform == "win32":
                import ctypes

                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("BobMusic.App")

            webview.create_window(
                "Bob Music", URL, width=1240, height=820, min_size=(760, 560),
                background_color="#0b0b10",
            )
            webview.start(icon=str(ICON) if ICON.exists() else None)
            server.should_exit = True
            return
        except Exception as exc:  # noqa: BLE001
            logging.exception("Janela nativa indisponível (%s); abrindo no navegador.", exc)

    webbrowser.open(URL)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.should_exit = True


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
