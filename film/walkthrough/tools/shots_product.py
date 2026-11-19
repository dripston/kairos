#!/usr/bin/env python3
"""Shot specs for the product film's live-action clips (graded; real UI composited onto screens).
    python3 tools/shots_product.py [name …]     → comp/<name>.mov via tools/screen_comp
"""
import json, os, subprocess, sys
H = "../assets/human/"; B = os.path.expandvars("$TMPDIR/wt-film/film/assets/broll/")
IN_T = "captures/inbound_tall/f%05d.jpg"          # inbound page at a phone-shaped viewport (1440×1150 @2.5)
IN_SCREEN = [70, 457, 855, 1425]                  # its WhatsApp screen below the status bar, in px
OV = "captures/out_vikram/f%05d.jpg"; AA = "captures/out_aarav/f%05d.jpg"; DL = "captures/deployed_voice/f%05d.jpg"
def ph(scene): return "captures/phone_%s/f00001.jpg" % scene
LAPTOP_ROI = [430, 150, 1060, 680]                # H6_1 POV: screen region
TOP_ROI = [430, 40, 1100, 520]                    # H6_4 top-down
OTS_ROI = [280, 10, 1160, 850]                    # H6_3 over the shoulder (a woman's hands): Asha's own laptop
def lbl(cap, name):
    d = json.load(open("captures/%s/log.json" % cap)); m = {}
    for f in d["frames"]: m.setdefault(f["label"], f["i"])
    return m[name]
def inb(name): return {"img": IN_T % lbl("inbound_tall", name), "crop": IN_SCREEN}
SHOTS = {
  "T01_engineer": {"clip": H + "H13_8_ots_dev_curved_monitor.mp4", "in": 6.0, "dur": 3.6, "mode": "none"},
  "T03_street": {"clip": H + "H1_4_mumbai_street_autos.mp4", "in": 3.0, "dur": 2.8, "mode": "none"},
  "T07_sunita_call": {"clip": H + "H4_3_woman_kurta_phone_call.mp4", "in": 0.3, "dur": 5.4, "mode": "none"},
  "T11_engineer_face": {"clip": H + "H13_5_code_on_face_close.mp4", "in": 2.6, "dur": 2.0, "mode": "none"},
  "T12_team_claps": {"clip": H + "H11_2_team_meeting_presenter_woman.mp4", "in": 13.0, "dur": 2.0, "mode": "none"},
  "T07b_person_reviews": {"clip": H + "H10_1_agent_headset_closeup.mp4", "in": 6.5, "dur": 3.8, "mode": "none"},
  "T10_engineer": {"clip": H + "H13_2_engineers_monitor_code.mp4", "in": 3.6, "dur": 2.6, "mode": "quad", "uiGain": 0.9,
                   "quads": [{"t": 0, "q": [[206, 104], [856, 136], [874, 850], [236, 1120]]}, {"t": 2.6, "q": [[126, 114], [824, 174], [840, 930], [160, 1120]]}],
                   "ui": [{"t": 0, "img": "out/termshot/termshot_003.50.png"}]},
  "M1_flyover": {"clip": H + "H1_3_mumbai_road_taxi_scooters.mp4", "in": 1.0, "dur": 1.9, "mode": "none"},
  "M2_rider": {"clip": H + "H2_2_rider_orange_shirt_phone.mp4", "in": 6.0, "dur": 1.3, "mode": "none"},
  "M3_bazaar": {"clip": H + "H2_5_bangalore_street_man_phone.mp4", "in": 3.4, "dur": 1.1, "mode": "none"},
  "M4_annoyed": {"clip": H + "H2_4_delhi_man_phone_annoyed_bed.mp4", "in": 0.6, "dur": 1.3, "mode": "none"},
  "M5_shopkeeper": {"clip": H + "H4_2_older_woman_shop_doorway.mp4", "in": 2.0, "dur": 1.3, "mode": "none"},
  "M6_breakfast": {"clip": H + "H3_3_phone_ringing_breakfast_table.mp4", "in": 1.6, "dur": 1.2, "mode": "none"},
  "M7_decline": {"clip": H + "H3_5_hand_declines_call_desk.mp4", "in": 5.6, "dur": 1.6, "mode": "none"},
  "V1_vikram_reads": {"clip": H + "H7_1_man_reads_text_serious.mp4", "in": 3.0, "dur": 2.6, "mode": "none"},
  "V1b_vikram_no_reply": {"clip": H + "H7_1_man_reads_text_serious.mp4", "in": 5.6, "dur": 2.0, "mode": "none"},
  "V2b_vikram_before_pay": {"clip": H + "H7_3_man_smiles_texting_close.mp4", "in": 4.8, "dur": 1.8, "mode": "none"},
  "V2_vikram_smiles": {"clip": H + "H7_3_man_smiles_texting_close.mp4", "in": 1.0, "dur": 2.0, "mode": "none"},
  "V3_vikram_pays": {"expand": 0.12, "clip": H + "H8_4_hands_34_table_green.mp4", "in": 8.0, "dur": 2.0, "mode": "green", "up": [0.35, -1], "ui": [{"t": 0, "img": ph("paid")}]},
  "V4_vikram_relief": {"clip": H + "H7_2_man_smiles_texting.mp4", "in": 1.0, "dur": 2.2, "mode": "none"},
  "B1_borrower_b": {"clip": H + "H4_1_stressed_woman_closeup.mp4", "in": 6.5, "dur": 2.2, "mode": "none"},
  "P01_city":      {"clip": H + "H1_3_mumbai_road_taxi_scooters.mp4", "in": 1.0, "dur": 3.9, "mode": "none"},
  "P02_vikram_sms": {"clip": H + "H8_4_hands_34_table_green.mp4", "in": 0.4, "dur": 3.8, "mode": "green", "up": [0.35, -1], "expand": 0.12, "ui": [{"t": 0, "img": ph("sms3")}]},
  "P02a_vikram_annoyed": {"clip": H + "H2_4_delhi_man_phone_annoyed_bed.mp4", "in": 4.0, "dur": 2.6, "mode": "none"},
  "P03_phone_lights": {"clip": H + "H3_2_phone_notification_desk.mp4", "in": 3.0, "dur": 1.9, "mode": "none"},
  "P03b_worried": {"clip": H + "H4_1_stressed_woman_closeup.mp4", "in": 3.0, "dur": 2.2, "mode": "none"},
  "P15b_vikram_relief": {"clip": H + "H7_6_delhi_man_smiling_laptop.mp4", "in": 6.0, "dur": 2.4, "mode": "none"},
  "P17a_sunita": {"clip": H + "H4_4_woman_kurta_texting.mp4", "in": 2.0, "dur": 2.4, "mode": "none"},
  "P04_asha_frustrated": {"clip": H + "H5_1_asha_frustrated_laptop.mp4", "in": 3.6, "dur": 3.6, "mode": "none"},
  "P06_asha_typing": {"clip": H + "H5_2_asha_typing_wide.mp4", "in": 2.0, "dur": 3.0, "mode": "none"},
  "P07_laptop_dashboard": {"clip": H + "H6_3_ots_woman_typing_blank.mp4", "in": 1.0, "dur": 4.5, "mode": "white", "roi": OTS_ROI, "expand": 0.0, "landscape": True,
                           "ui": [{"t": 0, "img": DL % 1}]},
  "P12_vikram_whatsapp": {"clip": H + "H8_4_hands_34_table_green.mp4", "in": 4.3, "dur": 3.7, "mode": "green", "up": [0.35, -1], "expand": 0.12,
                          "uiContrast": 1.08, "ui": [{"t": 0, "img": ph("wa1")}, {"t": 0.9, "img": ph("wa1t")}, {"t": 1.6, "img": ph("wa2")}]},
  "P14_upi": {"clip": H + "H9_3_hand_scanning_qr_window.mp4", "in": 0.2, "dur": 2.6, "mode": "none"},
  "P15_laptop_paid": {"clip": H + "H6_3_ots_woman_typing_blank.mp4", "in": 6.0, "dur": 2.8, "mode": "white", "roi": OTS_ROI, "expand": 0.0, "landscape": True,
                      "ui": [{"t": 0, "img": OV % lbl("out_vikram", "v41")}]},
  "P16a_asha_close": {"clip": H + "H5_3_asha_typing_close.mp4", "in": 12.0, "dur": 2.4, "mode": "none"},
  "P16b_laptop_held": {"clip": H + "H6_3_ots_woman_typing_blank.mp4", "in": 9.0, "dur": 2.8, "mode": "white", "roi": OTS_ROI, "expand": 0.0, "landscape": True,
                       "ui": [{"t": 0, "img": AA % lbl("out_aarav", "held")}]},
  "P17_sunita_otp": {"expand": 0.12, "clip": H + "H8_1_brown_hands_typing_green.mp4", "in": 1.5, "dur": 9.2, "mode": "green",
                     "ui": [dict(inb("tabA"), t=0), dict(inb("emi"), t=0.9), dict(inb("otp"), t=7.4)]},
  "P19_sunita_fake": {"expand": 0.12, "clip": H + "H8_1_brown_hands_typing_green.mp4", "in": 21.5, "dur": 4.8, "mode": "green",
                      "ui": [dict(inb("paylink"), t=0), dict(inb("fake"), t=0.5)]},
  "P20a_borrower_b": {"expand": 0.2, "keyLo": 0.04, "keyHi": 0.13, "uiGain": 1.0, "uiContrast": 1.12, "up": [1, -0.55], "clip": H + "H8_5_woman_hands_34_green.mp4", "in": 1.2, "dur": 3.6, "mode": "green",
                      "ui": [dict(inb("tabB"), t=0), dict(inb("hardship"), t=0.6)]},
  "P20b_asha_side": {"clip": H + "H5_4_asha_working_reading.mp4", "in": 9.0, "dur": 2.4, "mode": "none"},
  "P20c_laptop_console": {"clip": H + "H6_3_ots_woman_typing_blank.mp4", "in": 3.0, "dur": 3.8, "mode": "white", "roi": OTS_ROI, "expand": 0.0, "landscape": True,
                          "ui": [{"t": 0, "img": IN_T % lbl("inbound_tall", "takeoverB"), "crop": [0, 0, 3600, 2250]},
                                 {"t": 1.5, "img": IN_T % lbl("inbound_tall", "suggested"), "crop": [0, 0, 3600, 2250]},
                                 {"t": 2.7, "img": IN_T % lbl("inbound_tall", "agentsent"), "crop": [0, 0, 3600, 2250]}]},
  "P20d_borrower_b_reply": {"expand": 0.2, "keyLo": 0.04, "keyHi": 0.13, "uiGain": 1.0, "uiContrast": 1.12, "up": [1, -0.55], "clip": H + "H8_5_woman_hands_34_green.mp4", "in": 8.5, "dur": 2.8, "mode": "green", "ui": [inb("agentsent")]},
  "P21_sunita_stop": {"expand": 0.12, "clip": H + "H8_1_brown_hands_typing_green.mp4", "in": 15.5, "dur": 4.6, "mode": "green",
                      "ui": [dict(inb("tabA2"), t=0), dict(inb("stop"), t=0.7)]},
  "P22_team": {"clip": H + "H11_3_team_meeting_clapping.mp4", "in": 4.2, "dur": 3.2, "mode": "none"},
  "P23_night": {"clip": H + "H12_3_delhi_skyline_night.mp4", "in": 9.0, "dur": 3.0, "mode": "none"},
}
want = sys.argv[1:] or list(SHOTS)
os.makedirs("comp", exist_ok=True); os.makedirs("shots", exist_ok=True)
for k in want:
    s = dict(SHOTS[k]); s.setdefault("out", "comp/%s.mov" % k); s.setdefault("fps", 30)
    json.dump(s, open("shots/%s.json" % k, "w"), indent=1)
    r = subprocess.run(["./tools/screen_comp", "shots/%s.json" % k], capture_output=True, text=True)
    print(k, (r.stdout.strip() or r.stderr.strip())[-140:])
