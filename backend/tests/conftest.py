import csv
import os
from pathlib import Path

import pytest

from kairos_backend.policy import load_policy, policy_dir

# Sarvam models are OFF for the whole suite even when a key is configured on this machine
# (backend/.env, KAIROS_DOTENV, ~/.kairos/sarvam_api_key): the suite must be deterministic and offline.
# Tests that exercise the models pass fake transports explicitly.
os.environ["KAIROS_SARVAM_MODELS"] = "off"

K01_DIR = policy_dir() / "K01-decision-policy"
K13_DIR = policy_dir() / "K13-operational-exceptions"


@pytest.fixture(scope="session")
def policy():
    """K01 + K13 with the example lender registered as 'EXAMPLE' (as Ojas's reference does)."""
    p = load_policy(lender_files=[K01_DIR / "lender_caps.example.json"])
    p.lenders["EXAMPLE"] = p.lenders.pop("LENDER-EXAMPLE")
    return p


def read_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))
