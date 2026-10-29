"""Where the retrieved pages of the House of Assembly's site (www.assembly.nl.ca) are kept for the build: one file
per URL, named by the first 32 hex digits of the sha256 of the URL. A page the site answered "this page could not be
found" is kept as NOT_FOUND. The build and the checks only read these files; nothing here uses the network."""
from __future__ import annotations

import hashlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / '.cache'
NOT_FOUND = b'<!-- lexifina-cache: HTTP 404 -->'


def cache_path(url: str) -> Path:
    return CACHE / hashlib.sha256(url.encode()).hexdigest()[:32]


def read(url: str) -> bytes:
    """The retrieved page of a URL; stops when it is missing."""
    path = cache_path(url)
    if not path.exists():
        raise SystemExit(f'not retrieved: {url} ({path.name})')
    return path.read_bytes()
