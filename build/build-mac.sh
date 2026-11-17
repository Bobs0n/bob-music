#!/bin/bash
# Gera "Bob Music.app" e o instalador .dmg no macOS.
# Uso (num Mac, na pasta do projeto):  bash build/build-mac.sh
# Requisitos: Python 3.12 (python.org ou Homebrew). Internet para baixar dependências e o ffmpeg.
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-python3.12}
command -v "$PY" >/dev/null || PY=python3

"$PY" -m venv .venv-mac
source .venv-mac/bin/activate
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt pyinstaller

# ffmpeg estático (o mesmo que o spotDL usa), conforme o processador do Mac
mkdir -p build/vendor
if [ ! -f build/vendor/ffmpeg ]; then
  if [ "$(uname -m)" = "arm64" ]; then ARCH=darwin-arm64; else ARCH=darwin-x64; fi
  curl -fL -o build/vendor/ffmpeg "https://github.com/eugeneware/ffmpeg-static/releases/download/b4.4/$ARCH"
  chmod +x build/vendor/ffmpeg
fi

pyinstaller --noconfirm --clean --distpath build/dist --workpath build/work build/bobmusic.spec

# assinatura ad-hoc (sem conta de desenvolvedor Apple)
codesign --force --deep --sign - "build/dist/Bob Music.app"

# .dmg com atalho para Aplicativos (arrastar e soltar)
VERSION=1.1.0
ARCH_NAME=$(uname -m)
STAGE=build/dmg
rm -rf "$STAGE" && mkdir -p "$STAGE" build/installer
cp -R "build/dist/Bob Music.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
DMG="build/installer/BobMusic-$VERSION-Mac-$ARCH_NAME.dmg"
rm -f "$DMG"
# o hdiutil às vezes falha com "Resource busy" (antivírus do macOS); tenta de novo
for attempt in 1 2 3 4 5; do
  if hdiutil create -volname "Bob Music" -srcfolder "$STAGE" -ov -format UDZO "$DMG"; then
    break
  fi
  [ "$attempt" = 5 ] && { echo "hdiutil falhou"; exit 1; }
  echo "hdiutil ocupado, tentando de novo ($attempt)..."
  sleep $((attempt * 5))
done
echo "Pronto: $DMG"
