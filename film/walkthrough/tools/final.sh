#!/bin/bash
# v3 finals: render one film at 60 fps (or $FPS), mux, verify, and collect the QA evidence in qa/.
#
#   bash tools/final.sh product3 kairos_product   [out dir, default ../out]
#   bash tools/final.sh technical3 kairos_technical
#
# Needs the film already laid out (tools/build_*3.py), mixed (tools/mix) and captioned (tools/captions.py),
# the comp/*.mov clips (tools/shots_product.py) and film/ served on 127.0.0.1:8772.
set -e
FILM=$1; NAME=$2; OUT=${3:-../out}; FPS=${FPS:-60}
cd "$(dirname "$0")/.."
mkdir -p qa "$OUT"
L=qa/${NAME}_final.log; : > "$L"
./tools/mix mix/${FILM}.json | head -3 | tee -a "$L"
./tools/render --film "$FILM" --qa 1 | tail -1 | tee -a "$L"                     # overlay collisions → exit 2
afconvert -f m4af -d aac -b 256000 audio/${FILM}_mix.wav audio/${FILM}_mix.m4a
./tools/render --film "$FILM" --out out/${FILM}_final.mov --fps "$FPS" | tail -1 | tee -a "$L"
./tools/finish out/${FILM}_final.mov audio/${FILM}_mix.m4a "$OUT/$NAME.mp4" "$OUT/${NAME}_720p.mp4" 2>&1 | grep -E '\.mp4:|fail' | tee -a "$L"
./tools/qa_frames "$OUT/$NAME.mp4" 1 | tail -1 | tee -a "$L"                   # empty frames → exit 1
echo "afinfo $(afinfo "$OUT/$NAME.mp4" | grep -m1 'estimated duration')" | tee -a "$L"
# loudness after AAC encoding, from the master's own audio track
S=$(mktemp -d); afconvert -f WAVE -d LEI16@44100 "$OUT/$NAME.mp4" "$S/a.wav"
[ -x out/score_analyze ] || swiftc -O ../tools/score_analyze.swift -o out/score_analyze
echo "encoded $(out/score_analyze "$S/a.wav" | grep -iE 'integrated|peak' | tr '\n' ' ')" | tee -a "$L"
rm -rf "$S"
# what is on screen every 5 s, and thumbnails from the finished master at the same times
./tools/render --film "$FILM" --describe qa/${NAME}_5s.jsonl --every 5 | tail -1
rm -rf out/qa_$NAME; ./tools/thumbs "$OUT/$NAME.mp4" out/qa_$NAME 5 480 >/dev/null
./tools/sheet qa/${NAME}_every5s.jpg 5 380 out/qa_$NAME/*.png | tail -1
echo "done $NAME" | tee -a "$L"
