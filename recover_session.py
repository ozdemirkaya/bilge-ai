import requests
import os
from dotenv import load_dotenv
import sys

load_dotenv(override=True)

def recover_session():
    admin_secret = os.getenv("ADMIN_SECRET")
    if not admin_secret:
        print("HATA: .env dosyasında ADMIN_SECRET bulunamadı.")
        sys.exit(1)

    url = "http://127.0.0.1:5001/api/oturum-kilidi-kaldir"
    payload = {"admin_secret": admin_secret}

    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200:
            print("BAŞARILI:", response.json().get("mesaj"))
        else:
            print(f"HATA ({response.status_code}):", response.text)
    except Exception as e:
        print("Bağlantı hatası:", str(e))

if __name__ == "__main__":
    recover_session()

