# Victorious portal

Portal na kom klijenti agencije vide rezultate svojih kampanja.

- Tim se prijavljuje Google nalogom sa domena agencije i vidi sve klijente.
- Svaki klijent ima svoj link i lozinku i vidi samo svoje izveštaje.
- Svaki kanal ima svoj izveštaj: Google Ads, Meta Ads, Microsoft Ads (Bing),
  TikTok Ads i posete sajtu iz Google Analytics-a.
- Postoji i zbirni izveštaj preko svih kanala oglašavanja. Tim po klijentu bira
  koji se kanali prikazuju i da li se prikazuje zbirni.
- Podaci su pravi i samo se čitaju. Portal ništa ne menja u nalozima.

## Šta je provereno, a šta nije

Ceo tok je proveren automatskim testom sa simuliranim odgovorima platformi
(`tests/`). **Nijedan kanal još nije isproban sa pravim nalogom.** Pozivi su
pisani prema zvaničnoj dokumentaciji, ali prvi pravi poziv na svakom kanalu
treba smatrati probom: ako platforma nešto odbije, portal prikazuje njenu
poruku timu (klijent je ne vidi).

Kanali su nezavisni, pa ih vredi uključivati jedan po jedan, počev od Google-a.

## Šta klijent vidi u izveštaju

U podešavanjima klijenta svaki kanal ima svoj izbor „Šta klijent vidi u
izveštaju …“. Isto važi za zbirni izveštaj i za izveštaj o sajtu.

- **Kanali oglašavanja i zbirni izveštaj:** potrošeno, prikazi, klikovi, CTR,
  cena po kliku, cena za 1.000 prikaza, konverzije, stopa konverzije i cena po
  konverziji, vrednost konverzija i ROAS. Meta ima i kolone kao u Ads
  Manager-u: doseg, frekvenciju, rezultate i cenu po rezultatu, budžet
  kampanje, sve klikove i cenu po kliku (svi klikovi), preglede odredišne
  strane i cenu po pregledu, kao i ocene oglasa (kvalitet, angažovanje,
  konverzije).
  Od delova izveštaja tu su grafikon po danima, tabela kampanja i, samo za
  Google Ads, pojmovi pretrage. Grafikon može da prikazuje klikove, prikaze,
  potrošnju, konverzije ili vrednost konverzija.
- **Sajt (Analytics):** posete, posetioci, novi posetioci, prosečno trajanje
  posete, stopa angažovanja, pregledi strana i konverzije iz Analytics-a
  (ključni događaji). Od delova izveštaja tu su grafikon, izvori poseta i
  najgledanije strane.

Izbor se čuva po klijentu (kolona `layout`). Klijenti koji još nisu podešavani
vide isto što i ranije.

## Period koji klijent vidi

U podešavanjima klijenta tim bira period „od–do“, na primer od 01.01. do 31.01.
Klijent unutar tog perioda sam bira šta gleda: ceo period, poslednjih 30 ili 7
dana, jedan dan ili bilo koji raspon preko polja „od“ i „do“. Izvan perioda ne
može da vidi ništa, ni ako ručno promeni adresu.

- Ako je „do“ prazno, period ide do juče i sam se produžava.
- Ako su oba prazna, klijent vidi poslednjih 90 dana, a izveštaj se otvara na
  poslednjih 30.
- Ako je „do“ u budućnosti (na primer ceo tekući mesec), klijent vidi brojke
  zaključno sa jučerašnjim danom.
- Period traje najviše 366 dana.
- Lista klijenata za tim i dalje prikazuje poslednjih 7, 30 ili 90 dana, bez
  obzira na period klijenta.

Brojke za ceo period se sa platformi čitaju jednom i pamte, pa promena perioda
u izveštaju ne troši nove pozive. Izuzeci su Analytics i pojmovi pretrage, koji
se čitaju posebno za svaki izabrani raspon.

## Kako se kanali povezuju

Sve se radi na strani **Izvor podataka**. Šta je potrebno po kanalu:

| Kanal | Šta treba pripremiti | Kako se povezuje |
|---|---|---|
| Google Ads i Analytics | OAuth klijent u Google Cloud projektu "Victorious Portal" | dugme "Poveži Google nalog" |
| Meta Ads | Meta aplikacija sa Marketing API-jem; sistemski korisnik u Business Manager-u sa dozvolom `ads_read` i dodeljenim oglasnim nalozima | token se nalepi u portal |
| Microsoft Ads (Bing) | developer token iz Microsoft Ads-a; aplikacija u Microsoft Entra (Azure) | dugme "Poveži Microsoft Ads" |
| TikTok Ads | aplikacija na TikTok API for Business portalu, sa dozvolom za izveštaje | dugme "Poveži TikTok Ads" |

Tokeni se čuvaju u bazi šifrovani ključem izvedenim iz `SECRET_KEY`. Svaku vezu
možete prekinuti na istoj strani.

Važno za svaki kanal:

- **Google:** portal čita u ime Google naloga koji povežete, pa se u Google
  Ads-u ne dodaje nov korisnik. Google Ads nema dozvolu samo za čitanje, pa
  ekran za dozvolu pominje upravljanje kampanjama.
- **Meta:** sistemski korisnik i aplikacija vide se u podešavanjima Business
  Manager-a. Po Meta dokumentaciji, osnovni nivo pristupa važi za sopstvene
  oglasne naloge; za naloge drugih firmi može biti potreban App Review.
- **Microsoft:** izveštaji se prave u pozadini, pa prvo učitavanje naloga traje
  nekoliko sekundi duže nego kod ostalih.
- **TikTok:** odobrenje developerske aplikacije ume da potraje danima. Ako vas
  TikTok posle odobrenja ne vrati na portal, kod iz adrese (`auth_code`) može
  da se unese ručno.

## Podešavanje u Google Cloud konzoli (jednom)

Radi se u projektu **Victorious Portal**, jer je odobren pristup Google Ads
API-ju vezan za taj projekat.

1. **Google Auth Platform > Get started:** ime aplikacije `Victorious Portal`,
   Audience **Internal**.
2. **Clients > Create client:** tip **Web application**, a kao Authorized
   redirect URIs:
   - `http://127.0.0.1:5000/auth/google/callback`
   - `http://localhost:5000/auth/google/callback`
   - `https://VAŠA-ADRESA-NA-RENDERU/auth/google/callback` (kad bude poznata)
3. Zapišite **Client ID** i **Client secret**.
4. Opciono: uključite **Google Analytics Admin API**. Tada se GA4 property bira
   iz liste; bez toga se njegov broj upisuje ručno.

Adrese za povratak za Microsoft i TikTok portal prikazuje na strani Izvor
podataka, u uputstvu svakog kanala.

## Pokretanje na svom računaru

Potreban je Python 3.10 ili noviji.

```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

U `.env` upišite Google Client ID i secret i koje Google Ads naloge portal
čita. Ostali kanali su opcioni. Zatim:

```
flask --app app run
```

Otvorite http://127.0.0.1:5000 i redom:

1. Prijavite se Google nalogom agencije.
2. **Izvor podataka:** povežite kanale koje koristite.
3. **Klijenti > Dodaj klijenta:** upišite ime, pa u podešavanjima izaberite
   njegov nalog na svakom kanalu i napravite lozinku.

Lokalno se podaci čuvaju u fajlu `portal.db`.

## Koje Google Ads naloge portal čita

- **Samo određeni nalozi:** u `GOOGLE_ADS_ACCOUNT_IDS` upišite njihove ID-jeve.
  Portal tada čita isključivo njih i ne učitava spisak naloga iz MCC-a.
- **Svi nalozi ispod MCC-a:** ostavite to prazno i upišite `GOOGLE_ADS_MCC_ID`.

Ako povezani Google nalog pristupa klijentu preko MCC-a, `GOOGLE_ADS_MCC_ID`
treba upisati i u prvom slučaju; u MCC-u se pri tome ništa ne menja.

## Postavljanje na Render

1. Web Service iz ovog repozitorijuma (region Frankfurt).
   - Build command: `pip install -r requirements.txt`
   - Start command: `gunicorn app:app --timeout 120`
2. Promenljive okruženja:

| Promenljiva | Vrednost |
|---|---|
| `SECRET_KEY` | dugačak nasumičan niz: `python -c "import secrets; print(secrets.token_hex(32))"` |
| `DATABASE_URL` | connection string Postgres baze (Supabase) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | iz Google Cloud konzole |
| `GOOGLE_ADS_ACCOUNT_IDS` ili `GOOGLE_ADS_MCC_ID` | koje Google Ads naloge portal čita |
| `ALLOWED_DOMAIN` | `victorious.rs` |
| `ALLOWED_EMAILS` | opciono: samo ovi emailovi, odvojeni zarezom |
| `MICROSOFT_ADS_CLIENT_ID`, `MICROSOFT_ADS_CLIENT_SECRET`, `MICROSOFT_ADS_DEVELOPER_TOKEN` | opciono, za Microsoft Ads |
| `TIKTOK_APP_ID`, `TIKTOK_APP_SECRET` | opciono, za TikTok Ads |

3. Adrese za povratak sa Render domenom dodajte u Google, Microsoft i TikTok
   aplikaciju.

Napomene:

- `DATABASE_URL` je obavezan. Bez njega bi se koristio SQLite fajl, koji Render
  briše pri svakom postavljanju.
- Ako promenite `SECRET_KEY`, sačuvane veze postaju nečitljive i kanale treba
  povezati ponovo.
- Tabele se prave same i imaju prefiks `portal_`.

## Kako se računaju brojke

- Svi izveštaji prikazuju stanje zaključno sa jučerašnjim danom.
- Brojke se pamte 6 sati (tabela `portal_cache`), da se strane otvaraju brzo i
  da se ne troše dnevna ograničenja platformi. Dugme "Osveži podatke" briše
  zapamćeno. Ako platforma privremeno ne radi, prikazuju se poslednji zapamćeni
  podaci.
- **Valute se ne sabiraju.** Ako su kanali klijenta u različitim valutama,
  zbirni izveštaj prikazuje potrošnju po valuti i ne računa zbirnu cenu.
- **Konverzije** su onakve kakve ih svaka platforma prijavljuje, pa zbir preko
  kanala sabira različito merene stvari. Kod Meta Ads-a se, prema podešavanju
  klijenta, broje leadovi (`lead`) ili kupovine (`purchase`).
- **Klikovi** kod Meta Ads-a su klikovi na link, ne svi klikovi na objavu.
- **Pojmovi pretrage** postoje samo za Google Ads.
- **Vrednost konverzija** je onakva kakvu platforma prijavljuje: Google
  `conversions_value`, Meta vrednost kupovina, Microsoft `Revenue`, a TikTok
  ROAS kupovina pomnožen potrošnjom. **ROAS** = vrednost / potrošeno; u zbirnom
  izveštaju se računa samo kad su svi kanali u istoj valuti.
- **Rezultati (Meta)** se računaju prema cilju kampanje: upiti za Leads,
  kupovine za Sales, klikovi na link za Traffic, interakcije za Engagement,
  razgovori za Messages, instalacije za App promotion. Ads Manager gleda cilj
  optimizacije skupa oglasa, pa brojka može malo da odstupa. Kampanje za svest
  o brendu (rezultat je doseg) imaju „–“. Zbir rezultata postoji samo kad sve
  kampanje imaju isti cilj, kao u Ads Manager-u.
- **Budžet (Meta)** je trenutni budžet kampanje, ne budžet u izabranom
  periodu, pa se prikazuje samo u tabeli kampanja.
- **Ocene oglasa (Meta)** Meta daje samo na nivou oglasa i samo za oglase sa
  dovoljno prikaza; prikazuje se 10 oglasa sa najviše prikaza.
- **Doseg i frekvencija (Meta)** se ne mogu sabrati po danima, pa se za svaki
  izabrani period čitaju posebno, i to samo kad su uključeni.
- Klijent može da ima najviše jedan nalog po kanalu.

## Bezbednost: šta je urađeno

- Tim ulazi samo potvrđenim Google nalogom sa domena iz `ALLOWED_DOMAIN`.
  Svako sa takvim nalogom vidi sve klijente; `ALLOWED_EMAILS` to sužava.
- Klijentske lozinke čuvaju se samo kao heš i prikazuju se jednom.
- Posle 8 pogrešnih lozinki link se zaključava na 15 minuta.
- Nova lozinka odjavljuje sve koji su ušli sa starom; link može da se isključi.
- Sve forme imaju CSRF zaštitu; kolačići su HttpOnly, SameSite i Secure na serveru.
- Klijent nikad ne vidi tehničke poruke o greškama; njih vidi samo tim.
- Tajne (`.env`, `.secret_key`, ključevi) su u `.gitignore`.

## Šta je gde

| Fajl | Čemu služi |
|---|---|
| `app.py` | sve strane i pravila pristupa |
| `report.py` | sastavljanje izveštaja po kanalu, zbirnog i izveštaja o sajtu |
| `platforms/` | po jedan fajl za svaki kanal, plus zajednički keš |
| `google_auth.py` | Google prijava i dozvola za Google podatke |
| `connections.py` | šifrovano čuvanje veza sa platformama |
| `db.py`, `migrate.py` | baza (SQLite lokalno, Postgres na serveru) i prenos iz ranije verzije |
| `fmt.py` | ispis brojeva, novca i množine na srpskom |
| `templates/`, `static/` | izgled strana |
| `tests/` | provera celog toka sa lažnim platformama |

Provera posle izmena (ne izlazi na mrežu i ne dira pravu bazu):

```
python tests/test_flow.py
python tests/test_migration.py
```

Ako ste koristili raniju verziju (samo Google), njeni klijenti, lozinke i
Google veza prenose se sami pri prvom pokretanju. To je provereno na lokalnoj
SQLite bazi.

## Verzije API-ja

Verzije su upisane u fajlovima kanala i mogu se promeniti promenljivama
`GOOGLE_ADS_API_VERSION` (sada `v25`) i `META_API_VERSION` (sada `v25.0`).
Platforme gase stare verzije, pa ovo treba povremeno proveriti.
