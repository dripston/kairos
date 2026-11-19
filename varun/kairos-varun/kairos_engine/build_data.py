"""Step 1 - build the synthetic 50K portfolio from PL_Dummy_Collections_Data_500.csv.

Outputs (data/generated/seed_<seed>/), each a stand-in for an official dataset:
  borrowers.csv        D1/D2  accounts, loan, DPD, consents, language, AA signals
  history_summary.csv  D4     12 months of contact history per borrower x channel
  channel_costs.csv    D10    cost per touch
  templates.csv        D13    template registry (approved + one rejected)
  manifest.json               counts, seed, source file and the SIMULATED label
Swap in the official files by editing only `load_portfolio()` in engine.py.
"""
import json
import os

import numpy as np
import pandas as pd

from common import CHANNELS, RISKS, SEGMENTS, path, uniform
from world import World

HINDI_STATES = {"Bihar", "Uttar Pradesh", "Madhya Pradesh", "Rajasthan", "Delhi", "Haryana",
                "Jharkhand", "Chhattisgarh", "Uttarakhand", "Himachal Pradesh"}
HISTORY_TOUCHES = {"sms": 60, "whatsapp": 30, "email": 20, "ivr": 10, "voicebot": 0}
HISTORY_SLOT = {"sms": 1, "whatsapp": 2, "email": 0, "ivr": 4, "voicebot": 4}  # old fixed cadence


def out_dir(seed):
    return path("data", "generated", f"seed_{seed}")


def build(cfg, seed, verbose=True):
    src = pd.read_csv(path("data", "raw", "PL_Dummy_Collections_Data_500.csv"))
    n = cfg["n_borrowers"]
    u = lambda *k: uniform(seed, 500, *k)
    i = np.arange(n)
    pick = (u(i, 1) * len(src)).astype(int)
    s = src.iloc[pick].reset_index(drop=True)

    b = pd.DataFrame({"borrower_id": [f"BRW{k:06d}" for k in i + 1],
                      "loan_id": [f"PL{k:07d}" for k in i + 1],
                      "source_loan_id": s["Loan ID"].values})
    b["obligation_id"] = b["loan_id"] + "-EMI1"
    b["mobile_last4"] = (1000 + (u(i, 2) * 9000).astype(int)).astype(str)
    jitter = 0.85 + 0.30 * u(i, 3)
    b["emi_inr"] = np.round(s["EMI Amount"].values * jitter).astype(int)
    b["pos_inr"] = np.round(s["POS"].values * jitter).astype(int)
    b["tos_inr"] = np.round(s["TOS"].values * jitter).astype(int)

    # DPD: keep source DPD for a share, shift the rest into other K01 bands (labelled)
    mix = cfg["band_mix"]
    cuts = np.cumsum([mix["source_file"], mix["PRE_DUE"], mix["B2"], mix["B3"], mix["B4"]])
    which = np.searchsorted(cuts, u(i, 4) * cuts[-1], side="right")
    r = u(i, 5)
    dpd = s["DPD"].values.astype(int).copy()
    dpd = np.where(which == 1, (-6 + r * 7).astype(int), dpd)          # -6..0
    dpd = np.where(which == 2, (31 + r * 30).astype(int), dpd)
    dpd = np.where(which == 3, (61 + r * 30).astype(int), dpd)
    dpd = np.where(which == 4, (91 + r * 60).astype(int), dpd)
    b["dpd_at_start"] = dpd
    b["dpd_source"] = np.where(which == 0, "file", "synthetic_shift")
    months = np.maximum(1, np.ceil(np.maximum(dpd, 1) / 30)).astype(int)
    b["amount_due_inr"] = b["emi_inr"] * np.where(dpd <= 0, 1, months)

    b["state"] = s["State"].values
    b["age"] = s["Age"].values
    b["employment"] = s["Employment Type"].values
    b["emp_idx"] = (b["employment"] != "Salaried").astype(int)
    b["risk"] = s["Risk Tagging"].str[:2].values
    b["risk_idx"] = b["risk"].map({r_: k for k, r_ in enumerate(RISKS)}).astype(int)
    b["segment"] = b["risk"] + "-" + np.where(b["emp_idx"] == 0, "SAL", "SE")
    b["seg_idx"] = b["segment"].map({sg: k for k, sg in enumerate(SEGMENTS)}).astype(int)
    b["language"] = np.where(b["state"].isin(HINDI_STATES) & (u(i, 6) < 0.7), "hi", "en")

    bounce = s["Bounce String (last 6 months)"].astype(str)
    b["bounce_string_6m"] = bounce.values
    b["ontime_emis_6m"] = 6 - bounce.str.count("1").values
    nach = s["NACH/ECS Mandate (T/F)"].values == "T"
    rep = s["Re-presentment Eligibility (T/F)"].values == "T"
    # ASSUMPTION: mandate + re-presentment eligible => technical bounce (balance was there)
    b["nach_bounce_reason"] = np.where(nach & rep, "TECHNICAL", np.where(nach, "INSUFFICIENT_FUNDS", ""))
    aa = u(i, 7) < 0.35
    b["aa_consent"] = aa
    b["aa_net_cash_flow"] = np.where(aa, np.where(u(i, 8) < 0.55, "POSITIVE", "NEGATIVE"), "")
    b["aa_dti"] = np.where(aa, np.round(0.2 + 0.7 * u(i, 9), 2), np.nan)
    b["aa_risk_flags"] = np.where(aa, (u(i, 10) * 5).astype(int), 0)

    has_wa, has_email = u(i, 11) < 0.88, u(i, 12) < 0.70
    b["consent_sms"] = u(i, 13) < 0.97
    b["consent_whatsapp"] = has_wa & (u(i, 14) < 0.85)
    b["consent_email"] = has_email & (u(i, 15) < 0.90)
    b["consent_ivr"] = u(i, 16) < 0.90
    b["consent_voicebot"] = u(i, 17) < 0.55
    b["consent_version"] = "CV-2026-11-01"
    b["opted_out_at_start"] = u(i, 18) < 0.004

    # 12-month history (D4 stand-in), generated from the pre-drift world at the old cadence
    world = World(b, seed)
    for c, ch in enumerate(CHANNELS):
        consent = b[f"consent_{ch}"].to_numpy()
        lam = HISTORY_TOUCHES[ch] * consent
        rng = np.random.default_rng([seed, c, 55])
        touches = rng.poisson(lam)
        b[f"hist_touches_{ch}"] = touches
        b[f"hist_replies_{ch}"] = world.history(i, c, HISTORY_SLOT[ch], touches)
        b[f"hist_slot_{ch}"] = cfg["slots"][HISTORY_SLOT[ch]]

    os.makedirs(out_dir(seed), exist_ok=True)
    hist_cols = [c for c in b.columns if c.startswith("hist_")]
    b.drop(columns=hist_cols).to_csv(os.path.join(out_dir(seed), "borrowers.csv"), index=False)
    b[["borrower_id"] + hist_cols].to_csv(os.path.join(out_dir(seed), "history_summary.csv"), index=False)

    costs = pd.DataFrame({"channel": CHANNELS,
                          "cost_inr": [cfg["channel_cost_inr"][c] for c in CHANNELS],
                          "source": ["K01 test cases"] * 4 + ["ASSUMPTION"]})
    costs.to_csv(os.path.join(out_dir(seed), "channel_costs.csv"), index=False)

    rows = []
    for ch in CHANNELS:
        for purpose in ("reminder", "collection"):
            for lang in ("en", "hi"):
                if ch == "voicebot" and lang == "hi":
                    continue            # no approved Hindi voicebot script -> WAIT (E05)
                tid = f"T-{ch[:3].upper()}-{purpose[:3].upper()}-{lang.upper()}-01"
                rows.append([tid, ch, purpose, lang, "APPROVED"])
    rows.append(["T-WHA-COL-HI-02", "whatsapp", "collection", "hi", "REJECTED"])
    pd.DataFrame(rows, columns=["template_id", "channel", "purpose", "language", "status"]).to_csv(
        os.path.join(out_dir(seed), "templates.csv"), index=False)

    manifest = {"seed": seed, "label": cfg["run_label"], "borrowers": n,
                "source_file": "PL_Dummy_Collections_Data_500.csv (500 rows)",
                "band_counts": pd.Series(np.select([dpd <= 0, dpd <= 30, dpd <= 60, dpd <= 90], ["PRE_DUE", "B1", "B2", "B3"], "B4")).value_counts().to_dict(),
                "segments": b["segment"].value_counts().to_dict(),
                "language": b["language"].value_counts().to_dict(),
                "consent_rate": {ch: round(float(b[f"consent_{ch}"].mean()), 3) for ch in CHANNELS},
                "opted_out_at_start": int(b["opted_out_at_start"].sum())}
    with open(os.path.join(out_dir(seed), "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    if verbose:
        print(f"[build] {n:,} borrowers -> {out_dir(seed)}")
        print(f"[build] bands {manifest['band_counts']}  consent {manifest['consent_rate']}")
    return out_dir(seed)
