#!/bin/bash
# Re-render only a time range (with a 1 s handle each side), splice it into the film's silent master, re-mux.
#   bash tools/fixrange.sh <film id: product4|technical4> <output name, e.g. kairos_product_v4d> <from s> <to s> [fps=30]
W_OUT="$(cd "$(dirname "$0")/../../../.." && pwd)/film_out"
cd "$(dirname "$0")/.."
f=$1; name=$2; a=$(python3 -c "print(max(0, $3 - 1.0))"); b=$(python3 -c "print($4 + 1.0)"); fps=${5:-30}
./tools/render --film $f --out out/${f}_seg.mov --fps $fps --from $a --to $b | tail -1
cp out/${f}_preview30.mov out/${f}_preview30_before_fix.mov
./tools/splice out/${f}_preview30_before_fix.mov out/${f}_seg.mov $a out/${f}_preview30.mov
./tools/finish out/${f}_preview30.mov audio/${f}_mix.m4a "$W_OUT/${name}_preview30.mp4" "$W_OUT/${name}_preview30_720p.mp4" 2>&1 | grep -E '\.mp4:|fail'
