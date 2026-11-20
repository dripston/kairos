# Live WhatsApp for CM-03 (one real phone, existing Cloud API number)

This turns on **live mode** for the CM-03 inbound WhatsApp agent. Real WhatsApp messages from **one
allow-listed phone** reach the deployed backend, and Kairos answers on WhatsApp in real time: the OTP,
the replies, and the messages Asha (the agent) sends after she takes over. The borrower behind that
phone is **synthetic**: Borrower A (`B-2001`), with a synthetic loan and a synthetic bank feed, all
labelled SIMULATED. No real loan data exists anywhere.

Live mode is **off by default**. It switches on only when every setting in step 4 is present.
Otherwise CM-03 stays dry-run exactly as before.

**You do these steps yourself.** They involve tokens and secrets. Agents (Claude) never type, paste or
read them.

---

## What you need from the number's owner

The WhatsApp Business number is already registered on the Cloud API (a production number, not the
test number). The owner gives you three values, privately (not in a chat that is logged or shared):

| Value | Where they find it | Goes into |
|---|---|---|
| **Phone number ID** (digits) | WhatsApp Manager / app → WhatsApp → API Setup, next to the number | `WA_PHONE_NUMBER_ID` |
| **Permanent System User access token** with `whatsapp_business_messaging` and `whatsapp_business_management` | Business Settings → Users → System users → Generate token (select the app and both permissions; the system user must be assigned to the WhatsApp account) | `WA_TOKEN` |
| **App Secret** of the app the number is connected to | App dashboard → App settings → Basic → App secret → Show | `WA_APP_SECRET` |

You also need:

- **The callback URL:** `https://kairos-backend-5ifc.onrender.com/cm03/whatsapp/inbound`
- **The verify token:** the value in `work/wa_verify_token.txt` (not in the repo). It is not a
  high-value secret, but it must be **identical** in Meta (step 2) and on Render (step 3).
- **Your own WhatsApp number** in E.164 format (`+91` followed by 10 digits). This is the **only**
  number that will ever receive a message.

---

## 1. (Optional, 1 minute) Check the token and number from your laptop

This is read-only: two `GET` requests, and nothing is sent.

```bash
cd backend
zsh ../scripts/set_whatsapp_secrets.sh        # paste the Phone number ID, token and App Secret (hidden)
python scripts/live_wa_check.py               # prints the masked number, verified name, quality rating, webhook target
```

`set_whatsapp_secrets.sh` writes `WA_PHONE_NUMBER_ID`, `WA_TOKEN` and `WA_APP_SECRET` into
`backend/.env`. That file is git-ignored and chmod 600, and only local testing uses it.
`live_wa_check.py` puts the token only in the `Authorization` header and never prints it.

## 2. Point the number's webhooks at Kairos

Meta delivers webhooks for a number to, in this order:

1. the **phone-number override**, if one is set;
2. the WhatsApp Business Account (WABA) override, if one is set;
3. otherwise the **app's callback URL**.

([Webhook overrides](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/override))

### Option A, recommended: a per-phone-number override

This affects **only this number**. The owner's other numbers and their existing webhook keep
working. In Terminal, type the values yourself:

```bash
# set (the token is read from the prompt so it does not land in your shell history)
read -rs WA_TOKEN; export WA_TOKEN
curl -sS -X POST "https://graph.facebook.com/v23.0/<PHONE_NUMBER_ID>" \
  -H "Authorization: Bearer $WA_TOKEN" -H "Content-Type: application/json" \
  -d '{"webhook_configuration": {"override_callback_uri": "https://kairos-backend-5ifc.onrender.com/cm03/whatsapp/inbound",
                                 "verify_token": "<VERIFY_TOKEN from work/wa_verify_token.txt>"}}'
# -> {"success": true}

# read back: "phone_number" must be the Kairos URL
curl -sS "https://graph.facebook.com/v23.0/<PHONE_NUMBER_ID>?fields=webhook_configuration" -H "Authorization: Bearer $WA_TOKEN"

# remove the override after the demo (webhooks return to the app callback URL)
curl -sS -X POST "https://graph.facebook.com/v23.0/<PHONE_NUMBER_ID>" \
  -H "Authorization: Bearer $WA_TOKEN" -H "Content-Type: application/json" \
  -d '{"webhook_configuration": {"override_callback_uri": ""}}'
```

Meta checks the endpoint when you set the override. **Do step 3 first**, so that Render already has
`WA_VERIFY_TOKEN` and the service is awake (open `/health` once). For an override, the app must
already be subscribed to the WABA's webhooks (`POST /<WABA_ID>/subscribed_apps`). It is if the
number already receives webhooks today. The app's `messages` webhook field must also be subscribed.
The callback URL is limited to 200 characters. Meta's own example uses `v27.0`; any current version
works for this call.

### Option B: the app-level callback URL

App dashboard → **WhatsApp → Configuration → Webhook → Edit**:

- **Callback URL:** `https://kairos-backend-5ifc.onrender.com/cm03/whatsapp/inbound`
- **Verify token:** the value from `work/wa_verify_token.txt`
- **Verify and save.** Then, under **Webhook fields**, **Subscribe** to `messages`.

This moves the webhooks of **every number on this app** to Kairos, so use it only if the app serves
nothing else. Put the old URL back after the demo.

Either way, Kairos answers Meta's `GET` verification (`hub.challenge`) only when the token equals
`WA_VERIFY_TOKEN`. Every `POST` must be signed with the App Secret (`X-Hub-Signature-256`).
([Webhooks overview](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview/))

## 3. Render → `kairos-backend` → Environment

Add these values, then **Save, rebuild and deploy** (or Manual Deploy → Deploy latest commit):

| Key | Value |
|---|---|
| `KAIROS_CM03_LIVE_WHATSAPP` | `1` |
| `WA_TOKEN` | the System User token |
| `WA_PHONE_NUMBER_ID` | the Phone number ID (digits) |
| `WA_APP_SECRET` | the App Secret (**required**: without it every webhook POST is refused with 503 in live mode) |
| `WA_VERIFY_TOKEN` | the value from `work/wa_verify_token.txt` (the same value as in Meta) |
| `KAIROS_DEMO_ALLOWLIST` | your number in E.164, e.g. `+91XXXXXXXXXX`: **exactly one** number (two or more switch live mode off) |

Optional settings (the defaults are fine): `WA_GRAPH_VERSION` (`v23.0`), `KAIROS_CM03_LIVE_BORROWER`
(`B-2001`), `KAIROS_CM03_LIVE_MAX_PER_HOUR` (`60`), `KAIROS_CM03_LIVE_MIN_INTERVAL_S` (`1`).

The branch `feat/cm03-live-whatsapp` must be the one deployed: it has to be merged into the branch
Render builds, or Render pointed at it.

**Check:** `https://kairos-backend-5ifc.onrender.com/cm03/live/status` shows
`"live": {"enabled": true, "number": "+********XXXX", ...}`. If not, `missing` lists what is absent.
`/cm03/console` shows a green **LIVE WhatsApp** badge and the live chat panel.

## 4. Test: send "Hi" from your phone

1. Open `https://kairos-backend-5ifc.onrender.com/cm03/console` first. This wakes the free Render
   instance, which needs about 50 s from cold.
2. From **your** phone, send **Hi** to the business number.
3. You receive two messages: the 6-digit code, and the greeting asking for it. The console shows the
   chat as it happens, with ✓ sent, ✓✓ delivered and ✓✓ read ticks from Meta's status webhooks.
4. Type the code back, and you are **verified**. Ask "what is my balance" (a synthetic balance), or
   "I already paid".
5. **Fake screenshot:** in the console, click **Next screenshot = edited (SIMULATED OCR)**, then send
   any image from the phone. No image is downloaded or read. The next image is treated as an edited
   payment screenshot (SIMULATED), so it is caught and handed to a person.
6. **Take over (Asha)**, type a reply, and **Send as agent**: it arrives on the phone. While Asha owns
   the chat, the bot stays silent.
7. Send **STOP** from the phone. It is recorded on every channel, and Asha can no longer message.
8. **Reset live chat** closes the chat (audited) and resets the synthetic borrower's STOP / OTP
   lockout, so you can run the story again.

## What can and cannot happen (the rails)

- **Only your number** is ever messaged. Messages from any other number are ignored, audited (masked)
  and never answered.
- **Only CM-03's own replies** go out: the approved copy (Sarvam phrasing only after the validator,
  else the approved line verbatim), the OTP message, and messages a person types after taking over.
- **No business-initiated messages.** Free-form text goes out only within 24 h of your last message
  (Kairos stops 5 minutes early). Outside that window nothing is sent, and the attempt is logged
  `WINDOW_CLOSED`. No templates are used.
  ([Service messages / customer service window](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages))
- **Rate limit:** at least 1 s between messages, at most 60 per hour (`RATE_LIMITED` after that).
- **Kill switch:** the console's **Kill switch: pause outbound** (K06 `/control/outbound`) stops
  sends at the next message. So does setting `KAIROS_CM03_LIVE_WHATSAPP=0` on Render (redeploy).
- **Masking:** phone numbers show only their last 4 digits in logs, timeline, audit and console. The
  token and App Secret are never logged.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `/cm03/live/status` → `enabled: false` | `missing` names the absent setting. Common ones: `WA_APP_SECRET` not set, `KAIROS_DEMO_ALLOWLIST` with a typo or two numbers, `WA_PHONE_NUMBER_ID` not digits-only. |
| Meta says "The callback URL or verify token couldn't be validated" | `WA_VERIFY_TOKEN` on Render differs from the one you typed in Meta, or the service was asleep or deploying. Open `/health`, wait for it, and retry. |
| Nothing arrives in the console when you text the number | The webhook still points elsewhere: check with `python scripts/live_wa_check.py` (or `GET /<PHONE_NUMBER_ID>?fields=webhook_configuration`). Also check that the `messages` field is subscribed and the app is subscribed to the WABA. |
| The console shows your message, but no reply arrives on the phone | Read the reply's tick in the console. `not sent: WINDOW_CLOSED` means send a new message first (after a restart the window counts as closed until you write). `OUTBOUND_PAUSED` means the kill switch is on. `RATE_LIMITED` means wait. `failed: META_190` means the token is invalid or expired. `META_131030` means the recipient is not allowed. `META_131047` means the 24 h window closed on Meta's side. |
| 401 `bad signature` in Render logs | `WA_APP_SECRET` is not the secret of the app this number belongs to. |
| 503 `requires WA_APP_SECRET` | Live mode is on, but `WA_APP_SECRET` is missing. |
| Your number is ignored (an audit row `CM03_LIVE_IGNORED_SENDER` appears) | `KAIROS_DEMO_ALLOWLIST` is not exactly your number in E.164 (`+91` and 10 digits, no spaces). |
| Token expiry | A **System User** token can be created without expiry. A temporary token from API Setup lasts about 24 h. After it expires, sends fail with `META_190`: generate a new one, update `WA_TOKEN` on Render and redeploy. |
| "I am verified but the borrower is STOPPED / locked" after rehearsals | Press **Reset live chat** (it resets the synthetic borrower's STOP and OTP lockout). |

## Local test without Meta (no message leaves the laptop)

```bash
cd backend && python scripts/cm03_live_local_proof.py --out ../../qa/live_wa
```

This boots the real app in live mode with fake credentials and a fake Cloud API transport, plays the
whole story with signed Meta-shaped webhooks, and writes masked evidence (`evidence.json`,
`transcript.md`, `server.log`) plus a leak check.

To run the real thing locally against Meta (sends **real** messages to your number):

```bash
cd backend
set -a; . ./.env; set +a
export KAIROS_CM03_LIVE_WHATSAPP=1 KAIROS_DEMO_ALLOWLIST=+91XXXXXXXXXX WA_VERIFY_TOKEN=...
uvicorn kairos_backend.api:app --port 8000
```

For this, Meta's webhook has to reach your laptop through a tunnel (e.g. `cloudflared tunnel --url
http://localhost:8000`) set as the override URL.

References (checked 2026-11-20): [Webhook overrides](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/override),
[Webhooks overview](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/overview/),
[Service messages](https://developers.facebook.com/documentation/business-messaging/whatsapp/messages/send-messages),
[Status webhooks](https://developers.facebook.com/documentation/business-messaging/whatsapp/webhooks/reference/messages/status.md).
