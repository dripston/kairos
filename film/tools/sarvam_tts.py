import json, os, re, sys, base64, urllib.request, pathlib
env = pathlib.Path(sys.argv[1]).read_text()
m = re.search(r'sk_[A-Za-z0-9_]+', env); key = m.group(0)
def tts(text, speaker, model, out, lang="en-IN", pace=1.0):
    body = {"text": text, "target_language_code": lang, "speaker": speaker, "model": model, "pace": pace, "speech_sample_rate": 44100}
    req = urllib.request.Request("https://api.sarvam.ai/text-to-speech", data=json.dumps(body).encode(),
        headers={"api-subscription-key": key, "Content-Type": "application/json"})
    try:
        r = json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}"
    pathlib.Path(out).write_bytes(base64.b64decode("".join(r["audios"])))
    return "ok " + out
if __name__ == "__main__":
    for model, spk in [("bulbul:v3","ritu"),("bulbul:v3","advait"),("bulbul:v2","anushka"),("bulbul:v2","abhilash")]:
        print(model, spk, tts("Meet Kairos. It decides, per person, per day.", spk, model, f"{os.environ['TMPDIR']}/tts/test_{model[-2:]}_{spk}.wav"))
