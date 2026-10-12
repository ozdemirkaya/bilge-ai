import os
import unittest
import time
from unittest.mock import patch, MagicMock
from app import app, DEMO_STATE

class TestDemo(unittest.TestCase):
    def setUp(self):
        app.config['TESTING'] = True
        os.environ["SANDBOX_MODE"] = "True"
        os.environ["HEYGEN_API_KEY"] = "test_mock_api_key"
        os.environ.pop("ENVIRONMENT", None)
        os.environ.pop("RENDER", None)
        os.environ.pop("ENABLE_ADMIN_API", None)
        os.environ.pop("ADMIN_SECRET", None)
        self.istemci = app.test_client()
        # State'i temizle
        DEMO_STATE["aktif_oturum"] = None
        DEMO_STATE["ip_kullanim"] = {}

    def test_ana_sayfa(self):
        cevap = self.istemci.get('/')
        self.assertEqual(cevap.status_code, 200)
        self.assertIn(b"Bilge AI", cevap.data)

    @patch('app.requests.post')
    def test_oturum_olusturma_basarili(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"session_token": "test_token_123", "session_id": "sess_123"}}
        mock_post.return_value = mock_response

        cevap = self.istemci.post('/api/token-al')
        veri = cevap.get_json()
        
        self.assertEqual(cevap.status_code, 200)
        self.assertTrue(veri["durum"])
        self.assertEqual(veri["token"], "test_token_123")
        self.assertIsNotNone(DEMO_STATE["aktif_oturum"])

    @patch('app.requests.post')
    def test_oturum_olusturma_api_hatasi_401(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 401
        mock_response.text = "Unauthorized"
        mock_post.return_value = mock_response

        cevap = self.istemci.post('/api/token-al')
        veri = cevap.get_json()
        
        self.assertEqual(cevap.status_code, 401)
        self.assertFalse(veri["durum"])
        self.assertIsNone(DEMO_STATE["aktif_oturum"], "API hatasında oturum kilidi oluşmamalı")

    @patch('app.requests.post')
    def test_oturum_olusturma_api_hatasi_422(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 422
        mock_response.text = "Unprocessable Entity"
        mock_post.return_value = mock_response

        cevap = self.istemci.post('/api/token-al')
        veri = cevap.get_json()
        
        self.assertEqual(cevap.status_code, 422)
        self.assertFalse(veri["durum"])
        self.assertIsNone(DEMO_STATE["aktif_oturum"], "422 Hatasında oturum kilidi oluşmamalı")

    @patch('app.requests.post')
    def test_oturum_olusturma_api_hatasi_400(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = '{"code":4000,"message":"Bad request error"}'
        mock_post.return_value = mock_response

        cevap = self.istemci.post('/api/token-al')
        veri = cevap.get_json()
        
        self.assertEqual(cevap.status_code, 400)
        self.assertFalse(veri["durum"])
        self.assertIsNone(DEMO_STATE["aktif_oturum"], "400 Hatasında oturum kilidi oluşmamalı")

    @patch('app.requests.post')
    def test_oturum_olusturma_baglanti_hatasi(self, mock_post):
        mock_post.side_effect = Exception("Connection Timeout")

        cevap = self.istemci.post('/api/token-al')
        veri = cevap.get_json()
        
        self.assertEqual(cevap.status_code, 500)
        self.assertFalse(veri["durum"])
        self.assertIsNone(DEMO_STATE["aktif_oturum"], "Bağlantı hatasında oturum kilidi oluşmamalı")

    def test_es_zamanli_oturum_engelleme(self):
        DEMO_STATE["aktif_oturum"] = {"ip": "1.1.1.1", "bitis_zamani": time.time() + 120, "kapatma_basarisiz": False}
        cevap = self.istemci.post('/api/token-al')
        veri = cevap.get_json()
        
        self.assertEqual(cevap.status_code, 429)
        self.assertFalse(veri["durum"])
        self.assertIn("\u015eu an ba\u015fka", veri["mesaj"])

    def test_kapatma_basarisizken_yeni_oturum_engelleme(self):
        DEMO_STATE["aktif_oturum"] = {"ip": "127.0.0.1", "bitis_zamani": time.time() - 10, "kapatma_basarisiz": True, "session_id": "stuck"}
        cevap = self.istemci.post('/api/token-al')
        veri = cevap.get_json()
        
        self.assertEqual(cevap.status_code, 429)
        self.assertFalse(veri["durum"])
        self.assertIn("\u00d6nceki oturum", veri["mesaj"])

    def test_gunluk_limit(self):
        DEMO_STATE["ip_kullanim"]["127.0.0.1"] = 3
        cevap = self.istemci.post('/api/token-al')
        veri = cevap.get_json()
        
        self.assertEqual(cevap.status_code, 429)
        self.assertFalse(veri["durum"])
        self.assertIn("limitinize", veri["mesaj"])

    @patch('app.requests.post')
    def test_oturum_kapatma(self, mock_post):
        # 1. Start session
        mock_response_start = MagicMock()
        mock_response_start.status_code = 200
        mock_response_start.json.return_value = {"data": {"session_token": "test", "session_id": "test_sess_1"}}
        mock_post.return_value = mock_response_start

        self.istemci.post('/api/token-al')
        self.assertIsNotNone(DEMO_STATE["aktif_oturum"])
        
        # 2. Stop session
        mock_response_stop = MagicMock()
        mock_response_stop.status_code = 200
        mock_post.return_value = mock_response_stop
        
        cevap = self.istemci.post('/api/oturum-kapat')
        self.assertEqual(cevap.status_code, 200)
        self.assertIsNone(DEMO_STATE["aktif_oturum"])
        
        # Verify stop was called
        args, kwargs = mock_post.call_args
        self.assertEqual(kwargs.get('json', {}).get('session_id'), "test_sess_1")
        self.assertIn("stop", args[0] if args else kwargs.get('url', 'stop'))

    @patch('app.requests.post')
    def test_oturum_kapatma_api_hatasi(self, mock_post):
        # Ensure session cleanup doesn't crash the server if Stop API fails
        DEMO_STATE["aktif_oturum"] = {"ip": "127.0.0.1", "session_id": "err_sess", "bitis_zamani": time.time() + 120}
        
        mock_post.side_effect = Exception("Stop API Timeout")
        
        cevap = self.istemci.post('/api/oturum-kapat')
        self.assertEqual(cevap.status_code, 500)
        self.assertIsNotNone(DEMO_STATE["aktif_oturum"], "API hatasında yerel state hemen temizlenmez, thread'e bırakılır")
    def test_manuel_kurtarma_yetkisiz(self):
        os.environ["ENABLE_ADMIN_API"] = "true"
        DEMO_STATE["aktif_oturum"] = {"ip": "127.0.0.1", "session_id": "stuck"}
        # Secret olmadan istek
        cevap = self.istemci.post('/api/oturum-kilidi-kaldir', json={}, environ_base={'REMOTE_ADDR': '127.0.0.1'})
        self.assertEqual(cevap.status_code, 403)
        self.assertIsNotNone(DEMO_STATE["aktif_oturum"])

    def test_manuel_kurtarma_yetkili(self):
        os.environ["ENABLE_ADMIN_API"] = "true"
        DEMO_STATE["aktif_oturum"] = {"ip": "127.0.0.1", "session_id": "stuck"}
        os.environ["ADMIN_SECRET"] = "gizli_test_sifresi"
        cevap = self.istemci.post('/api/oturum-kilidi-kaldir', json={"admin_secret": "gizli_test_sifresi"}, environ_base={'REMOTE_ADDR': '127.0.0.1'})
        self.assertEqual(cevap.status_code, 200)
        self.assertIsNone(DEMO_STATE["aktif_oturum"])

    @patch('app.requests.post')
    def test_sandbox_payload(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"session_token": "test_token_123", "session_id": "sess_123"}}
        mock_post.return_value = mock_response

        # SANDBOX_MODE is True in setUp
        os.environ["CONTEXT_ID"] = "test-context"
        self.istemci.post('/api/token-al')
        
        args, kwargs = mock_post.call_args
        payload = kwargs.get('json', {})
        self.assertEqual(payload.get('avatar_id'), "dd73ea75-1218-4ef3-92ce-606d5f7fbc0a")
        self.assertTrue(payload.get('is_sandbox'))
        self.assertEqual(payload.get('avatar_persona', {}).get('context_id'), "test-context")
        self.assertEqual(payload.get('avatar_persona', {}).get('language'), "tr")

    @patch('app.requests.post')
    def test_sandbox_typo_fallback(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"session_token": "test", "session_id": "sess_1"}}
        mock_post.return_value = mock_response

        # Gecersiz deger ver
        os.environ["SANDBOX_MODE"] = "YanlisDeger"
        
        self.istemci.post('/api/token-al')
        
        args, kwargs = mock_post.call_args
        payload = kwargs.get('json', {})
        self.assertTrue(payload.get('is_sandbox'), "Typo yapıldığında varsayılan olarak Sandbox açılmalıdır")

    @patch('app.requests.post')
    def test_real_avatar_payload(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"session_token": "test_token_123", "session_id": "sess_123"}}
        mock_post.return_value = mock_response

        # Turn off sandbox
        os.environ["SANDBOX_MODE"] = "False"
        os.environ["AVATAR_ID"] = "real-avatar-id"
        os.environ["CONTEXT_ID"] = "real-context-id"
        
        self.istemci.post('/api/token-al')
        
        args, kwargs = mock_post.call_args
        payload = kwargs.get('json', {})
        self.assertEqual(payload.get('avatar_id'), "real-avatar-id")
        self.assertFalse(payload.get('is_sandbox'))
        self.assertEqual(payload.get('avatar_persona', {}).get('context_id'), "real-context-id")
        self.assertEqual(payload.get('avatar_persona', {}).get('language'), "tr")

    @patch('app.requests.post')
    def test_race_condition_es_zamanli_istekler(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        # Her biri farklı session_id döndürsün diye
        mock_response.json.return_value = {"data": {"session_token": "test", "session_id": "test_sess_1"}}
        
        def side_effect(*args, **kwargs):
            import time
            time.sleep(0.1) # Ağ gecikmesi simülasyonu
            return mock_response
            
        mock_post.side_effect = side_effect
        
        import threading
        results = []
        
        def worker():
            # TestClient requires application context inside thread or using client directly
            with app.test_client() as c:
                cevap = c.post('/api/token-al')
                results.append(cevap.status_code)
                
        threads = []
        for _ in range(5):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
            
        for t in threads:
            t.join()
            
        basarili_sayisi = results.count(200)
        self.assertEqual(basarili_sayisi, 1, "Race condition engellenemedi: Aynı anda birden fazla session oluşturuldu")

    @patch('app.requests.post')
    def test_ip_spoofing_proxy_fix(self, mock_post):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"session_token": "test", "session_id": "test_sess"}}
        mock_post.return_value = mock_response
        
        # We test that rate limit is applied to the REAL IP, regardless of X-Forwarded-For spoofing.
        # Assume the proxy passes X-Forwarded-For: "spoofed_ip, real_ip"
        from app import DEMO_STATE
        for i in range(4):
            DEMO_STATE["aktif_oturum"] = None
            cevap = self.istemci.post(
                '/api/token-al', 
                headers={'X-Forwarded-For': f'10.0.0.{i}, 192.168.1.100', 'X-Forwarded-Proto': 'https'}
            )
            
        # The 4th request should be rate-limited (HTTP 429) because the real IP 192.168.1.100 has been used 3 times.
        self.assertEqual(cevap.status_code, 429)
        veri = cevap.get_json()
        self.assertIn("limitinize", veri["mesaj"])

    def test_admin_spoofing_proxy_fix(self):
        # Dışarıdan gelen isteklerde REMOTE_ADDR proxy tarafından 127.0.0.1 değilse reddedilir
        os.environ["ENABLE_ADMIN_API"] = "true"
        cevap = self.istemci.post(
            '/api/oturum-kilidi-kaldir',
            environ_base={'REMOTE_ADDR': '203.0.113.5'},
            headers={'X-Forwarded-For': '127.0.0.1, 203.0.113.5'},
            json={"admin_secret": "gizli_sifre"}
        )
        self.assertEqual(cevap.status_code, 403)

    def test_admin_api_production_kapali_404(self):
        # Production ortamında admin API her zaman 404 döner
        os.environ["ENVIRONMENT"] = "production"
        os.environ["ENABLE_ADMIN_API"] = "true"
        os.environ["ADMIN_SECRET"] = "test_secret"
        cevap = self.istemci.post(
            '/api/oturum-kilidi-kaldir',
            json={"admin_secret": "test_secret"},
            environ_base={'REMOTE_ADDR': '127.0.0.1'}
        )
        self.assertEqual(cevap.status_code, 404)
        self.assertFalse(cevap.get_json()["durum"])

    def test_admin_api_local_varsayilan_kapali_404(self):
        # Local ortamda ENABLE_ADMIN_API tanımlı değilse 404 dönmelidir
        cevap = self.istemci.post(
            '/api/oturum-kilidi-kaldir',
            json={"admin_secret": "test_secret"},
            environ_base={'REMOTE_ADDR': '127.0.0.1'}
        )
        self.assertEqual(cevap.status_code, 404)

    def test_admin_api_hmac_yanlis_secret_403(self):
        # hmac.compare_digest ile gizli anahtar yanlışsa genel yetkisiz erişim mesajı
        os.environ["ENABLE_ADMIN_API"] = "true"
        os.environ["ADMIN_SECRET"] = "dogru_sifre"
        cevap = self.istemci.post(
            '/api/oturum-kilidi-kaldir',
            json={"admin_secret": "yanlis_sifre"},
            environ_base={'REMOTE_ADDR': '127.0.0.1'}
        )
        self.assertEqual(cevap.status_code, 403)
        self.assertEqual(cevap.get_json()["mesaj"], "Yetkisiz erisim.")

    def test_production_eksik_cf_ip_reddedilir_400(self):
        # Production ortamında CF-Connecting-IP eksikse istek 400 ile reddedilir
        os.environ["ENVIRONMENT"] = "production"
        cevap = self.istemci.post('/api/token-al')
        self.assertEqual(cevap.status_code, 400)
        self.assertFalse(cevap.get_json()["durum"])
        self.assertIn("dogrulanamadi", cevap.get_json()["mesaj"])

    def test_production_gecersiz_cf_ip_reddedilir_400(self):
        # Production ortamında geçersiz IP formatı veya çoklu IP enjeksiyonu 400 ile reddedilir
        os.environ["ENVIRONMENT"] = "production"
        for bad_ip in ["gecersiz_ip", "1.1.1.1, 2.2.2.2", "999.999.999.999"]:
            cevap = self.istemci.post(
                '/api/token-al',
                headers={'CF-Connecting-IP': bad_ip}
            )
            self.assertEqual(cevap.status_code, 400)
            self.assertFalse(cevap.get_json()["durum"])

    @patch('app.requests.post')
    def test_production_sahte_xff_engellenir_cf_ip_kullanilir(self, mock_post):
        # Saldırgan sahte X-Forwarded-For gönderse bile sistem sadece CF-Connecting-IP'yi baz alır
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"session_token": "token_xyz", "session_id": "sess_xyz"}}
        mock_post.return_value = mock_response

        os.environ["ENVIRONMENT"] = "production"
        cevap = self.istemci.post(
            '/api/token-al',
            headers={
                'CF-Connecting-IP': '203.0.113.195',
                'X-Forwarded-For': '10.0.0.1, 1.2.3.4'
            }
        )
        self.assertEqual(cevap.status_code, 200)
        self.assertEqual(DEMO_STATE["aktif_oturum"]["ip"], "203.0.113.195")

    @patch('app.requests.post')
    def test_farkli_kullanici_ipleri_ayri_limitler(self, mock_post):
        # Farklı IP adreslerine sahip iki kullanıcı birbirinin limitini etkilemez
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"session_token": "token_abc", "session_id": "sess_abc"}}
        mock_post.return_value = mock_response

        os.environ["ENVIRONMENT"] = "production"
        ip_a = "198.51.100.10"
        ip_b = "198.51.100.20"

        # Kullanıcı A'nın 3 hakkını tüket
        for _ in range(3):
            DEMO_STATE["aktif_oturum"] = None
            resp = self.istemci.post('/api/token-al', headers={'CF-Connecting-IP': ip_a})
            self.assertEqual(resp.status_code, 200)

        # Kullanıcı A'nın 4. isteği 429 almalı
        DEMO_STATE["aktif_oturum"] = None
        resp_a4 = self.istemci.post('/api/token-al', headers={'CF-Connecting-IP': ip_a})
        self.assertEqual(resp_a4.status_code, 429)

        # Kullanıcı B farklı IP ile istek attığında başarılı olmalı (ayrı limit)
        resp_b = self.istemci.post('/api/token-al', headers={'CF-Connecting-IP': ip_b})
        self.assertEqual(resp_b.status_code, 200)
        
    @patch('app.requests.post')
    def test_exception_releases_lock(self, mock_post):
        # Simulate a crash during the API call
        mock_post.side_effect = Exception("Sudden API Crash")
        
        cevap = self.istemci.post('/api/token-al')
        self.assertEqual(cevap.status_code, 500)
        
        # Lock should be released, so another request can be made
        mock_post.side_effect = None
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"data": {"session_token": "test", "session_id": "sess_1"}}
        mock_post.return_value = mock_response
        
        cevap2 = self.istemci.post('/api/token-al')
        self.assertEqual(cevap2.status_code, 200)

if __name__ == '__main__':
    unittest.main()

