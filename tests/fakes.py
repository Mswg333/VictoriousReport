"""Lazne platforme za testove: Google, Meta, Microsoft i TikTok.

Nista odavde ne izlazi na mrezu. Brojke su namerno jednostavne (svaki dan
isto), da ocekivani zbirovi mogu da se izracunaju napamet:

    kanal        dnevno: potroseno / prikazi / klikovi / konverzije
    Google Ads   30 EUR / 4000 / 50 / 1,5      (dve kampanje)
    Meta Ads      5 EUR / 2000 / 20 / 2 upita ili 1 kupovina
    Microsoft     2 EUR /  500 / 10 / 1
    TikTok        3 USD / 4000 / 30 / 0
"""
import io
import json
import time
import re
import zipfile
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import requests

import google_auth
from platforms import base, google_ads, meta_ads, microsoft_ads, tiktok_ads

YESTERDAY = date.today() - timedelta(days=1)


class FakeResponse:
    def __init__(self, status, payload=None, content=b""):
        self.status_code, self._payload, self.content = status, payload, content

    def json(self):
        if self._payload is None:
            raise ValueError("nije JSON")
        return self._payload


def days_back(count, end=None):
    end = end or YESTERDAY
    return [(end - timedelta(days=i)).isoformat() for i in range(count)]


def days_between(start, end):
    return days_back((end - start).days + 1, end)


class FakeNet:
    """Zamena za biblioteku requests: jedna funkcija request() za sve pozive."""

    RequestException = requests.RequestException

    def __init__(self):
        self.calls = []            # (metod, adresa)
        self.gaql = []             # Google Ads upiti
        self.down = set()          # kanali koji "ne rade": google_ads, meta, microsoft, tiktok
        self.ms_polls = 0
        self.ms_refresh_tokens = ["ms-rt-1"]
        self.tiktok_ranges = []
        self.ranges = []           # (kanal, od, do) za svaki zahtev za dnevne brojke
        self.reach_calls = []
        self.ranking_calls = 0
        self.ga4_behavior_calls = 0
        self.ga4_no_ecommerce = False
        self.meta_used = []        # (token, putanja) za svaki Meta poziv
        self.meta_extended = []    # tokeni koji su produzeni      # (nivo, od) za svaki zahtev za doseg (Meta)
        self.ms_range = None

    def count(self, part):
        return sum(1 for _, url in self.calls if part in url)

    def request(self, method, url, headers=None, params=None, json=None, data=None, timeout=None, **kwargs):
        self.calls.append((method, url))
        headers, params = headers or {}, params or {}
        host = urlparse(url).netloc
        if host in ("oauth2.googleapis.com", "openidconnect.googleapis.com"):
            return self._google_oauth(url, headers, data)
        if host == "googleads.googleapis.com":
            return self._google_ads(url, headers, json)
        if host in ("analyticsdata.googleapis.com", "analyticsadmin.googleapis.com"):
            return self._analytics(url, headers, json)
        if host == "graph.facebook.com":
            return self._meta(url, headers, params)
        if host == "login.microsoftonline.com":
            return self._microsoft_oauth(data)
        if host in ("clientcenter.api.bingads.microsoft.com", "reporting.api.bingads.microsoft.com"):
            return self._microsoft(url, headers, json)
        if host == "download.example.microsoft.test":
            return self._microsoft_download()
        if host == "business-api.tiktok.com":
            return self._tiktok(method, url, headers, params, json)
        raise AssertionError(f"neocekivan poziv: {method} {url}")

    # ------------------------------------------------------------ Google

    def _google_oauth(self, url, headers, data):
        if url == google_auth.REVOKE_URL:
            return FakeResponse(200, {})
        if url == google_auth.USERINFO_URL:
            if headers["Authorization"] == "Bearer at-outsider":
                return FakeResponse(200, {"email": "neko@gmail.com", "email_verified": True, "name": "Neko"})
            return FakeResponse(
                200,
                {"email": "nenad@victorious.rs", "email_verified": True, "hd": "victorious.rs", "name": "Nenad Test"},
            )
        assert url == google_auth.TOKEN_URL, url
        if data["grant_type"] == "refresh_token":
            assert data["refresh_token"] == "rt-secret"
            return FakeResponse(200, {"access_token": "at-data", "expires_in": 3600})
        full = "openid email profile " + google_auth.SCOPE_ADS + " " + google_auth.SCOPE_ANALYTICS
        answers = {
            "code-outsider": {"access_token": "at-outsider", "scope": "openid email profile"},
            "code-login": {"access_token": "at-login", "scope": "openid email profile"},
            "code-connect": {"access_token": "at-login", "refresh_token": "rt-secret", "scope": full},
            "code-connect-nothing": {"access_token": "at-login", "refresh_token": "rt-x", "scope": "openid email profile"},
        }
        if data["code"] in answers:
            return FakeResponse(200, answers[data["code"]])
        return FakeResponse(400, {"error": "invalid_grant", "error_description": "Bad Request"})

    def _google_ads(self, url, headers, payload):
        assert headers["Authorization"] == "Bearer at-data", headers
        assert headers.get("login-customer-id") == (google_ads.MCC_ID or None), headers
        assert "developer-token" not in headers
        query = payload["query"]
        self.gaql.append(query)
        customer = re.search(r"/customers/(\d+)/", url).group(1)
        if "google_ads" in self.down:
            return FakeResponse(503, {"error": {"message": "The service is currently unavailable."}})
        names = {"1112045501": ("Nameštaj Hrast", "EUR"), "2223187742": ("Klinika Vita", "RSD")}
        if "FROM customer LIMIT 1" in query:
            name, currency = names[customer]
            return FakeResponse(
                200, {"results": [{"customer": {"id": customer, "descriptiveName": name, "currencyCode": currency}}]}
            )
        if "FROM customer_client" in query:
            assert customer == google_ads.MCC_ID
            return FakeResponse(
                200,
                {
                    "results": [
                        {"customerClient": {"id": cid, "descriptiveName": name, "currencyCode": currency}}
                        for cid, (name, currency) in {**names, "6543737887": ("Ugašen nalog", "EUR")}.items()
                    ]
                },
            )
        if customer == "6543737887":
            return FakeResponse(
                403,
                {
                    "error": {
                        "message": "The caller does not have permission",
                        "details": [
                            {"errors": [{"message": "The customer account can't be accessed because it is not yet enabled or has been deactivated."}]}
                        ],
                    }
                },
            )
        if "FROM campaign" in query:
            first, last = re.search(r"BETWEEN '([\d-]+)' AND '([\d-]+)'", query).groups()
            first, last = date.fromisoformat(first), date.fromisoformat(last)
            assert last <= YESTERDAY, query
            self.ranges.append(("google_ads", first, last))
            rows = []
            for day in days_between(first, last):
                rows.append(
                    {
                        "campaign": {"id": "1", "name": "Pretraga: kuhinje"},
                        "segments": {"date": day},
                        "metrics": {
                            "costMicros": "20000000", "impressions": "1000", "clicks": "40",
                            "conversions": 1.5, "conversionsValue": 120.0,
                        },
                    }
                )
                rows.append(
                    {
                        "campaign": {"id": "2", "name": "Performance Max"},
                        "segments": {"date": day},
                        # Google izostavlja polja koja su nula
                        "metrics": {"costMicros": "10000000", "impressions": "3000", "clicks": "10"},
                    }
                )
            return FakeResponse(200, {"results": rows})
        if "FROM search_term_view" in query:
            return FakeResponse(
                200,
                {
                    "results": [
                        {"searchTermView": {"searchTerm": "kuhinje po meri"}, "metrics": {"clicks": "30"}},
                        {"searchTermView": {"searchTerm": "kuhinje po meri"}, "metrics": {"clicks": "12"}},
                        {"searchTermView": {"searchTerm": "ormari cena"}, "metrics": {"clicks": "9"}},
                    ]
                },
            )
        raise AssertionError("neocekivan upit " + query)

    def _analytics(self, url, headers, payload):
        assert headers["Authorization"] == "Bearer at-data", headers
        if url.endswith("/accountSummaries"):
            return FakeResponse(
                200,
                {
                    "accountSummaries": [
                        {
                            "displayName": "Hrast",
                            "propertySummaries": [{"property": "properties/555000111", "displayName": "hrast.rs"}],
                        }
                    ]
                },
            )
        assert url.endswith("/properties/555000111:batchRunReports"), url
        reports = payload["requests"]
        if reports[0].get("dimensions") == [{"name": "eventName"}]:
            return self._analytics_behavior(reports)
        assert len(reports) == 4 and reports[0]["dateRanges"][0]["endDate"] <= YESTERDAY.isoformat()
        start = date.fromisoformat(reports[0]["dateRanges"][0]["startDate"])
        end = date.fromisoformat(reports[0]["dateRanges"][0]["endDate"])
        days = (end - start).days + 1

        def row(label, *values):
            return {"dimensionValues": [{"value": label}], "metricValues": [{"value": str(v)} for v in values]}

        return FakeResponse(
            200,
            {
                "reports": [
                    {"rows": [{"metricValues": [{"value": str(v)} for v in (
                        41 * days, 30 * days, "134.6", 20 * days, "0.625", 70 * days, 2 * days)]}]},
                    {"rows": [row(day.replace("-", ""), 41, 30, 2) for day in days_between(start, end)]},
                    {"rows": [row("Paid Search", 500), row("Organic Search", 400), row("Nepoznat kanal", 1)]},
                    {"rows": [row("/", 300), row("/kontakt", 45)]},
                ]
            },
        )

    def _analytics_behavior(self, reports):
        """Ponasanje posetilaca: put do kupovine, proizvodi, dogadjaji, ulazne strane."""
        self.ga4_behavior_calls += 1
        assert len(reports) == 4
        assert reports[0]["dimensionFilter"]["filter"]["inListFilter"]["values"][-1] == "purchase"
        assert [m["name"] for m in reports[1]["metrics"]] == ["itemsViewed", "itemsAddedToCart", "itemsPurchased"]
        assert reports[3]["dimensions"] == [{"name": "landingPage"}]

        def row(label, *values):
            return {"dimensionValues": [{"value": label}], "metricValues": [{"value": str(v)} for v in values]}

        funnel = [] if self.ga4_no_ecommerce else [
            row("view_item", 400), row("add_to_cart", 120), row("begin_checkout", 60),
            row("add_payment_info", 40), row("purchase", 25),
        ]
        products = [] if self.ga4_no_ecommerce else [
            row("Kuhinja Hrast", 300, 90, 20), row("(not set)", 50, 1, 0), row("Sto Bor", 100, 2, 0),
        ]
        return FakeResponse(200, {"reports": [
            {"rows": funnel},
            {"rows": products},
            {"rows": [row("page_view", 5000, 900), row("scroll", 900, 500), row("click", 340, 200),
                      row("form_submit", 45, 40), row("poziv_telefon", 30, 28)]},
            {"rows": [row("/", 600, "0.35"), row("/akcija", 200, "0.72"), row("/blog/stari-tekst", 5, "1")]},
        ]})

    # ------------------------------------------------------------ Meta

    def _meta(self, url, headers, params):
        path, query = urlparse(url).path, parse_qs(urlparse(url).query)
        # Pozivi bez korisnickog tokena: prijava preko Facebook-a i provera roka.
        if path.endswith("/oauth/access_token"):
            assert params["client_id"] == "meta-app" and params["client_secret"] == "meta-secret"
            if params.get("code"):
                if params["code"] != "fb-code":
                    return FakeResponse(400, {"error": {"message": "This authorization code has expired.", "code": 100}})
                return FakeResponse(200, {"access_token": "short-2", "token_type": "bearer"})
            assert params["grant_type"] == "fb_exchange_token"
            self.meta_extended.append(params["fb_exchange_token"])
            assert params["fb_exchange_token"] == "short-2"
            return FakeResponse(200, {"access_token": "meta-token-2", "token_type": "bearer", "expires_in": 5184000})
        if path.endswith("/debug_token"):
            assert params["access_token"] == "meta-app|meta-secret"
            expires = 0 if params["input_token"] == "meta-token" else int(time.time()) + 3600
            if params["input_token"] == "meta-token-2":
                expires = int(time.time()) + 5184000
            return FakeResponse(200, {"data": {"is_valid": True, "expires_at": expires}})
        token = headers["Authorization"].split()[1]
        self.meta_used.append((token, path))
        if token == "meta-token-2" and "meta" not in self.down:
            return self._meta_colleague(path, params)
        if token != "meta-token" or "meta" in self.down:
            return FakeResponse(400, {"error": {"message": "Invalid OAuth access token.", "code": 190}})
        assert path.startswith("/" + meta_ads.API_VERSION + "/"), path
        if path.endswith("/me"):
            return FakeResponse(200, {"name": "Portal sistemski korisnik", "id": "1"})
        if path.endswith("/me/adaccounts"):
            return FakeResponse(
                200,
                {"data": [{"id": "act_9001", "account_id": "9001", "name": "Hrast Meta", "currency": "EUR", "account_status": 1}]},
            )
        if "/act_9002" in path:  # kolegin nalog: ovaj token ga ne vidi
            return FakeResponse(400, {"error": {"message": "Unsupported get request.", "code": 100}})
        if path.endswith("/act_9001"):
            return FakeResponse(200, {"account_id": "9001", "id": "act_9001"})
        if path.endswith("/act_9001/campaigns"):
            assert "objective" in params["fields"]
            return FakeResponse(
                200, {"data": [{"id": "51", "objective": "OUTCOME_LEADS", "daily_budget": "1000"}]}
            )
        assert path.endswith("/act_9001/insights"), path
        if params and params.get("level") == "ad":  # ocene oglasa
            self.ranking_calls += 1
            return FakeResponse(200, {"data": [
                {"ad_id": "1", "ad_name": "Video: nova kuhinja", "impressions": "9000", "quality_ranking": "ABOVE_AVERAGE",
                 "engagement_rate_ranking": "AVERAGE", "conversion_rate_ranking": "BELOW_AVERAGE_35"},
                {"ad_id": "2", "ad_name": "Slika: malo prikaza", "impressions": "50", "quality_ranking": "UNKNOWN",
                 "engagement_rate_ranking": "UNKNOWN", "conversion_rate_ranking": "UNKNOWN"},
            ]})
        if "after" in query:  # druga strana
            since, until = date.fromisoformat(query["since"][0]), date.fromisoformat(query["until"][0])
            all_days = days_between(since, until)
            days = all_days[len(all_days) // 2:]
            paging = {"cursors": {"after": "x"}}
        elif "time_increment" not in params:  # doseg za ceo period
            self.reach_calls.append((params["level"], json.loads(params["time_range"])["since"]))
            if params["level"] == "account":
                return FakeResponse(200, {"data": [{"reach": "18000", "frequency": "3.3333"}]})
            return FakeResponse(200, {"data": [{"campaign_id": "51", "reach": "18000", "frequency": "3.3333"}]})
        else:
            assert params["level"] == "campaign" and params["time_increment"] == 1
            time_range = json.loads(params["time_range"])
            since, until = date.fromisoformat(time_range["since"]), date.fromisoformat(time_range["until"])
            assert until <= YESTERDAY
            self.ranges.append(("meta", since, until))
            all_days = days_between(since, until)
            days = all_days[: len(all_days) // 2]
            paging = {
                "next": f"https://graph.facebook.com/{meta_ads.API_VERSION}/act_9001/insights"
                f"?after=abc&access_token=x&since={since}&until={until}"
            }
        rows = [
            {
                "campaign_id": "51",
                "campaign_name": "Leadovi: kuhinje",
                "date_start": day,
                "date_stop": day,
                "spend": "5.00",
                "impressions": "2000",
                "inline_link_clicks": "20",
                "clicks": "35",
                "actions": [
                    {"action_type": "link_click", "value": "20"},
                    {"action_type": "landing_page_view", "value": "16"},
                    {"action_type": "lead", "value": "2"},
                    {"action_type": "omni_purchase", "value": "1"},
                ],
                "action_values": [{"action_type": "omni_purchase", "value": "40.00"}],
            }
            for day in days
        ]
        return FakeResponse(200, {"data": rows, "paging": paging})

    def _meta_colleague(self, path, params):
        """Drugi clan tima: vidi samo svoj oglasni nalog 9002."""
        if path.endswith("/me"):
            return FakeResponse(200, {"id": "777", "name": "Kolega Petar"})
        if path.endswith("/me/adaccounts"):
            return FakeResponse(200, {"data": [
                {"id": "act_9002", "account_id": "9002", "name": "Druga firma Meta", "currency": "EUR", "account_status": 1}
            ]})
        if path.endswith("/act_9002"):
            return FakeResponse(200, {"account_id": "9002", "id": "act_9002"})
        if path.endswith("/act_9002/campaigns"):
            return FakeResponse(200, {"data": [{"id": "91", "objective": "OUTCOME_TRAFFIC"}]})
        if path.endswith("/act_9002/insights"):
            time_range = json.loads(params["time_range"])
            since, until = date.fromisoformat(time_range["since"]), date.fromisoformat(time_range["until"])
            return FakeResponse(200, {"data": [
                {"campaign_id": "91", "campaign_name": "Saobraćaj: prodavnica", "date_start": day, "date_stop": day,
                 "spend": "1.00", "impressions": "100", "inline_link_clicks": "4", "clicks": "6", "actions": []}
                for day in days_between(since, until)
            ]})
        return FakeResponse(400, {"error": {"message": "Unsupported get request.", "code": 100}})

    # ------------------------------------------------------------ Microsoft

    def _microsoft_oauth(self, data):
        assert data["client_id"] == microsoft_ads.CLIENT_ID and data["client_secret"] == microsoft_ads.CLIENT_SECRET
        if data["grant_type"] == "authorization_code":
            if data["code"] != "ms-code":
                return FakeResponse(400, {"error": "invalid_grant", "error_description": "AADSTS70008: expired.\r\nTrace ID: 1"})
            return FakeResponse(200, {"access_token": "ms-at", "refresh_token": "ms-rt-1", "expires_in": 3600})
        # Microsoft pri svakom obnavljanju izdaje nov refresh token.
        assert data["refresh_token"] == self.ms_refresh_tokens[-1], "mora se koristiti poslednji izdat token"
        self.ms_refresh_tokens.append(f"ms-rt-{len(self.ms_refresh_tokens) + 1}")
        return FakeResponse(200, {"access_token": "ms-at", "refresh_token": self.ms_refresh_tokens[-1], "expires_in": 3600})

    def _microsoft(self, url, headers, payload):
        assert headers["Authorization"] == "Bearer ms-at" and headers["DeveloperToken"] == microsoft_ads.DEVELOPER_TOKEN
        if "microsoft" in self.down:
            return FakeResponse(500, {"OperationErrors": [{"Code": 0, "Message": "Internal service error."}]})
        if url.endswith("/User/Query"):
            return FakeResponse(200, {"User": {"Id": "4242", "UserName": "nenad@victorious.rs"}})
        if url.endswith("/Accounts/Search"):
            assert payload["Predicates"] == [{"Field": "UserId", "Operator": "Equals", "Value": "4242"}]
            return FakeResponse(
                200,
                {
                    "Accounts": [
                        {"Id": "8001", "Name": "Hrast Bing", "Number": "X8001", "ParentCustomerId": "777",
                         "CurrencyCode": "EUR", "AccountLifeCycleStatus": "Active"},
                        {"Id": "8002", "Name": "Stari nalog", "Number": "X8002", "ParentCustomerId": "777",
                         "CurrencyCode": "EUR", "AccountLifeCycleStatus": "Inactive"},
                    ]
                },
            )
        assert headers["CustomerId"] == "777" and headers["CustomerAccountId"] == "8001", headers
        if url.endswith("/GenerateReport/Submit"):
            request = payload["ReportRequest"]
            assert request["Type"] == "CampaignPerformanceReportRequest" and request["Aggregation"] == "Daily"
            assert request["Scope"] == {"AccountIds": [8001]}
            first, last = request["Time"]["CustomDateRangeStart"], request["Time"]["CustomDateRangeEnd"]
            first = date(first["Year"], first["Month"], first["Day"])
            last = date(last["Year"], last["Month"], last["Day"])
            assert last <= YESTERDAY
            self.ms_range = (first, last)
            self.ranges.append(("microsoft", first, last))
            self.ms_polls = 0
            return FakeResponse(200, {"ReportRequestId": "req-1"})
        assert url.endswith("/GenerateReport/Poll") and payload == {"ReportRequestId": "req-1"}, url
        self.ms_polls += 1
        if self.ms_polls == 1:
            return FakeResponse(200, {"ReportRequestStatus": {"ReportDownloadUrl": None, "Status": "Pending"}})
        return FakeResponse(
            200,
            {"ReportRequestStatus": {"ReportDownloadUrl": "https://download.example.microsoft.test/r.zip", "Status": "Success"}},
        )

    def _microsoft_download(self):
        lines = ['"TimePeriod","CampaignId","CampaignName","Spend","Impressions","Clicks","Conversions","Revenue"']
        lines += [f'"{day}","61","Bing: kuhinje","2.00","500","10","1.00","5.00"' for day in days_between(*self.ms_range)]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("report.csv", "﻿" + "\r\n".join(lines) + "\r\n")
        return FakeResponse(200, content=buffer.getvalue())

    # ------------------------------------------------------------ TikTok

    def _tiktok(self, method, url, headers, params, payload):
        path = urlparse(url).path

        def ok(data):
            return FakeResponse(200, {"code": 0, "message": "OK", "data": data})

        if path.endswith("/oauth2/access_token/"):
            assert method == "POST" and payload["app_id"] == tiktok_ads.APP_ID and payload["secret"] == tiktok_ads.APP_SECRET
            if payload["auth_code"] not in ("tt-code", "tt-code-rucno"):
                return FakeResponse(200, {"code": 40105, "message": "Auth code is invalid or expired.", "data": {}})
            return ok({"access_token": "tt-token", "advertiser_ids": ["7001"], "scope": [4]})
        if headers.get("Access-Token") != "tt-token" or "tiktok" in self.down:
            return FakeResponse(200, {"code": 40105, "message": "Access token is incorrect or has been revoked.", "data": {}})
        if path.endswith("/oauth2/advertiser/get/"):
            assert params["app_id"] == tiktok_ads.APP_ID and params["secret"] == tiktok_ads.APP_SECRET
            return ok({"list": [{"advertiser_id": "7001", "advertiser_name": "Hrast TikTok"}]})
        if path.endswith("/advertiser/info/"):
            assert json.loads(params["advertiser_ids"]) == ["7001"]
            return ok({"list": [{"advertiser_id": "7001", "name": "Hrast TikTok", "currency": "USD"}]})
        assert path.endswith("/report/integrated/get/"), path
        assert params["advertiser_id"] == "7001" and params["data_level"] == "AUCTION_CAMPAIGN"
        assert json.loads(params["dimensions"]) == ["campaign_id", "stat_time_day"]
        start, end = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
        assert (end - start).days < 30, "najvise 30 dana po zahtevu"
        self.tiktok_ranges.append((start, end))
        self.ranges.append(("tiktok", start, end))
        rows = [
            {
                "dimensions": {"campaign_id": "71", "stat_time_day": f"{day} 00:00:00"},
                "metrics": {"campaign_name": "TikTok: video", "spend": "3.00", "impressions": "4000", "clicks": "30", "conversion": "0",
                            "complete_payment_roas": "2"},
            }
            for day in days_back((end - start).days + 1, end)
        ]
        return ok({"list": rows, "page_info": {"page": 1, "page_size": 1000, "total_number": len(rows), "total_page": 1}})


def install():
    """Zamenjuje mrezne pozive u aplikaciji laznim platformama i vraca ih."""
    fake = FakeNet()
    base.requests = fake
    microsoft_ads._sleep = lambda seconds: None  # bez cekanja u testovima
    return fake
