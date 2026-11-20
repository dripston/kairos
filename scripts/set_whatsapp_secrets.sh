#!/bin/zsh
# Saves the WhatsApp Cloud API secrets for LOCAL testing of CM-03 live mode into backend/.env
# (git-ignored, chmod 600, readable only by you). You type / paste them; nothing is shown on screen,
# nothing is sent anywhere. On Render, set the same values in the dashboard instead (docs/LIVE_WHATSAPP.md).
#
#   zsh scripts/set_whatsapp_secrets.sh                 # this checkout's backend/
#   zsh scripts/set_whatsapp_secrets.sh /path/to/backend
BACKEND="${1:-${0:A:h}/../backend}"
cd "$BACKEND" || { echo "Could not find $BACKEND"; exit 1; }

ask() {  # ask <VAR> <prompt>  -> reads silently into REPLY_VALUE
  printf "%s (it will not be shown, Enter to keep the current value): " "$2"
  read -rs REPLY_VALUE
  echo
}

touch .env && chmod 600 .env
cp .env .env.tmp && chmod 600 .env.tmp
changed=0
for pair in "WA_PHONE_NUMBER_ID|Phone number ID (digits)" "WA_TOKEN|System User access token" "WA_APP_SECRET|App Secret"; do
  var="${pair%%|*}"; label="${pair#*|}"
  ask "$var" "$label"
  if [ -n "$REPLY_VALUE" ]; then
    if [ "$var" = "WA_PHONE_NUMBER_ID" ] && ! [[ "$REPLY_VALUE" =~ '^[0-9]+$' ]]; then
      echo "  The Phone number ID is digits only; $var not changed."
    else
      grep -v "^${var}=" .env.tmp > .env.tmp2; mv .env.tmp2 .env.tmp
      printf '%s=%s\n' "$var" "$REPLY_VALUE" >> .env.tmp
      changed=$((changed + 1))
    fi
  fi
done
unset REPLY_VALUE
mv .env.tmp .env && chmod 600 .env
echo "Updated $changed value(s) in $(pwd)/.env (chmod 600)."
if git check-ignore -q .env 2>/dev/null; then
  echo "backend/.env is git-ignored. Check it with: python scripts/live_wa_check.py"
else
  echo "WARNING: .env is not git-ignored here. Do not commit anything; tell Claude."
fi
