"""Provera celog toka bez pravih platformi.

Odgovori Google-a, Mete, Microsoft-a i TikTok-a su lazni (tests/fakes.py),
pa test ne izlazi na mrezu i ne trazi nikakve kljuceve. Pokretanje iz
korena projekta:

    python tests/test_flow.py
"""
import json
import os
import re
import sys
import tempfile
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.update(
    SQLITE_PATH=os.path.join(tempfile.mkdtemp(), "test.db"),
    SECRET_KEY="test-secret",
    DATABASE_URL="",  # prazno namerno: test nikad ne sme da dira pravu bazu iz .env fajla
    GOOGLE_CLIENT_ID="test-client-id",
    GOOGLE_CLIENT_SECRET="test-client-secret",
    GOOGLE_ADS_MCC_ID="123-456-7890",
    GOOGLE_ADS_ACCOUNT_IDS="",
    ALLOWED_DOMAIN="victorious.rs",
    ALLOWED_EMAILS="",
    MICROSOFT_ADS_CLIENT_ID="ms-client",
    MICROSOFT_ADS_CLIENT_SECRET="ms-secret",
    MICROSOFT_ADS_DEVELOPER_TOKEN="ms-dev-token",
    TIKTOK_APP_ID="tt-app",
    TIKTOK_APP_SECRET="tt-secret",
    META_APP_ID="meta-app",
    META_APP_SECRET="meta-secret",
    META_LOGIN_CONFIG_ID="",
)

import app as portal  # noqa: E402
import connections  # noqa: E402
import db  # noqa: E402
import fmt  # noqa: E402
import google_auth  # noqa: E402
import platforms  # noqa: E402
from platforms import google_ads, meta_ads, meta_organic  # noqa: E402
from tests.fakes import YESTERDAY, install  # noqa: E402

assert not db.IS_PG, "test radi samo nad privremenom SQLite bazom"
fake = install()
flask_app = portal.app


def csrf(html):
    return re.search(r'name="_csrf" value="([^"]+)"', html).group(1)


def text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def hero(html):
    return text(re.search(r'<h1 class="hero">(.*?)</h1>', html, re.S).group(1)).strip()


def google(client, token, purpose, code):
    resp = client.post("/auth/google/start", data={"_csrf": token, "svrha": purpose})
    assert resp.status_code == 302 and resp.location.startswith(google_auth.AUTH_URL), resp.location
    params = parse_qs(urlparse(resp.location).query)
    back = client.get(f"/auth/google/callback?state={params['state'][0]}&code={code}", follow_redirects=True)
    return params, back


team = flask_app.test_client()

# 1. Bez prijave nema pristupa.
for path in ("/tim", "/tim/izvor", "/tim/klijent/1", "/auth/microsoft/callback", "/auth/tiktok/callback"):
    resp = team.get(path)
    assert resp.status_code == 302 and "/tim/prijava" in resp.location, path
login_page = team.get("/tim/prijava")
assert "Prijavi se preko Google-a" in login_page.text
token = csrf(login_page.text)

# 2. Prijava: samo osnovni podaci, samo domen agencije, state vazi jednom.
resp = team.get("/auth/google/callback?state=pogresan&code=code-login", follow_redirects=True)
assert "Prijava je istekla" in resp.text
params, resp = google(team, token, "login", "code-outsider")
assert params["scope"] == ["openid email profile"] and params["hd"] == ["victorious.rs"] and "access_type" not in params
assert "samo nalozima sa domena victorious.rs" in resp.text and team.get("/tim").status_code == 302
params, resp = google(team, csrf(resp.text), "login", "code-login")
assert "Još nema klijenata na portalu" in resp.text and "Nenad Test" in resp.text
resp = team.get(f"/auth/google/callback?state={params['state'][0]}&code=code-login", follow_redirects=True)
assert "Prijava je istekla" in resp.text
token = csrf(team.get("/tim/izvor").text)

# 3. Google: bez dozvola se odbija; sa dozvolama se token cuva sifrovan.
assert text(team.get("/tim/izvor").text).count("Nije povezano") == 4
_, resp = google(team, token, "connect", "code-connect-nothing")
assert "Niste dozvolili pristup ni Google Ads-u ni Analytics-u" in resp.text
params, resp = google(team, token, "connect", "code-connect")
assert params["access_type"] == ["offline"] and google_auth.SCOPE_ADS in params["scope"][0]
assert "Google nalog je povezan" in resp.text
assert "Aktivnih naloga ispod MCC-a 123-456-7890: 3." in text(resp.text), text(resp.text)
with flask_app.app_context():
    stored = db.query("SELECT * FROM portal_connections WHERE platform = 'google'", one=True)
    assert "rt-secret" not in json.dumps(stored) and stored["label"] == "nenad@victorious.rs"

# 4. Meta: neispravan token se odbija, ispravan se cuva sifrovan i vise se ne prikazuje.
resp = team.post("/tim/izvor/meta/token", data={"_csrf": token, "token": "los-token"}, follow_redirects=True)
assert "Meta nije prihvatila token: Invalid OAuth access token." in resp.text
resp = team.post("/tim/izvor/meta/token", data={"_csrf": token, "token": " meta-token "}, follow_redirects=True)
plain = text(resp.text)
assert "Meta Ads je povezan (Portal sistemski korisnik)." in plain and "Veza ne ističe." in plain
assert "Oglasnih naloga koje portal vidi: 1." in plain and "meta-token" not in resp.text
with flask_app.app_context():
    assert "meta-token" not in json.dumps(db.query("SELECT * FROM portal_connections"))

# 4b. Kolega povezuje svoj Facebook nalog: portal spaja naloge obojice i za svaki koristi pravi token.
resp = team.post("/auth/meta/start", data={"_csrf": token})
fb = parse_qs(urlparse(resp.location).query)
assert resp.location.startswith(f"https://www.facebook.com/{meta_ads.API_VERSION}/dialog/oauth?")
assert fb["client_id"] == ["meta-app"] and fb["scope"][0].split(",")[:2] == ["ads_read", "pages_show_list"]
assert fb["redirect_uri"] == ["http://localhost/auth/meta/callback"]
resp = team.get("/auth/meta/callback?state=pogresan&code=fb-code", follow_redirects=True)
assert "Povezivanje je isteklo" in resp.text
resp = team.post("/auth/meta/start", data={"_csrf": token})
state = parse_qs(urlparse(resp.location).query)["state"][0]
resp = team.get(f"/auth/meta/callback?state={state}&code=fb-code", follow_redirects=True)
plain = text(resp.text)
assert "Meta Ads je povezan (Kolega Petar)." in plain, plain[:500]
assert fake.meta_extended == ["short-2"], "kratkotrajni token se odmah produzava"
assert "Veza ističe za 59 dana." in plain or "Veza ističe za 60 dana." in plain, plain
assert "Veza ne ističe." in plain and "Oglasnih naloga koje portal vidi: 2." in plain, plain
with flask_app.app_context():
    assert "meta-token-2" not in json.dumps(db.query("SELECT * FROM portal_connections"))
    names = [a["name"] for a in platforms.accounts(meta_ads)]
    assert names == ["Druga firma Meta", "Hrast Meta"], names
    meta_ads.forget()  # kao posle restarta: portal sam nalazi ciji token vidi nalog
    fake.meta_used.clear()
    payload, error = platforms.daily("meta", "9002", *platforms.default_range())
    assert error is None and payload["rows"][0][2] == "Saobraćaj: prodavnica", error
    assert all(tok == "meta-token-2" for tok, p in fake.meta_used if "9002/" in p), fake.meta_used
    payload, error = platforms.daily("meta", "9001", *platforms.default_range())
    assert error is None and all(tok == "meta-token" for tok, p in fake.meta_used if "9001/" in p)
    platforms.clear("meta", "9002"); platforms.clear("meta", "9001")
    # Rok isticе: upozorenje za tim; istekao token se ne koristi.
    secret = connections.secret("meta:777")
    connections.update_secret("meta:777", {**secret, "expires_at": int(time.time()) + 3 * 86400 + 60})
plain = text(team.get("/tim").text)
assert "Meta veza (Kolega Petar) ističe za 3 dana." in plain, plain[:400]
with flask_app.app_context():
    connections.update_secret("meta:777", {**secret, "expires_at": int(time.time()) - 10})
    meta_ads.forget()
    assert [t["label"] for t in meta_ads.prepare()] == ["Portal sistemski korisnik"]
    payload, error = platforms.daily("meta", "9002", *platforms.default_range())
    assert payload is None and "Unsupported get request." in error, error
    platforms.clear("meta", "9002")
assert "Meta veza (Kolega Petar) je istekla." in text(team.get("/tim/izvor").text)
# Prekid veze jedne osobe ne dira ostale.
resp = team.post("/tim/izvor/meta:777/prekini", data={"_csrf": token}, follow_redirects=True)
plain = text(resp.text)
assert "Kolega Petar" not in plain and "Portal sistemski korisnik" in plain and "Oglasnih naloga koje portal vidi: 1." in plain
assert team.post("/tim/izvor/meta:999/prekini", data={"_csrf": token}).status_code == 404

# 5. Microsoft: OAuth, lista aktivnih naloga, cuvanje novog refresh tokena.
resp = team.post("/auth/microsoft/start", data={"_csrf": token})
ms = parse_qs(urlparse(resp.location).query)
assert resp.location.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize")
assert ms["scope"] == ["https://ads.microsoft.com/msads.manage offline_access"]
assert ms["redirect_uri"] == ["http://localhost/auth/microsoft/callback"]
resp = team.get("/auth/microsoft/callback?state=pogresan&code=ms-code", follow_redirects=True)
assert "Povezivanje je isteklo" in resp.text
resp = team.post("/auth/microsoft/start", data={"_csrf": token})
state = parse_qs(urlparse(resp.location).query)["state"][0]
resp = team.get(f"/auth/microsoft/callback?state={state}&code=ms-code", follow_redirects=True)
plain = text(resp.text)
assert "Microsoft Ads je povezan." in plain and "Naloga koje portal vidi: 1." in plain, plain
with flask_app.app_context():
    assert connections.secret("microsoft")["refresh_token"] == fake.ms_refresh_tokens[-1] != "ms-rt-1"

# 6. TikTok: OAuth povratak, pa rucni unos koda.
resp = team.post("/auth/tiktok/start", data={"_csrf": token})
tt = parse_qs(urlparse(resp.location).query)
assert resp.location.startswith("https://business-api.tiktok.com/portal/auth?") and tt["app_id"] == ["tt-app"]
assert tt["redirect_uri"] == ["http://localhost/auth/tiktok/callback"]
resp = team.get(f"/auth/tiktok/callback?state={tt['state'][0]}&auth_code=tt-code", follow_redirects=True)
plain = text(resp.text)
assert "TikTok Ads je povezan." in plain and "Oglašivača koje portal vidi: 1." in plain, plain
team.post("/tim/izvor/tiktok/prekini", data={"_csrf": token})
resp = team.post("/tim/izvor/tiktok/kod", data={"_csrf": token, "auth_code": "los"}, follow_redirects=True)
assert "TikTok Ads nije povezan: Auth code is invalid or expired." in resp.text
resp = team.post("/tim/izvor/tiktok/kod", data={"_csrf": token, "auth_code": "tt-code-rucno"}, follow_redirects=True)
assert "TikTok Ads je povezan." in resp.text
assert team.post("/tim/izvor/nepoznato/prekini", data={"_csrf": token}).status_code == 404
assert "Nije povezano" not in text(team.get("/tim/izvor").text)

# 7. Novi klijent i izbor naloga po kanalu.
assert "Upišite ime klijenta" in team.post("/tim/dodaj", data={"_csrf": token, "name": " "}, follow_redirects=True).text
resp = team.post("/tim/dodaj", data={"_csrf": token, "name": "Nameštaj Hrast"}, follow_redirects=True)
for expected in ("Nameštaj Hrast (111-204-5501, EUR)", "Klinika Vita (222-318-7742, RSD)", "Hrast Meta (9001, EUR)",
                 "Hrast Bing (8001, EUR)", "Hrast TikTok (7001, USD)", "Hrast / hrast.rs (555000111)"):
    assert expected in resp.text, expected
assert "Stari nalog" not in resp.text  # neaktivan Microsoft nalog se ne nudi
SETTINGS = {
    "_csrf": token, "name": "Nameštaj Hrast d.o.o.", "conv_kind": "upit",
    "show_combined": "1",
    "lay_google_ads": "1", "m_google_ads": ["spend", "impressions", "clicks", "cpc", "conversions", "cpa"],
    "s_google_ads": ["chart", "campaigns", "terms"], "chart_google_ads": "clicks",
    "src_google_ads": "1112045501", "vis_google_ads": "1", "src_meta": "9001", "vis_meta": "1",
    "src_microsoft": "777:8001", "vis_microsoft": "1", "src_tiktok": "7001", "vis_tiktok": "1",
    "src_ga4": "555000111", "vis_ga4": "1",
}
resp = team.post("/tim/klijent/1", data={**SETTINGS, "src_meta": "666"}, follow_redirects=True)
assert "Meta Ads: izabrani nalog nije među nalozima koje portal vidi" in resp.text
assert "Ostale izmene su sačuvane" in resp.text
resp = team.post("/tim/klijent/1", data=SETTINGS, follow_redirects=True)
assert "Izmene su sačuvane." in resp.text and resp.text.count("Izabrano") == 5
with flask_app.app_context():
    saved = {s["platform"]: (s["account_id"], s["currency"]) for s in db.query("SELECT * FROM portal_sources")}
assert saved == {"google_ads": ("1112045501", "EUR"), "meta": ("9001", "EUR"), "microsoft": ("777:8001", "EUR"),
                 "tiktok": ("7001", "USD"), "ga4": ("555000111", "")}, saved

# 7b. Ogranicenje Google Ads-a na navedene naloge: MCC se ne pretrazuje, drugi nalozi se ne citaju.
google_ads.ONLY_IDS = ["1112045501"]
fake.gaql.clear()
page = team.get("/tim/klijent/1").text
assert "Nameštaj Hrast (111-204-5501, EUR)" in page and "Klinika Vita" not in page
with flask_app.app_context():
    payload, error = platforms.daily("google_ads", "2223187742", *platforms.default_range())
    assert payload is None and "nije među nalozima koje portal sme da čita" in error
    platforms.clear("google_ads", "2223187742")
assert fake.gaql and all("FROM customer LIMIT 1" in q for q in fake.gaql), fake.gaql
assert "Čita samo naloge koje ste naveli: 111-204-5501." in text(team.get("/tim/izvor").text)
google_ads.ONLY_IDS = []

# 8. Lista klijenata: zbir preko kanala, valute se ne sabiraju.
before = len(fake.calls)
plain = text(team.get("/tim?dana=30").text)
assert "Na portalu: 1 klijent. Ukupno potrošeno 1.110 € i 90 $ u poslednjih 30 dana" in plain, plain
assert "Google Ads, Meta Ads, Microsoft Ads (Bing), TikTok Ads, Analytics" in plain
assert "3.300" in plain and "135" in plain
assert len(fake.tiktok_ranges) == 3 and (fake.tiktok_ranges[0][1], fake.tiktok_ranges[-1][0]) == (YESTERDAY, YESTERDAY - timedelta(days=89))
loaded = len(fake.calls)
assert loaded > before
assert "1.110 €" not in text(team.get("/tim?dana=7").text) and len(fake.calls) == loaded, "drugi period ne zove platforme"

# 9. Lozinka i ulaz klijenta.
team.post("/tim/klijent/1/lozinka", data={"_csrf": token})
settings = team.get("/tim/klijent/1").text
password = re.search(r'class="secret big">([^<]+)<', settings).group(1)
path = urlparse(re.search(r'id="link">([^<]+)<', settings).group(1)).path
visitor = flask_app.test_client()
gate = visitor.get(path)
assert "Izveštaj za Nameštaj Hrast d.o.o." in gate.text
assert "Lozinka nije tačna" in visitor.post(path, data={"_csrf": csrf(gate.text), "lozinka": "pogresna"}).text
assert visitor.post(path, data={"_csrf": csrf(gate.text), "lozinka": password}).status_code == 302

# 9b. Logo klijenta: u zaglavlju izvestaja i strane za lozinku, i kao ikonica taba.
import io  # noqa: E402
import struct  # noqa: E402
import zlib  # noqa: E402


def tiny_png(color):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"\x00" + bytes(color) * 2
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def upload(data, name="logo.png"):
    return team.post("/tim/klijent/1/logo", data={"_csrf": token, "logo": (io.BytesIO(data), name)},
                     content_type="multipart/form-data", follow_redirects=True)


assert "Dodaj logo" in team.get("/tim/klijent/1").text and 'rel="icon"' not in visitor.get(path).text
resp = upload(b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "logo.svg")
assert "Logo mora biti PNG, JPG ili WebP slika." in resp.text
assert "Logo je prevelik" in upload(b"\x89PNG\r\n\x1a\n" + b"0" * (1024 * 1024)).text
assert "Fajl je prevelik" in upload(b"0" * (3 * 1024 * 1024)).text
first = tiny_png((151, 93, 165))
resp = upload(first)
assert "Logo za Nameštaj Hrast d.o.o. je sačuvan." in resp.text and "Zameni logo" in resp.text
logo_src = re.search(r'class="client-logo" src="([^"]+)"', resp.text).group(1)
page = visitor.get(path).text
assert f'<link rel="icon" href="{logo_src}">' in page and 'class="cobrand-amp"' in page and logo_src in page
gate = flask_app.test_client().get(path).text  # i pre unosa lozinke
assert "Lozinka" in gate and logo_src in gate
served = flask_app.test_client().get(logo_src)
assert served.status_code == 200 and served.data == first and served.mimetype == "image/png"
assert served.headers["X-Content-Type-Options"] == "nosniff" and served.headers["Content-Security-Policy"] == "default-src 'none'"
upload(tiny_png((0, 0, 0)))
assert logo_src not in team.get("/tim/klijent/1").text, "nov logo dobija novu adresu, pa ga browser ne kesira"
assert flask_app.test_client().get("/k/nepostoji/logo").status_code == 404
resp = team.post("/tim/klijent/1/logo/ukloni", data={"_csrf": token}, follow_redirects=True)
assert "Logo je uklonjen." in resp.text and 'class="client-logo"' not in resp.text
assert 'rel="icon"' not in visitor.get(path).text
upload(first)

# 10. Zbirni izvestaj je prvi; kanali u razlicitim valutama nemaju zbirnu cenu.
page = visitor.get(path).text
tabs = re.findall(r'<a href="[^"]*kanal=([a-z0-9_]+)[^"]*"[^>]*>([^<]+)</a>', page.split('class="tabs"')[1].split("</nav>")[0])
assert [name for _, name in tabs] == ["Ukupno", "Google Ads", "Meta Ads", "Microsoft Ads (Bing)", "TikTok Ads", "Sajt (Analytics)"], tabs
assert hero(page) == "U poslednjih 30 dana oglasi na svim kanalima su vam doneli 135 upita.", hero(page)
plain = text(page)
for expected in ("1.110 € i 90 $", "3.300", "Rezultati po kanalima", "Microsoft Ads (Bing)", "Klikovi po danima"):
    assert expected in plain, expected
assert "Napomene za tim" not in plain and "Šta su ljudi tražili" not in plain

# 11. Poseban izvestaj za svaki kanal.
EXPECTED = {
    "google_ads": "U poslednjih 30 dana Google Ads oglasi su vam doneli 45 upita, po prosečnoj ceni od 20,00 € po upitu.",
    "meta": "U poslednjih 30 dana Meta Ads oglasi su vam doneli 60 upita, po prosečnoj ceni od 2,50 € po upitu.",
    "microsoft": "U poslednjih 30 dana Microsoft Ads (Bing) oglasi su vam doneli 30 upita, po prosečnoj ceni od 2,00 € po upitu.",
    "tiktok": "U poslednjih 30 dana TikTok Ads oglasi su vam doneli 0 upita.",
    "ga4": "U poslednjih 30 dana vaš sajt je imao 1.230 poseta.",
}
for key, sentence in EXPECTED.items():
    page = visitor.get(f"{path}?kanal={key}").text
    assert hero(page) == sentence, (key, hero(page))
    assert len(re.findall(r'class="bar( peak)?"', page)) == 30, key
plain = text(visitor.get(f"{path}?kanal=google_ads").text)
for expected in ("900 €", "120.000", "1.500", "0,60 €", "Pretraga: kuhinje", "Performance Max", "nema upita",
                 "kuhinje po meri", "42 klika", "Šta su ljudi tražili"):
    assert expected in plain, expected
assert "Bing: kuhinje" in text(visitor.get(f"{path}?kanal=microsoft").text)
plain = text(visitor.get(f"{path}?kanal=tiktok").text)
assert "90 $" in plain and "TikTok: video" in plain and "Šta su ljudi tražili" not in plain
plain = text(visitor.get(f"{path}?kanal=ga4").text)
for expected in ("900", "2 min 15 s", "Plaćena pretraga", "500 poseta", "Nepoznat kanal", "1 poseta", "/kontakt", "45 pregleda", "Posete po danima"):
    assert expected in plain, expected
week = visitor.get(f"{path}?kanal=google_ads&od={YESTERDAY - timedelta(days=6)}&do={YESTERDAY}").text
assert "doneli 10,5 upita, po prosečnoj ceni od 20,00 € po upitu" in hero(week)
quarter = visitor.get(f"{path}?kanal=meta&od={YESTERDAY - timedelta(days=89)}&do={YESTERDAY}").text
assert "doneli 180 upita" in hero(quarter) and len(re.findall(r'class="bar( peak)?"', quarter)) == 13

# 12. Podesavanja menjaju izvestaj: kupovine umesto upita, sakriven kanal, bez zbirnog.
team.post("/tim/klijent/1", data={**SETTINGS, "conv_kind": "kupovina"})
assert hero(visitor.get(f"{path}?kanal=meta").text) == (
    "U poslednjih 30 dana Meta Ads oglasi su vam doneli 30 kupovina, po prosečnoj ceni od 5,00 € po kupovini."
)
hidden = {k: v for k, v in SETTINGS.items() if k != "vis_tiktok"}
team.post("/tim/klijent/1", data=hidden)
page = visitor.get(path + "?kanal=tiktok").text  # sakriven kanal se ne moze otvoriti ni direktno
assert "TikTok" not in page
assert hero(page) == "U poslednjih 30 dana oglasi na svim kanalima su vam doneli 135 upita, po prosečnoj ceni od 8,22 € po upitu."
assert "TikTok Ads" in text(team.get("/tim").text), "tim i dalje vidi sakriven kanal"
team.post("/tim/klijent/1", data={k: v for k, v in hidden.items() if k != "show_combined"})
assert hero(visitor.get(path).text).startswith("U poslednjih 30 dana Google Ads oglasi")
team.post("/tim/klijent/1", data={**SETTINGS, "lay_meta": "1", "m_meta": ["impressions", "clicks"], "s_meta": ["campaigns"]})
plain = text(visitor.get(f"{path}?kanal=meta").text)
assert "doneli 600 klikova ka sajtu" in plain and "€" not in plain and "Upiti" not in plain
assert "Prikazi" in plain and "Klikovi po danima" not in plain

# 12c. Izbor brojki po kanalu: svaki kanal ima svoj izbor, nezavisno od ostalih.
team.post("/tim/klijent/1", data={
    **SETTINGS,
    "lay_meta": "1", "m_meta": ["ctr", "cpm", "conv_rate", "nepoznato"], "s_meta": ["chart"], "chart_meta": "spend",
    "lay_ga4": "1", "m_ga4": ["users", "new_users", "engagement", "page_views", "key_events"], "s_ga4": ["chart", "pages"],
    "chart_ga4": "key_events",
    "lay_ukupno": "1", "m_ukupno": ["impressions"], "s_ukupno": ["chart"], "chart_ukupno": "spend",
})
settings_page = team.get("/tim/klijent/1").text
assert "Šta klijent vidi u izveštaju Meta Ads" in settings_page and "4 od 24 uključeno" in text(settings_page)
meta = visitor.get(f"{path}?kanal=meta").text
plain = text(meta)
assert hero(meta) == "U poslednjih 30 dana Meta Ads oglasi su bili aktivni.", hero(meta)
for expected in ("Prikaza koji su doneli klik (CTR)", "Cena za 1.000 prikaza", "Klikova koji su doneli upit", "Potrošnja po danima"):
    assert expected in plain, expected
assert "Rezultati po kampanjama" not in plain and "Potrošeno na oglase" not in plain
google_page = text(visitor.get(f"{path}?kanal=google_ads").text)
assert "Potrošeno na oglase" in google_page and "Rezultati po kampanjama" in google_page, "Google ima svoj izbor"
site = visitor.get(f"{path}?kanal=ga4").text
plain = text(site)
assert hero(site) == "U poslednjih 30 dana vaš sajt je posetilo 900 posetilaca.", hero(site)
for expected in ("600", "Novih posetilaca", "62,5 %", "2.100", "Konverzije po danima", "Najgledanije strane"):
    assert expected in plain, expected
assert "Odakle dolaze posete" not in plain and "Prosečno trajanje" not in plain
combined = visitor.get(path).text
plain = text(combined)
assert hero(combined).startswith("U poslednjih 30 dana oglasi na svim kanalima su prikazani"), hero(combined)
assert "Potrošnja po danima" not in plain and "Rezultati po kanalima" not in plain
assert "različitim valutama" in text(team.get("/tim/klijent/1/pregled").text)
# Kanal cija polja nisu poslata zadrzava svoj izbor.
team.post("/tim/klijent/1", data=SETTINGS)
assert "Konverzije po danima" in text(visitor.get(f"{path}?kanal=ga4").text)
# 12d. Vrednost konverzija, ROAS i (samo Meta) doseg i frekvencija.
fake.reach_calls.clear()
team.post("/tim/klijent/1", data={
    **SETTINGS, "conv_kind": "kupovina",
    "lay_meta": "1", "m_meta": ["spend", "reach", "frequency", "conversions", "value", "roas"], "s_meta": ["campaigns"],
    "m_google_ads": ["value", "roas"], "s_google_ads": ["chart"], "chart_google_ads": "value",
    "lay_ukupno": "1", "m_ukupno": ["value", "roas"], "s_ukupno": ["campaigns"],
})
meta = visitor.get(f"{path}?kanal=meta").text
plain = text(meta)
assert hero(meta) == "U poslednjih 30 dana Meta Ads oglasi su vam doneli 30 kupovina u vrednosti od 1.200 €.", hero(meta)
for expected in ("150 €", "18.000", "Ljudi je videlo oglas (doseg)", "3,33", "8,00x", "Doseg", "Frekvencija", "ROAS"):
    assert expected in plain, expected
assert sorted(fake.reach_calls) == [("account", str(YESTERDAY - timedelta(days=29))), ("campaign", str(YESTERDAY - timedelta(days=29)))]
visitor.get(f"{path}?kanal=meta")
assert len(fake.reach_calls) == 2, "doseg se pamti"
google_page = visitor.get(f"{path}?kanal=google_ads").text
plain = text(google_page)
assert "3.600 €" in plain and "4,00x" in plain and "Vrednost kupovina po danima" in plain, plain[:600]
assert "Doseg" not in plain and "Doseg" not in text(team.get("/tim/klijent/1").text).split("Šta klijent vidi u izveštaju Google Ads")[1].split("Meta Ads")[0]
plain = text(visitor.get(path).text)
assert "4.950 € i 180 $" in plain and "ROAS" in plain  # u tabeli po kanalima, ali bez zbirnog ROAS-a
assert "Prihod po 1 uloženom" not in plain, "zbirni ROAS se ne racuna preko razlicitih valuta"
assert "4,00x" in plain and "2,00x" in plain  # ROAS po kanalu u tabeli: Google i TikTok
visitor.get(f"{path}?kanal=google_ads")
assert len(fake.reach_calls) == 2, "doseg se ne cita kad nije ukljucen"

# 12e. Meta kolone kao u Ads Manager-u: rezultati prema cilju, budzet, svi klikovi, odredisna strana, ocene.
team.post("/tim/klijent/1", data={
    **SETTINGS,
    "lay_meta": "1",
    "m_meta": ["spend", "budget", "results", "cost_per_result", "clicks", "clicks_all", "cpc_all",
               "landing_views", "cost_per_landing"],
    "s_meta": ["campaigns", "rankings"],
})
settings_text = text(team.get("/tim/klijent/1").text)
assert "Rezultati (prema cilju kampanje)" in settings_text and "Ocene oglasa" in settings_text
assert settings_text.count("Budžet kampanje") == 1, "budzet se nudi samo za Metu"
meta = visitor.get(f"{path}?kanal=meta").text
assert hero(meta) == "U poslednjih 30 dana Meta Ads oglasi su vam doneli 60 upita, po prosečnoj ceni od 2,50 € po rezultatu.", hero(meta)
plain = text(meta)
for expected in ("60 upita", "Cena po rezultatu", "2,50 €", "Klikova na link", "1.050", "0,14 €", "480",
                 "Pregleda odredišne strane", "0,31 €", "10 € dnevno", "Budžet", "Kako Meta ocenjuje oglase",
                 "Video: nova kuhinja", "Iznad proseka", "Prosečno", "Ispod proseka (donjih 35 %)"):
    assert expected in plain, expected
assert "Slika: malo prikaza" not in plain, "oglasi bez ocene se ne prikazuju"
calls = fake.ranking_calls
visitor.get(f"{path}?kanal=meta")
assert fake.ranking_calls == calls == 1, "ocene se pamte"

# 12f. Analytics: gde kupci klikcu i gde odustaju.
team.post("/tim/klijent/1", data={**SETTINGS, "lay_ga4": "1", "m_ga4": ["sessions"], "s_ga4": ["chart"]})
visitor.get(f"{path}?kanal=ga4")
assert fake.ga4_behavior_calls == 0, "ponasanje se ne cita dok nije ukljuceno"
team.post("/tim/klijent/1", data={
    **SETTINGS, "lay_ga4": "1", "m_ga4": ["sessions"], "s_ga4": ["funnel", "products", "events", "landing"],
})
site = visitor.get(f"{path}?kanal=ga4").text
plain = text(site)
for expected in (
    "Put do kupovine",
    "Najviše ljudi odustaje između koraka „Pogledali proizvod“ i „Dodali u korpu“: od 400 njih 280 (70 %) ne nastavi dalje.",
    "Posetili sajt", "Započeli kupovinu", "Uneli podatke za plaćanje", "Kupili", "nastavilo 30 %, odustalo 70 %",
    "Kuhinja Hrast", "30 %", "Sto Bor", "2 %",
    "Šta posetioci rade na sajtu", "Klik na link ka drugom sajtu", "340", "200 posetilaca", "Poslali formu", "poziv_telefon",
    "Najviše ljudi odmah ode sa strane /akcija: 72 % poseta završi se bez ikakve aktivnosti.", "/blog/stari-tekst",
):
    assert expected in plain, expected
assert "Uneli podatke za dostavu" not in plain, "korak koji sajt ne salje se izostavlja"
assert "(not set)" not in plain and "page_view" not in plain
assert site.count('class="funnel-bar"') == 6
fake.ga4_no_ecommerce = True
with flask_app.app_context():
    platforms.clear("ga4", "555000111")
plain = text(visitor.get(f"{path}?kanal=ga4").text)
assert "Put do kupovine" not in plain and "Šta posetioci rade na sajtu" in plain
preview = text(team.get("/tim/klijent/1/pregled?kanal=ga4").text)
assert "Put do kupovine nije prikazan: Analytics ne beleži e-commerce događaje" in preview
fake.ga4_no_ecommerce = False
with flask_app.app_context():
    platforms.clear("ga4", "555000111")

# 12g. Podaci iz dokumenta "Potrebe za informacijama po klijentima".
# GA4: transakcije, Google Ads kampanje iz Analytics-a, izvori, uredjaji.
team.post("/tim/klijent/1", data={
    **SETTINGS, "lay_ga4": "1", "m_ga4": ["sessions"],
    "s_ga4": ["transactions", "ads_campaigns", "sources", "devices"],
})
plain = text(visitor.get(f"{path}?kanal=ga4").text)
for expected in (
    "Transakcije", "2 transakcije, ukupno 33.480 RSD.", "36137", "29.10.2026.", "15.480 RSD", "35810",
    "Google Ads kampanje (iz Analytics-a)", "Performance Max - Zlatni nakit", "17.974 RSD", "11.983 RSD", "2.067",
    "4.400 RSD", "0,24x", "Ukupno 2,5 28.744 RSD 11.498 RSD 3.402",
    "Posete po izvoru", "google / cpc", "8.147", "95,28 %", "6.900", "3.019", "0 min 21 s", "google / organic",
    "Posetioci po uređaju", "Mobilni telefon", "5.895", "92,50 %", "6,12 %", "0,85 %", "Računar", "Tablet",
):
    assert expected in plain, expected
assert "(not set)" not in plain and "(organic)" not in plain
fake.ga4_old_ads_dimension = True
with flask_app.app_context():
    platforms.clear("ga4", "555000111")
assert "Stara kampanja" in text(visitor.get(f"{path}?kanal=ga4").text), "starija GA4 svojstva"
fake.ga4_old_ads_dimension = False

# Google Ads: interakcije i interaction rate.
team.post("/tim/klijent/1", data={
    **SETTINGS, "m_google_ads": ["impressions", "clicks", "interactions", "interaction_rate"], "s_google_ads": ["campaigns"],
})
plain = text(visitor.get(f"{path}?kanal=google_ads").text)
assert "3.300" in plain and "Interaction rate (interakcije / prikazi)" in plain and "2,75 %" in plain, plain[:800]
# Avg. cost (trosak / interakcije) i interaction rate samo Search kampanja, kao u Google Ads koloni.
team.post("/tim/klijent/1", data={
    **SETTINGS, "m_google_ads": ["clicks", "avg_cost", "search_interaction_rate"], "s_google_ads": ["campaigns"],
})
plain = text(visitor.get(f"{path}?kanal=google_ads").text)
assert "Prosečna cena interakcije (Avg. cost)" in plain and "0,27 €" in plain, plain[:1500]
assert "Interaction rate samo Search kampanja" in plain and "4,00 %" in plain, plain[:1500]
row_pmax = plain.split("Performance Max")[1][:80]
assert "–" in row_pmax, row_pmax  # PMax nije Search kampanja
assert "Interaction rate" not in text(team.get("/tim/klijent/1").text).split("Šta klijent vidi u izveštaju Meta Ads")[1].split("Ocene oglasa")[0]

# Facebook stranica i Instagram (organski, Meta Business Suite > Insights).
settings_page = text(team.get("/tim/klijent/1").text)
assert "Hrast stranica (301)" in settings_page and "@hrast.namestaj (Hrast stranica) (401)" in settings_page, settings_page[:3000]
resp = team.post("/tim/klijent/1", data={
    **SETTINGS, "src_fb_page": "301", "vis_fb_page": "1", "src_instagram": "401", "vis_instagram": "1",
}, follow_redirects=True)
assert "Izmene su sačuvane." in resp.text
with flask_app.app_context():
    assert "page-token" not in json.dumps(db.query("SELECT * FROM portal_cache")), "tokeni stranica se ne pamte u bazi"
page = visitor.get(path).text
tabs = re.findall(r'kanal=([a-z0-9_]+)[^"]*"[^>]*>([^<]+)</a>', page.split('class="tabs"')[1].split("</nav>")[0])
assert ("fb_page", "Facebook stranica") in tabs and ("instagram", "Instagram") in tabs, tabs
fb_page = visitor.get(f"{path}?kanal=fb_page").text
assert hero(fb_page) == "U poslednjih 30 dana sadržaj na vašoj Facebook stranici pregledan je 15.000 puta.", hero(fb_page)
plain = text(fb_page)
for expected in ("8.200", "Ljudi je videlo sadržaj", "540", "Interakcija sa sadržajem", "1.234", "Ukupno pratilaca danas",
                 "210", "Poseta profilu", "Novih pratilaca u periodu",
                 "Pregledi po danima"):
    assert expected in plain, expected
ig_page = visitor.get(f"{path}?kanal=instagram").text
assert hero(ig_page) == "U poslednjih 30 dana sadržaj na vašem Instagram profilu pregledan je 9.000 puta.", hero(ig_page)
plain = text(ig_page)
for expected in ("3.000", "Naloga je videlo sadržaj (doseg)", "360", "30", "Klikova na link u profilu", "850"):
    assert expected in plain, expected
assert "Facebook stranica, Instagram" in text(team.get("/tim").text)
# Jedna Meta metrika koja ne radi ne obara ceo izvestaj; tim vidi koja.
fake.fb_engagement_broken = True
with flask_app.app_context():
    platforms.clear("fb_page", "301")
preview = text(team.get("/tim/klijent/1/pregled?kanal=fb_page").text)
assert "15.000 puta" in preview and "„Interakcije sa sadržajem“ nije učitano" in preview, preview[:800]
assert "nije učitano" not in text(visitor.get(f"{path}?kanal=fb_page").text)
fake.fb_engagement_broken = False
# Duzi period na Instagramu: delovi od najvise 30 dana.
fake.organic_calls.clear()
visitor.get(f"{path}?kanal=instagram&od={YESTERDAY - timedelta(days=59)}&do={YESTERDAY}")
assert "600" in text(visitor.get(f"{path}?kanal=instagram").text), "posete profila (20 dnevno)"

# 12g2. Stranica iz business portfolija klijenta: nema je na spisku, dodaje se linkom; Instagram ide sam.
assert "Nalepite link ili ID stranice" in team.get("/tim/klijent/1").text
resp = team.post("/tim/klijent/1", data={**SETTINGS, "src_fb_page": "", "src_instagram": "",
                                          "manual_fb_page": "https://www.facebook.com/999"}, follow_redirects=True)
assert "Stranica postoji, ali nijedan povezani Facebook nalog nema pristup njenim uvidima" in resp.text
resp = team.post("/tim/klijent/1", data={**SETTINGS, "src_fb_page": "", "src_instagram": "", "vis_fb_page": "1",
                                          "vis_instagram": "1", "manual_fb_page": "https://www.facebook.com/blokvrata/?ref=bookmarks"},
                 follow_redirects=True)
assert "Izmene su sačuvane." in resp.text, text(resp.text)[:500]
with flask_app.app_context():
    saved = {r["platform"]: r["account_id"] for r in db.query("SELECT * FROM portal_sources WHERE client_id = 1")}
assert saved.get("fb_page") == "303" and saved.get("instagram") == "403", saved
settings_page = text(team.get("/tim/klijent/1").text)
assert "Block Lock Doors (303, dodato ručno)" in settings_page and "@bld.vrata (Block Lock Doors) (403, dodato ručno)" in settings_page
assert "15.000 puta" in hero(visitor.get(f"{path}?kanal=fb_page").text)
# Posle restarta portal ne zna token stranice iz memorije: trazi ga sam, a Instagram cita licnim tokenom.
meta_organic.forget()
with flask_app.app_context():
    platforms.clear("fb_page", "303"); platforms.clear("instagram", "403")
assert "15.000 puta" in hero(visitor.get(f"{path}?kanal=fb_page").text)
meta_organic.forget()
assert "9.000 puta" in hero(visitor.get(f"{path}?kanal=instagram").text)
assert fake.ig_user_token_calls > 0
# Ponovno cuvanje bez izmena zadrzava rucno dodatu stranicu.
team.post("/tim/klijent/1", data={**SETTINGS, "src_fb_page": "303", "vis_fb_page": "1", "src_instagram": "403", "vis_instagram": "1"})
with flask_app.app_context():
    assert {r["platform"] for r in db.query("SELECT platform FROM portal_sources WHERE client_id = 1")} >= {"fb_page", "instagram"}
# Izvor podataka: koliko stranica vidi nalog i koje dozvole fale.
plain = text(team.get("/tim/izvor").text)
assert "Facebook stranica na spisku: 2." in plain, plain[:2000]
fake.meta_granted = ["ads_read", "pages_show_list"]
meta_organic._diag["at"] = 0
plain = text(team.get("/tim/izvor").text)
assert "Nedostaju dozvole: pages_read_engagement, read_insights, instagram_basic, instagram_manage_insights." in plain
fake.meta_granted = ["ads_read", "pages_show_list", "pages_read_engagement", "read_insights", "instagram_basic",
                     "instagram_manage_insights"]
meta_organic._diag["at"] = 0
team.post("/tim/klijent/1", data={**SETTINGS, "src_fb_page": "", "src_instagram": ""})

# Granice dana za Metu su po beogradskom vremenu (oktobar: leto, CEST), ne po vremenu servera.
from datetime import date as _d, datetime as _dt, timezone as _tz  # noqa: E402
assert _dt.fromtimestamp(meta_organic._ts(_d(2026, 10, 1)), _tz.utc).isoformat() == "2026-09-30T22:00:00+00:00"
assert _dt.fromtimestamp(meta_organic._ts(_d(2026, 10, 31), True), _tz.utc).isoformat() == "2026-10-31T23:00:00+00:00"

# 12h. Tabela po mesecima, za oglase, zbirni izvestaj, sajt, Facebook i Instagram.
from datetime import date as _date  # noqa: E402
import report  # noqa: E402
assert [m[0] for m in report._months(_date(2026, 10, 15), _date(2026, 12, 3))] == ["15.–31. okt 2026", "nov 2026", "1.–3. dec 2026"]
assert [m[0] for m in report._months(_date(2026, 1, 1), _date(2026, 12, 31))][0] == "jan 2026"
last_month_end = YESTERDAY.replace(day=1) - timedelta(days=1)
three_start = (last_month_end.replace(day=1) - timedelta(days=1)).replace(day=1)
three_start = (three_start - timedelta(days=1)).replace(day=1)
months = report._months(three_start, last_month_end)
assert len(months) == 3 and all("–" not in m[0] for m in months), months
fake.reach_calls.clear()
team.post("/tim/klijent/1", data={
    **SETTINGS, "src_fb_page": "301", "vis_fb_page": "1", "src_instagram": "401", "vis_instagram": "1",
    "range_start": str(three_start), "range_end": str(last_month_end),
    "s_google_ads": ["monthly"], "m_google_ads": ["spend", "clicks"],
    "lay_meta": "1", "m_meta": ["spend", "impressions", "reach"], "s_meta": ["monthly"],
    "lay_ukupno": "1", "m_ukupno": ["spend", "clicks"], "s_ukupno": ["monthly"],
    "lay_ga4": "1", "m_ga4": ["sessions", "users"], "s_ga4": ["monthly"],
    "lay_fb_page": "1", "m_fb_page": ["views", "new_follows", "followers"], "s_fb_page": ["monthly"],
    "lay_instagram": "1", "m_instagram": ["reach", "profile_visits", "new_follows"], "s_instagram": ["monthly"],
})
names = [m[0] for m in months]
day_counts = [(b - a).days + 1 for _, a, b in months]
google_page = text(visitor.get(f"{path}?kanal=google_ads").text)
assert "Po mesecima" in google_page and all(n in google_page for n in names), google_page[:600]
assert "Potrošeno " + " ".join(f"{30 * d} €" for d in day_counts) in google_page, google_page
meta_page = text(visitor.get(f"{path}?kanal=meta").text)
assert "Prikazi " + " ".join(fmt.num(2000 * d) for d in day_counts) in meta_page, meta_page
month_reach = {a for lvl, a in fake.reach_calls if lvl == "account"}
assert {str(a) for _, a, _ in months} <= month_reach, fake.reach_calls  # doseg se cita posebno za svaki mesec
combined_page = text(visitor.get(path).text)
assert "Po mesecima" in combined_page and "Potrošeno " in combined_page
site_page = text(visitor.get(f"{path}?kanal=ga4").text)
assert "Posete " + " ".join(fmt.num(41 * d) for d in day_counts) in site_page, site_page
fb_page = text(visitor.get(f"{path}?kanal=fb_page").text)
assert "Pregledi " + " ".join(fmt.num(500 * d) for d in day_counts) in fb_page, fb_page
assert "Novi pratioci u periodu " + " ".join(str(d) for d in day_counts) in fb_page
assert "Ukupno pratilaca" not in fb_page.split("Po mesecima")[1], "ukupan broj pratilaca nije mesecna brojka"
ig_page = text(visitor.get(f"{path}?kanal=instagram").text)
assert "Posete profila " + " ".join(fmt.num(20 * d) for d in day_counts) in ig_page, ig_page
assert "Novi pratioci u periodu " + " ".join(fmt.num(2 * d) for d in day_counts) in ig_page
team.post("/tim/klijent/1", data={**SETTINGS, "src_fb_page": "", "src_instagram": "", "range_start": "", "range_end": ""})

# Povratak na podrazumevano za dalje provere.
DEFAULT_ADS = {"m": ["spend", "impressions", "clicks", "cpc", "conversions", "cpa"], "s": ["chart", "campaigns"]}
team.post("/tim/klijent/1", data={
    **SETTINGS,
    **{f"lay_{k}": "1" for k in ("meta", "ukupno", "ga4")},
    **{f"m_{k}": DEFAULT_ADS["m"] for k in ("meta", "ukupno")}, **{f"s_{k}": DEFAULT_ADS["s"] for k in ("meta", "ukupno")},
    "m_ga4": ["sessions", "users", "avg_time"], "s_ga4": ["chart", "channels", "pages"], "chart_ga4": "sessions",
})
team.post("/tim/klijent/1", data=SETTINGS)

# 12b. Period koji odredjuje tim; klijent bira bilo koji raspon unutar njega.
month_end = YESTERDAY.replace(day=1) - timedelta(days=1)
month_end = (month_end.replace(day=1) - timedelta(days=1)).replace(day=1) - timedelta(days=1)  # pre tri meseca
month_start = month_end.replace(day=1)
month_days = month_end.day
month_name = fmt.MONTHS_GENITIVE[month_end.month - 1]
with_year = f" {month_end.year}." if month_end.year != YESTERDAY.year else ""
for bad, message in [
    ({"range_start": "", "range_end": str(month_end)}, "upišite i datum od kog"),
    ({"range_start": str(month_end), "range_end": str(month_start)}, "je pre datuma"),
    ({"range_start": str(YESTERDAY - timedelta(days=400)), "range_end": ""}, "najviše 366 dana"),
    ({"range_start": "31.01.2026", "range_end": ""}, "datum nije ispravan"),
]:
    resp = team.post("/tim/klijent/1", data={**SETTINGS, **bad}, follow_redirects=True)
    assert message in resp.text, (bad, text(resp.text)[:300])
resp = team.post(
    "/tim/klijent/1", data={**SETTINGS, "range_start": str(month_start), "range_end": str(month_end)}, follow_redirects=True
)
assert f"Sada: Od {fmt.date_numeric(month_start)} do {fmt.date_numeric(month_end)}" in text(resp.text)

fake.ranges.clear()
page = visitor.get(f"{path}?kanal=google_ads").text  # bez izbora: ceo period
assert hero(page) == (
    f"Od 1. do {month_days}. {month_name}{with_year} Google Ads oglasi su vam doneli {fmt.conv(1.5 * month_days)} upita, "
    "po prosečnoj ceni od 20,00 € po upitu."
), hero(page)
assert ("google_ads", month_start, month_end) in fake.ranges
assert f'min="{month_start}"' in page and f'max="{month_end}"' in page
labels = re.findall(r'>([^<]+)</a>', page.split('aria-label="Brzi izbor perioda"')[1].split("</nav>")[0])
assert labels == ["Ceo period", "30 dana", "7 dana", "1 dan"], labels
calls = len(fake.ranges)
two = visitor.get(f"{path}?kanal=google_ads&od={month_start + timedelta(days=4)}&do={month_start + timedelta(days=5)}").text
assert hero(two) == f"Od 5. do 6. {month_name}{with_year} Google Ads oglasi su vam doneli 3 upita, po prosečnoj ceni od 20,00 € po upitu."
assert len(fake.ranges) == calls, "kraci period unutar odobrenog ne trazi dnevne brojke ponovo"
one = visitor.get(f"{path}?kanal=meta&od={month_end}&do={month_end}").text
assert hero(one).startswith(f"Dana {month_end.day}. {month_name}{with_year} Meta Ads oglasi su vam doneli 2 upita")
assert 'class="bars"' not in one, "za jedan dan nema grafikona"
# Pokusaj izlaska iz perioda se svodi na odobreni period.
outside = visitor.get(f"{path}?kanal=google_ads&od={month_start - timedelta(days=40)}&do={YESTERDAY}").text
assert hero(outside) == hero(page)
combined = visitor.get(f"{path}?kanal=ukupno&od={month_start}&do={month_start + timedelta(days=9)}").text
assert hero(combined) == f"Od 1. do 10. {month_name}{with_year} oglasi na svim kanalima su vam doneli 45 upita."
assert ("tiktok", month_start, month_end) in fake.ranges or any(r[0] == "tiktok" and r[2] == month_end for r in fake.ranges)
site = visitor.get(f"{path}?kanal=ga4&od={month_start}&do={month_start + timedelta(days=1)}").text
assert hero(site) == f"Od 1. do 2. {month_name}{with_year} vaš sajt je imao 82 posete."
# Lista za tim i dalje pokazuje poslednjih 30 dana.
assert "1.110 € i 90 $" in text(team.get("/tim").text)

# Otvoren kraj: od datuma do juce, period raste sam.
team.post("/tim/klijent/1", data={**SETTINGS, "range_start": str(YESTERDAY - timedelta(days=9)), "range_end": ""})
page = visitor.get(f"{path}?kanal=google_ads").text
assert hero(page).startswith("U poslednjih 10 dana Google Ads oglasi su vam doneli 15 upita")
assert "do juče, i period se sam produžava" in text(team.get("/tim/klijent/1").text)
assert hero(visitor.get(f"{path}?kanal=google_ads&od={YESTERDAY}&do={YESTERDAY}").text).startswith("Juče Google Ads oglasi")

# Period koji jos nije poceo.
team.post("/tim/klijent/1", data={**SETTINGS, "range_start": str(YESTERDAY + timedelta(days=3)), "range_end": ""})
assert "Izveštaj počinje" in visitor.get(path).text

# Bez perioda: kao ranije, poslednjih 30 od 90 dana.
team.post("/tim/klijent/1", data={**SETTINGS, "range_start": "", "range_end": ""})
page = visitor.get(f"{path}?kanal=google_ads").text
assert hero(page).startswith("U poslednjih 30 dana Google Ads oglasi")
labels = re.findall(r'>([^<]+)</a>', page.split('aria-label="Brzi izbor perioda"')[1].split("</nav>")[0])
assert labels == ["90 dana", "30 dana", "7 dana", "1 dan"], labels

# 12i. Ceo izvestaj na jednoj strani (za male klijente): bez kartica, svi delovi jedan ispod drugog.
assert "Ceo izveštaj na jednoj strani" in team.get("/tim/klijent/1").text
team.post("/tim/klijent/1", data={**SETTINGS, "single_page": "1"})
page = visitor.get(path).text
plain = text(page)
assert 'class="tabs"' not in page
titles = re.findall(r'<h2 class="part-title"[^>]*>([^<]+)</h2>', page)
assert titles == ["Ukupno", "Google Ads", "Meta Ads", "Microsoft Ads (Bing)", "TikTok Ads", "Sajt (Analytics)"], titles
heroes = [text(h).strip() for h in re.findall(r'<p class="hero part-hero">(.*?)</p>', page, re.S)]
assert len(heroes) == 6 and heroes[1].startswith("U poslednjih 30 dana Google Ads oglasi su vam doneli 45 upita"), heroes
assert "vaš sajt je imao 1.230 poseta" in plain and plain.count("Izveštaj prikazuje stanje zaključno sa") == 1
week = text(visitor.get(f"{path}?od={YESTERDAY - timedelta(days=6)}&do={YESTERDAY}").text)
assert "U poslednjih 7 dana Google Ads oglasi" in week and "U poslednjih 7 dana Meta Ads oglasi" in week
team.post("/tim/klijent/1", data=SETTINGS)
assert 'class="tabs"' in visitor.get(path).text

# 13. Klijent ne vidi tim, ni pregled, ni nepostojece linkove.
assert visitor.get("/tim").status_code == 302 and visitor.get("/tim/klijent/1/pregled").status_code == 302
assert visitor.get("/k/nepostoji").status_code == 404

# 14. Kad platforma ne radi, prikazuju se ranije sacuvani podaci; tim vidi napomenu, klijent ne.
with flask_app.app_context():
    db.execute("UPDATE portal_cache SET fetched_at = ?", (int(time.time()) - 7 * 3600,))
fake.down = {"meta", "microsoft"}
page = visitor.get(f"{path}?kanal=meta").text
assert "doneli 60 upita" in hero(page) and "Napomene za tim" not in page
preview = text(team.get("/tim/klijent/1/pregled?kanal=meta").text)
assert "Napomene za tim" in preview and "Invalid OAuth access token." in preview
preview = text(team.get("/tim/klijent/1/pregled?kanal=ukupno").text)
assert "doneli 135 upita" in preview and "Microsoft Ads (Bing): u zbiru su ranije sačuvani podaci" in preview
fake.down = set()

# 14b. Meta odbija prevelik zahtev: portal ga sam deli na manje delove.
with flask_app.app_context():
    platforms.clear("meta", "9001")
fake.meta_max_days = 6
fake.ranges.clear()
page = visitor.get(f"{path}?kanal=meta").text
assert "doneli 60 upita" in hero(page), hero(page)
meta_parts = [r for r in fake.ranges if r[0] == "meta"]
assert fake.meta_refused > 0 and all((e - s).days + 1 <= 6 for _, s, e in meta_parts), meta_parts
assert min(s for _, s, _ in meta_parts) == platforms.default_range()[0] and max(e for _, _, e in meta_parts) == YESTERDAY
fake.meta_max_days = 0

# 14c. Spora platforma ne zaustavlja stranu: posle roka strana se prikaze, a ucitavanje se nastavlja u pozadini.
with flask_app.app_context():
    platforms.clear("tiktok", "7001")
started = time.time()
team.get("/tim?dana=7")  # koliko traje strana kad su podaci vec zapamceni (osim TikTok-a)
baseline = time.time() - started
with flask_app.app_context():
    platforms.clear("tiktok", "7001")
platforms.WAIT_SECONDS = 0.3
fake.slow = {"tiktok": 3}
started = time.time()
plain = text(team.get("/tim").text)
assert time.time() - started < baseline + 2, "strana ne ceka sporu platformu"
assert "TikTok Ads: Podaci se još učitavaju. Osvežite stranu za minut." in plain, plain[:600]
page = visitor.get(f"{path}?kanal=tiktok").text
assert "Podaci se upravo učitavaju." in page and "Osvežite stranu za minut" in page
time.sleep(3.2)  # pozadinsko ucitavanje se zavrsava i pamti
fake.slow = {}
platforms.WAIT_SECONDS = 20
assert "TikTok Ads: Podaci se još" not in text(team.get("/tim").text)
assert "doneli 0 upita" in hero(visitor.get(f"{path}?kanal=tiktok").text)

# 15. Prekinut kanal: klijent dobija poruku bez tehnickih detalja, ostali kanali rade.
team.post("/tim/izvor/meta/prekini", data={"_csrf": token})
page = visitor.get(f"{path}?kanal=meta").text
assert "Podaci za ovaj izveštaj trenutno nisu dostupni." in page and "token" not in text(page).lower()
assert "doneli 45 upita" in hero(visitor.get(f"{path}?kanal=google_ads").text)
assert "Meta Ads: kanal nije povezan" in text(team.get("/tim").text)
settings = text(team.get("/tim/klijent/1").text)
assert "Izabran je nalog Hrast Meta (9001), ali kanal trenutno nije povezan." in settings
team.post("/tim/klijent/1", data={k: v for k, v in SETTINGS.items() if "meta" not in k})
with flask_app.app_context():
    assert db.query("SELECT account_id FROM portal_sources WHERE platform = 'meta'") == [{"account_id": "9001"}]

# 16. Prekid Google veze opoziva dozvolu; klijent bez ijednog kanala vidi samo poruku.
team.post("/tim/izvor/google/prekini", data={"_csrf": token})
assert fake.count(google_auth.REVOKE_URL) == 1
team.post("/tim/dodaj", data={"_csrf": token, "name": "Prazan klijent"})
team.post("/tim/klijent/2/lozinka", data={"_csrf": token})
empty = urlparse(re.search(r'id="link">([^<]+)<', team.get("/tim/klijent/2").text).group(1)).path
assert "Lozinka" in visitor.get(empty).text  # lozinka prvog klijenta ne otvara drugog
resp = team.get("/tim/klijent/2/pregled", follow_redirects=True)
assert "Klijent još nema nijedan kanal" in resp.text

# 17. Uklanjanje klijenta gasi njegov link i brise njegove kanale.
team.post("/tim/klijent/1/ukloni", data={"_csrf": token})
assert visitor.get(path).status_code == 404
with flask_app.app_context():
    assert db.query("SELECT * FROM portal_sources") == []
    assert db.query("SELECT client_id FROM portal_logos") == [], "logo se brise sa klijentom"

# 18. Odjava.
team.post("/tim/odjava", data={"_csrf": token})
assert team.get("/tim").status_code == 302

assert fmt.money_sum({"EUR": 10, "RSD": 5}) == "10 € i 5 RSD"
# Kampanje sa razlicitim ciljevima nemaju zbirni "rezultat" (kao u Ads Manager-u).
import report  # noqa: E402
kind = fmt.CONV_KINDS["upit"]
mixed = report._ads_values(kind, {"EUR": 100}, 1000, 50, 0, {"EUR": 0}, None, {"results": 30}, ["leads", "link_clicks"])
same = report._ads_values(kind, {"EUR": 100}, 1000, 50, 0, {"EUR": 0}, None, {"results": 30}, ["link_clicks"])
assert mixed["results"] is None and mixed["cost_per_result"] is None
assert same["results"] == "30 klikova na link" and same["cost_per_result"] == "3,33 €"
assert report._ads_values(kind, {"EUR": 1}, 1, 1, 0, {"EUR": 0}, None, {"results": 1}, ["None"])["results"] is None

print("SVE PROVERE SU PROSLE")
