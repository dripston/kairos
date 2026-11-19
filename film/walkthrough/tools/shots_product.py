#!/usr/bin/env python3
"""Shot specs for the product film's live-action clips (graded; real UI composited onto screens).
    python3 tools/shots_product.py [name …]     → comp/<name>.mov via tools/screen_comp
"""
import json, os, subprocess, sys
H = "../assets/human/"; B = os.path.expandvars("$TMPDIR/wt-film/film/assets/broll/")
IN_T = "captures/inbound_tall/f%05d.jpg"          # inbound page at a phone-shaped viewport (1440×1150 @2.5)
IN_SCREEN = [70, 457, 855, 1425]                  # its WhatsApp screen below the status bar, in px
OV = "captures/out_vikram/f%05d.jpg"; AA = "captures/out_aarav/f%05d.jpg"; DL = "captures/deployed_light/f%05d.jpg"
def ph(scene): return "captures/phone_%s/f00001.jpg" % scene
LAPTOP_ROI = [430, 150, 1060, 680]                # H6_1 POV: screen region
TOP_ROI = [430, 40, 1100, 520]                    # H6_4 top-down
def lbl(cap, name):
    d = json.load(open("captures/%s/log.json" % cap)); m = {}
    for f in d["frames"]: m.setdefault(f["label"], f["i"])
    return m[name]
def inb(name): return {"img": IN_T % lbl("inbound_tall", name), "crop": IN_SCREEN}
SHOTS = {
  "P01_city":      {"clip": H + "H1_3_mumbai_road_taxi_scooters.mp4", "in": 1.0, "dur": 3.9, "mode": "none"},
  "P02_vikram_sms": {"clip": H + "H8_4_hands_34_table_green.mp4", "in": 0.4, "dur": 5.3, "mode": "green", "up": [0.35, -1], "expand": 0.12, "ui": [{"t": 0, "img": ph("sms3")}]},
  "P03_phone_lights": {"clip": H + "H3_2_phone_notification_desk.mp4", "in": 3.0, "dur": 1.9, "mode": "none"},
  "P04_asha_frustrated": {"clip": H + "H5_1_asha_frustrated_laptop.mp4", "in": 3.6, "dur": 3.6, "mode": "none"},
  "P06_asha_typing": {"clip": H + "H5_2_asha_typing_wide.mp4", "in": 2.0, "dur": 3.0, "mode": "none"},
  "P07_laptop_dashboard": {"clip": H + "H6_1_pov_hands_typing_blank.mp4", "in": 2.0, "dur": 4.5, "mode": "white", "roi": LAPTOP_ROI, "expand": 0.0,
                           "ui": [{"t": 0, "img": DL % 1}]},
  "P12_vikram_whatsapp": {"clip": H + "H8_3_hand_34_table_green.mp4", "in": 0.3, "dur": 6.2, "mode": "green", "up": [-0.6, -1], "expand": 0.12,
                          "ui": [{"t": 0, "img": ph("wa1")}, {"t": 3.4, "img": ph("wa2")}]},
  "P14_upi": {"clip": B + "shop_pay.mp4", "in": 3.6, "dur": 2.6, "mode": "none"},
  "P15_laptop_paid": {"clip": H + "H6_1_pov_hands_typing_blank.mp4", "in": 9.0, "dur": 2.8, "mode": "white", "roi": LAPTOP_ROI, "expand": 0.0,
                      "ui": [{"t": 0, "img": OV % lbl("out_vikram", "v41")}]},
  "P16a_asha_close": {"clip": H + "H5_3_asha_typing_close.mp4", "in": 12.0, "dur": 2.4, "mode": "none"},
  "P16b_laptop_held": {"clip": H + "H6_4_topdown_hands_trackpad_blank.mp4", "in": 1.0, "dur": 2.8, "mode": "white", "roi": TOP_ROI, "expand": 0.0,
                       "ui": [{"t": 0, "img": AA % lbl("out_aarav", "held")}]},
  "P17_sunita_otp": {"expand": 0.12, "clip": H + "H8_1_brown_hands_typing_green.mp4", "in": 1.5, "dur": 9.2, "mode": "green",
                     "ui": [dict(inb("tabA"), t=0), dict(inb("emi"), t=0.9), dict(inb("otp"), t=7.6)]},
  "P19_sunita_fake": {"expand": 0.12, "clip": H + "H8_2_brown_hands_scroll_green.mp4", "in": 6.5, "dur": 4.8, "mode": "green",
                      "ui": [dict(inb("paylink"), t=0), dict(inb("fake"), t=0.5)]},
  "P20a_borrower_b": {"expand": 0.12, "up": [1, -0.55], "clip": H + "H8_5_woman_hands_34_green.mp4", "in": 1.2, "dur": 3.6, "mode": "green",
                      "ui": [dict(inb("tabB"), t=0), dict(inb("hardship"), t=0.6)]},
  "P20b_asha_side": {"clip": H + "H5_4_asha_working_reading.mp4", "in": 9.0, "dur": 2.4, "mode": "none"},
  "P20c_laptop_console": {"clip": H + "H6_1_pov_hands_typing_blank.mp4", "in": 13.0, "dur": 3.8, "mode": "white", "roi": LAPTOP_ROI, "expand": 0.0,
                          "ui": [{"t": 0, "img": IN_T % lbl("inbound_tall", "takeoverB"), "crop": [0, 0, 3600, 2250]},
                                 {"t": 1.5, "img": IN_T % lbl("inbound_tall", "suggested"), "crop": [0, 0, 3600, 2250]},
                                 {"t": 2.7, "img": IN_T % lbl("inbound_tall", "agentsent"), "crop": [0, 0, 3600, 2250]}]},
  "P20d_borrower_b_reply": {"expand": 0.12, "up": [1, -0.55], "clip": H + "H8_5_woman_hands_34_green.mp4", "in": 8.5, "dur": 2.8, "mode": "green", "ui": [inb("agentsent")]},
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
