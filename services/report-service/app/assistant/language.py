from __future__ import annotations

import re

# Swahili support for retrieval.
#
# The operational content pack is written in English, and retrieval matches on
# keywords. A Swahili question therefore scores zero against every entry and
# would always come back "unsupported" — the assistant would appear broken to
# any staff member who speaks to it in Swahili.
#
# This module closes that gap deterministically, with a fixed vocabulary map
# rather than a model. Nothing here reaches the model, nothing here changes what
# a caller is permitted to see, and expansion runs after nothing and before
# ranking only: access filtering by tenant, role, department, approval state and
# effective date is applied by the retrieval layer either way, so a translated
# term can never promote an entry the caller may not read.

# Swahili question words. These are the direct counterparts of the English
# question words already treated as stopwords, and they discriminate between
# entries no better than "how" or "where" do.
SWAHILI_STOPWORDS: frozenset[str] = frozenset(
    {
        "jinsi",
        "namna",
        "vipi",
        "wapi",
        "nini",
        "nani",
        "lini",
        "kwanini",
        "mbona",
        "kwa",
        "kama",
        "katika",
        "hii",
        "hiyo",
        "huyu",
        "yule",
        "hapa",
        "pale",
        "tafadhali",
        "naomba",
        "nataka",
        "ninataka",
        "naweza",
        "ninawezaje",
        "kuna",
        "yangu",
        "yako",
        "yake",
        "hospitali",
        "mfumo",
        "mgonjwa",
        "wagonjwa",
    }
)

# Swahili term -> the English words the content pack actually uses.
#
# Inflected and dialect forms are listed explicitly rather than stemmed, because
# a wrong stem silently changes which content a nurse is shown, and a table can
# be read and corrected by someone who speaks the language.
SWAHILI_TO_ENGLISH: dict[str, tuple[str, ...]] = {
    # People and roles
    "daktari": ("doctor", "consultation"),
    "madaktari": ("doctor", "consultation"),
    "muuguzi": ("nurse",),
    "wauguzi": ("nurse",),
    "nesi": ("nurse",),
    "mfanyakazi": ("staff",),
    "wafanyakazi": ("staff",),
    "mhudumu": ("staff",),
    "cheo": ("role",),
    "vyeo": ("role",),
    "jukumu": ("role",),
    # Reception and registration
    "mapokezi": ("reception",),
    "sajili": ("register",),
    "kusajili": ("register",),
    "usajili": ("register",),
    "andikisha": ("register",),
    "kuandikisha": ("register",),
    "tafuta": ("search",),
    "kutafuta": ("search",),
    "foleni": ("queue",),
    "mstari": ("queue",),
    "ziara": ("visit",),
    "mahudhurio": ("visit",),
    # Triage and consultation
    "uchunguzi": ("triage", "assessment"),
    "tathmini": ("triage", "assessment"),
    "kupima": ("triage", "assessment"),
    "vipimo": ("laboratory", "results", "request"),
    "kipimo": ("laboratory", "results", "request"),
    "matokeo": ("results",),
    "historia": ("history",),
    "ushauri": ("consultation",),
    "mashauriano": ("consultation",),
    # Laboratory and imaging
    "maabara": ("laboratory",),
    "sampuli": ("specimen",),
    "mionzi": ("radiology", "imaging"),
    "picha": ("radiology", "imaging"),
    "eksirei": ("radiology", "imaging"),
    "ratiba": ("schedule",),
    # Pharmacy
    "dawa": ("pharmacy", "prescription"),
    "madawa": ("pharmacy", "prescription"),
    "famasi": ("pharmacy",),
    "duka": ("pharmacy", "stock"),
    "agizo": ("prescription", "request"),
    "maagizo": ("prescription", "request"),
    "kutoa": ("dispense",),
    "stoki": ("stock",),
    "bidhaa": ("stock",),
    # Ward
    "wodi": ("ward",),
    "kitanda": ("bed", "ward"),
    "vitanda": ("bed", "ward"),
    "kulazwa": ("admission", "ward"),
    "kulaza": ("admission", "ward"),
    "ruhusa": ("discharge",),
    "kuruhusiwa": ("discharge",),
    "zamu": ("handover", "shift"),
    "makabidhiano": ("handover",),
    "wageni": ("visitors",),
    "mtembeleaji": ("visitors",),
    # Billing
    "malipo": ("payment", "billing"),
    "lipa": ("payment", "billing"),
    "kulipa": ("payment", "billing"),
    "bili": ("bill", "billing"),
    "ankara": ("bill", "invoice"),
    "mapato": ("revenue",),
    "muhtasari": ("summary",),
    "jumla": ("summary", "totals"),
    # Reports and administration
    "ripoti": ("report", "reports"),
    "taarifa": ("report", "reports"),
    "utawala": ("administration",),
    "mipangilio": ("settings", "administration"),
    # Screen verbs and nouns
    "fungua": ("open",),
    "kufungua": ("open",),
    "bonyeza": ("click", "select"),
    "chagua": ("select",),
    "kuchagua": ("select",),
    "hifadhi": ("save",),
    "kuhifadhi": ("save",),
    "ukurasa": ("page", "screen"),
    "skrini": ("screen",),
    "orodha": ("list",),
    "ingia": ("open", "sign"),
}

_WORD = re.compile(r"[a-z0-9]+")

# Verb prefixes that commonly front a Swahili infinitive or subject form. They
# are stripped only as a fallback, and only when the stripped form is itself a
# known term, so an unrelated word can never be mangled into a match.
_PREFIXES: tuple[str, ...] = ("ku", "ni", "wa", "ya", "vi", "mi")


def _lookup(token: str) -> tuple[str, ...]:
    direct = SWAHILI_TO_ENGLISH.get(token)
    if direct:
        return direct
    for prefix in _PREFIXES:
        if token.startswith(prefix) and len(token) > len(prefix) + 2:
            stripped = SWAHILI_TO_ENGLISH.get(token[len(prefix) :])
            if stripped:
                return stripped
    return ()


def expand_query(query: str) -> str:
    """Append the English equivalents of any Swahili terms in a query.

    The original wording is kept, so an English or code-mixed question is
    unaffected and a question containing both languages gains from both. The
    result is used for keyword matching only; it is never shown to a user, never
    stored, and never sent to the model.
    """
    if not query or not isinstance(query, str):
        return query or ""

    additions: list[str] = []
    seen: set[str] = set()
    for token in _WORD.findall(query.lower()):
        for english in _lookup(token):
            if english not in seen:
                seen.add(english)
                additions.append(english)

    if not additions:
        return query
    return f"{query} {' '.join(additions)}"


def contains_swahili(text: str) -> bool:
    """Return whether a query carries recognisable Swahili vocabulary.

    Used for diagnostics only. It never gates access and never changes which
    content is retrieved.
    """
    if not text or not isinstance(text, str):
        return False
    for token in _WORD.findall(text.lower()):
        if token in SWAHILI_STOPWORDS or _lookup(token):
            return True
    return False
