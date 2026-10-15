"""Victorious portal: klijenti vide rezultate svojih kampanja.

Tim se prijavljuje Google nalogom agencije i vidi sve klijente. Svaki
klijent ima svoj link i lozinku i vidi iskljucivo svoje izvestaje: po
jedan za svaki kanal oglasavanja, zbirni i izvestaj o posetama sajtu.
"""
from dotenv import load_dotenv

load_dotenv()  # lokalno cita .env; na serveru promenljive stizu iz okruzenja

import hmac  # noqa: E402
import os  # noqa: E402
import secrets  # noqa: E402
import time  # noqa: E402
from datetime import date, datetime, timedelta, timezone  # noqa: E402
from functools import wraps  # noqa: E402

from flask import (  # noqa: E402
    Flask,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix  # noqa: E402
from werkzeug.security import check_password_hash, generate_password_hash  # noqa: E402

import connections  # noqa: E402
import db  # noqa: E402
import fmt  # noqa: E402
import layout  # noqa: E402
import google_auth  # noqa: E402
import migrate  # noqa: E402
import platforms  # noqa: E402
import report  # noqa: E402
from platforms import base, ga4, google_ads, meta_ads, microsoft_ads, tiktok_ads  # noqa: E402
from platforms.base import SourceError  # noqa: E402

ON_RENDER = bool(os.environ.get("RENDER"))
PRODUCTION = ON_RENDER or db.IS_PG

MAX_ATTEMPTS = 8          # pogresnih lozinki pre zakljucavanja
LOCK_SECONDS = 15 * 60    # koliko traje zakljucavanje
PERIODS = (7, 30, 90)


def load_secret_key():
    secret = os.environ.get("SECRET_KEY")
    if secret:
        return secret
    if PRODUCTION:
        raise RuntimeError("Podesi promenljivu SECRET_KEY pre pokretanja na serveru.")
    # Lokalno: kljuc se pravi jednom i cuva u fajlu, da prijave i veze sa
    # platformama prezive restart. Fajl je u .gitignore.
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".secret_key")
    if os.path.exists(path):
        with open(path) as handle:
            return handle.read().strip()
    secret = secrets.token_hex(32)
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as handle:
        handle.write(secret)
    return secret


app = Flask(__name__)
app.config.update(
    SECRET_KEY=load_secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=PRODUCTION,
    PERMANENT_SESSION_LIFETIME=timedelta(days=14),
)
if ON_RENDER:
    # Render stoji ispred aplikacije; ovo cuva https i pravi domen u linkovima.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

connections.init(app.config["SECRET_KEY"])
app.teardown_appcontext(db.close_conn)
app.add_template_filter(fmt.num, "num")


@app.template_global()
def account_label(platform, account_id):
    """ID naloga u obliku u kom ga prikazuje sama platforma."""
    if platform in platforms.BY_KEY:
        return platforms.get(platform).format_id(account_id)
    return account_id


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


# ---------------------------------------------------------------- pomocne

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def make_password():
    """Lozinka od 12 znakova, bez znakova koji se lako pomesaju (0/O, 1/l)."""
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    chars = [secrets.choice(alphabet) for _ in range(12)]
    return "-".join("".join(chars[i:i + 4]) for i in (0, 4, 8))


def get_days():
    try:
        days = int(request.args.get("dana", 30))
    except ValueError:
        days = 30
    return days if days in PERIODS else 30


# ---------------------------------------------------------------- CSRF

def csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_urlsafe(32)
    return session["_csrf"]


@app.before_request
def check_csrf():
    if request.method == "POST":
        sent = request.form.get("_csrf", "")
        if not sent or not hmac.compare_digest(sent, session.get("_csrf", "")):
            abort(400, "Forma je istekla. Osvežite stranu i pokušajte ponovo.")


@app.context_processor
def inject_helpers():
    user = current_user()
    return {
        "csrf_token": csrf_token,
        "team_user": user,
        "periods": PERIODS,
        # Upozorenja o vezama koje isticu vidi samo tim.
        "team_warnings": meta_ads.warnings() if user else [],
    }


# ---------------------------------------------------------------- tim: prijava

def current_user():
    user_id = session.get("team_user_id")
    if not user_id:
        return None
    return db.query("SELECT id, email, name FROM portal_team_users WHERE id = ?", (user_id,), one=True)


def team_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            return redirect(url_for("team_login"))
        return view(*args, **kwargs)

    return wrapped


def callback_url(endpoint="google_callback"):
    return url_for(endpoint, _external=True)


def new_state(key, **extra):
    """Jednokratna oznaka kojom se proverava da je povratak sa platforme nas zahtev."""
    state = secrets.token_urlsafe(24)
    session[key] = {"state": state, **extra}
    return state


def take_state(key, received):
    """Uzima sacuvanu oznaku (vazi jednom) i poredi je sa primljenom."""
    pending = session.pop(key, None)
    if not pending or not received or not hmac.compare_digest(pending["state"], received):
        return None
    return pending


@app.get("/")
def home():
    return redirect(url_for("clients"))


@app.get("/healthz")
def healthz():
    return "ok"


@app.get("/tim/prijava")
def team_login():
    if current_user():
        return redirect(url_for("clients"))
    return render_template(
        "team_login.html",
        google_ready=google_auth.configured(),
        domain=google_auth.ALLOWED_DOMAIN,
        callback=callback_url(),
    )


@app.post("/auth/google/start")
def google_start():
    purpose = request.form.get("svrha", "login")
    if purpose not in ("login", "connect"):
        abort(400, "Nepoznat zahtev.")
    user = current_user()
    if purpose == "connect" and not user:
        return redirect(url_for("team_login"))
    if not google_auth.configured():
        flash("Google prijava još nije podešena (GOOGLE_CLIENT_ID i GOOGLE_CLIENT_SECRET).", "error")
        return redirect(url_for("data_source" if user else "team_login"))
    state = new_state("oauth_google", purpose=purpose)
    return redirect(
        google_auth.build_auth_url(
            callback_url(), state, purpose, login_hint=user["email"] if user else None
        )
    )


@app.get("/auth/google/callback")
def google_callback():
    pending = take_state("oauth_google", request.args.get("state", ""))
    purpose = (pending or {}).get("purpose", "login")
    back = "data_source" if current_user() else "team_login"

    if request.args.get("error"):
        flash("Prijava preko Google-a je otkazana.", "error")
        return redirect(url_for(back))
    code = request.args.get("code", "")
    if not pending or not code:
        flash("Prijava je istekla. Pokušajte ponovo.", "error")
        return redirect(url_for(back))

    try:
        tokens = google_auth.exchange_code(code, callback_url())
        userinfo = google_auth.fetch_userinfo(tokens["access_token"])
        email = google_auth.check_allowed(userinfo)
    except SourceError as exc:
        flash(str(exc), "error")
        return redirect(url_for(back))

    if purpose == "connect":
        user = current_user()
        if not user:
            return redirect(url_for("team_login"))
        granted = tokens.get("scope", "").split()
        if google_auth.SCOPE_ADS not in granted and google_auth.SCOPE_ANALYTICS not in granted:
            flash(
                "Niste dozvolili pristup ni Google Ads-u ni Analytics-u. Pokrenite povezivanje "
                "ponovo i ostavite uključene stavke.",
                "error",
            )
            return redirect(url_for("data_source"))
        if not tokens.get("refresh_token"):
            flash("Google nije izdao trajnu dozvolu. Pokušajte povezivanje ponovo.", "error")
            return redirect(url_for("data_source"))
        google_auth.save_connection(email, tokens["refresh_token"], " ".join(granted), user["email"])
        base.cache_clear()
        flash("Google nalog je povezan.", "ok")
        return redirect(url_for("data_source"))

    # Prijava clana tima: nalog se pravi sam pri prvom ulasku.
    name = (userinfo.get("name") or email.split("@")[0])[:120]
    user = db.query("SELECT id FROM portal_team_users WHERE email = ?", (email,), one=True)
    if user:
        db.execute(
            "UPDATE portal_team_users SET name = ?, last_login_at = ? WHERE id = ?",
            (name, now_iso(), user["id"]),
        )
        user_id = user["id"]
    else:
        user_id = db.insert(
            "INSERT INTO portal_team_users (email, name, created_at, last_login_at) "
            "VALUES (?, ?, ?, ?) RETURNING id",
            (email, name, now_iso(), now_iso()),
        )
    session.clear()
    session.permanent = True
    session["team_user_id"] = user_id
    return redirect(url_for("clients"))


@app.post("/tim/odjava")
def team_logout():
    session.clear()
    return redirect(url_for("team_login"))


# ---------------------------------------------------------------- tim: izvor podataka

def channel_status(mod):
    """Stanje jednog kanala za stranu "Izvor podataka"."""
    status = {
        "key": mod.KEY,
        "name": mod.NAME,
        "configured": mod.configured(),
        "connected": mod.connected(),
        "connection": connections.get(mod.CONNECTION),
        "accounts": None,
        "error": None,
    }
    if status["connected"] and status["configured"]:
        try:
            status["accounts"] = len(platforms.accounts(mod))
        except SourceError as exc:
            status["error"] = str(exc)
    return status


@app.get("/tim/izvor")
@team_required
def data_source():
    return render_template(
        "source.html",
        google_ready=google_auth.configured(),
        google=connections.get(google_auth.PLATFORM),
        google_ads=channel_status(google_ads),
        has_analytics=ga4.connected(),
        mcc_id=google_ads.MCC_ID,
        only_ids=google_ads.ONLY_IDS,
        meta=channel_status(meta_ads),
        meta_people=meta_ads.people(),
        meta_login=meta_ads.login_ready(),
        meta_warn_days=meta_ads.WARN_DAYS,
        microsoft=channel_status(microsoft_ads),
        tiktok=channel_status(tiktok_ads),
        callbacks={
            "google": callback_url(),
            "microsoft": callback_url("microsoft_callback"),
            "tiktok": callback_url("tiktok_callback"),
            "meta": callback_url("meta_callback"),
        },
        domain=google_auth.ALLOWED_DOMAIN,
        cache_hours=base.DATA_TTL // 3600,
    )


@app.post("/tim/izvor/<platform>/prekini")
@team_required
def data_source_disconnect(platform):
    if platform == google_auth.PLATFORM:
        google_auth.remove_connection()
    elif platform == meta_ads.CONNECTION:  # sve Meta veze
        for person in meta_ads.people():
            connections.remove(person["platform"])
        meta_ads.forget()
    elif platform.startswith(meta_ads.CONNECTION + ":") and connections.get(platform):  # veza jedne osobe
        connections.remove(platform)
        meta_ads.forget()
    elif platform in (microsoft_ads.CONNECTION, tiktok_ads.CONNECTION):
        connections.remove(platform)
        microsoft_ads.forget_token()
    else:
        abort(404)
    base.cache_clear()
    flash("Veza je prekinuta. Izveštaji za taj kanal nisu dostupni dok se ponovo ne poveže.", "ok")
    return redirect(url_for("data_source"))


@app.post("/tim/izvor/osvezi")
@team_required
def data_source_refresh():
    base.cache_clear()
    flash("Sačuvani podaci su obrisani. Sveži se učitavaju pri sledećem otvaranju.", "ok")
    return redirect(url_for("data_source"))


@app.post("/tim/izvor/meta/token")
@team_required
def meta_connect():
    token = request.form.get("token", "").strip()
    if not token:
        flash("Upišite token sistemskog korisnika.", "error")
        return redirect(url_for("data_source"))
    try:
        meta_ads.list_accounts(token)  # proverava da token ima dozvolu ads_read
        label = meta_ads.connect(token, current_user()["email"])
    except SourceError as exc:
        flash(f"Meta nije prihvatila token: {exc}", "error")
        return redirect(url_for("data_source"))
    base.cache_clear()
    flash(f"Meta Ads je povezan ({label}).", "ok")
    return redirect(url_for("data_source"))


@app.post("/auth/meta/start")
@team_required
def meta_start():
    if not meta_ads.login_ready():
        flash("Prijava preko Facebook-a još nije podešena (META_APP_ID i META_APP_SECRET).", "error")
        return redirect(url_for("data_source"))
    state = new_state("oauth_meta")
    return redirect(meta_ads.auth_url(callback_url("meta_callback"), state))


@app.get("/auth/meta/callback")
@team_required
def meta_callback():
    pending = take_state("oauth_meta", request.args.get("state", ""))
    code = request.args.get("code", "")
    if request.args.get("error"):
        flash("Povezivanje Facebook naloga je otkazano.", "error")
    elif not pending or not code:
        flash("Povezivanje je isteklo. Pokušajte ponovo.", "error")
    else:
        try:
            short = meta_ads.exchange_code(code, callback_url("meta_callback"))
            token, _ = meta_ads.extend(short)
            meta_ads.list_accounts(token)  # proverava dozvolu ads_read
            label = meta_ads.connect(token, current_user()["email"])
            base.cache_clear()
            flash(f"Meta Ads je povezan ({label}). Veza traje oko 60 dana; portal će vas upozoriti pre isteka.", "ok")
        except SourceError as exc:
            flash(f"Meta Ads nije povezan: {exc}", "error")
    return redirect(url_for("data_source"))


@app.post("/auth/microsoft/start")
@team_required
def microsoft_start():
    if not microsoft_ads.configured():
        flash("Microsoft Ads još nije podešen u okruženju aplikacije.", "error")
        return redirect(url_for("data_source"))
    state = new_state("oauth_microsoft")
    return redirect(microsoft_ads.auth_url(callback_url("microsoft_callback"), state))


@app.get("/auth/microsoft/callback")
@team_required
def microsoft_callback():
    pending = take_state("oauth_microsoft", request.args.get("state", ""))
    code = request.args.get("code", "")
    if request.args.get("error"):
        flash("Povezivanje Microsoft Ads naloga je otkazano.", "error")
    elif not pending or not code:
        flash("Povezivanje je isteklo. Pokušajte ponovo.", "error")
    else:
        try:
            tokens = microsoft_ads.exchange_code(code, callback_url("microsoft_callback"))
            _, label = microsoft_ads.whoami(tokens["access_token"])
            connections.save(
                microsoft_ads.CONNECTION, label, {"refresh_token": tokens["refresh_token"]}, current_user()["email"]
            )
            microsoft_ads.forget_token()
            base.cache_clear()
            flash("Microsoft Ads je povezan.", "ok")
        except SourceError as exc:
            flash(f"Microsoft Ads nije povezan: {exc}", "error")
    return redirect(url_for("data_source"))


@app.post("/auth/tiktok/start")
@team_required
def tiktok_start():
    if not tiktok_ads.configured():
        flash("TikTok Ads još nije podešen u okruženju aplikacije.", "error")
        return redirect(url_for("data_source"))
    state = new_state("oauth_tiktok")
    return redirect(tiktok_ads.auth_url(callback_url("tiktok_callback"), state))


def save_tiktok(auth_code):
    try:
        data = tiktok_ads.exchange(auth_code)
    except SourceError as exc:
        flash(f"TikTok Ads nije povezan: {exc}", "error")
        return
    count = len(data.get("advertiser_ids") or [])
    connections.save(
        tiktok_ads.CONNECTION,
        f"TikTok oglašivači: {count}",
        {"access_token": data["access_token"]},
        current_user()["email"],
    )
    base.cache_clear()
    flash("TikTok Ads je povezan.", "ok")


@app.get("/auth/tiktok/callback")
@team_required
def tiktok_callback():
    pending = take_state("oauth_tiktok", request.args.get("state", ""))
    auth_code = request.args.get("auth_code", "")
    if not pending or not auth_code:
        flash("Povezivanje TikTok naloga je isteklo ili je otkazano. Pokušajte ponovo.", "error")
    else:
        save_tiktok(auth_code)
    return redirect(url_for("data_source"))


@app.post("/tim/izvor/tiktok/kod")
@team_required
def tiktok_connect_code():
    """Rucni put: kod (auth_code) prepisan iz adrese na koju je TikTok vratio korisnika."""
    auth_code = request.form.get("auth_code", "").strip()
    if not tiktok_ads.configured():
        flash("TikTok Ads još nije podešen u okruženju aplikacije.", "error")
    elif not auth_code:
        flash("Upišite kod (auth_code).", "error")
    else:
        save_tiktok(auth_code)
    return redirect(url_for("data_source"))


# ---------------------------------------------------------------- tim: klijenti

def link_status(client):
    if not client["password_hash"]:
        return "none"
    return "active" if client["link_active"] else "off"


def sources_of(client_id):
    """Kanali klijenta: {oznaka kanala: red iz baze}."""
    rows = db.query("SELECT * FROM portal_sources WHERE client_id = ?", (client_id,))
    return {row["platform"]: row for row in rows}


@app.get("/tim")
@team_required
def clients():
    days = get_days()
    rows = db.query("SELECT * FROM portal_clients ORDER BY name")
    by_client = {}
    for src in db.query("SELECT * FROM portal_sources"):
        by_client.setdefault(src["client_id"], {})[src["platform"]] = src

    # Tim uvek vidi poslednjih 7, 30 ili 90 dana, bez obzira na period klijenta.
    data_start, data_end = platforms.default_range()
    view_start = data_end - timedelta(days=days - 1)
    pairs = [
        (key, src["account_id"], data_start, data_end)
        for sources in by_client.values()
        for key, src in sources.items()
        if key in platforms.BY_KEY and platforms.get(key).connected()
    ]
    errors = platforms.warm(pairs)

    items, grand_total = [], {}
    for c in rows:
        sources = by_client.get(c["id"], {})
        spend, clicks, conversions, problems, channels = {}, 0, 0.0, [], []
        for mod in platforms.ADS:
            src = sources.get(mod.KEY)
            if not src:
                continue
            channels.append(mod.NAME)
            payload, _ = platforms.daily(mod.KEY, src["account_id"], data_start, data_end, fetch=False)
            if payload is None:
                reason = errors.get((mod.KEY, src["account_id"])) or (
                    "kanal nije povezan" if not mod.connected() else "podaci još nisu učitani"
                )
                problems.append(f"{mod.NAME}: {reason}")
                continue
            totals = platforms.summarize(payload, view_start, data_end, c["conv_kind"])["totals"]
            currency = src["currency"] or "EUR"
            spend[currency] = spend.get(currency, 0) + totals["spend"]
            grand_total[currency] = grand_total.get(currency, 0) + totals["spend"]
            clicks += totals["clicks"]
            conversions += totals["conversions"]
        if ga4.KEY in sources:
            channels.append("Analytics")
        items.append(
            {
                "id": c["id"],
                "name": c["name"],
                "channels": channels,
                "has_numbers": bool(spend),
                "spend": fmt.money_sum(spend),
                "clicks": fmt.num(clicks),
                "conversions": fmt.conv(conversions),
                "problems": problems,
                "status": link_status(c),
            }
        )

    return render_template(
        "clients.html",
        days=days,
        items=items,
        count_text=f"{len(items)} {fmt.plural(len(items), ('klijent', 'klijenta', 'klijenata'))}",
        total_spend=fmt.money_sum(grand_total),
        add_open=request.args.get("dodaj") == "1",
    )


@app.post("/tim/dodaj")
@team_required
def add_client():
    name = request.form.get("name", "").strip()[:120]
    if not name:
        flash("Upišite ime klijenta.", "error")
        return redirect(url_for("clients", dodaj=1))
    client_id = db.insert(
        "INSERT INTO portal_clients (name, token, created_at) VALUES (?, ?, ?) RETURNING id",
        (name, secrets.token_urlsafe(16), now_iso()),
    )
    flash("Klijent je dodat. Izaberite njegove kanale i napravite mu lozinku.", "ok")
    return redirect(url_for("client_settings", client_id=client_id))


def get_client_or_404(client_id):
    client = db.query("SELECT * FROM portal_clients WHERE id = ?", (client_id,), one=True)
    if not client:
        abort(404)
    return client


def layout_choices(client, key):
    """Sta tim moze da izabere za jedan izvestaj, sa trenutnim izborom."""
    metrics, sections, charts = layout.options(key)
    current = layout.get(client, key)
    return {
        "key": key,
        "metrics": [{"value": m, "label": label, "on": m in current["m"]} for m, label in metrics],
        "sections": [{"value": v, "label": label, "on": v in current["s"]} for v, label in sections],
        "charts": [{"value": c, "label": label, "on": c == current["chart"]} for c, label in charts],
        "count": len(current["m"]) + len(current["s"]),
        "total": len(metrics) + len(sections),
    }


def set_source(client_id, platform, account_id, name, currency, visible):
    db.execute("DELETE FROM portal_sources WHERE client_id = ? AND platform = ?", (client_id, platform))
    if account_id:
        db.execute(
            "INSERT INTO portal_sources (client_id, platform, account_id, account_name, currency, visible) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (client_id, platform, account_id, name or "", currency or "", 1 if visible else 0),
        )


def save_channels(client_id, form):
    """Cuva izbor naloga po kanalu. Vraca poruke o kanalima koji nisu sacuvani."""
    current = sources_of(client_id)
    problems = []
    for mod in platforms.ADS:
        field = f"src_{mod.KEY}"
        if field not in form:
            continue  # kanal nije povezan, pa na strani nije ni bilo izbora
        chosen = form.get(field, "").strip()
        visible = bool(form.get(f"vis_{mod.KEY}"))
        existing = current.get(mod.KEY)
        if not chosen:
            set_source(client_id, mod.KEY, "", "", "", False)
            continue
        if existing and existing["account_id"] == chosen:
            set_source(client_id, mod.KEY, chosen, existing["account_name"], existing["currency"], visible)
            continue
        try:
            account = next((a for a in platforms.accounts(mod) if a["id"] == chosen), None)
        except SourceError as exc:
            problems.append(f"{mod.NAME}: lista naloga nije dostupna ({exc})")
            continue
        if not account:
            problems.append(f"{mod.NAME}: izabrani nalog nije među nalozima koje portal vidi")
            continue
        set_source(client_id, mod.KEY, account["id"], account["name"], account["currency"], visible)

    if "src_ga4" in form:
        property_id = ga4.normalize_id(form.get("src_ga4"))[:20]
        set_source(client_id, ga4.KEY, property_id, "", "", bool(form.get("vis_ga4")))
    return problems


@app.route("/tim/klijent/<int:client_id>", methods=["GET", "POST"])
@team_required
def client_settings(client_id):
    client = get_client_or_404(client_id)
    if request.method == "POST":
        conv_kind = request.form.get("conv_kind", "upit")
        if conv_kind not in fmt.CONV_KINDS:
            conv_kind = "upit"
        name = request.form.get("name", "").strip()[:120] or client["name"]
        keys = [mod.KEY for mod in platforms.ADS] + [layout.COMBINED, layout.SITE]
        db.execute(
            "UPDATE portal_clients SET name = ?, show_combined = ?, conv_kind = ?, layout = ? WHERE id = ?",
            (
                name,
                1 if request.form.get("show_combined") else 0,
                conv_kind,
                layout.from_form(client, request.form, keys),
                client_id,
            ),
        )
        problems = save_channels(client_id, request.form)
        range_problem = save_range(client_id, request.form)
        if range_problem:
            problems.append(range_problem)
        for problem in problems:
            flash(problem, "error")
        flash("Izmene su sačuvane." if not problems else "Ostale izmene su sačuvane.", "ok")
        return redirect(url_for("client_settings", client_id=client_id))

    sources = sources_of(client_id)
    channels = []
    for mod in platforms.ADS:
        channel = {
            "key": mod.KEY,
            "name": mod.NAME,
            "connected": mod.connected(),
            "source": sources.get(mod.KEY),
            "accounts": None,
            "error": None,
            "layout": layout_choices(client, mod.KEY),
        }
        if channel["connected"]:
            try:
                channel["accounts"] = platforms.accounts(mod)
            except SourceError as exc:
                channel["error"] = str(exc)
        channels.append(channel)

    has_analytics = ga4.connected()
    properties, properties_error = None, None
    if has_analytics:
        try:
            properties = ga4.list_properties()
        except SourceError as exc:
            properties_error = str(exc)

    new_password = None
    stored = session.pop("new_password", None)
    if stored and stored.get("client_id") == client_id:
        new_password = stored["password"]
    return render_template(
        "client_settings.html",
        client=client,
        combined_layout=layout_choices(client, layout.COMBINED),
        site_layout=layout_choices(client, layout.SITE),
        conv_kinds=fmt.CONV_KINDS,
        status=link_status(client),
        link=url_for("client_report", token=client["token"], _external=True),
        new_password=new_password,
        channels=channels,
        site=sources.get(ga4.KEY),
        has_analytics=has_analytics,
        properties=properties,
        properties_error=properties_error,
        window=describe_window(client),
        max_days=base.MAX_RANGE_DAYS,
    )


@app.post("/tim/klijent/<int:client_id>/lozinka")
@team_required
def client_new_password(client_id):
    get_client_or_404(client_id)
    password = make_password()
    # Nov access_key odjavljuje sve koji su usli sa starom lozinkom.
    db.execute(
        "UPDATE portal_clients SET password_hash = ?, access_key = ?, "
        "failed_attempts = 0, locked_until = 0 WHERE id = ?",
        (generate_password_hash(password), secrets.token_urlsafe(16), client_id),
    )
    # Lozinka se prikazuje samo jednom, na sledecoj strani. U bazi je samo hes.
    session["new_password"] = {"client_id": client_id, "password": password}
    return redirect(url_for("client_settings", client_id=client_id))


@app.post("/tim/klijent/<int:client_id>/link")
@team_required
def client_toggle_link(client_id):
    client = get_client_or_404(client_id)
    active = 0 if client["link_active"] else 1
    db.execute("UPDATE portal_clients SET link_active = ? WHERE id = ?", (active, client_id))
    flash("Link je uključen." if active else "Link je isključen.", "ok")
    return redirect(url_for("client_settings", client_id=client_id))


@app.post("/tim/klijent/<int:client_id>/osvezi")
@team_required
def client_refresh(client_id):
    get_client_or_404(client_id)
    for platform, src in sources_of(client_id).items():
        platforms.clear(platform, src["account_id"])
    flash("Sveži podaci se učitavaju pri sledećem otvaranju izveštaja.", "ok")
    return redirect(url_for("client_settings", client_id=client_id))


@app.post("/tim/klijent/<int:client_id>/ukloni")
@team_required
def client_remove(client_id):
    client = get_client_or_404(client_id)
    db.execute("DELETE FROM portal_sources WHERE client_id = ?", (client_id,))
    db.execute("DELETE FROM portal_clients WHERE id = ?", (client_id,))
    flash(f"{client['name']} je uklonjen sa portala. Njegov link više ne radi.", "ok")
    return redirect(url_for("clients"))


@app.get("/tim/klijent/<int:client_id>/pregled")
@team_required
def client_preview(client_id):
    client = get_client_or_404(client_id)
    return render_report(client, preview=True)


# ---------------------------------------------------------------- klijent

@app.route("/k/<token>", methods=["GET", "POST"])
def client_report(token):
    client = db.query("SELECT * FROM portal_clients WHERE token = ?", (token,), one=True)
    if not client:
        abort(404)
    if not client["password_hash"] or not client["link_active"]:
        return render_template("client_gate.html", client=None, unavailable=True), 403

    unlocked = session.get("clients", {})
    key = str(client["id"])

    if request.method == "POST":
        error = None
        now = int(time.time())
        if client["locked_until"] > now:
            minutes = (client["locked_until"] - now) // 60 + 1
            error = f"Previše pogrešnih pokušaja. Pokušajte ponovo za {minutes} min."
        elif check_password_hash(client["password_hash"], request.form.get("lozinka", "")):
            db.execute(
                "UPDATE portal_clients SET failed_attempts = 0, locked_until = 0 WHERE id = ?",
                (client["id"],),
            )
            unlocked[key] = client["access_key"]
            session["clients"] = unlocked
            session.permanent = True
            return redirect(url_for("client_report", token=token))
        else:
            register_failure(client)
            error = "Lozinka nije tačna. Proverite poruku u kojoj ste je dobili."
        return render_template("client_gate.html", client=client, error=error, unavailable=False)

    if not client["access_key"] or unlocked.get(key) != client["access_key"]:
        return render_template("client_gate.html", client=client, error=None, unavailable=False)
    return render_report(client, preview=False)


def register_failure(client):
    """Broji pogresne lozinke i zakljucava link posle MAX_ATTEMPTS."""
    attempts = client["failed_attempts"] + 1
    if attempts >= MAX_ATTEMPTS:
        db.execute(
            "UPDATE portal_clients SET failed_attempts = 0, locked_until = ? WHERE id = ?",
            (int(time.time()) + LOCK_SECONDS, client["id"]),
        )
    else:
        db.execute("UPDATE portal_clients SET failed_attempts = ? WHERE id = ?", (attempts, client["id"]))


# ---------------------------------------------------------------- period izvestaja

def parse_date(text):
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return None


def client_window(client):
    """Period koji klijent sme da gleda: (od, do, odredio_tim).

    Ako tim nije odredio period, to je poslednjih 90 dana. "Do" nikad nije
    posle juce, jer za danasnji dan jos nema potpunih brojki. Ako period jos
    nije poceo, "do" je pre "od".
    """
    start = parse_date(client["range_start"])
    if not start:
        default_start, default_end = platforms.default_range()
        return default_start, default_end, False
    end = parse_date(client["range_end"]) or base.yesterday()
    end = min(end, base.yesterday())
    # Period bez kraja raste svakog dana; cita se najvise MAX_RANGE_DAYS dana unazad.
    start = max(start, end - timedelta(days=base.MAX_RANGE_DAYS - 1))
    return start, end, True


def describe_window(client):
    """Opis perioda za stranu podesavanja."""
    start = parse_date(client["range_start"])
    end = parse_date(client["range_end"])
    if not start:
        return "Nije određen: klijent vidi poslednjih 90 dana, a izveštaj se otvara na poslednjih 30."
    if not end:
        return f"Od {fmt.date_numeric(start)} do juče, i period se sam produžava."
    if end >= base.yesterday() + timedelta(days=1):
        return (
            f"Od {fmt.date_numeric(start)} do {fmt.date_numeric(end)} "
            "Dok period traje, klijent vidi brojke zaključno sa jučerašnjim danom."
        )
    return f"Od {fmt.date_numeric(start)} do {fmt.date_numeric(end)}"


def save_range(client_id, form):
    """Cuva period koji klijent vidi. Vraca poruku o gresci ili None."""
    if "range_start" not in form:
        return None
    start_text, end_text = form.get("range_start", "").strip(), form.get("range_end", "").strip()
    start, end = parse_date(start_text), parse_date(end_text)
    if (start_text and not start) or (end_text and not end):
        return "Period: datum nije ispravan."
    if end and not start:
        return "Period: upišite i datum od kog klijent vidi izveštaj."
    if start and end and end < start:
        return "Period: datum „do“ je pre datuma „od“."
    if start and ((end or base.yesterday()) - start).days + 1 > base.MAX_RANGE_DAYS:
        return f"Period može da traje najviše {base.MAX_RANGE_DAYS} dana. Skratite ga ili upišite i datum „do“."
    db.execute(
        "UPDATE portal_clients SET range_start = ?, range_end = ? WHERE id = ?",
        (start.isoformat() if start else "", end.isoformat() if end else "", client_id),
    )
    return None


def view_range(window_start, window_end, fixed):
    """Period koji je klijent izabrao u izvestaju, uvek unutar dozvoljenog."""
    start, end = parse_date(request.args.get("od")), parse_date(request.args.get("do"))
    if not start and not end:
        if fixed:
            return window_start, window_end
        return max(window_start, window_end - timedelta(days=29)), window_end
    start = min(max(start or window_start, window_start), window_end)
    end = min(max(end or window_end, window_start), window_end)
    return (end, start) if end < start else (start, end)


def presets(window_start, window_end, fixed):
    """Brzi izbori perioda: ceo period, pa 30, 7 i 1 dan, koliko staje u period."""
    length = (window_end - window_start).days + 1
    items = [("Ceo period" if fixed else f"{length} dana", window_start)]
    for count, label in ((30, "30 dana"), (7, "7 dana"), (1, "1 dan")):
        if count < length:
            items.append((label, window_end - timedelta(days=count - 1)))
    return [(label, start, window_end) for label, start in items]


def render_report(client, preview):
    sources = sources_of(client["id"])
    tabs = report.tabs_for(client, sources)
    if not tabs:
        if preview:
            flash("Klijent još nema nijedan kanal koji mu se prikazuje. Izaberite kanale ispod.", "error")
            return redirect(url_for("client_settings", client_id=client["id"]))
        return render_template("client_gate.html", client=client, unavailable=True, no_data=True), 503

    keys = [key for key, _ in tabs]
    tab = request.args.get("kanal", "")
    if tab not in keys:
        tab = keys[0]

    if preview:
        endpoint, args = "client_preview", {"client_id": client["id"]}
    else:
        endpoint, args = "client_report", {"token": client["token"]}

    window_start, window_end, fixed = client_window(client)
    if window_end < window_start:
        # Tim je odredio period koji jos nije poceo.
        view, notes, start, end = None, [], window_start, window_start
        not_started = fmt.date_long(window_start, window_start.year != base.yesterday().year)
    else:
        start, end = view_range(window_start, window_end, fixed)
        view, notes = report.build(client, sources, tab, start, end, (window_start, window_end))
        not_started = None

    def link(**changes):
        params = {"kanal": tab, "od": start.isoformat(), "do": end.isoformat(), **changes}
        return url_for(endpoint, **params, **args)

    return render_template(
        "report.html",
        client=client,
        preview=preview,
        team_notes=notes if preview else [],
        tab=tab,
        tabs=[{"key": key, "name": name, "url": link(kanal=key)} for key, name in tabs],
        presets=[
            {"label": label, "url": link(od=s.isoformat(), do=e.isoformat()), "active": (s, e) == (start, end)}
            for label, s, e in presets(window_start, window_end, fixed)
        ]
        if not not_started
        else [],
        range_form={
            "action": url_for(endpoint, **args),
            "tab": tab,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "min": window_start.isoformat(),
            "max": window_end.isoformat(),
        },
        not_started=not_started,
        view=view,
    )


# ---------------------------------------------------------------- greske

@app.errorhandler(400)
@app.errorhandler(404)
def simple_error(err):
    return render_template("error.html", code=err.code, message=err.description), err.code


db.init_schema()
with app.app_context():
    migrate.run()
