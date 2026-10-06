"""Tests for indexer/pg_filter.py (Ben's PG rule, TEAM_BRIEF update 6).

Every sentence here is invented for the test; none comes from a transcript.

Run: uv run --no-project --with pytest python -m pytest tests/test_pg_filter.py -q
"""

import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indexer.pg_filter import REMOVED, is_pg, smooth  # noqa: E402

SWAPS = [
    # damn
    ("Damn, okay.", "Darn, okay."),
    ("Damn.", "Darn."),
    ("that damn model", "that darn model"),
    ("the damned thing broke", "the darned thing broke"),
    ("Dammit, it crashed again.", "Darn it, it crashed again."),
    ("damn it, the cache", "darn it, the cache"),
    ("damnit", "darn it"),
    ("I did my damnedest", "I did my darnedest"),
    ("Goddamn it, the build failed.", "Darn it, the build failed."),
    ("this goddamn notebook", "this darn notebook"),
    ("the god damned thing", "the darned thing"),
    ("Goddammit.", "Darn it."),
    # hell
    ("what the hell?", "what the heck?"),
    ("What the hell is going on", "What the heck is going on"),
    ("why in the hell would you", "why in the heck would you"),
    ("confusing the hell out of regulators", "confusing the heck out of regulators"),
    ("get the hell out", "get the heck out"),
    ("that's a hell of a result", "that's a heck of a result"),
    ("one hell of an algorithm", "one heck of an algorithm"),
    ("hell yeah", "heck yeah"),
    ("Hell no.", "Heck no."),
    ("it's hard as hell", "it's hard as heck"),
    ("Hell, I don't know.", "Heck, I don't know."),
    ("oh hell, the demo", "oh heck, the demo"),
    ("a helluva model", "a heck of a model"),
    ("to hell with the baseline", "to heck with the baseline"),
    ("WHAT THE HELL", "WHAT THE HECK"),
    # shit
    ("a whole bunch of other shit.", "a whole bunch of other stuff."),
    ("this shit works", "this stuff works"),
    ("Shit happens.", "Stuff happens."),
    ("Oh, now I have to edit the video, shit, okay.", "Oh, now I have to edit the video, shoot, okay."),
    ("Shit!", "Shoot!"),
    ("oh shit I forgot", "oh shoot I forgot"),
    ("Holy shit, look at that.", "Holy cow, look at that."),
    ("total bullshit", "total nonsense"),
    ("that's bullshit.", "that's nonsense."),
    ("you're totally bullshitting me", "you're totally kidding me"),
    ("a shitty dataset", "a lousy dataset"),
    ("the shittiest chart", "the lousiest chart"),
    ("it really just shits the bed.", "it really just falls flat."),
    ("they always shit the bed", "they always fall flat"),
    ("it shitting the bed again", "it falling flat again"),
    ("and which all goes, to shit. Okay.", "and which all goes, to pot. Okay."),
    ("it went to shit", "it went to pot"),
    ("no shit", "no kidding"),
    ("are you shitting me?", "are you kidding me?"),
    ("a shitload of data", "a ton of data"),
    ("a shit ton of tokens", "a ton of tokens"),
    ("a total shitshow", "a total mess"),
    ("it scared the shit out of me", "it scared the heck out of me"),
    ("I don't know shit about GPUs", "I don't know anything about GPUs"),
    ("he's full of shit", "he's full of it"),
    ("who gives a shit", "who gives a hoot"),
    ("piece of shit laptop", "piece of junk laptop"),
    ("tough shit", "tough luck"),
    ("SHIT", "SHOOT"),
    # f-word
    ("Hey, fuck AI. We don't want any AI.", "Hey, forget AI. We don't want any AI."),
    ("fuck it, let's ship", "forget it, let's ship"),
    ("what the fuck", "what the heck"),
    ("What the fuck is this?", "What the heck is this?"),
    ("this fucking model", "this freaking model"),
    ("it's fuckin' slow", "it's freaking slow"),
    ("it's fuckin slow", "it's freaking slow"),
    ("so f-ing slow", "so freaking slow"),
    ("so effing slow", "so freaking slow"),
    ("Fucking hell.", "Freaking heck."),
    ("we fucked up the split", "we messed up the split"),
    ("I fucked up.", "I messed up."),
    ("don't fuck with the seed", "don't mess with the seed"),
    ("stop fucking around", "stop messing around"),
    ("it's fucked", "it's messed up"),
    ("a big fuck-up", "a big mess-up"),
    ("Fuck.", "Heck."),
    ("oh fuck, I forgot", "oh heck, I forgot"),
    ("I don't give a fuck", "I don't give a hoot"),
    ("zero fucks given", "zero hoots given"),
    ("fuck off", "get lost"),
    ("that motherfucker", "that jerk"),
    ("a motherfucking mess", "a freaking mess"),
    ("those fuckers", "those jerks"),
    ("fuck yeah", "heck yeah"),
    ("WTF is this", "WHAT THE HECK is this"),
    ("wtf", "what the heck"),
    # asterisks and ASR spellings
    ("f***", "heck"),
    ("what the f***", "what the heck"),
    ("this f***ing thing", "this freaking thing"),
    ("this f*cking thing", "this freaking thing"),
    ("f**k it", "forget it"),
    ("s**t happens", "stuff happens"),
    ("sh*t.", "shoot."),
    ("a b*tch to install", "a pain to install"),
    ("a**hole", "jerk"),
    ("d*mn", "darn"),
    ("what the h*ll", "what the heck"),
    # ass
    ("got its ass kicked", "got its butt kicked"),
    ("pain in the ass", "pain in the butt"),
    ("cover your ass", "cover your butt"),
    ("what an asshole", "what a jerk"),
    ("those assholes", "those jerks"),
    ("a badass model", "a tough model"),
    ("a kick-ass demo", "an awesome demo"),
    ("kick-ass results", "awesome results"),
    ("an effing mess", "a freaking mess"),
    ("An asshole wrote this.", "A jerk wrote this."),
    ("a half-assed answer", "a half-baked answer"),
    ("don't be a dumbass", "don't be a dummy"),
    ("a smartass reply", "a smart aleck reply"),
    ("that jackass", "that jerk"),
    # bitch
    ("it's a bitch to install", "it's a pain to install"),
    ("son of a bitch", "son of a gun"),
    ("stop bitching", "stop complaining"),
    ("they bitch about the grades", "they complain about the grades"),
    ("he bitched about it", "he complained about it"),
    # piss
    ("people were pissed, right?", "people were annoyed, right?"),
    ("he's pissed off", "he's annoyed"),
    ("it would make me pissed if", "it would make me annoyed if"),
    ("it pisses me off", "it ticks me off"),
    ("that pissed the whole team off", "that ticked the whole team off"),
    ("Piss off!", "Get lost!"),
    ("piss-poor accuracy", "really poor accuracy"),
    ("a pissing contest", "a turf war"),
    # bastard and insults
    ("you bastard", "you jerk"),
    ("those bastards", "those jerks"),
    ("don't be a dick", "don't be a jerk"),
    ("a total douchebag", "a total jerk"),
    # the Lord's name as an exclamation
    ("Oh my God.", "Oh my goodness."),
    ("Oh, my God.", "Oh, my goodness."),
    ("oh my god, I already planned 7 weeks", "oh my goodness, I already planned 7 weeks"),
    ("Oh my gawd", "Oh my goodness"),
    ("OH MY GOD", "OH MY GOODNESS"),
    ("Oh God, not again.", "Oh goodness, not again."),
    ("My God, look at that curve.", "My goodness, look at that curve."),
    ("God, that's a lot of parameters.", "Goodness, that's a lot of parameters."),
    ("Thank God it converged.", "Thank goodness it converged."),
    ("for God's sake", "for goodness' sake"),
    ("God knows how many tokens", "Heaven knows how many tokens"),
    ("Good God!", "Good grief!"),
    ("Jesus, that's a lot of data.", "Goodness, that's a lot of data."),
    ("Jesus Christ!", "Goodness!"),
    ("Oh Jesus.", "Oh goodness."),
    ("and, Christ, it failed", "and, goodness, it failed"),
    ("Jesus H. Christ, really?", "Goodness, really?"),
    # slurs
    ("you retard", f"you {REMOVED}"),
    ("that's retarded", f"that's {REMOVED}"),
]


@pytest.mark.parametrize("src,want", SWAPS)
def test_swaps(src, want):
    got, n = smooth(src)
    assert got == want
    assert n >= 1


UNCHANGED = [
    # substrings must never match (Scunthorpe problem)
    "We assess the classifier on a classic benchmark.",
    "Assessment, assignment, assumption, associate, assets, bass, embarrass, passed, mass, glasses.",
    "Charles Dickens wrote about it; Dick Cheney did not.",
    "John Hancock signed it in the cockpit over a cocktail.",
    "Scunthorpe United beat Middlesbrough.",
    "The therapist and the analyst agreed.",
    "Shiitake mushrooms and shitake soup.",
    "Hello, shell, Michelle, hellenistic, Hellman.",
    "Matthew, Matthias, Mathews.",
    "Moby Dick is a novel.",
    "Amsterdam built a dam on the river. They dammed it in 1300.",
    "The evidence was damning. Condemn the damnation scene.",
    "The bastardized version of the algorithm.",
    "Godfather, goddess, Goddard, godlike, god mode, give it god permissions.",
    "by God's grace I finished all five deliverables.",
    "People say that God or their lives are not good for math.",
    "Jesus taught in parables, and Jesus Christ is central to the reading.",
    "The story of Jesus. They followed Jesus.",
    "Christmas, Christopher, Christian, Christchurch.",
    "God bless America.",
    "Dante's Hell has nine circles.",
    "We had dinner at Hell's Kitchen.",
    "This is dependency hell and callback hell.",
    "heaven and hell",
    "A chink in the armor.",
    "A* search and IDA* are heuristic searches; f*(x) is the optimum and f**2 squares it.",
    "x = n**2 + f**k_val",
    "**bold** and *italic* markdown",
    "That was crappy, a load of crap.",
    "Pissarro painted Mississippi scenes.",
    "Bitcoin and Bitbucket.",
    "The Japanese market.",
    "Ass. Prof. Smith",
    "Shoot, darn, heck, freaking, goodness, butt, jerk, stuff.",
    "[student] asked a question and [person] answered.",
    "",
]


@pytest.mark.parametrize("src", UNCHANGED)
def test_unchanged(src):
    got, n = smooth(src)
    assert got == src
    assert n == 0
    assert is_pg(src)


def test_counts_and_labels():
    counts = Counter()
    text, n = smooth("Damn, what the hell? This shit is fucking slow. Damn.", counts)
    assert text == "Darn, what the heck? This stuff is freaking slow. Darn."
    assert n == 5
    assert counts["damn -> darn"] == 2
    assert counts["hell -> heck"] == 1
    assert counts["shit -> stuff"] == 1
    assert counts["f-word -> freaking"] == 1
    assert sum(counts.values()) == n


def test_slur_labels_never_spell_the_word():
    counts = Counter()
    smooth("you retard", counts)
    assert list(counts) == ["slur -> [removed]"]


def test_case_preservation():
    assert smooth("DAMN")[0] == "DARN"
    assert smooth("Damn")[0] == "Darn"
    assert smooth("damn")[0] == "darn"
    assert smooth("What The Hell Out Of It")[0] == "What The Heck Out Of It"
    assert smooth("That's Bullshit.")[0] == "That's Nonsense."
    # God is capitalised by convention: the substitute is lowercase unless it starts a sentence
    assert smooth("and oh my God, it worked")[0] == "and oh my goodness, it worked"
    assert smooth("God, it worked")[0] == "Goodness, it worked"


def test_punctuation_and_quotes_survive():
    assert smooth('I was like, "Damn, okay."')[0] == 'I was like, "Darn, okay."'
    assert smooth("I was like, “Damn, okay.” I tried")[0] == "I was like, “Darn, okay.” I tried"
    assert smooth("(shit!)")[0] == "(shoot!)"
    assert smooth("damn—really?")[0] == "darn—really?"
    assert smooth("it’s fuckin’ slow")[0] == "it’s freaking slow"


def test_idempotent():
    for src, _ in SWAPS:
        once, _n = smooth(src)
        twice, n2 = smooth(once)
        assert twice == once and n2 == 0, src


def test_multiline_and_long_text():
    src = "Line one is fine.\nDamn, line two.\nLine three: what the hell?\n"
    got, n = smooth(src)
    assert got == "Line one is fine.\nDarn, line two.\nLine three: what the heck?\n"
    assert n == 2
