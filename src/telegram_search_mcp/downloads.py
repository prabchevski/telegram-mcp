"""Bounded file exports to private local storage; never execute retrieved content."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import shutil
import stat
import uuid

from pydantic import Field
from .backend import MediaError, MediaTooLargeError
from .models import OutputModel, TrustBoundary
from .navigation import Request, ChatId, Id, cloud_chat
from .paths import ensure_private_dir

MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024


class DownloadRequest(Request):
    chat_id: ChatId
    message_id: Id
    max_bytes: int = Field(default=20 * 1024 * 1024, strict=True, ge=1, le=MAX_DOWNLOAD_BYTES)


class DownloadResult(OutputModel):
    trust_boundary: TrustBoundary = Field(default_factory=TrustBoundary)
    chat_id: int
    message_id: int
    path: str = Field(max_length=4096)
    file_name: str = Field(max_length=200)
    size_bytes: int = Field(ge=1, le=MAX_DOWNLOAD_BYTES)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    mime_type: str = Field(max_length=100)


def candidate(content: dict) -> tuple[dict, str, str]:
    for kind, key, file_key, suffix in (
        ('messageDocument', 'document', 'document', '.bin'), ('messageAudio', 'audio', 'audio', '.mp3'),
        ('messageVideo', 'video', 'video', '.mp4'), ('messageVoiceNote', 'voice_note', 'voice', '.ogg'),
        ('messageVideoNote', 'video_note', 'video', '.mp4'), ('messageAnimation', 'animation', 'animation', '.mp4')):
        if content.get('@type') == kind:
            media = content.get(key) or {}
            return media.get(file_key) or {}, str(media.get('file_name') or ('telegram' + suffix)), str(media.get('mime_type') or 'application/octet-stream')
    if content.get('@type') == 'messagePhoto':
        sizes = (content.get('photo') or {}).get('sizes') or []
        if sizes:
            best = max(sizes, key=lambda s: s.get('width', 0) * s.get('height', 0))
            return best.get('photo') or {}, 'telegram-photo.jpg', 'image/jpeg'
    raise MediaError('This message has no downloadable document, photo, audio or video')


def export_file(source: Path, cache: Path, destination: Path, filename: str, maximum: int) -> tuple[Path, int, str]:
    try:
        resolved = source.resolve(strict=True)
        resolved.relative_to(cache.resolve(strict=True))
        if source != resolved:
            raise ValueError()
    except (OSError, ValueError, RuntimeError):
        raise MediaError('TDLib returned an unsafe media path') from None
    ensure_private_dir(destination)
    folder = destination / uuid.uuid4().hex
    folder.mkdir(mode=0o700)
    # Names remain usable, but never select directories or hidden/control files.
    name = ''.join(c for c in filename.replace('\\', '/').split('/')[-1] if c.isprintable() and c not in '/\\:')
    name = name.strip(' .')
    while len(name.encode('utf-8')) > 180:
        name = name[:-1]
    name = name or 'telegram-file.bin'
    target = folder / name
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise MediaError('Downloaded media must be a regular file owned by the current user')
            if not 1 <= info.st_size <= maximum:
                raise MediaTooLargeError('Downloaded file exceeds the requested limit or is empty')
            digest, size = hashlib.sha256(), 0
            out_fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(out_fd, 'wb') as out:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > maximum:
                        raise MediaTooLargeError('Downloaded file grew beyond the requested limit')
                    digest.update(chunk)
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            after = os.fstat(stream.fileno())
            if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns) or size != info.st_size:
                raise MediaError('Downloaded file changed while exporting; retry the download')
        return target, size, digest.hexdigest()
    except BaseException:
        shutil.rmtree(folder)
        raise


def download(session, request: DownloadRequest, *, cache: Path, destination: Path) -> dict:
    chat = cloud_chat(session, request.chat_id)
    message = session.request({'@type': 'getMessage', 'chat_id': request.chat_id, 'message_id': request.message_id})
    if message.get('chat_id') != request.chat_id or message.get('id') != request.message_id:
        raise MediaError('Telegram returned a different message')
    properties = session.request({'@type': 'getMessageProperties', 'chat_id': request.chat_id, 'message_id': request.message_id})
    content = message.get('content') or {}
    if chat.get('has_protected_content') or properties.get('can_be_saved') is not True or content.get('is_secret'):
        raise MediaError('Telegram does not allow saving this message')
    if any(message.get(k) for k in ('ttl', 'ttl_expires_in', 'self_destruct_type', 'self_destruct_in')):
        raise MediaError('Self-destructing media cannot be downloaded')
    file, name, mime = candidate(content)
    file_id = file.get('id', 0)
    if file_id <= 0:
        raise MediaError('This attachment has no downloadable file')
    if max(file.get('size', 0), file.get('expected_size', 0)) > request.max_bytes:
        raise MediaTooLargeError('Attachment exceeds max_bytes; the maximum local download is 100 MiB')
    result = session.request({'@type': 'downloadFile', 'file_id': file_id, 'priority': 16, 'offset': 0,
        'limit': request.max_bytes + 1, 'synchronous': True}, timeout=50)
    local = result.get('local') or {}
    if not local.get('is_downloading_completed'):
        # Cancellation affects only downloading, never the Telegram message.
        session.request({'@type': 'cancelDownloadFile', 'file_id': file_id, 'only_if_pending': False})
        raise MediaError('Download did not finish within its bounds; retry or raise max_bytes within 100 MiB')
    path, size, digest = export_file(Path(local.get('path', '')), cache, destination, name, request.max_bytes)
    return DownloadResult(chat_id=request.chat_id, message_id=request.message_id, path=str(path),
        file_name=path.name, size_bytes=size, sha256=digest, mime_type=mime[:100]).model_dump(mode='json')
