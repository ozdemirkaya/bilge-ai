from flask import Flask, jsonify, request, render_template
import os
import requests
import time
from dotenv import load_dotenv
import threading
import ipaddress
import hmac
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv(override=True)

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1)

session_creation_lock = threading.RLock()

@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://esm.sh; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https: wss:; "
        "media-src 'self' blob: https:; "
        "frame-ancestors 'none';"
    )
    response.headers['Content-Security-Policy'] = csp
    return response

app.config['MAX_CONTENT_LENGTH'] = 1 * 1024 * 1024  # 1 MB max request size

# --- RAM Tabanlı Basit Durum Yönetimi (Yerel Prototip İçin) ---
# Gerçek canlı ortamda Redis veya kalıcı bir veritabanı ile rate limiting yapılmalıdır.
DEMO_STATE = {
    "aktif_oturum": None,       # {"ip": "...", "bitis_zamani": ..., "session_id": "...", "kapatma_basarisiz": False, "retry_count": 0}
    "ip_kullanim": {},          # {"ip": kac_kere_kullandi}
}

MAX_DEMO_SURESI_SN = 60         # 1 Dakika (Gerçek avatar testi için)
MAX_GUNLUK_HAK = 3              # IP başına günlük maksimum demo hakkı
MAX_RETRY_COUNT = 3             # Oturum kapatma maksimum deneme sayısı

def is_production():
    """Sunucu ortamının production olup olmadığını belirler."""
    return os.getenv("RENDER", "false").lower() == "true" or os.getenv("ENVIRONMENT", "").lower() == "production"

def get_client_ip():
    """
    Güvenilir istemci IP adresini döndürür.
    - Production ortamında: Yalnızca Render/Cloudflare tarafından garanti edilen CF-Connecting-IP başlığını kabul eder.
      ipaddress.ip_address() ile tekil, geçerli bir IPv4 veya IPv6 doğrulaması yapar.
      Başlık eksik veya geçersizse None döner (güvensiz X-Forwarded-For'a asla düşmez).
    - Local geliştirme ortamında: Varsa CF-Connecting-IP, yoksa request.remote_addr kullanır.
    """
    cf_header = request.headers.get("CF-Connecting-IP")
    if cf_header:
        raw_ip = cf_header.strip()
        try:
            return str(ipaddress.ip_address(raw_ip))
        except ValueError:
            return None

    if is_production():
        return None

    # Local geliştirme / test ortamı
    remote_ip = (request.remote_addr or "").strip()
    if remote_ip:
        try:
            return str(ipaddress.ip_address(remote_ip))
        except ValueError:
            return None

    return None

def stop_heygen_session(session_id, reason="UNKNOWN"):
    """HeyGen API'sine oturumu durdurma isteği atar (Gerçek API çağrısı)"""
    api_key = os.getenv("HEYGEN_API_KEY")
    if not api_key or not session_id: return False
    
    url = "https://api.liveavatar.com/v1/sessions/stop"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json"
    }
    payload = {"session_id": session_id, "reason": reason}
    
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        if resp.status_code == 200:
            app.logger.info(f"HeyGen backend stop successful for session: {session_id}")
            return True
        else:
            app.logger.warning(f"HeyGen backend stop failed: {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        app.logger.error(f"HeyGen backend stop exception: {str(e)}")
        return False

def temizle_suresi_biten_oturumu():
    """Süresi biten veya kapatılamamış aktif oturumu temizler."""
    with session_creation_lock:
        oturum = DEMO_STATE["aktif_oturum"]
        if not oturum:
            return True

        # Eğer süre dolduysa veya daha önceden kapatılamadıysa
        if time.time() > oturum["bitis_zamani"] or oturum.get("kapatma_basarisiz", False):
            sess_id = oturum.get("session_id")
            if sess_id:
                basarili = stop_heygen_session(sess_id, reason="MAX_DURATION_REACHED")
                if basarili:
                    DEMO_STATE["aktif_oturum"] = None
                    return True
                else:
                    # Başarısızlık durumunda oturum kilitli kalır, KESİNLİKLE açılmaz
                    # Güvenlik amacıyla bakiye harcamasını önlemek için kilitte kalması zorunludur.
                    oturum["kapatma_basarisiz"] = True
                    oturum["retry_count"] = oturum.get("retry_count", 0) + 1
                    app.logger.warning(f"Oturum kapatılamadı. Kilit aktif. Deneme: {oturum['retry_count']}")
                    return False
            else:
                # Session ID yoksa (hata), kilidi direkt kaldır
                DEMO_STATE["aktif_oturum"] = None
                return True
        return False # Oturum hala aktif ve süresi dolmamış

@app.route('/api/oturum-kilidi-kaldir', methods=['POST'])
def oturum_kilidi_kaldir():
    """Manuel kurtarma yöntemi (Production ortamında varsayılan olarak devre dışıdır)."""
    # 1. Production ortamında endpoint her zaman kapalıdır (404)
    # Yerel geliştirme ortamında ise ENABLE_ADMIN_API=true ile açıkça etkinleştirilmedikçe kapalıdır (404)
    if is_production() or os.getenv("ENABLE_ADMIN_API", "false").lower() != "true":
        return jsonify({"durum": False, "mesaj": "Sayfa bulunamadi."}), 404

    # 2. Yalnızca localhost üzerinden erişilebilir
    if request.remote_addr not in ('127.0.0.1', '::1'):
        return jsonify({"durum": False, "mesaj": "Yetkisiz erisim."}), 403
        
    admin_secret = os.getenv("ADMIN_SECRET", "")
    req_secret = request.json.get("admin_secret", "") if request.is_json and request.json else ""
    
    # Timing attack koruması için hmac.compare_digest kullanılır; ayrıntılı hata sızdırılmaz
    if not admin_secret or not req_secret or not hmac.compare_digest(admin_secret, req_secret):
        return jsonify({"durum": False, "mesaj": "Yetkisiz erisim."}), 403

    with session_creation_lock:
        oturum = DEMO_STATE["aktif_oturum"]
        if not oturum:
            return jsonify({"durum": False, "mesaj": "Kilitli oturum yok."})
        
        DEMO_STATE["aktif_oturum"] = None
    app.logger.info("Manuel kurtarma: Oturum kilidi yetkili CLI istegiyle zorla kaldırıldı.")
    return jsonify({"durum": True, "mesaj": "Kilit kaldırıldı."})

def arka_plan_zamanlayici():
    """
    Tarayıcı kapansa bile süresi biten oturumları kapatan bağımsız arka plan döngüsü.
    DİKKAT: Flask daemon thread mekanizması uygulamanın tamamen çökmesi veya 
    sunucunun yeniden başlatılması gibi durumlarda garanti sağlamaz.
    Gerçek üretim ortamında Redis/Celery gibi dışsal bir zamanlayıcı kullanılmalıdır.
    """
    while True:
        time.sleep(5)
        try:
            temizle_suresi_biten_oturumu()
        except Exception as e:
            app.logger.error(f"Arka plan temizleme hatası: {e}")

# Uygulama başlatılırken arka plan görevini başlat
# Werkzeug dev reloader'ın thread'i iki kere başlatmasını önle
if not app.debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
    monitor_thread = threading.Thread(target=arka_plan_zamanlayici, daemon=True)
    monitor_thread.start()

@app.route('/')
def ana_sayfa():
    demo_dev_tools = os.getenv("DEMO_DEV_TOOLS", "False").lower() == "true"
    return render_template('index.html', demo_dev_tools=demo_dev_tools)

@app.route('/api/token-al', methods=['POST'])
def token_al():
    temizle_suresi_biten_oturumu()
    ip = get_client_ip()
    if not ip:
        return jsonify({
            "durum": False,
            "mesaj": "Istemci kimligi dogrulanamadi."
        }), 400

    with session_creation_lock:
        # 1. Eş zamanlı oturum kontrolü (Sadece 1 aktif oturuma izin ver)
        oturum = DEMO_STATE["aktif_oturum"]
        if oturum:
            if oturum.get("kapatma_basarisiz", False):
                return jsonify({
                    "durum": False,
                    "mesaj": "Önceki oturum sunucu tarafında tam olarak kapatılamadı. Lütfen kısa süre sonra tekrar deneyin."
                }), 429
            if oturum["ip"] != ip:
                return jsonify({
                    "durum": False,
                    "mesaj": "Şu an başka bir ziyaretçi demoyu test ediyor. Lütfen birkaç dakika sonra tekrar deneyin."
                }), 429
            else:
                return jsonify({
                    "durum": False,
                    "mesaj": "Zaten aktif bir oturumunuz bulunuyor. Lütfen önce mevcut oturumu kapatın."
                }), 429

        # 2. IP bazlı kullanım limiti kontrolü
        kullanim = DEMO_STATE["ip_kullanim"].get(ip, 0)
        if kullanim >= MAX_GUNLUK_HAK:
            return jsonify({
                "durum": False,
                "mesaj": "Günlük demo kullanım limitinize (3/3) ulaştınız."
            }), 429

        sandbox_mode = os.getenv("SANDBOX_MODE", "True").lower() != "false"
        api_key = os.getenv("HEYGEN_API_KEY")

        if sandbox_mode:
            # Sandbox mode SADECE belirli test avatarlarını destekler
            avatar_id = "dd73ea75-1218-4ef3-92ce-606d5f7fbc0a"
            context_id = os.getenv("CONTEXT_ID")
        else:
            avatar_id = os.getenv("AVATAR_ID")
            context_id = os.getenv("CONTEXT_ID")

        url = "https://api.liveavatar.com/v1/sessions/token"
        headers = {
            "x-api-key": api_key,
            "Content-Type": "application/json"
        }

        # FULL Mode ve is_sandbox flagi
        payload = {
            "mode": "FULL",
            "avatar_id": avatar_id,
            "is_sandbox": sandbox_mode
        }

        if context_id:
            payload["avatar_persona"] = {
                "context_id": context_id,
                "language": "tr"
            }
        elif sandbox_mode:
            payload["avatar_persona"] = {
                "language": "tr"
            }

        try:
            cevap = requests.post(url, headers=headers, json=payload, timeout=10)
            if cevap.status_code == 200:
                veri = cevap.json()
                session_token = veri.get("data", {}).get("session_token")
                session_id = veri.get("data", {}).get("session_id")
            else:
                app.logger.error(f"HeyGen Hatası: {cevap.status_code} - {cevap.text}")
                return jsonify({"durum": False, "mesaj": "Avatar sunucusuna bağlanılamadı."}), cevap.status_code
        except Exception as e:
            app.logger.error(f"Bağlantı Hatası: {str(e)}")
            return jsonify({"durum": False, "mesaj": "Sunucu iletişimi başarısız."}), 500

        # Başarılı - Oturumu kaydet
        DEMO_STATE["aktif_oturum"] = {
            "ip": ip,
            "session_id": session_id,
            "bitis_zamani": time.time() + MAX_DEMO_SURESI_SN,
            "kapatma_basarisiz": False,
            "retry_count": 0
        }
        DEMO_STATE["ip_kullanim"][ip] = kullanim + 1

        return jsonify({
            "durum": True,
            "token": session_token,
            "session_id": session_id,
            "max_sure_sn": MAX_DEMO_SURESI_SN
        })

@app.route('/api/oturum-kapat', methods=['POST'])
def oturum_kapat():
    ip = get_client_ip()
    if not ip:
        return jsonify({
            "durum": False,
            "mesaj": "Istemci kimligi dogrulanamadi."
        }), 400
    with session_creation_lock:
        oturum = DEMO_STATE["aktif_oturum"]
        if oturum and oturum["ip"] == ip:
            sess_id = oturum.get("session_id")
            if sess_id:
                basarili = stop_heygen_session(sess_id, reason="USER_CLOSED")
                if basarili:
                    DEMO_STATE["aktif_oturum"] = None
                    return jsonify({"durum": True, "mesaj": "Oturum sonlandırıldı."})
                else:
                    # Kapanma başarısızsa durumu işaretle, thread tekrar denesin
                    oturum["kapatma_basarisiz"] = True
                    return jsonify({"durum": False, "mesaj": "HeyGen API'ye ulaşılamadı. Arka planda denenecek."}), 500
            else:
                DEMO_STATE["aktif_oturum"] = None
                return jsonify({"durum": True, "mesaj": "Oturum yerel olarak sonlandırıldı."})
        return jsonify({"durum": False, "mesaj": "Kapatılacak aktif oturum bulunamadı."})

if __name__ == '__main__':
    app.run(debug=True, port=5001)
