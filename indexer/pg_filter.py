"""Keep course text PG: gently swap cursing for a simple, mild word.

Ben's rule (Oct 5 2026): "gently smooth over any cursing in the text with a
simple word substitute since there may be cursing and we want to keep this PG".

- damn -> darn, hell (as an expletive) -> heck, shit -> stuff, the f-word ->
  heck / freaking / messed up, ass -> butt, bitch -> pain, piss(ed) -> annoyed,
  bastard -> jerk, the Lord's name as an exclamation -> goodness. The
  substitute keeps the sentence grammatical and keeps the capitalisation
  ("Damn." -> "Darn.", "WHAT THE HELL" -> "WHAT THE HECK").
- Slurs become ``[removed]``.
- "crap" is PG and stays.
- Legitimate uses stay: "hell" is changed only in expletive phrases ("what the
  hell", "hell of a", "Hell, I don't know"), never as a place or in "Hell's
  Kitchen"; "God", "Jesus" and "Christ" are changed only when used as an
  exclamation ("Oh my God.", "Jesus, that's a lot"), never in "by God's grace"
  or a reading about religion. Matching is on whole words, so "assess",
  "class", "Dickens", "Hancock", "cocktail", "Scunthorpe", "shiitake",
  "Hello" and "therapist" are never touched.
- Asterisked forms (f***, s**t, b*tch) and common ASR spellings (fuckin',
  f-ing, effing, dammit, goddam) are handled.

The word list is in this file on purpose: it holds no private data.

Usage:
    from pg_filter import smooth          # or: from indexer.pg_filter import smooth
    new_text, n_changes = smooth(text)
    new_text, n_changes = smooth(text, counts)   # counts: Counter of "damn -> darn" labels
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Callable

REMOVED = "[removed]"

# Whole-word boundaries. An asterisk counts as part of a word so "f***" is one token
# and the "*" in "A* search" or "f*(x)" never starts a match.
_L = r"(?<![A-Za-z0-9_*])"
_R = r"(?![A-Za-z0-9_*])"

# Roots, including asterisked and ASR spellings.
F = r"(?:fuck|fuk|fck|phuck|f\*ck|fu\*k|f\*\*k|fu\*\*|f\*{2,})"
S = r"(?:shit|sh\*t|s\*\*t|sh\*\*|s\*it|s\*{3})"
B = r"(?:bitch|biatch|b\*tch|bi\*ch|b\*\*ch|b\*{4})"

_INTERJECTION_BEFORE = re.compile(r"(?i)(?:^|[^\w])(?:oh|ah|aw|well|man|boy|wow|yeah|ugh)[,!]?$")


def _sentence_start(text: str, i: int) -> bool:
    before = text[:i].rstrip(" \"'“‘(")
    return before == "" or before[-1] in ".!?\n…"


def _clause_start(text: str, i: int, interjections: bool = True) -> bool:
    """True when position i begins a clause: start of text, after punctuation, or after "oh"."""
    before = text[:i].rstrip()
    if not before or before[-1] in ".!?,;:([\"“”—–-…\n":
        return True
    return interjections and bool(_INTERJECTION_BEFORE.search(before[-12:]))


def _clause_end(text: str, j: int) -> bool:
    """True when position j ends a clause: end of text or a following punctuation mark."""
    after = text[j:].lstrip(" ")
    return after == "" or after[0] in ",.!?;:…—–-\"”)\n"


def _exclaim(text: str, m: re.Match) -> bool:
    """An interjection: "Damn.", "Oh my God!", "Jesus, that's a lot", "video, shit, okay"."""
    return _clause_start(text, m.start()) and _clause_end(text, m.end())


def _not_proper(text: str, m: re.Match) -> bool:
    """Skip a capitalised word in mid-sentence (a proper noun such as Dante's "Hell")."""
    w = m.group("w") if "w" in m.re.groupindex and m.group("w") else m.group(0)
    if w[:1].isupper() and not w.isupper():
        return _sentence_start(text, m.start("w") if "w" in m.re.groupindex else m.start())
    return True


def _lowercase(text: str, m: re.Match) -> bool:
    w = m.group("w") if "w" in m.re.groupindex and m.group("w") else m.group(0)
    return w.islower()


def _not_allcaps(text: str, m: re.Match) -> bool:
    return not m.group(0).isupper()


def _not_abbrev(text: str, m: re.Match) -> bool:
    """"Ass. Prof." is an abbreviation, not a curse."""
    return not (m.group(0)[:1].isupper() and text[m.end():m.end() + 1] == ".")


@dataclass
class Rule:
    rx: re.Pattern
    repl: str | dict | Callable[[re.Match], str]
    label: str
    when: Callable[[str, re.Match], bool] | None = None
    case: str = "match"  # "match": copy the case; "proper": source is capitalised by convention (God)


RULES: list[Rule] = []


def _rule(pattern: str, repl, label: str, when=None, case: str = "match") -> None:
    RULES.append(Rule(re.compile(_L + "(?:" + pattern + ")" + _R, re.IGNORECASE), repl, label, when, case))


def _both(*preds):
    return lambda text, m: all(p(text, m) for p in preds)


# ---------------------------------------------------------------------------
# The list, in priority order: earlier rules win where two would overlap.
# A named group "w" marks the part to replace; without it the whole match is
# replaced. A dict replacement is keyed by the lowercase "sfx" group.
# ---------------------------------------------------------------------------

# --- slurs -> [removed] (label kept generic so counts never spell them) ---
for _p in (
    r"nigg(?:ers?|as?|az|ahs?)", r"n\*{3,}(?:rs?|as?)", r"faggots?|fags?", r"chinks?(?!\s+in\b)", r"gooks?",
    r"spics?", r"kikes?", r"wetbacks?", r"ragheads?", r"towelheads?", r"beaners?", r"trann(?:y|ies)",
    r"retards?|retarded", r"spaz(?:zes)?", r"pakis?", r"wops?", r"dagos?",
):
    _rule(_p, REMOVED, "slur -> [removed]")
_rule(r"japs?", REMOVED, "slur -> [removed]", when=_not_allcaps)

# --- the Lord's name as an exclamation ---
_rule(r"god[\s-]*dam(?:n|m)?[\s-]*it", "darn it", "goddammit -> darn it")
_rule(r"god[\s-]*dam(?:n|m)?(?P<sfx>ed)?", {"": "darn", "ed": "darned"}, "goddamn -> darn")
_rule(r"oh,?\s+my,?\s+(?P<w>god|gawd|lord)", "goodness", "oh my God -> oh my goodness", case="proper")
_rule(r"good\s+(?P<w>god|lord)(?!['’])", "grief", "good God -> good grief", case="proper")
_rule(r"(?:oh|dear),?\s+(?P<w>god|gawd|lord)(?!['’])", "goodness", "oh God -> oh goodness", case="proper")
_rule(r"thank\s+(?P<w>god|gawd)", "goodness", "thank God -> thank goodness", case="proper")
_rule(r"for\s+(?P<w>(?:god|christ|jesus)['’]?s?)\s+sakes?", "goodness'", "for God's sake -> for goodness' sake",
      case="proper")
_rule(r"(?P<w>god)(?=\s+(?:forbid|knows)\b)", "heaven", "God knows -> heaven knows", case="proper")
_rule(r"my\s+(?P<w>god|gawd)", "goodness", "my God -> my goodness", when=_exclaim, case="proper")
_rule(r"(?P<w>jesus(?:\s+h\.?)?(?:\s+(?:fucking\s+|f\*+ing\s+)?christ)?|christ(?:\s+almighty)?)", "goodness",
      "Jesus -> goodness", when=_exclaim, case="proper")
_rule(r"(?P<w>god|gawd)", "goodness", "God -> goodness", when=_exclaim, case="proper")

# --- damn ---
_rule(r"damn[\s-]*it|dammit|damnit", "darn it", "dammit -> darn it")
_rule(r"damn(?P<sfx>ed|edest|s)?", {"": "darn", "ed": "darned", "edest": "darnedest", "s": "darns"},
      "damn -> darn")
_rule(r"d\*mn|d\*\*n", "darn", "damn -> darn")

# --- hell (expletive phrases only) ---
_rule(r"(?:what|who|where|why|how|whatever|wherever|whoever|when)(?:\s+in)?\s+the\s+(?P<w>hell|h\*ll)(?!['’])",
      "heck", "hell -> heck")
_rule(r"the\s+(?P<w>hell|h\*ll)(?=\s+(?:out|outta|with|away|up|of\s+it)\b)", "heck", "hell -> heck")
_rule(r"(?P<w>hell|h\*ll)(?=\s+(?:of\s+(?:an?|it)|yeah|yes|yea|no)\b)", "heck", "hell -> heck")
_rule(r"helluva", "heck of a", "hell -> heck")
_rule(r"(?:as|like|oh|bloody|" + F + r"(?:ing|in['’]?))\s+(?P<w>hell)(?!['’])", "heck", "hell -> heck", when=_not_proper)
_rule(r"(?:go|goes|went|going|gone)\s+to\s+(?P<w>hell)(?!['’])", "heck", "hell -> heck", when=_not_proper)
_rule(r"to\s+(?P<w>hell)(?=\s+(?:with|and\s+back)\b)", "heck", "hell -> heck", when=_not_proper)
_rule(r"(?P<w>hell|h\*ll)", "heck", "hell -> heck", when=_exclaim)

# --- the f-word ---
_rule(r"the\s+(?P<w>" + F + r")", "heck", "f-word -> heck")
_rule(r"(?:give|gives|gave|giving|given)\s+(?:a\s+)?(?:single\s+)?(?P<w>" + F + r")", "hoot", "f-word -> hoot")
_rule(r"(?:zero|no)\s+(?P<w>" + F + r"s)(?=\s+(?:given|to\s+give)\b)", "hoots", "f-word -> hoot")
_rule(r"(?:mother|mutha|motha)[\s-]?" + F + r"(?P<sfx>ers?|ing|in['’]?)",
      {"er": "jerk", "ers": "jerks", "ing": "freaking", "in": "freaking", "in'": "freaking", "in’": "freaking"},
      "motherf... -> jerk / freaking")
_rule(r"(?P<w>" + F + r"(?P<sfx>ed|ing|in['’]?|s)?)(?=[\s-]+(?:up|with|around)\b)",
      {"": "mess", "ed": "messed", "ing": "messing", "in": "messing", "in'": "messing", "in’": "messing",
       "s": "messes"}, "f-word up -> mess up")
_rule(F + r"-?ups?", "mess-up", "f-word up -> mess up")
_rule(F + r"\s+off", "get lost", "f-word off -> get lost")
_rule(F + r"(?P<sfx>ers?)", {"er": "jerk", "ers": "jerks"}, "f-word -> jerk")
_rule(F + r"(?:ing|in['’]?|n)|f['’-]ing|f['’]in['’]?|eff?in(?:g|['’])?|fking|fkin['’]?", "freaking",
      "f-word -> freaking")
_rule(F + r"e?d", "messed up", "f-word -> messed up")
_rule(r"(?P<w>" + F + r")(?=\s+(?:yeah|yes|no|me)\b)", "heck", "f-word -> heck")
_rule(r"(?P<w>" + F + r")(?=\s+[A-Za-z\[])", "forget", "f-word -> forget",
      when=lambda text, m: _clause_start(text, m.start(), interjections=False))
_rule(F + r"s", "messes up", "f-word -> messes up")
_rule(F, "heck", "f-word -> heck")
_rule(r"wtf", "what the heck", "wtf -> what the heck")
_rule(r"stfu", "shut up", "stfu -> shut up")

# --- shit ---
_rule(r"holy\s+(?P<w>" + S + r")", "cow", "shit -> cow (holy cow)")
_rule(r"bull[\s-]?" + S + r"(?P<sfx>ting|tin['’]?|ted|ters?)?",
      {"": "nonsense", "ting": "kidding", "tin": "kidding", "tin'": "kidding", "tin’": "kidding",
       "ted": "fooled", "ter": "phony", "ters": "phonies"}, "bullshit -> nonsense")
_rule(r"horse[\s-]?" + S, "nonsense", "shit -> nonsense")
_rule(r"chicken[\s-]?" + S, "petty", "shit -> petty")
_rule(r"dip[\s-]?" + S + r"(?P<sfx>s)?", {"": "dummy", "s": "dummies"}, "shit -> dummy")
_rule(S + r"[\s-]?head(?P<sfx>s)?", {"": "jerk", "s": "jerks"}, "shit -> jerk")
_rule(S + r"[\s-]?hole(?P<sfx>s)?", {"": "dump", "s": "dumps"}, "shit -> dump")
_rule(S + r"[\s-]?(?:show|storm)(?P<sfx>s)?", {"": "mess", "s": "messes"}, "shit -> mess")
_rule(S + r"[\s-]?(?:load|ton)(?P<sfx>s)?", {"": "ton", "s": "tons"}, "shit -> ton")
_rule(S + r"t?(?P<sfx>y|ier|iest)", {"y": "lousy", "ier": "lousier", "iest": "lousiest"}, "shitty -> lousy")
_rule(S + r"less", "stiff", "shit -> stiff")
_rule(r"(?:" + S + r"(?P<sfx>s|t?ted|t?ting|tin['’]?)?|(?P<shat>shat))\s+the\s+bed",
      lambda m: "fell flat" if m.group("shat") else {"": "fall flat", "s": "falls flat", "ted": "fell flat",
                                                    "tted": "fell flat"}.get((m.group("sfx") or "").lower(),
                                                                             "falling flat"),
      "shit the bed -> fall flat")
_rule(r"(?:you|ya)\s+(?P<w>" + S + r"t?(?:ing|in['’]?))\s+me", "kidding", "shitting -> kidding")
_rule(r"no\s+(?P<w>" + S + r")", "kidding", "shit -> kidding (no kidding)")
_rule(r"piece\s+of\s+(?P<w>" + S + r")", "junk", "shit -> junk")
_rule(r"(?:give|gives|gave|giving)\s+a\s+(?:single\s+)?(?P<w>" + S + r")", "hoot", "shit -> hoot")
_rule(r"(?:know|knows|knew)\s+(?P<w>" + S + r")(?=\s+about\b)", "anything", "shit -> anything")
_rule(r"full\s+of\s+(?P<w>" + S + r")", "it", "shit -> it (full of it)")
_rule(r"the\s+(?P<w>" + S + r")(?=\s+out\s+of\b)", "heck", "shit -> heck")
_rule(r"tough\s+(?P<w>" + S + r")", "luck", "shit -> luck (tough luck)")
_rule(r"deep\s+(?P<w>" + S + r")", "trouble", "shit -> trouble (deep trouble)")
_rule(r"to\s+(?P<w>" + S + r")(?=\s*(?:[.!?,;:…]|$))", "pot", "shit -> pot (gone to pot)")
_rule(r"the\s+(?P<w>" + S + r"s)", "pits", "shit -> pits")
_rule(r"oh,?\s+(?P<w>" + S + r")", "shoot", "shit -> shoot")
_rule(r"(?P<w>" + S + r")", "shoot", "shit -> shoot", when=_exclaim)
_rule(S + r"t?(?:ing|in['’]?)", "messing", "shit -> messing")
_rule(S + r"(?P<sfx>s)?", {"": "stuff", "s": "stuff"}, "shit -> stuff")

# --- ass ---
_rule(r"(?:ass|arse)[\s-]?hole(?P<sfx>s)?|a\*+hole(?P<sfx2>s)?|a-hole(?P<sfx3>s)?",
      lambda m: "jerks" if (m.group("sfx") or m.group("sfx2") or m.group("sfx3")) else "jerk", "asshole -> jerk")
_rule(r"bad[\s-]?ass(?P<sfx>es)?", {"": "tough", "es": "tough ones"}, "badass -> tough")
_rule(r"kick-?ass", "awesome", "kick-ass -> awesome")
_rule(r"dumb[\s-]?ass(?P<sfx>es)?", {"": "dummy", "es": "dummies"}, "dumbass -> dummy")
_rule(r"jack[\s-]?ass(?P<sfx>es)?|ass[\s-]?hat(?P<sfx2>s)?",
      lambda m: "jerks" if (m.group("sfx") or m.group("sfx2")) else "jerk", "jackass -> jerk")
_rule(r"smart[\s-]?ass(?P<sfx>es)?", {"": "smart aleck", "es": "smart alecks"}, "smartass -> smart aleck")
_rule(r"half[\s-]?ass(?:ed)?", "half-baked", "half-assed -> half-baked")
_rule(r"(?:ass|arse)(?P<sfx>es|s)?", {"": "butt", "es": "butts", "s": "butts"}, "ass -> butt", when=_not_abbrev)

# --- bitch ---
_rule(r"son\s+of\s+a\s+(?P<w>" + B + r")", "gun", "bitch -> gun (son of a gun)")
_rule(r"(?P<w>" + B + r"(?P<sfx>es|ed|ing|in['’]?)?)(?=\s+(?:about|at)\b)",
      {"": "complain", "es": "complains", "ed": "complained", "ing": "complaining", "in": "complaining",
       "in'": "complaining", "in’": "complaining"}, "bitch -> complain")
_rule(B + r"(?:ing|in['’]?)", "complaining", "bitching -> complaining")
_rule(B + r"ed", "complained", "bitch -> complain")
_rule(B + r"(?P<sfx>y|ier|iest)", {"y": "cranky", "ier": "crankier", "iest": "crankiest"}, "bitchy -> cranky")
_rule(B + r"(?P<sfx>es)?", {"": "pain", "es": "pains"}, "bitch -> pain")

# --- piss ---
_rule(r"pissed[\s-]+off", "annoyed", "pissed -> annoyed")
_rule(r"pissing[\s-]+off", "annoying", "pissed -> annoyed")
_rule(r"(?P<w>piss(?P<sfx>es|ed|ing)?)(?=(?:\s+[A-Za-z\[\]'’]+){1,3}\s+off\b)",
      {"": "tick", "es": "ticks", "ed": "ticked", "ing": "ticking"}, "piss off -> tick off")
_rule(r"piss[\s-]+off", "get lost", "piss off -> get lost", when=lambda text, m: _clause_end(text, m.end()))
_rule(r"piss[\s-]+off", "annoy", "piss off -> annoy")
_rule(r"piss[\s-]?poor", "really poor", "piss-poor -> really poor")
_rule(r"piss(?:ing)?\s+(?:contest|match)", "turf war", "pissing contest -> turf war")
_rule(r"piss(?P<sfx>y|ed|es|ing)?",
      {"": "pee", "y": "cranky", "ed": "annoyed", "es": "annoys", "ing": "annoying"}, "pissed -> annoyed")

# --- bastard and other insults ---
_rule(r"bastard(?P<sfx>s)?", {"": "jerk", "s": "jerks"}, "bastard -> jerk")
_rule(r"douche(?:[\s-]?bag)?(?P<sfx>s)?", {"": "jerk", "s": "jerks"}, "douche -> jerk")
_rule(r"dick[\s-]?head(?P<sfx>s)?", {"": "jerk", "s": "jerks"}, "dick -> jerk")
_rule(r"dick(?P<sfx>s)?", {"": "jerk", "s": "jerks"}, "dick -> jerk", when=_lowercase)  # never "Dick" (a name)
_rule(r"(?:cunt|twat|wanker|cocksucker|mofo)(?P<sfx>s)?", {"": "jerk", "s": "jerks"}, "insult -> jerk")
_rule(r"pussy(?!\s*(?:cat|willow|foot))|pussies", lambda m: "wimps" if m.group(0).lower().endswith("ies") else "wimp",
      "pussy -> wimp", when=_lowercase)
_rule(r"bollocks", "nonsense", "bollocks -> nonsense")


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------
def _match_case(src: str, repl: str, proper: bool, at_start: bool) -> str:
    if repl.startswith("["):
        return repl
    letters = [c for c in src if c.isalpha()]
    if len(letters) > 1 and all(c.isupper() for c in letters):
        return repl.upper()
    if src[:1].isupper() and (not proper or at_start):
        return repl[:1].upper() + repl[1:]
    return repl


_ARTICLE = re.compile(r"(?<![A-Za-z0-9])(an?)([ \t]+)$", re.IGNORECASE)


def _replacement(rule: Rule, m: re.Match) -> str:
    if callable(rule.repl):
        return rule.repl(m)
    if isinstance(rule.repl, dict):
        sfx = (m.group("sfx") or "") if "sfx" in m.re.groupindex else ""
        return rule.repl.get(sfx.lower(), rule.repl[""] if "" in rule.repl else next(iter(rule.repl.values())))
    return rule.repl


def smooth(text: str, counts: Counter | None = None) -> tuple[str, int]:
    """Return (text with cursing swapped for mild words, number of substitutions).

    ``counts`` (optional) is incremented per substitution with a label such as
    "damn -> darn". Labels never contain the matched text, so they are safe to
    write to review files.
    """
    if not text:
        return text, 0
    taken: list[tuple[int, int]] = []
    edits: list[tuple[int, int, str, str]] = []
    for rule in RULES:
        for m in rule.rx.finditer(text):
            has_w = "w" in rule.rx.groupindex and m.group("w") is not None
            a, b = (m.start("w"), m.end("w")) if has_w else (m.start(), m.end())
            if any(a < y and x < b for x, y in taken):
                continue
            if rule.when is not None and not rule.when(text, m):
                continue
            src = text[a:b]
            new = _match_case(src, _replacement(rule, m), rule.case == "proper", _sentence_start(text, a))
            art = _ARTICLE.search(text, max(0, a - 12), a)
            if art and not new.startswith("["):  # "an asshole" -> "a jerk", "a kick-ass demo" -> "an awesome demo"
                old_art, space = art.group(1), art.group(2)
                want_an = new[:1].lower() in "aeiou"
                if want_an != (old_art.lower() == "an"):
                    fixed = "an" if want_an else "a"
                    fixed = fixed.upper() if old_art.isupper() and len(old_art) > 1 else (
                        fixed.capitalize() if old_art[:1].isupper() else fixed)
                    a -= len(old_art) + len(space)
                    new = fixed + space + new
            taken.append((a, b))
            edits.append((a, b, new, rule.label))
    if not edits:
        return text, 0
    out, pos = [], 0
    for a, b, new, label in sorted(edits):
        out.append(text[pos:a])
        out.append(new)
        pos = b
        if counts is not None:
            counts[label] += 1
    out.append(text[pos:])
    return "".join(out), len(edits)


def is_pg(text: str) -> bool:
    """True when smooth() would change nothing."""
    return smooth(text)[1] == 0
