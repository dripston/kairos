> **In this repo** (moved from `kairos-spine-k03-clean.zip`): the `spine/` and `replay/` packages live in
> `backend/`, the scripts in `backend/scripts/`, the tests in `backend/tests/k03/`, and the policy is the
> repo's single copy at `policy/K01-decision-policy/policy.json`. Run everything from `backend/`:
> `python scripts/spine_report.py` (was `main.py`), `python scripts/make_dataset.py`,
> `python scripts/load_dataset.py data/synthetic_50k.csv`, `python scripts/run_replay.py`, `pytest tests/k03`.
> K06 (`kairos_backend/ledger.py`) reads borrowers, consents, loans, EMIs and payments from this spine.

# K03 – Account and loan spine

## Rules come from K01 (Ojas)

policy/policy.json is a copy of Ojas's K01 decision policy (v1.1.0). DPD bands (PRE_DUE, B1 1-30,
B2 31-60, B3 61-90, B4 91+), channel names (sms, whatsapp, email, ivr, voicebot) and the
missing/stale DPD HOLD rule are read from it, never hard-coded. Every event stores policy_version.
When Ojas updates the policy, replace policy/policy.json and run the tests.

## K03 task (built from the team dataset)

    python3 main.py ~/Downloads/PL_Dummy_Collections_Data_500.csv --reset
    python3 -m unittest

Takes two real rows from PL_Dummy_Collections_Data_500 (PL100033 and PL100034) and links them
to one customer, CUST-K03-0001. Amounts, DPD, bounce dates and risk tags come from the file.
Added (the file has none): consent (marked 'assumed'), one payment on PL100033, two contact
events on PL100034. The mobile number is replaced with TEST_PHONE.
Done when: every event joins to the right EMI; paying PL100033 clears it, PL100034 stays overdue.

## Create the synthetic dataset (main use, agreed with Ojas)

    python3 make_dataset.py                          # data/synthetic_50k.csv, 50,000 loans (~2 s)
    python3 load_dataset.py data/synthetic_50k.csv   # load into kairos_dataset.db (~5 s)
    python3 run_replay.py --dataset data/synthetic_50k.csv   # K15 replay on the same borrowers

Same columns as PL_Dummy_Collections_Data_500.csv, plus WhatsApp/SMS/Email/IVR/Voice consent
and Opted Out columns. All mobile numbers start with 0, so no real phone can be reached.
Buckets follow the K01 boundaries. Same seed = identical file.

## Load the team's PL_Dummy file (still supported)

    python3 load_dataset.py ~/Downloads/PL_Dummy_Collections_Data_500.csv
    python3 load_dataset.py ~/Downloads/PL_Dummy_Collections_Data_500.csv --reset   # start fresh

Loads every row into kairos_dataset.db: one borrower, one loan and the bounced EMI per row.
Bad rows are rejected with a reason. Prints checks: DPD vs file, bucket vs K01 bands,
duplicate names, consent, integrity. Settings in spine/config.py (DATASET_...).
The CSV is not committed to Git (.gitignore) because it contains phone numbers.

## Synthetic two-loan borrower (test fixture)

The dataset has one loan per person, so this fixture still proves that
paying one loan never closes another loan of the same person.

One synthetic borrower with two loans, consent, EMIs (obligations), payments and events.
Pure Python 3 standard library: nothing to install.

    python3 main.py            # build the database and print the report
    python3 main.py --reset    # delete the database and rebuild from scratch
    python3 -m unittest -v     # run all tests

Done when: every event joins to the right obligation, and clearing one loan leaves the other open.
DPD bands in spine/config.py are placeholders until K01 is approved.
Replace TEST_PHONE in spine/config.py with an approved sandbox number before any real send.

---

# K15 – Safety, load and story replay

Runs a whole campaign for the baseline and the Kairos policy on the same 50,000 synthetic
borrowers, then compares them. Also runs 500 chats at once.

    python3 run_replay.py                          # full run: 50,000 borrowers, 14 days (~10 seconds)
    python3 run_replay.py --borrowers 5000 --days 7   # quick run
    python3 run_replay.py --seed 7                 # different random world, same rules

Measures: eligible -> attempted -> delivered/connected -> meaningful reply -> verified payment,
replies per rupee, verified payments, safety violations, planning time, chat response time.
Saves everything to reports/replay_report.json for K16 (demo) and K23 (cohort review).

Plug-in points (stand-ins are labelled in every report):
- K09 policy: replace ReferencePolicy in replay/policies.py (needs plan_day + observe)
- K10 chat agent: replace PlaceholderChatHandler in replay/chat_load.py (needs async handle)
- Official simulator / baseline (K02): replace DevSimulator / FixedCadenceBaseline
- Costs, templates, caps, budget: replay/settings.py (PLACEHOLDER values marked)
