"""Ispis brojeva, novca, datuma i mnozine na srpskom."""

MONTHS = ["jan", "feb", "mar", "apr", "maj", "jun", "jul", "avg", "sep", "okt", "nov", "dec"]
CURRENCY_SIGNS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF", "RSD": "RSD"}

CLICK_FORMS = ("klik", "klika", "klikova")
VISIT_FORMS = ("poseta", "posete", "poseta")        # 21 poseta
VISIT_FORMS_OBJECT = ("posetu", "posete", "poseta")  # sajt je imao 21 posetu
IMPRESSION_FORMS = ("prikaz", "prikaza", "prikaza")
TIMES_FORMS = ("put", "puta", "puta")
USER_FORMS = ("posetilac", "posetioca", "posetilaca")
KEY_EVENT_FORMS = ("konverzija", "konverzije", "konverzija")
KEY_EVENT_FORMS_OBJECT = ("konverziju", "konverzije", "konverzija")

# Kako se konverzije zovu u izvestaju klijenta.
CONV_KINDS = {
    "upit": {
        "forms": ("upit", "upita", "upita"),
        "forms_nom": ("upit", "upita", "upita"),
        "column": "Upiti",
        "rate": "Klikova koji su doneli upit",
        "value": "Vrednost upita",
        "per": "po upitu",
        "cost": "Cena po upitu",
        "none": "nema upita",
        "option": "Upiti (pozivi, poruke, popunjene forme)",
    },
    "kupovina": {
        "forms": ("kupovinu", "kupovine", "kupovina"),
        "forms_nom": ("kupovina", "kupovine", "kupovina"),
        "column": "Kupovine",
        "rate": "Klikova koji su doneli kupovinu",
        "value": "Vrednost kupovina",
        "per": "po kupovini",
        "cost": "Cena po kupovini",
        "none": "nema kupovina",
        "option": "Kupovine (online prodaja)",
    },
}


def is_whole(value):
    return abs(value - round(value)) < 0.05


def plural(n, forms):
    """Srpska mnozina: forms = (1 upit, 2 upita, 5 upita)."""
    if not is_whole(n):
        return forms[1]  # 12,5 upita
    n = abs(int(round(n)))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return forms[1]
    return forms[2]


def day_label(d):
    return f"{d.day}. {MONTHS[d.month - 1]}"


def num(value, decimals=0):
    """1234.5 -> 1.234,50 (srpski zapis)."""
    text = f"{value:,.{decimals}f}"
    return text.replace(",", " ").replace(".", ",").replace(" ", ".")


def conv(value):
    """Konverzije umeju da budu razlomljene (12,5); cele se pisu bez decimala."""
    return num(value) if is_whole(value) else num(value, 1)


def money(value, currency, decimals=0):
    return f"{num(value, decimals)} {CURRENCY_SIGNS.get(currency, currency)}"


def money_sum(by_currency, decimals=0):
    """{"EUR": 900, "RSD": 500} -> "900 € i 500 RSD". Valute se nikad ne sabiraju."""
    parts = [money(amount, currency, decimals) for currency, amount in sorted(by_currency.items())]
    return " i ".join(parts)


def percent(share, decimals=1):
    """0.0345 -> 3,5 %"""
    return f"{num(share * 100, decimals)} %"


def duration(seconds):
    return f"{int(seconds) // 60} min {int(seconds) % 60} s"


MONTHS_GENITIVE = [
    "januara", "februara", "marta", "aprila", "maja", "juna",
    "jula", "avgusta", "septembra", "oktobra", "novembra", "decembra",
]


def date_long(d, with_year=False):
    """15. januara (ili 15. januara 2026.)"""
    text = f"{d.day}. {MONTHS_GENITIVE[d.month - 1]}"
    return f"{text} {d.year}." if with_year else text


def date_numeric(d):
    """15.01.2026."""
    return f"{d.day:02d}.{d.month:02d}.{d.year}."


def period_phrase(start, end, yesterday):
    """Pocetak recenice za period: "U poslednjih 30 dana", "Od 1. do 31. januara"...

    Godina se pise samo kad nije tekuca ili kad period prelazi u drugu godinu.
    """
    days = (end - start).days + 1
    if end == yesterday:
        return "Juče" if days == 1 else f"U poslednjih {days} dana"
    if start == end:
        return f"Dana {date_long(start, start.year != yesterday.year)}"
    if start.year != end.year:
        return f"Od {date_long(start, True)} do {date_long(end, True)}"
    with_year = end.year != yesterday.year
    if start.month == end.month:
        return f"Od {start.day}. do {date_long(end, with_year)}"
    return f"Od {date_long(start)} do {date_long(end, with_year)}"
