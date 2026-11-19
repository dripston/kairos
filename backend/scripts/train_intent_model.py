"""Train the K09 intent stack's tier-2 model (pure Python, seeded, deterministic).

    python scripts/train_intent_model.py            # writes kairos_backend/data/intent_nb_v1.json

SIMULATED: the corpus is generated from templates (kairos_backend/intent_corpus.py); no customer data.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kairos_backend.intent_corpus import TRAIN_SEED, train_model  # noqa: E402
from kairos_backend.intent_stack import MODEL_PATH  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=TRAIN_SEED)
    ap.add_argument("--per-label", type=int, default=200)
    ap.add_argument("--out", default=str(MODEL_PATH))
    a = ap.parse_args()
    data = train_model(seed=a.seed, per_label=a.per_label)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fh.write("\n")
    print(f"wrote {a.out}: {data['corpus_size']} utterances, {len(data['features'])} features, "
          f"T={data['temperature']}, sha={data['weights_sha256'][:12]}")


if __name__ == "__main__":
    main()
