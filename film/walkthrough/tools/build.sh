#!/bin/sh
# Build both walkthrough films from the captures, recordings and narration in film/walkthrough.
#
#   sh film/walkthrough/tools/build.sh [fps]        (default 60; previews: 30)
#
# Needs: macOS Swift toolchain, the licensed MP3s in film/music/licensed (fetch_music.sh), the Pexels clip in
# film/assets/broll (fetch_broll.sh), and the captures in film/walkthrough/captures (tools/record + scripts/*.json
# against a local Kairos server; see QA.md). Serves film/ on 127.0.0.1:8772 while it renders.
set -e
FPS=${1:-60}
W=$(cd "$(dirname "$0")/.." && pwd)
cd "$W"
for t in record render mix finish sheet thumbs qa_frames; do
  [ tools/$t -nt tools/$t.swift ] || swiftc -O tools/$t.swift -o tools/$t
done
python3 ../tools/serve.py 8772 >/dev/null 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
sleep 1
mkdir -p out
for f in product technical; do
  ./tools/render --film $f --qa 1                      # overlay collisions → exit 2
  ./tools/render --film $f --clicks mix/${f}_clicks.json
  ./tools/mix mix/$f.json
  afconvert -f m4af -d aac -b 256000 audio/${f}_mix.wav audio/${f}_mix.m4a
  ./tools/render --film $f --out out/${f}_video.mov --fps "$FPS"
  ./tools/finish out/${f}_video.mov audio/${f}_mix.m4a out/kairos_$f.mp4 out/kairos_${f}_720p.mp4
  ./tools/qa_frames out/kairos_$f.mp4 1                # empty frames → exit 1
done
mkdir -p ../out
cp out/kairos_product.mp4 out/kairos_product_720p.mp4 out/kairos_technical.mp4 out/kairos_technical_720p.mp4 ../out/
echo "films in film/out/"
