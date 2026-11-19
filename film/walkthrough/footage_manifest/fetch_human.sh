#!/bin/sh
# Re-downloads the human stock footage for the Kairos films (see SOURCES.md).
# Pexels clips: Pexels License. Pixabay clip: Pixabay Content License (needs a pixabay.com referer).
# Usage: sh fetch_human.sh   (skips files already present; 6 downloads at a time)
cd "$(dirname "$0")" || exit 1
P=https://videos.pexels.com/video-files

get() {
  nm=$1; url=$2
  [ -s "$nm.mp4" ] && return 0
  case $url in
    *pixabay.com*) curl -sSL --fail --retry 3 -e https://pixabay.com/ -o "$nm.mp4.part" "$url" ;;
    *)             curl -sSL --fail --retry 3 -o "$nm.mp4.part" "$url" ;;
  esac && mv "$nm.mp4.part" "$nm.mp4" || { echo "FAIL $nm $url"; rm -f "$nm.mp4.part"; }
}

n=0
while read -r nm url; do
  case $nm in ''|'#'*) continue ;; esac
  case $url in http*) ;; *) url="$P/$url" ;; esac
  get "$nm" "$url" &
  n=$((n + 1)); [ $((n % 6)) -eq 0 ] && wait
done <<'LIST'
# H1 Indian city morning
H1_1_mumbai_sunrise_skyline        30453052/13051914_1920_1080_50fps.mp4
H1_2_mumbai_towers_morning         11095003/11095003-hd_1920_1080_60fps.mp4
H1_3_mumbai_road_taxi_scooters     30169856/12937201_1920_1080_30fps.mp4
H1_4_mumbai_street_autos           30811848/13178731_1920_1080_30fps.mp4
H1_5_bangalore_aerial_day          31017109/13258152_2048_1080_50fps.mp4
# H2 Vikram outdoors / annoyed at phone
H2_1_rider_helmet_phone_seafront   34849910/14770736_1920_1080_30fps.mp4
H2_2_rider_orange_shirt_phone      34849912/14770732_1920_1080_30fps.mp4
H2_3_young_man_phone_street        4333102/4333102-hd_1920_1080_30fps.mp4
H2_4_delhi_man_phone_annoyed_bed   10220591/10220591-uhd_2560_1440_24fps.mp4
H2_5_bangalore_street_man_phone    https://cdn.pixabay.com/video/2020/02/13/32287-391434445_large.mp4
# H3 phone lighting up
H3_1_phone_incoming_text_table     34786788/14748760_1920_1080_50fps.mp4
H3_2_phone_notification_desk       7821643/7821643-hd_1920_1080_30fps.mp4
H3_3_phone_ringing_breakfast_table 8060563/8060563-hd_2048_1080_25fps.mp4
H3_4_phone_ringing_red_shallow     8060389/8060389-hd_2048_1080_25fps.mp4
H3_5_hand_declines_call_desk       5741498/5741498-hd_2048_1080_25fps.mp4
# H4 second borrower (woman), worried
H4_1_stressed_woman_closeup        4584773/4584773-hd_1920_1080_25fps.mp4
H4_2_older_woman_shop_doorway      18214994/18214994-hd_1920_1080_60fps.mp4
H4_3_woman_kurta_phone_call        4333366/4333366-hd_1920_1080_25fps.mp4
H4_4_woman_kurta_texting           4326426/4326426-hd_1920_1080_25fps.mp4
# H5 Asha, ops lead (one actress: H5_1..H5_4)
H5_1_asha_frustrated_laptop        8836325/8836325-hd_1920_1080_25fps.mp4
H5_2_asha_typing_wide              8836329/8836329-hd_1920_1080_25fps.mp4
H5_3_asha_typing_close             8836328/8836328-hd_1920_1080_25fps.mp4
H5_4_asha_working_reading          8836330/8836330-hd_1920_1080_25fps.mp4
H5_5_alt_woman_desk_laptop         8061363/8061363-hd_1920_1080_25fps.mp4
H5_6_alt_frustrated_glasses        8632780/8632780-hd_1920_1080_25fps.mp4
H5_7_alt_delhi_office_laptop       10166640/10166640-hd_1920_1080_24fps.mp4
# H6 laptop with blank screen, hands
H6_1_pov_hands_typing_blank        6611946/6611946-hd_1920_1080_25fps.mp4
H6_2_ots_man_typing_blank          6611942/6611942-hd_1920_1080_25fps.mp4
H6_3_ots_woman_typing_blank        6985703/6985703-hd_2048_1080_25fps.mp4
H6_4_topdown_hands_trackpad_blank  6611947/6611947-hd_1920_1080_25fps.mp4
H6_5_hands_typing_front_blank      8165666/8165666-hd_2048_1080_25fps.mp4
# H7 evening at home, relieved man
H7_1_man_reads_text_serious        4326542/4326542-hd_1920_1080_25fps.mp4
H7_2_man_smiles_texting            4326423/4326423-hd_1920_1080_25fps.mp4
H7_3_man_smiles_texting_close      4326417/4326417-hd_1920_1080_25fps.mp4
H7_4_couple_sofa_phones            4326784/4326784-hd_1920_1080_25fps.mp4
H7_5_mumbai_man_sofa_phone         34848047/14770378_1920_1080_25fps.mp4
H7_6_delhi_man_smiling_laptop      10220594/10220594-hd_1920_1080_24fps.mp4
# H8 green-screen phone in hand
H8_1_brown_hands_typing_green      4685107/4685107-hd_1920_1080_24fps.mp4
H8_3_hand_34_table_green           8566854/8566854-hd_1920_1080_30fps.mp4
H8_4_hands_34_table_green          13107026/13107026-hd_1920_1080_30fps.mp4
H8_5_woman_hands_34_green          9558947/9558947-hd_2048_1080_25fps.mp4
H8_6_hands_bed_34_green            6620404/6620404-hd_1920_1080_25fps.mp4
H8_7_hand_34_desk_green            29141387/12587928_1920_1080_25fps.mp4
# H9 shop / QR scan
H9_1_kirana_store_interior         39688924/16922654_1920_1080_30fps.mp4
H9_2_hands_phone_scan_qr_box       7287306/7287306-hd_1920_1080_25fps.mp4
H9_3_hand_scanning_qr_window       5226464/5226464-hd_1920_1080_30fps.mp4
H9_4_mumbai_storefronts            30169852/12937207_1920_1080_30fps.mp4
H9_5_delhi_shopkeeper_seated       18214993/18214993-hd_1920_1080_60fps.mp4
# H10 support agent with headset (one actress)
H10_1_agent_headset_closeup        8865884/8865884-hd_1920_1080_25fps.mp4
H10_2_agent_headset_colleague      8865780/8865780-hd_1920_1080_25fps.mp4
H10_3_agents_monitor               8865902/8865902-hd_1920_1080_25fps.mp4
H10_4_agent_headset_smile_vertical 8865912/8865912-hd_1080_1920_25fps.mp4
# H11 Indian team meeting (one shoot)
H11_1_team_meeting_screen          10150922/10150922-hd_1920_1080_24fps.mp4
H11_2_team_meeting_presenter_woman 10150928/10150928-hd_1920_1080_24fps.mp4
H11_3_team_meeting_clapping        10150929/10150929-hd_1920_1080_24fps.mp4
H11_4_team_meeting_wide            10150923/10150923-hd_1920_1080_24fps.mp4
# H12 Indian city at night
H12_1_india_night_traffic          8361011/8361011-hd_1920_1080_24fps.mp4
H12_2_pune_aerial_night            31129872/13300901_1920_1080_30fps.mp4
H12_3_delhi_skyline_night          31067264/13276736_1920_1080_30fps.mp4
H12_4_mumbai_night_intersection    30213783/12953270_1920_1080_30fps.mp4
H12_5_timelapse_traffic_ludhiana   6577159/6577159-hd_1920_1080_25fps.mp4
# H13 technical film
H13_1_engineer_ots_monitor_code    8632599/8632599-hd_1920_1080_25fps.mp4
H13_2_engineers_monitor_code       8632602/8632602-hd_1920_1080_25fps.mp4
H13_3_engineer_monitor_talking     8632591/8632591-uhd_2560_1440_25fps.mp4
H13_4_code_on_face                 5474268/5474268-hd_2048_1080_25fps.mp4
H13_5_code_on_face_close           5474275/5474275-hd_2048_1080_25fps.mp4
H13_6_server_rack                  7140928/7140928-hd_1920_1080_24fps.mp4
H13_7_terminal_screen              6804117/6804117-hd_2048_1080_25fps.mp4
H13_8_ots_dev_curved_monitor       7989682/7989682-hd_1920_1080_25fps.mp4
LIST
wait
ls *.mp4 2>/dev/null | wc -l | xargs echo "clips on disk:"
