"""Minimal Sarvam AI REST client (stdlib only): text-to-speech and speech-to-text.

Endpoints (docs.sarvam.ai, checked 2026-11-19):
  POST https://api.sarvam.ai/text-to-speech   JSON {text, language_code, speaker?, model?} → {"audios": [base64 WAV]}
  POST https://api.sarvam.ai/speech-to-text   multipart {file, model?, language_code?} → {"transcript", "language_code"}
  Auth header: api-subscription-key

Set SARVAM_API_KEY to use it. Nothing in the replay or tests calls Sarvam. The
live phone route (placing calls, receiving the call webhook) goes through the
Sarvam voice-agent platform, which is configured from kairos/voice/agents/*.json.
"""
from __future__ import annotations

import base64
import json
import os
import uuid
from typing import Optional
from urllib.request import Request, urlopen

BASE_URL = "https://api.sarvam.ai"


class SarvamNotConfigured(RuntimeError):
    pass


class SarvamClient:
    def __init__(self, api_key: Optional[str] = None, base_url: str = BASE_URL, timeout: float = 30):
        self.api_key = api_key or os.environ.get("SARVAM_API_KEY")
        if not self.api_key:
            raise SarvamNotConfigured("Set SARVAM_API_KEY to call Sarvam.")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, body: bytes, content_type: str) -> dict:
        req = Request(f"{self.base_url}{path}", data=body, method="POST",
                      headers={"api-subscription-key": self.api_key, "Content-Type": content_type})
        with urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 (fixed https host)
            return json.loads(resp.read().decode())

    def tts(self, text: str, language_code: str = "hi-IN", speaker: Optional[str] = None,
            model: str = "bulbul:v3") -> bytes:
        """Return WAV bytes for `text`."""
        body = {"text": text, "language_code": language_code, "model": model}
        if speaker:
            body["speaker"] = speaker
        data = self._post("/text-to-speech", json.dumps(body).encode(), "application/json")
        return base64.b64decode(data["audios"][0])

    def stt(self, audio: bytes, filename: str = "audio.wav", language_code: str = "hi-IN",
            model: str = "saaras:v4") -> str:
        """Return the transcript of an audio clip."""
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in (("model", model), ("language_code", language_code)):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                     f"Content-Type: application/octet-stream\r\n\r\n".encode() + audio + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        data = self._post("/speech-to-text", b"".join(parts), f"multipart/form-data; boundary={boundary}")
        return data["transcript"]
