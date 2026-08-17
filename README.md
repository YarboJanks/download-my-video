# download-my-video

A simple [yt-dlp](https://github.com/yt-dlp/yt-dlp) wrapper that downloads a
YouTube video (or a batch of them) using cookies pulled live from a local
browser's existing, logged-in session — no manual cookie export, no
re-entering your YouTube password.

## Why this exists

YouTube increasingly requires a "proof of origin" (PO) token and a JS
"n-parameter" challenge solution to serve full-quality formats. This wrapper
wires up the pieces needed to satisfy both, and adds a couple of safety nets:

- **URL sanitizing**: strips stray backslashes that shells (notably zsh)
  sometimes insert before `?`/`=`/`&` when a URL is pasted or auto-escaped.
  Left uncorrected, yt-dlp's generic extractor can silently redirect a
  mangled URL to youtube.com's homepage and download your entire recommended
  feed as a giant "playlist" instead of the single video you asked for.
- **Resolve-before-download verification**: refuses to download if the URL
  resolves to anything other than the single expected video (e.g. a
  channel/playlist/redirect), and refuses if the resolved video ID doesn't
  match what was requested.

## Requirements

- Python 3.10+ (older interpreters have had issues with some yt-dlp/plugin
  logging internals).
- [ffmpeg](https://ffmpeg.org/) (`brew install ffmpeg`) — merges separate
  video+audio streams into a single file.
- [Deno](https://deno.com/) (`brew install deno`) — runtime yt-dlp uses to
  solve YouTube's JS "n-parameter" challenge.
- Node.js + a local clone of
  [Brainicism/bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider)
  (built via `npm install && npx tsc` in its `server/` folder), used to run a
  local HTTP server that mints the PO token YouTube requires for
  full-quality downloads. Clone it to `~/bgutil-ytdlp-pot-provider` so this
  script can find and auto-start it.

Python dependencies:

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Single video, will prompt for which browser's session to use
python3 download_my_video.py <youtube_url>

# Single video, skip the prompt
python3 download_my_video.py <youtube_url> --browser firefox

# Batch download from a file (one URL per line, '#' for comments)
cp urls.example.txt urls.txt   # then edit urls.txt with your own URLs
python3 download_my_video.py --batch-file urls.txt --browser chrome

# Custom output directory (default: ~/Downloads)
python3 download_my_video.py <youtube_url> --browser chrome --output-dir ~/Movies
```

Supported `--browser` values: `chrome`, `chromium`, `brave`, `edge`,
`firefox`, `opera`, `safari`, `vivaldi`.

## How it works

1. Starts (or reuses) a local PO-token HTTP server on port 4416.
2. Sanitizes and validates the input URL, extracting the canonical 11-char
   video ID.
3. Resolves the URL via yt-dlp without downloading, verifying it points at
   exactly one video matching the expected ID.
4. Downloads best video + audio and merges them into an mp4, using cookies
   from the chosen browser's local profile.
