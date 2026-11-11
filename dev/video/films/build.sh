#!/usr/bin/env bash
# Rebuild the two T-067 films from the GPU take (dev/video/out/footage/film1.mp4) in one command:
#   bash dev/video/films/build.sh [b45|kinoglaz ...]
# Music: Kevin MacLeod (incompetech.com), CC BY 4.0; fonts: Google Fonts (OFL) — downloaded, not committed.
# Render on the GPU: NVENC encode (--gpu) and Chrome with the host GPU (--browser-gpu).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
mkdir -p music fonts
inc() { [ -s "music/$2" ] || curl -fsSL -o "music/$2" "https://incompetech.com/music/royalty-free/mp3-royaltyfree/$1.mp3"; }
inc "Maple%20Leaf%20Rag" maple_leaf_rag.mp3
inc "Gymnopedie%20No%201" gymnopedie_no_1.mp3
inc "Mechanolith" mechanolith.mp3
gf() { [ -s "fonts/$2" ] || curl -fsSL --retry 3 -o "fonts/$2" "https://github.com/google/fonts/raw/main/ofl/$1"; }
gf "rubikmonoone/RubikMonoOne-Regular.ttf" RubikMonoOne-Regular.ttf
gf "oswald/Oswald%5Bwght%5D.ttf" Oswald.ttf
gf "playfairdisplay/PlayfairDisplay%5Bwght%5D.ttf" PlayfairDisplay.ttf
for film in "${@:-b45 kinoglaz}"; do
  for f in $film; do
    mkdir -p "$f/assets/fonts" "$f/assets/music" "$f/assets/sfx"
    cp fonts/*.ttf "$f/assets/fonts/"; cp music/*.mp3 "$f/assets/music/"
    [ -s "$f/assets/sfx/click.wav" ] || ffmpeg -v error -y -f lavfi -i "anoisesrc=d=0.03:c=white:a=0.9:r=48000:seed=7" \
      -af "highpass=f=2000,afade=t=out:st=0.004:d=0.026,volume=0.9" -ac 2 "$f/assets/sfx/click.wav"
    [ -s "$f/assets/paper.png" ] || ffmpeg -v error -y -f lavfi -i "color=c=0xEDE6D6:s=512x512,noise=alls=10:allf=u,format=rgb24" \
      -frames:v 1 "$f/assets/paper.png"
    (cd "$f" && python edl.py)
    python cut.py "$f"
    (cd "$f" && npx hyperframes check --json > /dev/null && npx hyperframes render --gpu --browser-gpu -q standard -o "../../out/film-$f.mp4")
    python loud.py "../out/film-$f.mp4"
  done
done
