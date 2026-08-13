import base64
import json
import os

import requests
from flask import Flask, request


app = Flask(__name__)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_API = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)

SYSTEM_PROMPT = """Sen bir kilo kontrol danışanları grubunda yer alan, sıcak ve
destekleyici bir motivasyon botusun.

Görevin: Grup üyelerinin paylaştığı mesaj veya fotoğrafı analiz edip, SADECE
şu iki durumda kısa, samimi, motive edici bir Türkçe cümle yazmak:
1) YEMEK paylaşımı: kullanıcı yediği/yiyeceği bir şeyi paylaşmışsa
2) KİLO paylaşımı: kullanıcı tartı sonucunu / kilosunu paylaşmışsa

Kurallar:
- Yemek paylaşımı sağlıklıysa kısaca öv; değilse asla yargılama, nazikçe
  destekle ve isteğe bağlı ufak bir öneri ekle.
- Kilo paylaşımında sayıya (arttı/azaldı) göre eleştirme veya aşırı
  övme yapma; sürece ve çabaya odaklanan, sakin bir motivasyon cümlesi yaz.
- Mesaj bunların dışındaysa (sohbet, soru, selamlaşma, alakasız fotoğraf
  vb.): kategori "diger" olarak işaretle ve cevap YAZMA.
- Cevaplar 1-2 cümle olsun, samimi ve doğal, emoji ölçülü kullanılabilir.
- Asla tıbbi tavsiye, kesin kalori/diyet talimatı verme.

SADECE şu JSON formatında cevap ver, başka hiçbir metin ekleme:
{"kategori": "yemek" | "kilo" | "diger", "cevap": "..."}
Kategori "diger" ise "cevap" alanını boş string bırak.
"""


def parse_response(raw_text: str):
    cleaned = raw_text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    try:
        data = json.loads(cleaned)
        return data.get("kategori", "diger"), data.get("cevap", "")
    except (TypeError, json.JSONDecodeError):
        return "diger", ""


def ask_gemini(parts):
    payload = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "maxOutputTokens": 300,
            "temperature": 0.6,
            "responseMimeType": "application/json",
        },
    }
    response = requests.post(
        GEMINI_API,
        params={"key": GEMINI_API_KEY},
        json=payload,
        timeout=25,
    )
    response.raise_for_status()
    data = response.json()
    raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
    return parse_response(raw_text)


def ask_gemini_text(text: str):
    return ask_gemini([{"text": text}])


def ask_gemini_photo(image_bytes: bytes, caption: str):
    b64_image = base64.b64encode(image_bytes).decode("utf-8")
    return ask_gemini(
        [
            {"inlineData": {"mimeType": "image/jpeg", "data": b64_image}},
            {
                "text": (
                    "Bu fotoğrafı analiz et. Kullanıcının notu (varsa): "
                    f"{caption}"
                )
            },
        ]
    )


def send_message(chat_id, text, reply_to_message_id=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_to_message_id:
        payload["reply_to_message_id"] = reply_to_message_id
    response = requests.post(
        f"{TELEGRAM_API}/sendMessage", json=payload, timeout=15
    )
    response.raise_for_status()


def get_file_bytes(file_id):
    response = requests.get(
        f"{TELEGRAM_API}/getFile", params={"file_id": file_id}, timeout=15
    )
    response.raise_for_status()
    file_path = response.json()["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_path}"
    file_response = requests.get(file_url, timeout=15)
    file_response.raise_for_status()
    return file_response.content


@app.route("/api/webhook", methods=["POST"])
def webhook():
    update = request.get_json(force=True, silent=True) or {}
    message = update.get("message")
    if not message:
        return "ok", 200

    chat_id = message["chat"]["id"]
    message_id = message.get("message_id")

    try:
        if "text" in message:
            kategori, cevap = ask_gemini_text(message["text"])
        elif "photo" in message:
            file_id = message["photo"][-1]["file_id"]
            image_bytes = get_file_bytes(file_id)
            kategori, cevap = ask_gemini_photo(
                image_bytes, message.get("caption", "")
            )
        else:
            return "ok", 200

        if kategori in ("yemek", "kilo") and cevap:
            send_message(chat_id, cevap, reply_to_message_id=message_id)
    except Exception as error:
        print("Hata:", error)

    return "ok", 200


@app.route("/api/webhook", methods=["GET"])
def health_check():
    return "Bot çalışıyor.", 200

