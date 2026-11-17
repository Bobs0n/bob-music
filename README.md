# Bob Music

App de desktop com interface própria, usando o [spotDL](https://github.com/spotDL/spotify-downloader) como motor.
Os metadados (nome, artista, álbum, capa) vêm do **Spotify** ou da **Apple Music**. O áudio vem do YouTube Music/YouTube.

## Como abrir

Use o atalho **Bob Music** da Área de Trabalho ou do Menu Iniciar (com o ícone do app).
Na primeira vez numa máquina nova, rode **`Bob Music.bat`**: ele instala tudo sozinho.

O ícone fica em `app/assets/bobmusic.ico`. Para trocar, substitua esse arquivo (e `app/web/icon.png`).

Pelo terminal:

```bash
.venv\Scripts\python.exe run.py            # janela própria
.venv\Scripts\python.exe run.py --browser  # no navegador
.venv\Scripts\python.exe run.py --server   # só o servidor em http://127.0.0.1:8765
```

Precisa de Python 3.12 e do ffmpeg no PATH (os dois já estão instalados nesta máquina).

## Instaladores

Os instaladores já incluem tudo (Python, dependências e ffmpeg). Quem instala não precisa de mais nada.

**Windows**: `build\installer\BobMusic-Setup-1.0.0-Windows.exe` (~100 MB).
- Instala sem pedir administrador.
- Cria os atalhos no Menu Iniciar e, se você marcar a opção, na Área de Trabalho.
- Desinstala por Configurações > Aplicativos.
- Para gerar de novo, rode `powershell -ExecutionPolicy Bypass -File build\build-windows.ps1`.
- O Windows SmartScreen pode avisar "Editor desconhecido". Clique em *Mais informações > Executar assim mesmo*. O aviso aparece porque o instalador não tem assinatura digital paga.

**Mac**: o `.dmg` só pode ser gerado num Mac. Há dois jeitos:
1. **Num Mac**, na pasta do projeto, rode `bash build/build-mac.sh`. O resultado sai em `build/installer/BobMusic-1.0.0-Mac-<arm64|x86_64>.dmg`.
2. **Pelo GitHub, sem Mac**: envie o projeto para um repositório e vá em *Actions > Instaladores > Run workflow*. Ele gera o `.dmg` para Mac Apple Silicon e Intel, e também o `.exe` do Windows.

Para instalar no Mac, abra o `.dmg` e arraste o **Bob Music** para *Aplicativos*. Na primeira vez, clique com o botão direito no app e escolha *Abrir*, depois *Abrir* de novo. Esse passo é necessário porque o app não tem assinatura da Apple, que custa US$ 99/ano.

Configurações e log ficam em `%APPDATA%\BobMusic` no Windows e em `~/Library/Application Support/BobMusic` no Mac.

## O que ele faz

- **Buscar**: escolha Spotify ou Apple Music, digite o nome de uma música ou cole um link (música, álbum, playlist ou artista). Ao colar um link, o app troca a origem sozinho.
  - Apple Music usa dados públicos da Apple (iTunes Lookup e a página pública da playlist), sem precisar de login. Playlists privadas não funcionam.
- **Downloads**: uma fila com progresso por música. Dá para cancelar, tentar de novo e abrir a pasta.
- **Biblioteca**: as músicas baixadas, com capa, filtro e um player embutido (barra de espaço pausa/toca).
- **Configurações**: pasta de destino, formato (MP3, M4A, FLAC, OPUS, OGG, WAV), bitrate, letra embutida e organização em pastas Artista/Álbum.

Pasta padrão: `Música\Bob Music`. As configurações ficam em `%APPDATA%\BobMusic\settings.json`.

## Estrutura

```
run.py             inicia o servidor e abre a janela (pywebview)
app/server.py      API FastAPI que usa o spotDL como biblioteca
app/apple.py       links e busca da Apple Music
app/web/           interface (HTML/CSS/JS, sem build)
Bob Music.bat      atalho para abrir
```

## Uso

Baixe só músicas que você tem direito de baixar e respeite os termos do Spotify e do YouTube.
