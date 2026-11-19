"""Creates the synthetic dataset (same format as PL_Dummy_Collections_Data_500.csv + consent columns).

Run:            python3 make_dataset.py                    -> data/synthetic_50k.csv (50,000 loans)
Smaller:        python3 make_dataset.py --rows 500
Other world:    python3 make_dataset.py --seed 7
"""
import argparse
import os

from spine.synthetic_dataset import write_dataset


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=50_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    out = args.out or os.path.join("data", f"synthetic_{args.rows // 1000}k.csv" if args.rows >= 1000
                                   else f"synthetic_{args.rows}.csv")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    write_dataset(out, args.rows, args.seed)
    print(f"Created {out}: {args.rows:,} loans, seed {args.seed}")
    print("Mobile numbers all start with 0, so they cannot reach a real phone.")
    print(f"Next: python3 load_dataset.py {out}")


if __name__ == "__main__":
    main()
