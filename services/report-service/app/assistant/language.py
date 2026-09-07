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
        "nifanyeje",
        "nifanye",
        "nitajuaje",
        "nitafanyaje",
        "ninaweza",
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
    "taarifa": ("report", "reports", "information"),
    "utawala": ("administration",),
    "mipangilio": ("settings", "administration"),
    # Account, access and passwords
    #
    # These were all missing, and their absence was not cosmetic: the assistant
    # would print "I can help you change your password" in its own capability
    # list and then fail to answer "Ninawezaje kubadilisha nywila yangu?",
    # because not one word of that sentence reached the content pack.
    "nywila": ("password",),
    "nenosiri": ("password",),
    "neno": ("password",),
    "badilisha": ("change", "password"),
    "kubadilisha": ("change", "password"),
    "mabadiliko": ("change",),
    "akaunti": ("account",),
    "kuingia": ("sign", "login", "open"),
    "kutoka": ("sign", "logout"),
    "ruhusiwa": ("authorised", "permission", "access"),
    "hauruhusiwi": ("authorised", "permission", "access"),
    "idhini": ("permission", "access"),
    "msaada": ("help",),
    "kusaidia": ("help",),
    # Words the Swahili example questions actually use.
    #
    # Every one of these was found by asserting that each Swahili example
    # question reaches the same entry or metric its English twin does. Without
    # them the questions parsed as Swahili, were answered in Swahili, and matched
    # nothing - which is the exact failure a nurse reported.
    "muda": ("time", "wait", "duration"),
    "kusubiri": ("wait", "waiting", "queue"),
    "subiri": ("wait", "waiting"),
    "kuzunguka": ("navigate", "around", "way", "find"),
    "njia": ("way", "navigation"),
    "msaidizi": ("assistant",),
    "naruhusiwa": ("access", "permission", "allowed"),
    "ninaruhusiwa": ("access", "permission", "allowed"),
    "waliolazwa": ("admitted", "admission", "ward"),
    "aliyelazwa": ("admitted", "admission", "ward"),
    "zimeisha": ("out", "stock", "finished"),
    "imeisha": ("out", "stock", "finished"),
    "ujumla": ("overall", "summary", "total"),
    "hali": ("status", "position", "state"),
    # Urgency
    "dharura": ("emergency", "urgent"),
    "haraka": ("urgent",),
    "hatari": ("critical", "urgent"),
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


# Object infixes that sit between the infinitive "ku" and the verb stem.
#
# Swahili puts the object inside the verb: "kusajili" is to register, but
# "kumsajili" is to register *him or her*, and "kumpima" is to assess *them*.
# That single letter defeated the prefix stripper above, so "Ninawezaje
# kumpima mgonjwa?" - how do I assess a patient, the commonest question a triage
# nurse has - matched no content at all and was answered with a list of things
# the assistant could do instead. "kupima" was in the map the whole time.
_OBJECT_INFIXES: tuple[str, ...] = (
    "m", "mu", "wa", "ni", "tu", "ki", "vi", "li", "zi", "ya", "i", "u",
)

# Every mapped term, also indexed by its bare stem, so a term stored with its
# infinitive ("kupima") is still found once the infinitive has been stripped off
# to get at an infix ("ku" + "m" + "pima").
_STEM_INDEX: dict[str, tuple[str, ...]] = {}
for _term, _english in SWAHILI_TO_ENGLISH.items():
    _STEM_INDEX.setdefault(_term, _english)
    if _term.startswith("ku") and len(_term) > 4:
        _STEM_INDEX.setdefault(_term[2:], _english)


def _lookup(token: str) -> tuple[str, ...]:
    direct = SWAHILI_TO_ENGLISH.get(token)
    if direct:
        return direct
    for prefix in _PREFIXES:
        if token.startswith(prefix) and len(token) > len(prefix) + 2:
            stripped = SWAHILI_TO_ENGLISH.get(token[len(prefix) :])
            if stripped:
                return stripped

    # "ku" + optional object infix + stem. Only a result that is itself a known
    # term is accepted, so an unrelated word cannot be mangled into a match: the
    # stripping is a way of asking the table a second question, never a guess.
    if token.startswith("ku") and len(token) > 5:
        rest = token[2:]
        hit = _STEM_INDEX.get(rest)
        if hit:
            return hit
        for infix in _OBJECT_INFIXES:
            if rest.startswith(infix) and len(rest) > len(infix) + 2:
                hit = _STEM_INDEX.get(rest[len(infix) :])
                if hit:
                    return hit
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


# Swahili function words used *only* to decide which language to answer in.
#
# Deliberately separate from SWAHILI_STOPWORDS, which retrieval merges into its
# own stopword set and which routing therefore tokenises against. A word added
# there stops being a term at all, so putting "kiasi" or "bado" in that list
# would silently disable the billing and laboratory triggers that depend on
# them. These words influence nothing but the language of the reply.
#
# They are the ordinary scaffolding of a spoken question - "ni kiasi gani",
# "vipimo vingapi", "watu wangapi" - which is why a question can be entirely
# Swahili and still contain no term the vocabulary map translates. "Ni kiasi
# gani bado hakijalipwa?" was answered in English for exactly that reason.
_SWAHILI_FUNCTION_WORDS: frozenset[str] = frozenset(
    {
        "ni", "gani", "kiasi", "bado", "ngapi", "vingapi", "wangapi",
        "zipi", "ipi", "yupi", "sasa", "je",
        # "vipi" and "wapi" are not repeated here: SWAHILI_STOPWORDS already
        # carries them, and contains_swahili checks both sets.
        # "leo" is deliberately absent: it is Swahili for today, but it is
        # also an ordinary English name, and "where is Leo's record" is not a
        # Swahili question. Routing reads it as a date term either way.
        "kwenye", "ili", "lakini", "pia", "sana", "tena", "bila",
        "hakuna", "kila", "wote", "zote", "yote", "hizi", "hao",
    }
)


def contains_swahili(text: str) -> bool:
    """Return whether a query carries recognisable Swahili vocabulary.

    Used for diagnostics, and to tell the model which language to answer in.
    Both are presentation: it never gates access, never changes which content is
    retrieved, and never changes which figures run. Being wrong about it can only
    produce an answer in the wrong language, never an answer somebody should not
    have seen.
    """
    if not text or not isinstance(text, str):
        return False
    for token in _WORD.findall(text.lower()):
        if (
            token in SWAHILI_STOPWORDS
            or token in _SWAHILI_FUNCTION_WORDS
            or _lookup(token)
        ):
            return True
    return False
