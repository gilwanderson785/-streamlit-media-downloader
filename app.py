from __future__ import annotations

import io
import mimetypes
import re
import shutil
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import streamlit as st
from yt_dlp import YoutubeDL


DOWNLOAD_ROOT = Path(tempfile.gettempdir()) / "streamlit_media_downloader"
MAX_FILESIZE = 250 * 1024 * 1024
MAX_IMAGES_IN_ZIP = 30
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
ALLOWED_HOSTS = (
    "youtube.com",
    "youtu.be",
    "instagram.com",
    "tiktok.com",
    "vm.tiktok.com",
)
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}


def normalize_url(url: str) -> str:
    value = url.strip()
    if value and not re.match(r"^https?://", value, flags=re.IGNORECASE):
        return f"https://{value}"
    return value


def clean_host(url: str) -> str:
    host = urlparse(normalize_url(url)).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def detect_platform(url: str) -> str | None:
    host = clean_host(url)
    if host == "youtu.be" or host.endswith("youtube.com"):
        return "YouTube"
    if host.endswith("instagram.com"):
        return "Instagram"
    if host.endswith("tiktok.com") or host == "vm.tiktok.com":
        return "TikTok"
    return None


def is_supported_url(url: str) -> bool:
    parsed = urlparse(normalize_url(url))
    if parsed.scheme not in {"http", "https"}:
        return False

    host = clean_host(url)
    return any(host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_HOSTS)


def safe_filename(value: str, fallback: str = "midia") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", " ", value or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:120] or fallback


def create_output_dir() -> Path:
    path = DOWNLOAD_ROOT / str(uuid.uuid4())
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


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def base_ydl_options() -> dict[str, Any]:
    return {
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "windowsfilenames": True,
        "restrictfilenames": False,
        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
        "force_ipv4": True,
        "http_headers": HTTP_HEADERS,
        "extractor_args": {
            "youtube": {
                "player_client": ["default", "ios", "android"],
            }
        },
    }


@st.cache_data(ttl=900, show_spinner=False)
def extract_media_info(url: str) -> dict[str, Any]:
    options = base_ydl_options()
    options.update(
        {
            "skip_download": True,
            "extract_flat": False,
            "noplaylist": False,
            "playlistend": MAX_IMAGES_IN_ZIP,
        }
    )
    with YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)

    if not isinstance(info, dict):
        raise RuntimeError("Nao consegui ler as informacoes desse link.")
    return info


def media_title(info: dict[str, Any]) -> str:
    return str(info.get("title") or info.get("fulltitle") or "Midia encontrada")


def info_entries(info: dict[str, Any]) -> list[dict[str, Any]]:
    entries = info.get("entries")
    if isinstance(entries, list):
        return [entry for entry in entries if isinstance(entry, dict)]
    return []


def is_image_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.startswith(("http://", "https://")):
        return False

    path = urlparse(value).path.lower()
    return any(path.endswith(ext) or ext in path for ext in IMAGE_EXTENSIONS)


def add_image_candidate(
    candidates: list[dict[str, str]],
    seen: set[str],
    url: Any,
    title: str,
    source: str,
) -> None:
    if not is_image_url(url) or url in seen:
        return

    seen.add(url)
    candidates.append(
        {
            "url": str(url),
            "title": safe_filename(title, "imagem"),
            "source": source,
        }
    )


def collect_image_candidates(info: dict[str, Any]) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    fallback_thumbnails: list[dict[str, str]] = []
    seen: set[str] = set()
    seen_fallback: set[str] = set()

    def walk(node: Any, inherited_title: str) -> None:
        if isinstance(node, dict):
            title = str(node.get("title") or node.get("alt_title") or inherited_title)
            ext = str(node.get("ext") or "").lower()
            direct_url = node.get("url")

            if ext in {"jpg", "jpeg", "png", "webp", "gif", "bmp", "avif"}:
                add_image_candidate(candidates, seen, direct_url, title, "imagem")

            for key in ("display_url", "display_src", "fullsize_url", "original_url", "url"):
                add_image_candidate(candidates, seen, node.get(key), title, "imagem")

            thumbnails = node.get("thumbnails")
            if isinstance(thumbnails, list):
                for thumbnail in thumbnails:
                    if isinstance(thumbnail, dict):
                        add_image_candidate(
                            fallback_thumbnails,
                            seen_fallback,
                            thumbnail.get("url"),
                            title,
                            "miniatura",
                        )

            for key in ("images", "entries", "formats"):
                value = node.get(key)
                if isinstance(value, list):
                    for item in value:
                        walk(item, title)
        elif isinstance(node, list):
            for item in node:
                walk(item, inherited_title)

    walk(info, media_title(info))
    return candidates or fallback_thumbnails


def best_thumbnail(info: dict[str, Any]) -> str | None:
    images = collect_image_candidates(info)
    if images:
        return images[0]["url"]

    thumbnails = info.get("thumbnails")
    if isinstance(thumbnails, list):
        for thumbnail in reversed(thumbnails):
            if isinstance(thumbnail, dict) and isinstance(thumbnail.get("url"), str):
                return thumbnail["url"]

    thumbnail = info.get("thumbnail")
    return thumbnail if isinstance(thumbnail, str) else None


def best_preview_video_url(info: dict[str, Any]) -> str | None:
    search_items = info_entries(info) or [info]

    for item in search_items:
        formats = item.get("formats") if isinstance(item, dict) else None
        if not isinstance(formats, list):
            continue

        playable: list[dict[str, Any]] = []
        for fmt in formats:
            if not isinstance(fmt, dict):
                continue
            url = fmt.get("url")
            ext = str(fmt.get("ext") or "").lower()
            vcodec = str(fmt.get("vcodec") or "").lower()
            acodec = str(fmt.get("acodec") or "").lower()
            protocol = str(fmt.get("protocol") or "").lower()
            if not isinstance(url, str) or vcodec == "none" or acodec == "none":
                continue
            if ext not in {"mp4", "webm"} or "m3u8" in protocol:
                continue
            playable.append(fmt)

        playable.sort(key=lambda fmt: int(fmt.get("height") or 0), reverse=True)
        if playable:
            return str(playable[0]["url"])
    return None


def render_preview(info: dict[str, Any], platform: str, images: list[dict[str, str]]) -> None:
    st.subheader("Pre-visualizacao")
    st.write(f"**{media_title(info)}**")
    st.caption(f"Plataforma detectada: {platform}")

    video_url = best_preview_video_url(info)
    thumbnail = best_thumbnail(info)

    if video_url:
        try:
            st.video(video_url)
        except Exception:
            if thumbnail:
                st.image(thumbnail, use_container_width=True)
    elif thumbnail:
        st.image(thumbnail, use_container_width=True)
    else:
        st.info("Consegui ler o link, mas a plataforma nao liberou uma pre-visualizacao direta.")

    description = info.get("description")
    if isinstance(description, str) and description.strip():
        with st.expander("Descricao"):
            st.write(description[:1200])

    entries = info_entries(info)
    if entries:
        st.caption(f"Foram encontrados {len(entries)} item(ns) nesse link.")

    if images:
        st.caption(f"Imagem(ns) encontrada(s): {len(images)}")


def build_download_options(
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

    common_options = base_ydl_options()
    common_options.update(
        {
            "outtmpl": str(output_dir / "%(extractor)s" / "%(title).180s [%(id)s].%(ext)s"),
            "progress_hooks": [progress_hook],
            "max_filesize": MAX_FILESIZE,
            "http_chunk_size": 10 * 1024 * 1024,
            "noplaylist": True,
        }
    )

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

    if has_ffmpeg:
        quality_map = {
            "Rapido ate 720p": "best[height<=720][ext=mp4][acodec!=none]/best[height<=720][acodec!=none]/bv*[height<=720]+ba/b[height<=720]",
            "Ate 1080p": "best[height<=1080][ext=mp4][acodec!=none]/best[height<=1080][acodec!=none]/bv*[height<=1080]+ba/b[height<=1080]",
            "Melhor qualidade": "best[ext=mp4][acodec!=none]/best[acodec!=none]/bv*+ba/best",
            "Arquivo menor": "worst[ext=mp4][acodec!=none]/worst[acodec!=none]/worst",
        }
        common_options["format"] = quality_map[quality]
        common_options["merge_output_format"] = "mp4"
        return common_options

    common_options["format"] = (
        "best[ext=mp4][vcodec!=none][acodec!=none]"
        "/best[vcodec!=none][acodec!=none]"
        "/best[height<=720][ext=mp4]"
        "/best"
    )
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


def read_download(file_path: Path) -> bytes:
    with file_path.open("rb") as file_handle:
        return file_handle.read()


def guess_mime(file_path: Path) -> str:
    suffix = file_path.suffix.lower()
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix in {".m4a", ".aac"}:
        return "audio/mp4"
    if suffix == ".wav":
        return "audio/wav"
    if suffix == ".webm":
        return "video/webm"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "video/mp4"


def image_extension(url: str, content_type: str = "") -> str:
    parsed_ext = Path(urlparse(url).path).suffix.lower()
    if parsed_ext in IMAGE_EXTENSIONS:
        return ".jpg" if parsed_ext == ".jpeg" else parsed_ext

    guessed = mimetypes.guess_extension(content_type.split(";")[0].strip()) if content_type else None
    if guessed and guessed.lower() in IMAGE_EXTENSIONS:
        return ".jpg" if guessed.lower() == ".jpeg" else guessed.lower()
    return ".jpg"


def fetch_image_bytes(candidate: dict[str, str], index: int) -> tuple[str, bytes, str]:
    request = Request(candidate["url"], headers=HTTP_HEADERS)
    with urlopen(request, timeout=30) as response:
        content_type = response.headers.get("content-type", "")
        data = response.read(MAX_FILESIZE + 1)

    if len(data) > MAX_FILESIZE:
        raise RuntimeError("A imagem ultrapassa o limite de tamanho permitido.")

    ext = image_extension(candidate["url"], content_type)
    filename = f"{index:02d}-{safe_filename(candidate.get('title', 'imagem'), 'imagem')}{ext}"
    mime = content_type.split(";")[0] or guess_mime(Path(filename))
    return filename, data, mime


def build_images_zip(candidates: list[dict[str, str]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, candidate in enumerate(candidates[:MAX_IMAGES_IN_ZIP], start=1):
            filename, data, _mime = fetch_image_bytes(candidate, index)
            archive.writestr(filename, data)
    return buffer.getvalue()


def friendly_error(exc: Exception) -> str:
    message = re.sub(r"\x1b\[[0-9;]*m", "", str(exc))
    message = re.sub(r"\s+", " ", message).strip()

    if "HTTP Error 403" in message or "Forbidden" in message:
        return (
            "O site bloqueou este arquivo no servidor online. "
            "Tente outro link publico, escolha uma qualidade menor ou use conteudo sem restricao. "
            "Instagram, TikTok e YouTube podem bloquear alguns links em servidores cloud."
        )

    if "ffmpeg" in message.lower():
        return "O FFmpeg e necessario para unir video com audio ou converter audio. Verifique o packages.txt no deploy."

    return message or "Erro desconhecido."


st.set_page_config(
    page_title="Baixador de Midias",
    page_icon="download",
    layout="centered",
)

st.title("Baixador de midias")
st.caption("YouTube, Instagram e TikTok para conteudo proprio, publico ou autorizado.")

st.markdown(
    """
    <style>
    .media-logo-row {
        align-items: center;
        display: flex;
        flex-wrap: wrap;
        gap: 0.6rem;
        margin: 0.8rem 0 1.2rem;
    }
    .media-logo-chip {
        align-items: center;
        background: #ffffff;
        border: 1px solid rgba(49, 51, 63, 0.18);
        border-radius: 8px;
        color: #262730;
        display: inline-flex;
        font-size: 0.92rem;
        font-weight: 600;
        gap: 0.45rem;
        line-height: 1;
        padding: 0.48rem 0.64rem;
    }
    .media-logo-chip img {
        display: block;
        height: 20px;
        width: 20px;
    }
    </style>
    <div class="media-logo-row" aria-label="Midias aceitas">
        <span class="media-logo-chip">
            <img src="https://cdn.simpleicons.org/youtube/FF0000" alt="YouTube">
            YouTube
        </span>
        <span class="media-logo-chip">
            <img src="https://cdn.simpleicons.org/instagram/E4405F" alt="Instagram">
            Instagram
        </span>
        <span class="media-logo-chip">
            <img src="https://cdn.simpleicons.org/tiktok/000000" alt="TikTok">
            TikTok
        </span>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.header("Configuracao")
    mode_label = st.radio("Tipo de download", ["Video com audio", "Somente audio", "Imagem/foto"])
    mode = {"Video com audio": "video", "Somente audio": "audio", "Imagem/foto": "image"}[mode_label]
    quality = st.selectbox(
        "Qualidade do video",
        ["Rapido ate 720p", "Ate 1080p", "Melhor qualidade", "Arquivo menor"],
        disabled=mode != "video",
    )
    audio_format = st.selectbox("Formato do audio", ["mp3", "m4a", "wav"], disabled=mode != "audio")

    st.divider()
    st.write("Sites aceitos:")
    st.write("YouTube, Instagram e TikTok")

url = st.text_input("Cole o link do video, musica, reel, post ou carrossel")
confirm_rights = st.checkbox("Confirmo que tenho direito ou autorizacao para baixar este conteudo.")
has_ffmpeg = ffmpeg_available()

if not has_ffmpeg:
    if mode == "audio":
        st.warning("Para converter audio para mp3, m4a ou wav, instale o FFmpeg e deixe o comando ffmpeg no PATH.")
    elif mode == "video":
        st.warning("FFmpeg nao foi encontrado. O app vai priorizar arquivos de video que ja tenham audio embutido.")

info: dict[str, Any] | None = None
images: list[dict[str, str]] = []
clean_url = normalize_url(url)
platform = detect_platform(clean_url) if clean_url else None

if clean_url:
    if not is_supported_url(clean_url):
        st.error("Link nao suportado. Cole uma URL do YouTube, Instagram ou TikTok.")
        st.stop()

    if platform:
        st.success(f"Link do {platform} identificado. Analisando pre-visualizacao e midias disponiveis.")

    try:
        with st.spinner("Analisando o link..."):
            info = extract_media_info(clean_url)
            images = collect_image_candidates(info)
    except Exception as exc:
        st.error(f"Nao foi possivel analisar este link: {friendly_error(exc)}")
        st.stop()

    render_preview(info, platform or "Desconhecida", images)

if mode == "image" and clean_url and info:
    st.subheader("Imagens disponiveis")
    if not images:
        st.warning("Nao encontrei imagens baixaveis nesse link. Se for video, escolha 'Video com audio'.")
    else:
        labels = [f"Imagem {index + 1} - {item['title']}" for index, item in enumerate(images)]
        selected_label = st.selectbox("Escolha uma imagem", labels)
        selected_index = labels.index(selected_label)
        selected_image = images[selected_index]
        st.image(selected_image["url"], caption=selected_label, use_container_width=True)

        col_one, col_two = st.columns(2)
        with col_one:
            if st.button("Preparar imagem escolhida", type="primary", disabled=not confirm_rights):
                try:
                    filename, data, mime = fetch_image_bytes(selected_image, selected_index + 1)
                    st.download_button("Baixar imagem escolhida", data=data, file_name=filename, mime=mime)
                except Exception as exc:
                    st.error(f"Nao foi possivel baixar a imagem: {friendly_error(exc)}")

        with col_two:
            if st.button("Preparar todas as imagens", disabled=not confirm_rights):
                try:
                    zip_bytes = build_images_zip(images)
                    st.download_button(
                        "Baixar todas em ZIP",
                        data=zip_bytes,
                        file_name=f"{safe_filename(media_title(info), 'imagens')}.zip",
                        mime="application/zip",
                    )
                except Exception as exc:
                    st.error(f"Nao foi possivel preparar as imagens: {friendly_error(exc)}")

elif clean_url and info:
    download_button = st.button("Baixar", type="primary", disabled=not confirm_rights)

    if download_button:
        output_dir = create_output_dir()
        status_box = st.empty()
        progress_bar = st.progress(0)

        try:
            ydl_options = build_download_options(
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
            st.error(f"Nao foi possivel baixar este conteudo: {friendly_error(exc)}")
            st.stop()

        if downloaded_files:
            st.success("Arquivo pronto para baixar.")
            for file_path in downloaded_files[:5]:
                try:
                    file_bytes = read_download(file_path)
                except OSError:
                    st.error("O arquivo foi baixado, mas nao foi possivel prepara-lo para entrega.")
                    continue

                st.download_button(
                    f"Baixar {file_path.name}",
                    data=file_bytes,
                    file_name=file_path.name,
                    mime=guess_mime(file_path),
                    key=str(file_path),
                )
        else:
            st.error("O download terminou, mas nao consegui preparar o arquivo final para entrega.")

st.divider()
st.caption(
    "Desenvolvido por Gil Wanderson - "
    "[Instagram](https://www.instagram.com/giloliveira147/)"
)
