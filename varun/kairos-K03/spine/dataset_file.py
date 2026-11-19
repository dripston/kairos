"""Finds the team dataset PL_Dummy_Collections_Data_500.csv (tracker 'Datasets' sheet: verified Drive file).

The CSV is NOT committed to Git (it contains phone numbers). Download it from the team Drive and
put it next to main.py, or set KAIROS_DATASET=/path/to/PL_Dummy_Collections_Data_500.csv.
"""
import glob
import os

NAME = "PL_Dummy_Collections_Data_500.csv"


def find_dataset(explicit=None):
    candidates = [explicit, os.environ.get("KAIROS_DATASET"), NAME, os.path.join("data", NAME),
                  os.path.expanduser(f"~/Downloads/{NAME}"), os.path.expanduser(f"~/Desktop/{NAME}")]
    candidates += sorted(glob.glob(os.path.expanduser(f"~/Desktop/kairos-folder/*/kairos_engine/data/raw/{NAME}")))
    for c in candidates:
        if c and os.path.exists(os.path.expanduser(c)):
            return os.path.expanduser(c)
    return None
