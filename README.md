# Baixador de midias com Streamlit

Aplicativo em Python/Streamlit para baixar conteudo do YouTube, Instagram e TikTok usando `yt-dlp`.

## Uso permitido

Use somente com conteudo proprio, publico com permissao de download ou autorizado pelo titular. O app nao remove marca d'agua, nao quebra DRM e nao contorna protecoes de acesso.

## Instalar

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Para converter audio em MP3/M4A/WAV, instale o FFmpeg e deixe `ffmpeg` disponivel no PATH.

## Rodar

```powershell
streamlit run app.py
```

Os arquivos baixados ficam, por padrao, na pasta `downloads`.
