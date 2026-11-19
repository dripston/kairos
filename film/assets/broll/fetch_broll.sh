#!/bin/sh
# Re-downloads the Pexels b-roll used by the film (see SOURCES.md).
cd "$(dirname "$0")" || exit 1
B=https://videos.pexels.com/video-files
while read nm rel; do [ -f "$nm.mp4" ] || curl -sSL --fail -o "$nm.mp4" "$B/$rel"; done <<'L'
city_aerial_mumbai_night 40380857/17256382_1920_1080_30fps.mp4
mumbai_train_night 38979012/16582801_1280_720_60fps.mp4
kolkata_buses 32665228/13926763_1920_1080_24fps.mp4
kolkata_traffic 32665222/13926707_1920_1080_24fps.mp4
phone_notification_closeup 34786856/14748770_1920_1080_50fps.mp4
snoozing_phone 6963828/6963828-hd_2048_1080_25fps.mp4
woman_phone_bed 6443877/6443877-hd_2048_864_25fps.mp4
man_turban_phone_night 36498230/15476260_1280_720_60fps.mp4
man_good_news_phone 7581309/7581309-hd_1920_1080_30fps.mp4
hands_laptop_dark 34771078/14741621_1920_1080_25fps.mp4
pay_phone_card 7534961/7534961-hd_720_1280_25fps.mp4
L
