#!/usr/bin/env python3
"""
Simple yt-dlp wrapper: takes a YouTube URL, pulls auth from a local browser's
existing login session (no need to re-enter credentials or export cookies
manually), and downloads to ~/Downloads.

Usage:
    python3 download_my_video.py <youtube_url>
    python3 download_my_video.py <youtube_url> --browser firefox
    python3 download_my_video.py --batch-file urls.txt --browser chrome

Requires (see README notes below for why each is needed):
    - A Python 3.10+ interpreter (this repo's .venv-ytdlp/ uses 3.12 - older
      yt-dlp/plugin combinations crash under 3.9's logging internals).
    - pip install yt-dlp bgutil-ytdlp-pot-provider
    - Node.js + a local clone of github.com/Brainicism/bgutil-ytdlp-pot-provider
      (built via `npm install && npx tsc` in its server/ folder), used to mint
      the "PO token" YouTube now requires for full-quality downloads.
    - Deno (`brew install deno`) - yt-dlp's JS challenge ("n-parameter")
      solver runtime.
    - ffmpeg (`brew install ffmpeg`) - to merge separate video+audio streams.
"""

import argparse
import re
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

try:
    import yt_dlp
except ImportError:
    print("yt-dlp isn't installed. Install it with: pip3 install yt-dlp")
    sys.exit(1)

# Browsers yt-dlp knows how to pull cookies from directly.
SUPPORTED_BROWSERS = [
    "chrome", "chromium", "brave", "edge", "firefox", "opera", "safari", "vivaldi",
]

# Matches a real YouTube video ID (11 chars, letters/digits/-/_) out of any
# common URL shape (watch?v=, youtu.be/, /shorts/, /embed/).
VIDEO_ID_RE = re.compile(
    r"(?:v=|youtu\.be/|/shorts/|/embed/)([A-Za-z0-9_-]{11})"
)

# Local PO-token HTTP server (bgutil-ytdlp-pot-provider), used to satisfy
# YouTube's proof-of-origin token requirement for full-quality formats.
POT_SERVER_PORT = 4416
POT_SERVER_SCRIPT = Path.home() / "bgutil-ytdlp-pot-provider" / "server" / "build" / "main.js"


def sanitize_url(raw_url: str) -> str:
    """
    Strip stray backslashes that shells (notably zsh, when a URL is pasted
    or auto-escaped) sometimes insert before '?' and '=', e.g.
    'watch\\?v\\=ID' instead of 'watch?v=ID'. Left as-is, yt-dlp's generic
    extractor doesn't recognize the mangled URL as a video link, silently
    redirects to youtube.com's homepage, and can end up treating your
    recommended-videos feed as a giant "playlist" to download instead of
    the single video you asked for - not a v=ID typo, but a real footgun
    since it can pull down unrelated content that isn't yours.
    """
    cleaned = raw_url.replace("\\?", "?").replace("\\=", "=").replace("\\&", "&")
    match = VIDEO_ID_RE.search(cleaned)
    if not match:
        raise ValueError(
            f"Could not find an 11-character YouTube video ID in: {raw_url!r}\n"
            "Pass a direct video URL, e.g. https://www.youtube.com/watch?v=XXXXXXXXXXX"
        )
    video_id = match.group(1)
    # Rebuild a clean, canonical single-video URL instead of trusting
    # whatever shape/escaping the input arrived in.
    return f"https://www.youtube.com/watch?v={video_id}"


def prompt_for_browser() -> str:
    """Ask which browser holds the logged-in YouTube session to borrow cookies from."""
    print("Which browser are you currently logged into YouTube with?")
    for i, name in enumerate(SUPPORTED_BROWSERS, 1):
        print(f"  {i}. {name}")
    while True:
        choice = input("Enter a number (or type the browser name): ").strip().lower()
        if choice.isdigit() and 1 <= int(choice) <= len(SUPPORTED_BROWSERS):
            return SUPPORTED_BROWSERS[int(choice) - 1]
        if choice in SUPPORTED_BROWSERS:
            return choice
        print("Not recognized, try again.")


def _port_is_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def ensure_pot_server_running() -> Optional[subprocess.Popen]:
    """
    Start the local bgutil PO-token HTTP server if it isn't already running.
    Returns the Popen handle if this call started it (so it can be cleaned
    up), or None if a server was already running / couldn't be started.
    """
    if _port_is_open(POT_SERVER_PORT):
        return None  # already running (e.g. started manually in another shell)

    if not POT_SERVER_SCRIPT.is_file():
        print(
            f"Note: PO-token server script not found at {POT_SERVER_SCRIPT}. "
            "Downloads may fail with 'Requested format is not available' "
            "without it - see this script's module docstring for setup steps."
        )
        return None

    proc = subprocess.Popen(
        ["node", str(POT_SERVER_SCRIPT), "-p", str(POT_SERVER_PORT)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(20):  # wait up to ~4s for it to come up
        if _port_is_open(POT_SERVER_PORT):
            break
        time.sleep(0.2)
    return proc


def download(url: str, expected_video_id: str, browser: str, output_dir: Path) -> None:
    ydl_opts = {
        # Pulls cookies live from the named browser's local profile - no
        # manual cookie export, no re-entering your YouTube password.
        "cookiesfrombrowser": (browser,),
        "outtmpl": str(output_dir / "%(title)s.%(ext)s"),
        "format": "bestvideo+bestaudio/best",
        "merge_output_format": "mp4",
        "noplaylist": True,
        # Allows yt-dlp to fetch its JS "n-parameter" challenge-solver
        # component (requires Deno) on demand rather than erroring out.
        "remote_components": ["ejs:github"],
        "extractor_args": {
            "youtube": {
                # web_creator/mweb are less affected by YouTube's SABR-only
                # rollout than the plain "web"/"tv" clients, which strip
                # direct format URLs and leave only an old, now-blocked
                # progressive format as a fallback.
                "player_client": ["web_creator", "mweb", "tv", "web"],
            },
            # Points at the locally-running bgutil PO-token server (started
            # by ensure_pot_server_running() below) so YouTube's proof-of-
            # origin token requirement for full-quality formats is satisfied.
            "youtubepot-bgutilhttp": {
                "base_url": [f"http://127.0.0.1:{POT_SERVER_PORT}"],
            },
        },
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        # Resolve first without downloading, so a malformed/redirected URL
        # that resolves to a channel/playlist/recommended feed (as opposed
        # to the single video requested) is caught and refused before
        # anything gets pulled down. This guards specifically against the
        # "resolved to homepage -> downloaded the recommended feed as a
        # 548-item playlist" failure mode.
        info = ydl.extract_info(url, download=False)
        if info.get("_type") not in (None, "video"):
            raise RuntimeError(
                f"Refusing to download: this URL resolved to a '{info.get('_type')}' "
                f"({info.get('title', 'untitled')}), not a single video. This usually "
                "means the URL got mangled (e.g. shell-escaped '?'/'=' characters) "
                "and redirected somewhere unexpected. Double-check the URL and retry."
            )
        actual_id = info.get("id")
        if actual_id != expected_video_id:
            raise RuntimeError(
                f"Refusing to download: expected video ID {expected_video_id!r} but "
                f"resolved to {actual_id!r} ('{info.get('title', 'untitled')}'). "
                "Aborting rather than downloading the wrong content."
            )
        ydl.download([url])


def process_one(url: str, browser: str, output_dir: Path) -> bool:
    """Download a single URL through the full safety pipeline. Returns True on success."""
    try:
        clean_url = sanitize_url(url)
    except ValueError as e:
        print(f"Skipping {url!r}: {e}")
        return False
    video_id = VIDEO_ID_RE.search(clean_url).group(1)

    if clean_url != url:
        print(f"Note: cleaned up URL to {clean_url} (raw input had escaping issues)")

    print(f"Downloading {clean_url} using {browser}'s session -> {output_dir}")
    try:
        download(clean_url, video_id, browser, output_dir)
        return True
    except RuntimeError as e:
        print(f"Error downloading {clean_url}: {e}")
        return False


def read_batch_urls(batch_file: Path) -> List[str]:
    """
    Read one URL per line from a text file. Blank lines and lines starting
    with '#' (comments) are skipped.
    """
    lines = batch_file.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "url", nargs="?",
        help="YouTube video URL. Omit if using --batch-file instead.",
    )
    parser.add_argument(
        "--batch-file",
        help="Path to a text file with one YouTube URL per line "
        "(blank lines and '#'-prefixed comments are ignored). Each URL "
        "goes through the same sanitize/verify/download steps as a single "
        "URL; a failure on one line is reported and skipped rather than "
        "aborting the rest of the list.",
    )
    parser.add_argument(
        "--browser",
        choices=SUPPORTED_BROWSERS,
        help="Skip the prompt and use this browser's cookies directly.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path.home() / "Downloads"),
        help="Where to save the video(s) (default: ~/Downloads).",
    )
    args = parser.parse_args()

    if not args.url and not args.batch_file:
        parser.error("Provide either a single url or --batch-file.")
    if args.url and args.batch_file:
        parser.error("Provide either a single url or --batch-file, not both.")

    browser = args.browser or prompt_for_browser()
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.batch_file:
        batch_path = Path(args.batch_file).expanduser()
        if not batch_path.is_file():
            print(f"Error: batch file not found: {batch_path}")
            sys.exit(1)
        urls = read_batch_urls(batch_path)
        if not urls:
            print(f"Error: no URLs found in {batch_path}")
            sys.exit(1)
        print(f"Found {len(urls)} URL(s) in {batch_path}")
    else:
        urls = [args.url]

    started_pot_server = ensure_pot_server_running()
    try:
        succeeded, failed = 0, 0
        for i, url in enumerate(urls, 1):
            if len(urls) > 1:
                print(f"\n[{i}/{len(urls)}]")
            if process_one(url, browser, output_dir):
                succeeded += 1
            else:
                failed += 1
        if len(urls) > 1:
            print(f"\nDone: {succeeded} succeeded, {failed} failed.")
        elif failed:
            sys.exit(1)
    finally:
        if started_pot_server is not None:
            started_pot_server.terminate()


if __name__ == "__main__":
    main()
