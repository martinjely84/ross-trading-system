#!/usr/bin/env python3
# Simple standalone bot test - run this instead of main.py to diagnose
import requests
import time

TOKEN = "8370287942:AAGKQPIbybD3WByLiF29aqg9NxnWXLWrH-Q"
offset = None

def send(chat_id, text):
    requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=5
    )

print("Bot test running... send /help to @Shearertradingbot")

while True:
    try:
        params = {"timeout": 0, "limit": 10}
        if offset:
            params["offset"] = offset
        resp = requests.get(
            f"https://api.telegram.org/bot{TOKEN}/getUpdates",
            params=params, timeout=5
        )
        updates = resp.json().get("result", [])
        for u in updates:
            offset = u["update_id"] + 1
            msg = u.get("message", {})
            chat_id = msg.get("chat", {}).get("id")
            text = msg.get("text", "")
            print(f"Got message: {text} from {chat_id}")
            if chat_id and text:
                send(chat_id, f"✅ Received: {text}\nBot is working!")
    except Exception as e:
        print(f"Error: {e}")
    time.sleep(2)
