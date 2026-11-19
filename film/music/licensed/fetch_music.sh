#!/bin/sh
cd "$(dirname "$0")" || exit 1
[ -f corporate_inspirational_atlasaudio.mp3 ] || curl -sSL --fail -e "https://pixabay.com/" -o corporate_inspirational_atlasaudio.mp3 "https://cdn.pixabay.com/download/audio/2026/11/17/audio_c2d0b7721a.mp3?filename=atlasaudio-corporate-inspirational-635582.mp3"
[ -f inspiring_technology_the_mountain.mp3 ] || curl -sSL --fail -e "https://pixabay.com/" -o inspiring_technology_the_mountain.mp3 "https://cdn.pixabay.com/download/audio/2023/12/03/audio_654340973a.mp3?filename=the_mountain-inspiring-technology-179473.mp3"
