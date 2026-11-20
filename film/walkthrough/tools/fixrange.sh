#!/bin/bash
# Re-render only a time range of a FINAL (with a 1 s handle each side) at 4K, splice it into the silent 4K master,
# and re-mux the unchanged audio into the final 4K and 1080p files.
#   bash tools/fixrange.sh <product4|technical4> <kairos_product|kairos_technical> <from s> <to s>
W_OUT="$(cd "$(dirname "$0")/../../../.." && pwd)/film_out"
cd "$(dirname "$0")/.."
f=$1; name=$2; a=$(python3 -c "print('%.4f' % (round(max(0, $3 - 1.0) * 30) / 30))"); b=$(python3 -c "print('%.4f' % (round(($4 + 1.0) * 30) / 30))")
# --preroll: the range starts with every glass window already painted (without it the first frames show bare background)
./tools/render --film $f --out out/${f}_seg_4k.mov --fps 30 --size 3840x2160 --from $a --to $b --preroll 1.0 | tail -1
cp out/${f}_final_4k.mov out/${f}_final_4k_before_fix.mov   # fixes accumulate: each splice starts from the current master
./tools/splice out/${f}_final_4k_before_fix.mov out/${f}_seg_4k.mov $a out/${f}_final_4k.mov
./tools/finish out/${f}_final_4k.mov audio/${f}_mix.m4a "$W_OUT/${name}_final_4k.mp4" "$W_OUT/${name}_final_1080p.mp4" 1080 2>&1 | grep -E '\.mp4:|fail'
