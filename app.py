from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import streamlit as st
from yt_dlp import YoutubeDL


APP_DIR = Path(__file__).resolve().parent
DEFAULT_DOWNLOAD_DIR = APP_DIR / "downloads"
ALLOWED_HOSTS = (
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "tiktok.com",
    "vm.tiktok.com",
)


def is_supported_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"}:
        return False

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    return any(host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_HOSTS)


def sanitize_output_dir(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = APP_DIR / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def human_bytes(value: float | int | None) -> str:
    if not value:
        return ""

    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def build_options(
    mode: str,
    output_dir: Path,
    audio_format: str,
    quality: str,
    has_ffmpeg: bool,
    status_box: Any,
    progress_bar: Any,
) -> dict[str, Any]:
    def progress_hook(data: dict[str, Any]) -> None:
        status = data.get("status")
        filename = Path(data.get("filename", "")).name

        if status == "downloading":
            downloaded = data.get("downloaded_bytes") or 0
            total = data.get("total_bytes") or data.get("total_bytes_estimate") or 0
            speed = human_bytes(data.get("speed"))
            percent = min(downloaded / total, 1.0) if total else 0

            progress_bar.progress(percent)
            status_box.info(
                f"Baixando {filename or 'arquivo'} - "
                f"{human_bytes(downloaded)} de {human_bytes(total) or 'tamanho desconhecido'}"
                + (f" - {speed}/s" if speed else "")
            )

        if status == "finished":
            progress_bar.progress(1.0)
            status_box.success(f"Download concluido: {filename}")

    common_options: dict[str, Any] = {
        "outtmpl": str(output_dir / "%(extractor)s" / "%(title).180s [%(id)s].%(ext)s"),
        "noplaylist": False,
        "progress_hooks": [progress_hook],
        "windowsfilenames": True,
        "restrictfilenames": False,
        "ignoreerrors": False,
        "quiet": True,
        "no_warnings": True,
    }

    if mode == "audio":
        if not has_ffmpeg:
            raise RuntimeError("Para baixar e converter audio, instale o FFmpeg e deixe ffmpeg no PATH.")

        common_options.update(
            {
                "format": "bestaudio/best",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": audio_format,
                        "preferredquality": "192",
                    }
                ],
            }
        )
        return common_options

    if not has_ffmpeg:
        common_options["format"] = "best[ext=mp4]/best"
        return common_options

    quality_map = {
        "Melhor qualidade": "bestvideo+bestaudio/best",
        "Ate 1080p": "bv*[height<=1080]+ba/b[height<=1080]/best",
        "Ate 720p": "bv*[height<=720]+ba/b[height<=720]/best",
        "Arquivo menor": "worstvideo+bestaudio/worst",
    }
    common_options["format"] = quality_map[quality]
    common_options["merge_output_format"] = "mp4"
    return common_options


def download_media(url: str, options: dict[str, Any]) -> list[Path]:
    output_root = Path(options["outtmpl"]).parents[1]
    before = {path for path in output_root.rglob("*") if path.is_file()} if output_root.exists() else set()

    with YoutubeDL(options) as ydl:
        result = ydl.extract_info(url, download=True)

    after = {path for path in output_root.rglob("*") if path.is_file()}
    new_files = sorted(after - before, key=lambda item: item.stat().st_mtime, reverse=True)

    if new_files:
        return new_files

    requested = result.get("requested_downloads", []) if isinstance(result, dict) else []
    files = [Path(item["filepath"]) for item in requested if item.get("filepath")]
    return [path for path in files if path.exists()]


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


st.set_page_config(
    page_title="Baixador de Midias",
    page_icon="download",
    layout="centered",
)

st.title("Baixador de midias")
st.caption("YouTube, Instagram e TikTok para conteudo proprio, publico ou autorizado.")

with st.sidebar:
    st.header("Configuracao")
    mode = st.radio("Tipo de download", ["video", "audio"], format_func=str.title)
    quality = st.selectbox(
        "Qualidade do video",
        ["Melhor qualidade", "Ate 1080p", "Ate 720p", "Arquivo menor"],
        disabled=mode == "audio",
    )
    audio_format = st.selectbox("Formato do audio", ["mp3", "m4a", "wav"], disabled=mode != "audio")
    output_dir_text = st.text_input("Pasta de saida", str(DEFAULT_DOWNLOAD_DIR))

    st.divider()
    st.write("Sites aceitos:")
    st.write("YouTube, Instagram e TikTok")

st.info(
    "Use este app somente para baixar conteudo que voce criou, que e publico com permissao de download "
    "ou que voce tem autorizacao para salvar. O app nao remove marcas d'agua nem contorna protecoes."
)

url = st.text_input("Cole o link do video, musica, reel ou post")
confirm_rights = st.checkbox("Confirmo que tenho direito ou autorizacao para baixar este conteudo.")

download_button = st.button("Baixar", type="primary", disabled=not url or not confirm_rights)

has_ffmpeg = ffmpeg_available()

if not has_ffmpeg:
    if mode == "audio":
        st.warning(
            "Para converter audio para mp3, m4a ou wav, instale o FFmpeg e deixe o comando ffmpeg no PATH."
        )
    else:
        st.warning(
            "FFmpeg nao foi encontrado. O app vai baixar video em arquivo unico, que pode ter qualidade menor."
        )

if download_button:
    clean_url = url.strip()

    if not is_supported_url(clean_url):
        st.error("Link nao suportado. Cole uma URL do YouTube, Instagram ou TikTok.")
        st.stop()

    output_dir = sanitize_output_dir(output_dir_text)
    status_box = st.empty()
    progress_bar = st.progress(0)

    try:
        ydl_options = build_options(
            mode,
            output_dir,
            audio_format,
            quality,
            has_ffmpeg,
            status_box,
            progress_bar,
        )
        downloaded_files = download_media(clean_url, ydl_options)
    except Exception as exc:
        message = re.sub(r"\s+", " ", str(exc)).strip()
        st.error(f"Nao foi possivel baixar este conteudo: {message}")
        st.stop()

    st.success(f"Arquivos salvos em: {output_dir}")

    if downloaded_files:
        st.subheader("Arquivos gerados")
        for file_path in downloaded_files[:10]:
            st.write(str(file_path))
            try:
                with file_path.open("rb") as file_handle:
                    st.download_button(
                        "Salvar uma copia",
                        data=file_handle,
                        file_name=file_path.name,
                        key=str(file_path),
                    )
            except OSError:
                st.write("Arquivo salvo no disco, mas nao foi possivel abrir para botao de copia.")
    else:
        st.write("O download terminou, mas nao consegui listar o arquivo final automaticamente.")
