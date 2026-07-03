"""Robust direct model download from Hugging Face (stdlib only).

Exists because the legacy `cdn-lfs.huggingface.co` route used by the
huggingface_hub version available on Python 3.9 no longer resolves; the
`/resolve/` redirect to the current CDN works fine. Downloads resume via
Range on flaky connections and are sha256-verified. Files land in
~/.localflow/models/faster-whisper-<name>/, which Transcriber picks up
before falling back to the huggingface_hub cache.
"""
import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.request

MODELS_DIR = pathlib.Path.home() / ".localflow" / "models"

REPOS = {
    "tiny": "Systran/faster-whisper-tiny",
    "base": "Systran/faster-whisper-base",
    "small": "Systran/faster-whisper-small",
    "medium": "Systran/faster-whisper-medium",
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
SKIP_FILES = {".gitattributes", "README.md"}

# Single-file ggml models for whisper.cpp, from the official ggerganov repo.
# Requested as "ggml:<name>", e.g. `localflow download ggml:large-v3-turbo-q5_0`.
GGML_REPO = "ggerganov/whisper.cpp"
PROGRESS_EVERY = 25 * 1024 * 1024  # print every 25 MB


def model_dir(name: str) -> pathlib.Path:
    return MODELS_DIR / ("faster-whisper-" + name)


def _fetch(url: str, dest: pathlib.Path, chunk: int = 1 << 20) -> None:
    done = dest.stat().st_size if dest.exists() else 0
    request = urllib.request.Request(url)
    if done:
        request.add_header("Range", "bytes=%d-" % done)
    try:
        response = urllib.request.urlopen(request, timeout=30)
    except urllib.error.HTTPError as err:
        if err.code == 416:  # requested range not satisfiable = already complete
            return
        raise
    mode = "ab" if done and response.status == 206 else "wb"
    written = 0
    with open(dest, mode) as fh:
        while True:
            block = response.read(chunk)
            if not block:
                break
            fh.write(block)
            written += len(block)
            if written % PROGRESS_EVERY < chunk:
                print("  ... %d MB" % (dest.stat().st_size // (1024 * 1024)), flush=True)


def _fetch_with_retry(url: str, dest: pathlib.Path, attempts: int = 10) -> None:
    for attempt in range(1, attempts + 1):
        try:
            _fetch(url, dest)
            return
        except Exception as exc:  # noqa: BLE001 — resume-and-retry is the whole point
            print("  retry %d/%d (%s)" % (attempt, attempts, exc), flush=True)
            time.sleep(3)
    raise SystemExit("download failed after %d attempts: %s" % (attempts, url))


def _sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _siblings(repo: str):
    api_url = "https://huggingface.co/api/models/%s?blobs=true" % repo
    with urllib.request.urlopen(api_url, timeout=15) as response:
        return json.load(response)["siblings"]


def _download_verified(url: str, dest: pathlib.Path, meta: dict) -> None:
    lfs = meta.get("lfs") or {}
    expected_size = meta.get("size") or lfs.get("size")
    expected_sha = lfs.get("oid")
    for round_ in ("resume", "fresh"):
        print("%s (%s)" % (dest.name, round_), flush=True)
        _fetch_with_retry(url, dest)
        size_ok = expected_size is None or dest.stat().st_size == expected_size
        sha_ok = expected_sha is None or _sha256(dest) == expected_sha
        if size_ok and sha_ok:
            return
        print("  verifica fallita (size_ok=%s sha_ok=%s) — riscarico da zero" % (size_ok, sha_ok), flush=True)
        dest.unlink(missing_ok=True)
    raise SystemExit("verification failed twice for " + dest.name)


def _download_ggml(short_name: str) -> pathlib.Path:
    fname = "ggml-" + short_name + ".bin"
    meta = next((s for s in _siblings(GGML_REPO) if s["rfilename"] == fname), None)
    if meta is None:
        raise SystemExit("No '%s' in %s — check the name (e.g. large-v3-turbo-q5_0)" % (fname, GGML_REPO))
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    dest = MODELS_DIR / fname
    _download_verified("https://huggingface.co/%s/resolve/main/%s" % (GGML_REPO, fname), dest, meta)
    print("OK -> %s" % dest, flush=True)
    return dest


def download(name: str) -> pathlib.Path:
    if name.startswith("ggml:"):
        return _download_ggml(name[len("ggml:"):])
    repo = REPOS.get(name)
    if repo is None:
        raise SystemExit(
            "Unknown model '%s'. Choose from: %s — or ggml:<name> for whisper.cpp"
            % (name, ", ".join(sorted(REPOS)))
        )
    target = model_dir(name)
    target.mkdir(parents=True, exist_ok=True)
    for meta in _siblings(repo):
        fname = meta["rfilename"]
        if fname in SKIP_FILES:
            continue
        _download_verified("https://huggingface.co/%s/resolve/main/%s" % (repo, fname), target / fname, meta)
    print("OK -> %s" % target, flush=True)
    return target
