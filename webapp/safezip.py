"""Extract an uploaded vault zip without trusting anything in it.

Rejects absolute paths, '..' (zip-slip), symlinks and device files, and
caps the file count and the real (not header-claimed) uncompressed size,
so a zip bomb stops at the cap instead of filling the disk.
"""

import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

READING_ORDER = "Manuscript Reading Order.md"


class UploadError(Exception):
    """The upload can't be used; the message is shown to the uploader."""


def _member_path(name: str) -> PurePosixPath:
    name = name.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or (path.parts and ":" in path.parts[0]):
        raise UploadError(f"The zip contains an unsafe path ({name!r}), so it was rejected.")
    return path


def extract(zip_path: Path, dest: Path, *, max_bytes: int, max_files: int) -> None:
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise UploadError("That file isn't a valid .zip.") from None
    with zf:
        members = zf.infolist()
        if len(members) > max_files:
            raise UploadError(f"The zip has more than {max_files} files.")
        dest = dest.resolve()
        written = 0
        for info in members:
            rel = _member_path(info.filename)
            if not rel.parts or rel.parts[0] == "__MACOSX":
                continue
            mode = info.external_attr >> 16
            if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):  # 0: no Unix type recorded
                raise UploadError(f"The zip contains a link or special file ({info.filename!r}).")
            target = (dest / rel).resolve()
            if not target.is_relative_to(dest):
                raise UploadError(f"The zip contains an unsafe path ({info.filename!r}).")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                with zf.open(info) as src, open(target, "wb") as out:
                    while chunk := src.read(64 * 1024):
                        written += len(chunk)
                        if written > max_bytes:
                            raise UploadError(f"The vault is larger than {max_bytes // 2**20} MB unzipped.")
                        out.write(chunk)
            except (zipfile.BadZipFile, RuntimeError, NotImplementedError) as e:
                raise UploadError(f"Couldn't read {info.filename!r} from the zip ({e}).") from None


def find_vault_root(root: Path, max_depth: int = 3) -> Path:
    """The folder holding Manuscript Reading Order.md: the zip's top level,
    or a folder inside it (zipping the vault folder itself adds one level)."""
    level = [root]
    for _ in range(max_depth + 1):
        for d in level:
            if (d / READING_ORDER).is_file():
                return d
        level = [c for d in level for c in sorted(d.iterdir())
                 if c.is_dir() and c.name != "__MACOSX" and not c.is_symlink()]
    raise UploadError(f"Couldn't find '{READING_ORDER}' in the zip. Zip your whole vault folder "
                      f"(the one holding Book Info.md and Manuscript/) and try again.")


def remove(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
