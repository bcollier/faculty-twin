"""De-identify class transcripts and mark who is speaking.

Privacy rule (Ben, Oct 5 2026): every person named in a transcript is
de-identified except Ben Collier himself.

- Roster students and people addressed in class become ``[student]``.
- Everyone else (researchers, executives, guests, family) becomes ``[person]``.
- Ben's own forms stay: Ben, Benjamin, Ben Collier, Professor / Dr. Collier.
- Company, product, model and place names are not people and stay
  (OpenAI, Claude, ResNet, Pittsburgh). Eponymous technical terms stay
  ("Turing test", "Naive Bayes", "Markov chain", "Moore's law").

Every cue is also labelled ``instructor``, ``student`` or ``unclear``. Only
``instructor`` cues may be used for narration material or video clips.

PG rule (Ben, Oct 5 2026): after de-identification, cursing in cue text and in
the Drive transcript is swapped for a mild word (``indexer/pg_filter.py``).
A changed cue carries ``"pg": true``; the clip stage rejects any window that
holds one, because audio cannot be cleaned. The review file counts the swaps.

Nothing in this file names a real student. The student scrub list is built at
run time from the private roster CSVs, plus an optional private overrides folder
written during the review pass. Neither is ever committed.

Inputs (private, never committed):
    <archive>/<course folder>/2026 Fall/<NN> <date> <title>/transcript_raw.vtt
    <archive>/<course folder>/2026 Fall/<NN> .../transcript_drive.md       (optional)
    <archive>/<course folder>/2026 Fall/<NN> .../whisper/audio*.vtt        (fallback when no Zoom VTT)
    <archive>/_import/FacultyTwinContent/transcripts-clean/*<date>*.cleaned.timed.jsonl
        (ASR-corrected Zoom cues; preferred for 45-884, matched by date)
    <archive>/_private/rosters/*.csv
    <archive>/_private/deid_overrides/global.json                          (optional)
    <archive>/_private/deid_overrides/<course>/s<NN>.json                  (optional)

Outputs (private):
    <archive>/_build/transcripts/<course>/s<NN>.json
    <archive>/_build/transcripts/<course>/s<NN>.review.md
    <archive>/_build/transcripts/<course>/s<NN>.drive.md     (when a Drive transcript exists)
    <archive>/_build/transcripts/summary.json                (counts only)

Override file format (all keys optional):
    {
      "labels":       [{"from": 812.0, "to": 1104.5, "speaker": "student", "note": "In the News"}],
      "scrub_terms":  ["..."],   # extra student / in-class names -> [student]
      "person_terms": ["..."],   # extra names of other people   -> [person]
      "keep_terms":   ["..."],   # capitalised non-names the heuristics wrongly masked
      "unsure":       [{"t": 640.2, "note": "..."}]
    }
Term lists from every override file apply to every session (a name heard once
may recur). A label override applies to every cue whose midpoint is in [from, to].

Usage (from the repo root):
    uv run --no-project --with rapidfuzz --with nicknames python indexer/deidentify.py run
    uv run --no-project --with rapidfuzz --with nicknames python indexer/deidentify.py sweep
    uv run --no-project --with rapidfuzz --with nicknames python indexer/deidentify.py dump 70445 3
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz, process
from rapidfuzz.distance import JaroWinkler

try:  # optional: public nickname <-> given-name pairs (no student data)
    from nicknames import NickNamer
except ImportError:  # pragma: no cover - degrade gracefully
    NickNamer = None

try:  # imported as a package (tests, worker)
    from indexer.pg_filter import smooth as pg_smooth
    from indexer.roster import blank_institution_terms, institution_spans, read_people
except ImportError:  # run as a script: python indexer/deidentify.py
    from pg_filter import smooth as pg_smooth
    from roster import blank_institution_terms, institution_spans, read_people

STUDENT = "[student]"
PERSON = "[person]"
SPEAKERS = ("instructor", "student", "unclear")

ARCHIVE = Path(os.environ.get("LECTURE_ARCHIVE", Path.home() / "Lecture Archive"))
TERM = "2026 Fall"
CLEAN_DIR = Path("_import") / "FacultyTwinContent" / "transcripts-clean"
CLEAN_COURSES = {"45884"}  # courses that use the ASR-corrected cleaned cues

# The instructor: never masked.
INSTRUCTOR_FORMS = {"ben", "benjamin", "collier"}

# ---------------------------------------------------------------------------
# People who are not students but are often named in class (researchers,
# executives, historical figures). They are masked as [person]. Listing them
# here only helps recall; the given-name, title and context rules catch others.
# ---------------------------------------------------------------------------
KNOWN_PERSON_PHRASES = [
    "Geoffrey Hinton", "Geoff Hinton", "Yann LeCun", "Yoshua Bengio", "Andrew Ng", "Fei-Fei Li",
    "Dario Amodei", "Daniela Amodei", "Sam Altman", "Greg Brockman", "Ilya Sutskever", "Mira Murati",
    "Demis Hassabis", "Mustafa Suleyman", "Andrej Karpathy", "Alan Turing", "John McCarthy",
    "Marvin Minsky", "Claude Shannon", "Herbert Simon", "Herbert A. Simon", "Herb Simon", "Allen Newell",
    "Frank Rosenblatt", "Ed Feigenbaum", "Edward Feigenbaum", "Joseph Weizenbaum", "Pedro Domingos",
    "Ray Kurzweil", "Judea Pearl", "Stuart Russell", "Peter Norvig", "Tom Mitchell", "Raj Reddy",
    "Arthur Samuel", "John Searle", "Hubert Dreyfus", "Noam Chomsky", "Ada Lovelace", "Charles Babbage",
    "John von Neumann", "Norbert Wiener", "Seymour Papert", "Terry Winograd", "Douglas Hofstadter",
    "Rodney Brooks", "Ian Goodfellow", "Jeff Dean", "Kai-Fu Lee", "Lee Sedol", "Garry Kasparov",
    "Ken Jennings", "Timnit Gebru", "Kate Crawford", "Cathy O'Neil", "Joy Buolamwini", "Gary Marcus",
    "Ethan Mollick", "Nick Bostrom", "Eliezer Yudkowsky", "Max Tegmark", "Ashish Vaswani", "Noam Shazeer",
    "Alex Krizhevsky", "Kaiming He", "Jeremy Howard", "Richard Sutton", "David Silver", "Jensen Huang",
    "Lisa Su", "Satya Nadella", "Sundar Pichai", "Mark Zuckerberg", "Elon Musk", "Jeff Bezos", "Andy Jassy",
    "Bill Gates", "Steve Jobs", "Steve Wozniak", "Tim Cook", "Larry Page", "Sergey Brin", "Marc Andreessen",
    "Reid Hoffman", "Peter Thiel", "Clayton Christensen", "Daniel Kahneman", "Amos Tversky", "Jack Ma",
    "Luis von Ahn", "Kevin Roose", "Kevin Scott", "Jack Clark", "Chris Olah", "Jan Leike", "Paul Christiano",
    "Emad Mostaque", "Arthur Mensch", "Aidan Gomez", "Clement Delangue", "Alexandr Wang", "Joseph Redmon",
    "Ross Girshick", "Karen Hao", "Tim Berners-Lee", "Nikola Tesla", "Henry Ford", "Thomas Edison",
    "Albert Einstein", "Isaac Newton", "Charles Darwin", "Thomas Bayes", "Andrey Markov", "Gordon Moore",
    "Warren Buffett", "Jamie Dimon", "Donald Trump", "Joe Biden", "Kamala Harris", "Barack Obama",
    "Taylor Swift", "Scarlett Johansson", "Joaquin Phoenix", "Stanley Kubrick", "Isaac Asimov",
    "Frank Herbert", "Rich Sutton", "Andrew Barto", "Michael Jordan", "Fei Fei Li",
]
KNOWN_PERSON_WORDS = {
    "Hinton", "LeCun", "Bengio", "Amodei", "Altman", "Brockman", "Sutskever", "Murati", "Hassabis",
    "Suleyman", "Karpathy", "Turing", "McCarthy", "Minsky", "Newell", "Rosenblatt", "Feigenbaum",
    "Weizenbaum", "Domingos", "Kurzweil", "Norvig", "Searle", "Dreyfus", "Chomsky", "Lovelace", "Babbage",
    "Neumann", "Papert", "Winograd", "Hofstadter", "Goodfellow", "Sedol", "Kasparov", "Gebru", "Buolamwini",
    "Mollick", "Bostrom", "Yudkowsky", "Tegmark", "Vaswani", "Shazeer", "Krizhevsky", "Nadella", "Pichai",
    "Zuckerberg", "Musk", "Bezos", "Jassy", "Wozniak", "Andreessen", "Thiel", "Kahneman", "Tversky",
    "Bayes", "Markov", "Gauss", "Euler", "Pavlov", "Darwin", "Aristotle", "Plato", "Socrates", "Leibniz",
    "Boole", "Godel", "Gödel", "Asimov", "Kubrick", "Einstein", "Newton", "Edison", "Hebb", "Rumelhart",
    "Werbos", "Hopfield", "Kohonen", "Vapnik", "Breiman", "Quinlan", "Koza", "Barto", "Olah", "Leike",
    "Redmon", "Girshick", "Mostaque", "Roose", "Fukushima", "Hubel", "Wiesel", "McCulloch", "Widrow",
    "Lenat", "Shortliffe", "Buffett", "Dimon", "Trump", "Biden", "Obama", "Kamala", "Johansson",
    "Krugman", "Brynjolfsson", "McAfee", "Acemoglu", "Autor", "Huang", "Sutton", "Shannon", "Hoffman",
    "Geoffrey", "Yann", "Yoshua", "Dario", "Andrej", "Demis", "Ilya", "Elon", "Satya", "Sundar", "Jensen",
    "DiCaprio", "Taleb", "Nassim", "Moravec", "Amara", "Friedman", "Kaplan", "Jevons", "Shakespeare",
    "Frankenstein", "Picard", "Vader", "Genghis", "Caesar", "Schrodinger", "Schrödinger", "Riemann",
    "Fourier", "Dirichlet", "Pareto", "Gantt", "Gini", "Navier", "Stokes", "Hornik",
}

# Eponymous technical terms: a person's surname used as part of a concept name.
EPONYM_AFTER = re.compile(
    r"^(?:['’]s?)?\s+(?:test|tests|machine|machines|award|awards|prize|theorem|rule|rules|chain|chains|model|"
    r"models|process|processes|law|laws|equilibrium|distribution|curve|principle|complete|completeness|"
    r"net|nets|network|networks|criterion|index|filter|filters|transform|series|"
    r"algorithm|property|assumption|blanket|decision|field|fields|entropy|limit|number|numbers|"
    r"architecture|complexity|tar ?pit|paradox|fallacy|effect|recursion|learning|classifier|classifiers|"
    r"allocation|chart|charts|equation|equations|cat|cube|cipher|hypothesis|frontier|coefficient|"
    r"scaling|approximation|analysis|method|methods|space|spaces|bound|sort|search|kernel|matrix)\b",
    re.IGNORECASE,
)
EPONYM_BEFORE = re.compile(r"(?:naive|naïve|hidden|gaussian|bayesian)\s+$", re.IGNORECASE)

# ---------------------------------------------------------------------------
# Not people: companies, products, models, AI assistants, places, institutions,
# calendar words, course vocabulary. These stay, even when a name list (or a
# public figure) shares the word: "Claude" the model stays, "Claude Shannon"
# the person does not (phrases win over single words).
# ---------------------------------------------------------------------------
NON_PERSON_WORDS = {
    # AI assistants, models, products
    "Clippy", "Eliza", "ELIZA", "Watson", "Siri", "Alexa", "Cortana", "Claude", "Gemini", "Bard",
    "Llama", "LLaMA", "Grok", "Copilot", "Cursor", "Devin", "Mistral", "Gemma", "Phi", "Qwen", "DeepSeek",
    "Kimi", "Manus", "Perplexity", "Jasper", "Grammarly", "Midjourney", "Sora", "Veo", "Dall", "DALL",
    "Whisper", "Codex", "Jarvis", "Ultron", "Skynet", "Tars", "Sydney", "Bing", "Tay", "Ameca",
    "Sonnet", "Opus", "Haiku", "Fable", "Mythos", "Turbo", "Davinci", "Ada", "Curie", "Instruct", "Bert",
    "BERT", "RoBERTa", "Elmo", "ELMo", "GPT", "ChatGPT", "Transformer", "Transformers", "ResNet", "AlexNet",
    "ImageNet", "YOLO", "Yolo", "Ultralytics", "OpenCV", "Roboflow", "CLIP", "Stable", "Diffusion", "Flux",
    "Runway", "Pika", "Suno", "Udio", "ElevenLabs", "Voyage", "Max", "Plus", "Pro", "Gold", "Echo", "Nest",
    "Roomba", "iRobot", "Atlas", "Spot", "Optimus", "Figure", "Neuralink", "Kinect", "Xbox", "Nintendo",
    "Deep", "Blue", "AlphaGo", "AlphaFold", "AlphaZero", "Wolfram", "Notion", "Obsidian", "Tableau",
    "Excel", "Word", "PowerPoint", "Outlook", "Gmail", "Chrome", "Safari", "Firefox", "Windows", "Mac",
    "MacBook", "iPhone", "iPad", "Android", "Linux", "GitHub", "Git", "Docker", "AWS", "Azure", "Bedrock",
    "Vertex", "Canvas", "Zoom", "Slack", "Piazza", "Gradescope", "Panopto", "Colab", "Jupyter", "Python",
    "Pandas", "Keras", "PyTorch", "TensorFlow", "Ollama", "LangChain", "LangGraph", "Zapier", "Make", "Lovable",
    "Replit", "Vercel", "Supabase", "Bolt", "Windsurf", "Kaggle", "Hugging", "Face", "Wikipedia",
    # companies and organisations
    "Waymo", "Cruise", "Zoox", "Uber", "Lyft", "Anthropic", "OpenAI", "Google", "Alphabet", "DeepMind", "Meta",
    "Microsoft", "Apple", "Amazon", "Nvidia", "NVIDIA", "Intel", "IBM", "Oracle", "Salesforce", "Duolingo",
    "Spotify", "Tinder", "Netflix", "Hulu", "Disney", "Pixar", "Adobe", "Canva", "Figma", "Premier", "UPMC",
    "Highmark", "Walmart", "Target", "Costco", "Kroger", "Wendy's", "Domino", "Domino's", "Starbucks",
    "Chipotle", "Deloitte", "McKinsey", "Accenture", "Bain", "Goldman", "Sachs", "Chase", "Wells",
    "Fargo", "Citi", "PNC", "Lockheed", "Boeing", "Bosch", "Siemens", "Samsung", "Sony", "Toyota",
    "Honda", "Hyundai", "Rivian", "Lucid", "Aurora", "Argo", "Mobileye", "Baidu", "Alibaba", "Tencent",
    "ByteDance", "TikTok", "Shein", "Temu", "Pinterest", "Reddit", "Instagram", "Facebook", "LinkedIn",
    "Twitter", "YouTube", "Yelp", "Airbnb", "DoorDash", "Instacart", "Etsy", "Shopify", "Stripe", "Klarna",
    "Zillow", "Tesla", "Ford", "Dell", "Kodak", "Blockbuster", "Nokia", "BlackBerry", "Xerox", "Polaroid",
    "Sears", "Napster", "Cognition", "Scale", "Cohere", "Inflection", "Character", "Replika", "Jetson",
    "Jetsons", "Rosie", "Pepper", "Asimo", "Boston", "Dynamics", "Joytunes", "Quad", "Graphics",
    "CognitiveRx", "Carvana", "Cadillac", "Ferrari", "Porsche", "Mercedes", "Benz", "Volkswagen",
    "Expedia", "Kayak", "Booking", "Grubhub", "Peloton", "Fitbit", "Garmin", "Oura", "Whoop", "Rolex",
    "Nike", "Adidas", "Patagonia", "Coca", "Cola", "Pepsi", "McDonald", "McDonald's", "Hershey", "Heinz",
    # schools, places, institutions
    "Tepper", "Carnegie", "Mellon", "CMU", "Dietrich", "Wharton", "Stanford", "Harvard", "Berkeley",
    "Princeton", "Yale", "MIT", "Penn", "Pitt", "Duquesne", "Allegheny", "Pittsburgh", "Wisconsin", "Madison",
    "Qatar", "Doha", "America", "American", "Americans", "Europe", "European", "China", "Chinese", "India",
    "Indian", "Japan", "Japanese", "Korea", "Korean", "Canada", "Mexico", "Brazil", "Africa", "Asia", "Greece",
    "London", "Paris", "Seattle", "Austin", "Phoenix", "Francisco", "Angeles", "Vegas", "Chicago", "Detroit",
    "Dallas", "Houston", "Denver", "Atlanta", "Miami", "Orlando", "Washington", "Virginia", "Ohio", "Texas",
    "California", "Arizona", "Nevada", "Florida", "Michigan", "Jersey", "York", "Georgia", "Carolina",
    "Liberty", "Shadyside", "Oakland", "Squirrel", "Hill",
    "Lawrenceville", "Strip", "Valley", "Silicon", "Wall", "Street", "Broadway", "Hollywood", "Bay",
    "English", "Spanish", "French", "German", "Hindi", "Mandarin", "Arabic", "Latin", "Greek", "Italian",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday", "January", "February",
    "March", "April", "May", "June", "July", "August", "September", "October", "November", "December",
    "Christmas", "Thanksgiving", "Halloween", "Easter", "God", "Lord", "Jesus", "Bible",
    "Team", "Python", "Professor", "Dr", "Mr", "Mrs", "Ms", "Prof", "Visual", "Studio", "VS", "Code", "Dartmouth", "NavLab", "Navlab",
    "OpenRouter", "Rakuten", "Ghibli", "Saudi", "Antigravity", "README", "NLP", "AGI", "Tavily", "Intercom",
    "Jetstream", "Astra", "Kiro", "Gradio", "XGBoost", "Medtronic", "Gartner", "Bloomberg", "Reuters", "Forbes",
    "Substack", "Trello", "Basecamp", "Calendly", "Venmo", "WhatsApp", "SpaceX", "Moderna", "Optum", "PwC",
    "Teleperformance", "Concentrix", "KeyBank", "Volvo", "Mattel", "Broadcom", "Lego", "Steelers", "Penguins",
    "Pirates", "Indiana", "Oregon", "Nebraska", "Omaha", "Manhattan", "Toronto", "Frankfurt", "Taiwan", "Italy",
    "Philippines", "Nigeria", "Norway", "Bangladesh", "Madagascar", "Australia", "Nashville", "Cincinnati",
}

# Phrases that address a person directly: "thanks, X", "go ahead, X" ...
VOCATIVE_BEFORE = (
    r"thanks|thank you|thank you so much|go ahead|go for it|yes|yeah|yep|yup|okay|ok|sure|right|"
    r"hi|hey|hello|welcome|great|good|nice|awesome|perfect|cool|correct|exactly|please|"
    r"how about you|what about you|over to you|you're up|come on up|congrats|congratulations|"
    r"good job|great job|nice job|well done|good point|great point|great question|good question|"
    r"interesting|love it|got it|i see|absolutely|definitely|totally|wow|oh"
)
VOCATIVE_AFTER = (
    r"what|how|do|did|does|can|could|would|will|are|is|any|go|you|your|tell|why|where|when|"
    r"which|have|had|thoughts|anything|want|wanna|let's|please|come|over"
)
POSSESSIVE_CONTEXT = (
    r"question|point|comment|example|team|group|idea|answer|project|presentation|slide|"
    r"article|story|news|turn|hand|demo|code|notebook|response|suggestion|"
    r"observation|insight|reply|classmate|partner"
)
REFERENCE_VERBS = (
    r"said|says|mentioned|asked|asks|brought up|raised|pointed out|suggested|presented|"
    r"shared|talked about|was saying|is saying|just said|wrote|found|"
    r"noted|answered|showed|is right|was right|nailed it|is presenting|will present|was talking|"
    r"will show|just showed|will tell|will talk|just discussed|discussed|will explain|explained|"
    r"will take over|will continue|is going to (?:show|talk|present|explain)"
)
# Self-introductions and hand-offs in student presentations: "my name is X", "I'm here with X and Y",
# "my partners are X and Y", "pass it on to X". Names in these lists are masked even when they are
# ordinary words ("Meadow", "Harbor") or two-letter initials ("QZ").
_NAME = r"(?:[A-Z][^\W\d_]+(?:['’]s)?|[A-Z]{2,3}\b)"
_NAME_LIST = rf"{_NAME}(?:\s+{_NAME})?(?:\s*(?:,\s*(?:and\s+)?|\s+and\s+|\s*&\s*){_NAME}(?:\s+{_NAME})?)*"
INTRO_STRONG_RE = re.compile(
    r"(?i:\b(?:my name is|my name's|here with|alongside|along with|joined by|presenting with|"
    r"partners? (?:is|are)|teammates? (?:is|are)|group members are|"
    r"pass(?:ing)? (?:it |this )?(?:on |over |back )?to|hand(?:ing)? (?:it |this )?(?:off |over |back )?to|"
    r"turn(?:ing)? it over to|let's welcome))[,:]?\s+(?P<names>" + _NAME_LIST + ")")
INTRO_WEAK_RE = re.compile(r"(?i:\b(?:i'm|i am|this is|we're|we are|call me))\s+(?P<names>" + _NAME_LIST + ")")
ROLL_CALL_RE = re.compile(
    r"^\W*(?:(?i:that's|it's|is it|yes|yeah|okay|ok|and|or|so|um|uh|oh)[,]?\s+)?"
    r"(?P<names>[A-Z][^\W\d_]+(?:\s+[A-Z][^\W\d_]+)?)\s*[?.!…]*\W*$")
# A participant display name read into the captions: "Firstname Lastname: text".
DISPLAY_LABEL_RE = re.compile(r"(?:^|[.?!]\s+)(?P<names>[A-Z][^\W\d_]+(?:\s+[A-Z][^\W\d_]+){1,2}):\s")
# everyday words that also appear in public given-name lists; never glued onto a mask
ABSORB_STOP = {
    "the", "a", "an", "no", "one", "part", "this", "that", "these", "those", "and", "or", "but", "so", "all",
    "my", "his", "her", "our", "their", "your", "with", "by", "for", "from", "to", "of", "in", "on", "at", "as",
    "if", "then", "like", "yes", "ok", "okay", "hi", "hey", "thanks", "thank", "think", "see", "day", "life",
    "list", "link", "case", "little", "major", "fall", "chat", "can", "new", "old", "big", "good",
    "great", "dear", "when", "where", "what", "who", "how", "why", "here", "there", "now", "just", "also",
    "even", "still", "only", "maybe", "said", "ask", "asked", "called", "named", "by", "via", "versus", "vs",
}
INTRO_STOP = {
    "the", "i", "a", "an", "and", "team", "group", "here", "going", "not", "just", "so", "very", "really", "sure",
    "glad", "happy", "excited", "back", "done", "ready", "sorry", "good", "fine", "okay", "ok", "yes", "no",
    "from", "in", "at", "on", "of", "to", "with", "for", "today", "we", "our", "my", "this", "that", "it",
    "they", "he", "she", "you", "everyone", "everybody", "guys", "all", "us", "them", "him", "her",
    "presenting", "talking", "next", "first", "also", "now", "then", "currently", "actually", "basically",
}
TITLE_RE = re.compile(r"\b(?:Dr|Mr|Mrs|Ms|Mx|Prof|Professor|Sir|Dame|Mister|Miss|Doctor)\.?\s+(?P<n>[A-Z][^\W\d_]+(?:['’]s)?)")

WORD_RE = re.compile(r"[^\W\d_](?:[^\W\d_]|['’\-](?=[^\W\d_]))*", re.UNICODE)


def _norm(token: str) -> str:
    """Lowercase, drop a trailing possessive, straighten quotes."""
    t = token.replace("’", "'")
    t = re.sub(r"'s$", "", t)
    t = t.rstrip("'")
    return t.lower()


NON_PERSON_LOW = {_norm(w) for w in NON_PERSON_WORDS}
KNOWN_PERSON_LOW = {_norm(w) for w in KNOWN_PERSON_WORDS}


def is_eponym(text: str, start: int, end: int) -> bool:
    """True when the name at text[start:end] is part of a concept name (Turing test, Naive Bayes)."""
    return bool(EPONYM_AFTER.match(text[end:end + 40]) or EPONYM_BEFORE.search(text[max(0, start - 12):start]))


# ---------------------------------------------------------------------------
# Word lists
# ---------------------------------------------------------------------------
class EnglishWords(set):
    """A word set that also accepts regular inflections (cats, asked, asking, studies, quickly)."""

    def __contains__(self, w) -> bool:  # type: ignore[override]
        base = super().__contains__
        if base(w):
            return True
        if not isinstance(w, str) or len(w) < 4:
            return False
        for suf, add in (("ies", "y"), ("ied", "y"), ("es", ""), ("s", ""), ("ed", ""), ("ed", "e"), ("d", ""),
                         ("ing", ""), ("ing", "e"), ("ly", ""), ("er", ""), ("ers", ""), ("ers", "e"),
                         ("ization", "ize"), ("ation", "ate"), ("ness", ""), ("ment", "")):
            if w.endswith(suf):
                stem = w[: -len(suf)] + add
                if len(stem) >= 3 and (base(stem) or (suf in ("ed", "ing", "er", "ers") and len(stem) >= 4
                                                      and stem[-1] == stem[-2] and base(stem[:-1]))):
                    return True
        return False


def load_english_words() -> set[str]:
    """Lowercase common English words: the lowercase entries of the system
    dictionary (its capitalised entries are proper nouns), regular inflections,
    plus spoken and tech forms."""
    words: set[str] = EnglishWords()
    for path in ("/usr/share/dict/words", "/usr/share/dict/web2"):
        p = Path(path)
        if p.exists():
            for line in p.read_text(errors="ignore").splitlines():
                w = line.strip()
                if w and w[0].islower():
                    words.add(w)
            break
    words |= {
        "ok", "okay", "yeah", "yep", "yup", "nope", "gonna", "wanna", "gotta", "kinda", "sorta", "lemme",
        "alright", "um", "uh", "hmm", "mm", "huh", "oh", "wow", "hey", "hi", "bye", "thanks", "email",
        "online", "website", "app", "apps", "internet", "data", "dataset", "datasets", "chatbot",
        "chatbots", "startup", "startups", "smartphone", "laptop", "laptops", "podcast", "blog",
        "selfie", "emoji", "tweet", "covid", "ai", "ml", "api", "apis", "llm", "llms", "goodbye", "gotcha",
        "lemmatization", "overfitted", "overfitting", "slideshow", "skateboard", "snowboard", "readme", "demo",
        "demos", "agentic", "tokenizer", "tokenization", "embeddings", "chatgpt", "workflow", "workflows",
    }
    return words


def lowercase_vocab(texts, min_count: int = 2) -> set[str]:
    """Words that occur in lowercase at least min_count times in the given texts."""
    c: Counter = Counter()
    for t in texts:
        for w in WORD_RE.findall(t):
            if w.islower():
                c[_norm(w)] += 1
    return {w for w, n in c.items() if n >= min_count}


def load_given_names() -> set[str]:
    """Generic given names (public lists, no student data) for spotting people not on a roster."""
    names: set[str] = set()
    p = Path("/usr/share/dict/propernames")
    if p.exists():
        names |= {w.strip().lower() for w in p.read_text(errors="ignore").splitlines() if w.strip()}
    if NickNamer is not None:
        try:
            nn = NickNamer()
            lookup = getattr(nn, "_nickname_lookup", None) or {}
            for k, vals in lookup.items():
                names.add(k.lower())
                names |= {v.lower() for v in vals}
        except Exception:  # pragma: no cover
            pass
    return {n for n in names if len(n) >= 2}


# ---------------------------------------------------------------------------
# Roster -> scrub list
# ---------------------------------------------------------------------------
PARTICLES = {"de", "da", "di", "la", "le", "van", "von", "der", "del", "du", "st", "bin", "al", "el"}


@dataclass
class ScrubList:
    """Tokens to scrub. Built at run time; never written to disk or printed."""

    exact: set[str] = field(default_factory=set)        # lowercase first/last name parts  -> [student]
    nick: set[str] = field(default_factory=set)         # nicknames / short forms           -> [student]
    ids: set[str] = field(default_factory=set)          # andrew ids / email local parts    -> [student]
    phrases: list[str] = field(default_factory=list)    # "first last" (lowercase)          -> [student]
    fuzzy_pool: list[str] = field(default_factory=list) # tokens >= 5 chars for fuzzy matching
    extra: set[str] = field(default_factory=set)        # override scrub_terms              -> [student]
    person: set[str] = field(default_factory=set)       # override person_terms             -> [person]

    def all_tokens(self) -> set[str]:
        return self.exact | self.nick | self.ids | self.extra

    def kind(self, low: str) -> str | None:
        if low in INSTRUCTOR_FORMS:
            return None
        if low in self.extra:
            return "override_student"
        if low in self.exact:
            return "roster_exact"
        if low in self.ids:
            return "roster_id"
        if low in self.nick:
            return "roster_nickname"
        return None


def _name_parts(value: str) -> list[str]:
    return [p for p in re.split(r"[\s,]+", value.strip()) if p]


def read_rosters(roster_dir: Path) -> list[dict]:
    """Return [{first:[...], last:[...], ids:[...]}] from every CSV in roster_dir.

    Column names vary by export (course roster, Canvas groups, gradebook);
    `indexer/roster.py` reads them all the same way for every stage.
    """
    people = []
    for p in read_people(Path(roster_dir)):
        ids = [v for v in (p["andrew_id"], p["email"].split("@")[0]) if v]
        people.append({"first": _name_parts(p["first"]), "last": _name_parts(p["last"]), "ids": ids})
    return people


def build_scrub_list(people: list[dict], english: set[str], extra_terms=(), person_terms=()) -> ScrubList:
    sl = ScrubList()
    nn = NickNamer() if NickNamer is not None else None
    for p in people:
        parts = []
        for part in p["first"] + p["last"]:
            parts.append(part)
            parts += [t for t in re.split(r"[-']", part) if t]
        for t in parts:
            low = _norm(t)
            if len(low) >= 2 and low not in PARTICLES:
                sl.exact.add(low)
        for i in p["ids"]:
            if len(i) >= 3:
                sl.ids.add(i.lower())
        if p["first"] and p["last"]:
            sl.phrases.append(f"{p['first'][0]} {p['last'][-1]}".lower())
        for f in p["first"]:
            low = _norm(f)
            if nn is not None:
                try:
                    sl.nick |= {n.lower() for n in nn.nicknames_of(low)}
                    sl.nick |= {n.lower() for n in nn.canonicals_of(low)}
                except Exception:  # pragma: no cover
                    pass
            # short forms: leading letters of a long first name ("Alexandra" -> "Alex")
            if len(low) >= 6:
                for k in (3, 4, 5):
                    pre = low[:k]
                    if k < len(low) - 1 and pre not in english:
                        sl.nick.add(pre)
    sl.extra = {_norm(t) for t in extra_terms if t.strip()}
    sl.person = {_norm(t) for t in person_terms if t.strip()}
    sl.exact -= INSTRUCTOR_FORMS
    sl.nick -= sl.exact | INSTRUCTOR_FORMS
    sl.fuzzy_pool = sorted({t for t in sl.exact | sl.extra if len(t) >= 5})
    return sl


# ---------------------------------------------------------------------------
# Transcript sources
# ---------------------------------------------------------------------------
TS_RE = re.compile(r"(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(?:(\d+):)?(\d{2}):(\d{2})[.,](\d{3})")
LABEL_RE = re.compile(r"^\s*[A-Z][\w.'\- ]{0,38}:\s+")


def _secs(h, m, s, ms) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(text: str, strip_labels: bool = True) -> list[dict]:
    """Parse WebVTT into [{start, end, text}] (seconds). Drops a "Name:" speaker label."""
    cues = []
    for b in re.split(r"\n\s*\n", text.replace("\r\n", "\n").strip()):
        lines = b.split("\n")
        for i, line in enumerate(lines):
            m = TS_RE.search(line)
            if m:
                g = m.groups()
                body = " ".join(l.strip() for l in lines[i + 1:] if l.strip())
                if strip_labels:
                    body = LABEL_RE.sub("", body, count=1)
                body = re.sub(r"<[^>]+>", "", body).strip()
                if body:
                    cues.append({"start": round(_secs(*g[:4]), 3), "end": round(_secs(*g[4:]), 3), "text": body})
                break
    return cues


def load_clean_jsonl(path: Path) -> list[dict]:
    cues = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        t = (r.get("text") or "").strip()
        if t:
            cues.append({"start": round(float(r["start_seconds"]), 3), "end": round(float(r["end_seconds"]), 3),
                         "text": t})
    return cues


def find_clean_file(archive: Path, date: str) -> Path | None:
    hits = sorted((archive / CLEAN_DIR).glob(f"*{date}*.cleaned.timed.jsonl"))
    return hits[0] if hits else None


def _media_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                              str(path)], capture_output=True, text=True, timeout=60)
        return float(out.stdout.strip())
    except Exception:
        return None


def whisper_problem(cues: list[dict]) -> str | None:
    """Return why a Whisper transcript looks like silence hallucination, or None if it looks like speech."""
    if not cues:
        return "empty"
    texts = Counter(re.sub(r"\W+", " ", c["text"].lower()).strip() for c in cues)
    top, top_n = texts.most_common(1)[0]
    minutes = max((cues[-1]["end"] - cues[0]["start"]) / 60, 1e-6)
    wpm = sum(len(c["text"].split()) for c in cues) / minutes
    if top_n / len(cues) >= 0.5:
        return f"{top_n}/{len(cues)} cues are the same text ({len(top.split())} words)"
    if len(texts) <= 3:
        return f"only {len(texts)} distinct cue texts"
    if wpm < 20:
        return f"{wpm:.0f} words per minute"
    return None


def load_whisper(folder: Path) -> tuple[list[dict], str | None]:
    """Whisper fallback: whisper/audio.vtt plus whisper/audio_part2.vtt offset by part 1's duration."""
    w = folder / "whisper"
    p1 = w / "audio.vtt"
    if not p1.exists():
        return [], "no whisper/audio.vtt"
    cues = parse_vtt(p1.read_text(errors="ignore"), strip_labels=False)
    p2 = w / "audio_part2.vtt"
    if p2.exists():
        offset = _media_duration(folder / "audio.m4a") or (cues[-1]["end"] if cues else 0.0)
        for c in parse_vtt(p2.read_text(errors="ignore"), strip_labels=False):
            cues.append({"start": round(c["start"] + offset, 3), "end": round(c["end"] + offset, 3), "text": c["text"]})
    return cues, whisper_problem(cues)


def merge_cues(cues: list[dict], max_gap: float = 0.3, min_words: int = 20, max_chars: int = 900) -> list[dict]:
    """Join Zoom's mid-sentence splits of long speech, keeping first start / last end.

    Only merges when the earlier cue does not end a sentence, both sides are long
    enough to be continuous lecture speech, and the gap is tiny. Short cues (where
    student turns live) are never merged into neighbours.
    """
    out: list[dict] = []
    for c in cues:
        if out:
            prev = out[-1]
            if (
                not re.search(r"[.?!][\"')\]]?$", prev["text"])
                and c["start"] - prev["end"] <= max_gap
                and len(prev["text"].split()) >= min_words
                and len(c["text"].split()) >= min_words
                and len(prev["text"]) + len(c["text"]) <= max_chars
            ):
                prev["text"] = f"{prev['text']} {c['text']}"
                prev["end"] = c["end"]
                continue
        out.append(dict(c))
    return out


# ---------------------------------------------------------------------------
# Name scrubbing
# ---------------------------------------------------------------------------
STUDENT_RULES = {"roster_exact", "roster_id", "roster_nickname", "roster_fuzzy", "override_student",
                 "context_name", "observed_name", "intro_name"}


class Scrubber:
    def __init__(self, scrub: ScrubList, english: set[str], given_names: set[str], keep_terms=(),
                 frequent: set[str] | None = None):
        self.s = scrub
        self.english = english
        # words the speakers actually use in lowercase; a capitalised name at the start of a
        # sentence is only left alone when it is one of these ("Will this work?" vs "Pedro wrote")
        self.frequent = frequent if frequent is not None else english
        self.given = given_names - INSTRUCTOR_FORMS
        self.given_full = load_given_names() - INSTRUCTOR_FORMS  # incl. everyday-word names (Mark, Will)
        self.keep = {_norm(k) for k in keep_terms}
        phrases = sorted(KNOWN_PERSON_PHRASES, key=len, reverse=True)
        self.person_phrase_re = re.compile(
            r"(?<![\w-])(?:" + "|".join(re.escape(p) for p in phrases) + r")(?:['’]s)?(?![\w-])", re.IGNORECASE)
        self.observed: set[str] = set()  # names found in address/reference context, masked everywhere
        self.strong_ctx = [
            re.compile(r"\b(?i:" + VOCATIVE_BEFORE + r")[,!.]?\s+(?P<n>[A-Z][^\W\d_]+(?:['’]s)?)(?=\s*(?:[,.!?;]|$))"),
            re.compile(r"(?:^|[.?!]\s+)(?P<n>[A-Z][^\W\d_]+),\s+(?i:" + VOCATIVE_AFTER + r")\b"),
            re.compile(r"\b(?P<n>[A-Z][^\W\d_]+)['’]s\s+(?:" + POSSESSIVE_CONTEXT + r")\b"),
            re.compile(r"\b(?i:my name is|name's|meet|named|called on|ask)\s+(?P<n>[A-Z][^\W\d_]+)\b"),
        ]
        self.weak_ctx = [
            re.compile(r"(?<![.?!]\s)\b(?P<n>[A-Z][^\W\d_]+)\s+(?:" + REFERENCE_VERBS + r")\b"),
        ]

    # -- helpers -----------------------------------------------------------
    def is_common(self, low: str) -> bool:
        return low in self.frequent

    @staticmethod
    def _sentence_start(text: str, i: int) -> bool:
        before = text[:i].rstrip(" \"'“‘(*#-")
        return before == "" or before[-1] in ".?!:;]" or before.endswith("\n")

    def _ctx_candidate(self, tok: str, strong: bool) -> bool:
        """Is a capitalised token in address/reference context plausibly a person?"""
        low = _norm(tok)
        if low in INSTRUCTOR_FORMS or low in NON_PERSON_LOW or low in self.keep:
            return False
        if tok.isupper() and len(tok) <= 5:
            return False
        if self.s.kind(low) or (low in self.given and low not in self.frequent):
            return True
        return strong and low not in self.english

    def intro_names(self, text: str) -> list[tuple[int, int]]:
        """Spans of names in self-introductions, hand-offs and roll calls (all -> [student])."""
        spans = []
        for rx, strict in ((INTRO_STRONG_RE, False), (INTRO_WEAK_RE, True), (ROLL_CALL_RE, True),
                           (DISPLAY_LABEL_RE, True)):
            for m in rx.finditer(text):
                base = m.start("names")
                for t in WORD_RE.finditer(m.group("names")):
                    tok = t.group(0)
                    low = _norm(tok)
                    if low in INTRO_STOP or low in INSTRUCTOR_FORMS or low in NON_PERSON_LOW or low in self.keep:
                        continue
                    if low in KNOWN_PERSON_LOW:
                        continue
                    if tok.isupper() and len(tok) > 3:
                        continue  # acronyms such as MBA, NASA
                    if strict and (low in self.frequent or low in self.english and low not in self.given
                                   and not self.s.kind(low)):
                        continue
                    spans.append((base + t.start(), base + t.end()))
        return spans

    def collect_context_names(self, text: str) -> None:
        """First pass over all transcripts: remember non-roster names found in address context."""
        for a, b in self.intro_names(text):
            low = _norm(text[a:b])
            # ordinary words named in an introduction are masked there, but only real-looking
            # names are learned and masked everywhere
            if not self.s.kind(low) and len(low) >= 3 and (low not in self.english or low in self.given):
                self.observed.add(low)
        for rxs, strong in ((self.strong_ctx, True), (self.weak_ctx, False)):
            for rx in rxs:
                for m in rx.finditer(text):
                    tok = m.group("n")
                    low = _norm(tok)
                    if not self.s.kind(low) and self._ctx_candidate(tok, True) and low not in KNOWN_PERSON_LOW:
                        self.observed.add(low)

    # -- main --------------------------------------------------------------
    def scrub(self, text: str, counts: Counter | None = None) -> str:
        counts = counts if counts is not None else Counter()
        hits: dict[int, tuple[int, str]] = {}  # start -> (end, rule)
        # 0) institution terms ("Andrew ID", "andrew.cmu.edu"; indexer/roster.py) are never names
        kept = institution_spans(text)

        def free(a: int, b: int) -> bool:
            return not any(x < b and a < y for x, y in kept)

        # 1) roster "first last" phrases -> [student]
        for ph in self.s.phrases:
            for m in re.finditer(r"\b" + re.escape(ph) + r"(?:['’]s)?\b", text, re.IGNORECASE):
                if free(m.start(), m.end()):
                    hits[m.start()] = (m.end(), "roster_exact")
        # 2) known public figures by full name -> [person] (beats the product word in "Claude Shannon")
        for m in self.person_phrase_re.finditer(text):
            if m.start() not in hits and not is_eponym(text, m.start(), m.end()) and free(m.start(), m.end()):
                hits[m.start()] = (m.end(), "known_person")
        covered = kept + [(a, b) for a, (b, _) in hits.items()]

        # 3) self-introductions, hand-offs and roll calls -> [student]
        for a, b in self.intro_names(text):
            if a not in hits and not any(x <= a < y for x, y in covered):
                hits[a] = (b, "intro_name")
        covered = kept + [(a, b) for a, (b, _) in hits.items()]

        ctx_starts: set[int] = set()
        for rx in self.strong_ctx + self.weak_ctx:
            for m in rx.finditer(text):
                ctx_starts.add(m.start("n"))
        title_starts = {m.start("n") for m in TITLE_RE.finditer(text)}

        tokens = list(WORD_RE.finditer(text))
        for idx, m in enumerate(tokens):
            tok = m.group(0)
            a, b = m.start(), m.end()
            if any(x <= a < y for x, y in covered):
                continue
            low = _norm(tok)
            if low in INSTRUCTOR_FORMS or (low in self.keep and not self.s.kind(low)):
                continue
            cap = tok[:1].isupper()
            acronym = tok.isupper() and 1 < len(tok) <= 5
            sent_start = self._sentence_start(text, a)
            common = self.is_common(low)
            non_person = low in NON_PERSON_LOW
            kind = self.s.kind(low)
            in_ctx = a in ctx_starts
            prev_tok = tokens[idx - 1].group(0) if idx > 0 else ""
            next_tok = tokens[idx + 1].group(0) if idx + 1 < len(tokens) else ""
            adjacent = (prev_tok, next_tok)
            near_name = any(t[:1].isupper() and (self.s.kind(_norm(t)) or _norm(t) in self.given)
                            and _norm(t) not in NON_PERSON_LOW and _norm(t) not in INSTRUCTOR_FORMS for t in adjacent)
            rule = None

            # --- students (roster and overrides) ---
            if kind == "roster_id":
                if not common and not acronym:
                    rule = "roster_id"
            elif kind == "override_student":
                if cap or not common:
                    rule = kind
            elif kind:
                if acronym and len(low) <= 3:
                    pass
                elif non_person and not (in_ctx or near_name):
                    pass  # e.g. a student sharing a product/place name; only masked in name context
                elif not common:
                    if cap or len(low) >= 4:
                        rule = kind
                elif cap and (not sent_start or in_ctx or near_name):
                    rule = kind
            epo = is_eponym(text, a, b)
            if rule is None and epo:
                continue  # "Nash equilibrium", "Turing test": a concept, not a mention of the person
            if rule is None and cap and not acronym and not non_person and \
                    (low in self.s.person or low in KNOWN_PERSON_LOW):
                rule = "override_person" if low in self.s.person else "known_person"
            if rule is None and cap and not acronym and low in self.observed and not non_person:
                if not common or not sent_start or in_ctx:
                    rule = "observed_name"
            if rule is None and cap and not acronym and in_ctx and self._ctx_candidate(tok, True) \
                    and low not in KNOWN_PERSON_LOW:
                rule = "context_name"
            if rule is None and cap and not acronym and len(low) >= 5 and low not in self.english and not non_person \
                    and low not in KNOWN_PERSON_LOW and low not in self.given and self.s.fuzzy_pool:
                best = process.extractOne(low, self.s.fuzzy_pool, scorer=fuzz.ratio, score_cutoff=90)
                if best is None and len(low) >= 6:
                    best = process.extractOne(low, self.s.fuzzy_pool, scorer=JaroWinkler.normalized_similarity,
                                              score_cutoff=0.93)
                if best is not None:
                    rule = "roster_fuzzy"

            # --- everyone else -> [person] ---
            if rule is None and low in self.s.person and (cap or not common):
                rule = "override_person"
            if rule is None and cap and not acronym and not non_person and not is_eponym(text, a, b):
                if low in KNOWN_PERSON_LOW and not is_eponym(text, a, b) and (not common or not sent_start):
                    rule = "known_person"
                elif a in title_starts and (not common or not sent_start):
                    rule = "title_name"
                elif low in self.given and (not sent_start or not common or near_name):
                    rule = "given_name"
                elif near_name and low not in self.english and not sent_start:
                    rule = "name_pair"  # surname next to a first name: "Priya Xyz"
            if rule and rule not in STUDENT_RULES and kind and kind != "roster_id":
                rule = kind  # a roster student caught by a generic rule is still a student
            if rule:
                hits[a] = (b, rule)

        if not hits:
            return text
        out, pos = [], 0
        for a in sorted(hits):
            b, rule = hits[a]
            if a < pos:
                continue
            out.append(text[pos:a])
            seg = text[a:b]
            suffix = "'s" if re.search(r"['’]s$", seg) else ""
            out.append((STUDENT if rule in STUDENT_RULES else PERSON) + suffix)
            counts[rule] += 1
            pos = b
        out.append(text[pos:])
        res = "".join(out)
        # a first name right before a mask ("Mark [person]") or an unknown surname right after one
        # ("[person] Xyzzy") belongs to the same person
        def _absorb_before(m):
            low = m.group(1).lower()
            if low in self.frequent and self._sentence_start(m.string, m.start()):
                return m.group(0)  # "Will [student] present?" keeps the verb
            if low in self.given_full and low not in NON_PERSON_LOW and low not in INSTRUCTOR_FORMS \
                    and low not in ABSORB_STOP:
                counts["name_pair"] += 1
                return m.group(2)
            return m.group(0)

        def _absorb_after(m):
            low = _norm(m.group(2))
            if low not in self.english and low not in NON_PERSON_LOW and low not in INSTRUCTOR_FORMS \
                    and low not in self.keep:
                counts["name_pair"] += 1
                return m.group(1)
            return m.group(0)

        res = re.sub(r"\b([A-Z][a-z]+)\s+(\[(?:student|person)\])", _absorb_before, res)
        res = re.sub(r"(\[(?:student|person)\])\s+([A-Z][a-z]+)\b(?!['’]s)", _absorb_after, res)
        # first + last name -> one mask; a student mask absorbs a neighbouring person mask
        res = re.sub(r"\[(student|person)\](?:\s+\[(?:student|person)\])+",
                     lambda m: STUDENT if STUDENT in m.group(0) else PERSON, res)
        return res


# ---------------------------------------------------------------------------
# Speaker labelling heuristics
# ---------------------------------------------------------------------------
STUDENT_MARKERS = re.compile(
    r"\b(?:my company|my team|my job|my internship|my manager|at my work|where i work|i work(?:ed)? (?:at|for|in)|"
    r"in my experience|my undergrad|my previous|i was wondering|i have a question|quick question|"
    r"can you (?:explain|repeat|go back|clarify)|could you (?:explain|repeat|go back|clarify)|"
    r"professor|in my country|back home|my article|our article|the article i|i found an article|"
    r"i chose|i picked|my news|our group|my group|my partner|my partners|we decided to|my name is|"
    r"next slide|i'm here with|pass it on to|pass it over to|our presentation|we're team|our team|"
    r"thank you for listening|any questions for us|my teammates?)\b",
    re.IGNORECASE,
)
# A student presentation starts with a self-introduction and runs until the presenters hand back.
PRESENTATION_START = re.compile(
    r"\b(?:my name is|i'm \[student\],? and|we're team|we are team|we are group|we're group|"
    r"my partners? (?:is|are)|i'm here with|our (?:news|article|presentation|topic) (?:is|today))\b", re.IGNORECASE)
PRESENTATION_END = re.compile(
    r"\b(?:thank you for listening|thanks for listening|that's (?:it|all) (?:for us|from us)|"
    r"any questions\??$|questions for (?:us|them)|let's give (?:them|it up)|round of applause|"
    r"great job|nice job|good job|well done|awesome job)\b", re.IGNORECASE)
MAX_PRESENTATION_S = 20 * 60

INSTRUCTOR_OPENERS = re.compile(
    r"^(?:yeah|yes|great|awesome|exactly|good|nice|perfect|right|okay|ok|all right|alright|so|and so|"
    r"that's|thank you|thanks|cool|correct|absolutely|interesting|love it|wonderful)\b",
    re.IGNORECASE,
)


def label_speakers(cues: list[dict]) -> list[str]:
    """Heuristic first pass. Prefers 'unclear' whenever a cue might be a student."""
    n = len(cues)
    labels = ["instructor"] * n
    words = [len(c["text"].split()) for c in cues]

    # Before the lecture starts: chatter until the first long, lecture-like cue.
    first_long = next((i for i, w in enumerate(words) if w >= 30), 0)
    for i in range(first_long):
        labels[i] = "unclear"

    for i, c in enumerate(cues):
        if i < first_long:
            continue
        t = c["text"].strip()
        w = words[i]
        prev = cues[i - 1] if i else None
        prev_q = bool(prev and prev["text"].rstrip().endswith("?") and c["start"] - prev["end"] < 12)
        if STUDENT_MARKERS.search(t) and w <= 60:
            labels[i] = "unclear" if w > 40 else "student"
            continue
        if w >= 35:
            continue  # long explanatory speech: instructor
        if w < 6:
            labels[i] = "unclear"
            continue
        if INSTRUCTOR_OPENERS.match(t) and w >= 14:
            continue
        if prev_q and w <= 30:
            labels[i] = "unclear"
            continue
        if t.endswith("?") and w <= 20 and re.match(
                r"^(?:is|are|can|could|do|does|did|will|would|should|what if|how do|how does|why|so is|so does|so do|"
                r"what about|is there|isn't)\b", t, re.IGNORECASE):
            labels[i] = "unclear"
            continue
        if w <= 12:
            labels[i] = "unclear"

    # Student presentations: from a self-introduction until a hand-back phrase (max 20 minutes).
    i = 0
    while i < n:
        if PRESENTATION_START.search(cues[i]["text"]):
            t0 = cues[i]["start"]
            j = i
            while j < n and cues[j]["start"] - t0 <= MAX_PRESENTATION_S:
                labels[j] = "student"
                if j > i and PRESENTATION_END.search(cues[j]["text"]):
                    break
                j += 1
            i = j + 1
        else:
            i += 1

    # A short 'instructor' cue between two non-instructor cues is probably part of the exchange.
    for i in range(1, n - 1):
        if labels[i] == "instructor" and words[i] < 20 and labels[i - 1] != "instructor" and labels[i + 1] != "instructor":
            labels[i] = "unclear"

    # After the lecture ends: trailing short chatter.
    j = n - 1
    while j >= 0 and words[j] < 20:
        labels[j] = "unclear"
        j -= 1
    return labels


def apply_label_overrides(cues: list[dict], labels: list[str], overrides: list[dict]) -> int:
    changed = 0
    for ov in overrides or []:
        lo, hi, sp = float(ov["from"]), float(ov["to"]), ov["speaker"]
        if sp not in SPEAKERS:
            raise ValueError(f"bad speaker in override: {sp}")
        for i, c in enumerate(cues):
            mid = (c["start"] + c["end"]) / 2
            if lo <= mid <= hi and labels[i] != sp:
                labels[i] = sp
                changed += 1
    return changed


# ---------------------------------------------------------------------------
# Sessions and IO
# ---------------------------------------------------------------------------
@dataclass
class Session:
    course: str      # "70445"
    session: int
    date: str
    title: str
    folder: Path

    @property
    def vtt(self) -> Path:
        return self.folder / "transcript_raw.vtt"

    @property
    def drive(self) -> Path:
        return self.folder / "transcript_drive.md"

    @property
    def key(self) -> str:
        return f"s{self.session:02d}"


def find_sessions(archive: Path) -> list[Session]:
    out = []
    for course_dir in sorted(archive.glob("[0-9][0-9]-[0-9][0-9][0-9] *")):
        code = course_dir.name.split()[0].replace("-", "")
        for sdir in sorted((course_dir / TERM).glob("[0-9][0-9] *")):
            m = re.match(r"(\d{2})\s+(\d{4}-\d{2}-\d{2})\s+(.*)$", sdir.name)
            if m:
                out.append(Session(code, int(m.group(1)), m.group(2), m.group(3), sdir))
    return out


def load_session_cues(archive: Path, s: Session) -> tuple[list[dict], str | None, str | None]:
    """Return (cues, source, skip_reason). Prefers cleaned cues for CLEAN_COURSES, then the Zoom VTT,
    then Whisper (only when it is real speech)."""
    if s.course in CLEAN_COURSES:
        f = find_clean_file(archive, s.date)
        if f is not None:
            return load_clean_jsonl(f), "faculty_twin_content_cleaned", None
    if s.vtt.exists():
        return parse_vtt(s.vtt.read_text(errors="ignore")), "zoom_vtt", None
    cues, problem = load_whisper(s.folder)
    if cues and problem is None:
        return cues, "whisper", None
    if cues:
        return [], None, f"whisper transcript rejected as non-speech: {problem}"
    return [], None, "no transcript (no Zoom VTT, no cleaned cues, no Whisper)"


def load_overrides(archive: Path, course: str | None = None, key: str | None = None) -> dict:
    base = archive / "_private" / "deid_overrides"
    path = base / "global.json" if course is None else base / course / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _all_overrides(archive: Path, sessions: list[Session]) -> tuple[dict, dict]:
    g = load_overrides(archive)
    per = {(s.course, s.key): load_overrides(archive, s.course, s.key) for s in sessions}
    return g, per


def _fmt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}"


def _snippet(text: str, n: int = 6) -> str:
    w = text.split()
    return " ".join(w[:n]) + (" ..." if len(w) > n else "")


def build_context(archive: Path, sessions: list[Session] | None = None):
    """Word lists, roster scrub list and override terms. Never prints contents."""
    sessions = sessions if sessions is not None else find_sessions(archive)
    english = load_english_words()
    given = load_given_names()
    people = read_rosters(archive / "_private" / "rosters")
    g, per = _all_overrides(archive, sessions)
    extra, person, keep = set(), set(), set()
    for ov in [g, *per.values()]:
        extra |= set(ov.get("scrub_terms", []))
        person |= set(ov.get("person_terms", []))
        keep |= set(ov.get("keep_terms", []))
    scrub = build_scrub_list(people, english, extra, person)
    return english, given, people, scrub, keep, per


def process_all(archive: Path = ARCHIVE, out_dir: Path | None = None, verbose: bool = True) -> list[dict]:
    out_dir = out_dir or archive / "_build" / "transcripts"
    sessions = find_sessions(archive)
    english, given, people, scrub, keep, per_session = build_context(archive, sessions)
    all_texts = []
    for s in sessions:
        cues, _, _ = load_session_cues(archive, s)
        all_texts += [c["text"] for c in cues]
        if s.drive.exists():
            all_texts += s.drive.read_text(errors="ignore").splitlines()
    frequent = {w for w in lowercase_vocab(all_texts) if w in english}
    given = given - lowercase_vocab(all_texts, 5)  # name lists contain everyday words ("chat", "part")
    scrubber = Scrubber(scrub, english, given, keep, frequent)

    loaded: dict = {}
    for s in sessions:
        cues, source, skip = load_session_cues(archive, s)
        loaded[(s.course, s.key)] = (merge_cues(cues), source, skip)
        for c in loaded[(s.course, s.key)][0]:
            scrubber.collect_context_names(c["text"])
        if s.drive.exists():
            scrubber.collect_context_names(s.drive.read_text(errors="ignore"))

    if verbose:
        print(f"roster people: {len(people)}; student scrub tokens: {len(scrub.all_tokens())}; "
              f"names found in address context: {len(scrubber.observed)}")
    summary = []
    for s in sessions:
        cues, source, skip = loaded[(s.course, s.key)]
        if skip:
            summary.append({"course": s.course, "session": s.session, "skipped": skip})
            if verbose:
                print(f"{s.course} {s.key}: SKIPPED ({skip})")
            continue
        ov = per_session[(s.course, s.key)]
        counts: Counter = Counter()
        pg_counts: Counter = Counter()
        for c in cues:
            c["text"] = scrubber.scrub(c["text"], counts)
        labels = label_speakers(cues)
        heur = Counter(labels)
        n_over = apply_label_overrides(cues, labels, ov.get("labels", []))
        for c, lab in zip(cues, labels):
            c["speaker"] = lab
        pg_cues = apply_pg(cues, pg_counts)
        dest = out_dir / s.course
        dest.mkdir(parents=True, exist_ok=True)
        doc = {"course": s.course, "session": s.session, "date": s.date, "title": s.title, "source": source,
               "cues": cues}
        _write_private(dest / f"{s.key}.json", json.dumps(doc, ensure_ascii=False, indent=1))

        drive_counts: Counter = Counter()
        drive_pg: Counter = Counter()
        if s.drive.exists():
            lines = [pg_smooth(scrubber.scrub(l, drive_counts), drive_pg)[0]
                     for l in s.drive.read_text(errors="ignore").splitlines()]
            _write_private(dest / f"{s.key}.drive.md", "\n".join(lines) + "\n")

        mins = Counter()
        for c in cues:
            mins[c["speaker"]] += (c["end"] - c["start"]) / 60
        n_student = sum(v for k, v in counts.items() if k in STUDENT_RULES)
        row = {
            "course": s.course, "session": s.session, "source": source, "cues": len(cues),
            "replacements": sum(counts.values()), "student_masks": n_student,
            "person_masks": sum(counts.values()) - n_student, "by_rule": dict(counts),
            "drive_replacements": sum(drive_counts.values()),
            "labels": dict(Counter(labels)), "minutes": {k: round(v, 1) for k, v in mins.items()},
            "overridden": n_over,
            "pg_cues": pg_cues, "pg_changes": sum(pg_counts.values()), "pg_by_word": dict(pg_counts),
            "drive_pg_changes": sum(drive_pg.values()),
        }
        summary.append(row)
        review = dest / f"{s.key}.review.md"
        write_review(review, s, source, cues, counts, drive_counts, heur, n_over, ov, pg_counts, drive_pg)
        # the spec's review location (never uploaded): _build/review/<course>-s<NN>.txt
        review_dir = out_dir.parent / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        _write_private(review_dir / f"{s.course}-{s.key}.txt", review.read_text())
        if verbose:
            print(f"{s.course} {s.key}: src={source} cues={len(cues)} student={n_student} "
                  f"person={row['person_masks']} drive={row['drive_replacements']} "
                  f"min instr={row['minutes'].get('instructor', 0)} stud={row['minutes'].get('student', 0)} "
                  f"uncl={row['minutes'].get('unclear', 0)} overrides={n_over} "
                  f"pg={row['pg_changes']} ({pg_cues} cues) drive_pg={row['drive_pg_changes']}")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary


def apply_pg(cues: list[dict], counts: Counter | None = None) -> int:
    """PG rule (Ben, Oct 5): after de-identification, swap cursing for a mild word in each cue.

    A changed cue gets ``pg: true`` (the clip stage rejects any window holding one, since
    audio cannot be cleaned). Returns the number of changed cues; ``counts`` gets
    "damn -> darn" style labels only, never cue text.
    """
    changed = 0
    for c in cues:
        c["text"], n = pg_smooth(c["text"], counts)
        if n:
            c["pg"] = True
            changed += 1
        else:
            c.pop("pg", None)
    return changed


def _write_private(path: Path, text: str) -> None:
    """Write a build output readable by the owner only (outputs stay private until uploaded)."""
    path.write_text(text)
    os.chmod(path, 0o600)


def write_review(path: Path, s: Session, source, cues, counts, drive_counts, heur, n_over, ov,
                 pg_counts: Counter | None = None, drive_pg: Counter | None = None) -> None:
    lab = Counter(c["speaker"] for c in cues)
    mins = Counter()
    for c in cues:
        mins[c["speaker"]] += (c["end"] - c["start"]) / 60
    L = [f"# De-identification review: {s.course} {s.key} ({s.date})", "",
         f"Source: {source}. PRIVATE. Counts only; quoted words already have names masked.", "",
         "## Replacements by rule (transcript cues)", ""]
    n_st = sum(v for k, v in counts.items() if k in STUDENT_RULES)
    L += [f"Totals: {STUDENT} {n_st}, {PERSON} {sum(counts.values()) - n_st}", ""]
    L += [f"- {k} -> {STUDENT if k in STUDENT_RULES else PERSON}: {v}" for k, v in sorted(counts.items())] or ["- none"]
    L += ["", f"Drive transcript replacements: {sum(drive_counts.values())} "
              f"({', '.join(f'{k} {v}' for k, v in sorted(drive_counts.items())) or 'none'})", "",
          "## Cues by speaker label", "",
          "| label | cues | minutes | heuristic-only cues |", "|---|---|---|---|"]
    for k in SPEAKERS:
        L.append(f"| {k} | {lab.get(k, 0)} | {mins.get(k, 0):.1f} | {heur.get(k, 0)} |")
    pg_counts, drive_pg = pg_counts or Counter(), drive_pg or Counter()
    L += ["", "## PG language (counts only)", "",
          f"Cues changed: {sum(1 for c in cues if c.get('pg'))} (marked pg: true; no clip may contain one). "
          f"Substitutions: {sum(pg_counts.values())}; Drive transcript: {sum(drive_pg.values())}.", ""]
    L += [f"- {k}: {v}" for k, v in sorted(pg_counts.items())] or ["- none"]
    L += ["", f"Cues changed by the review pass: {n_over}", "", "## Review-pass label ranges", ""]
    for o in ov.get("labels", []):
        L.append(f"- {_fmt(o['from'])} to {_fmt(o['to'])}: {o['speaker']} ({o.get('note', '')})")
    if not ov.get("labels"):
        L.append("- none")
    L += ["", "## Unsure spots", ""]
    for u in ov.get("unsure", []):
        L.append(f"- {_fmt(u['t'])}: {u.get('note', '')}")
    shown = 0
    for i, c in enumerate(cues):
        if c["speaker"] == "unclear" and 6 <= len(c["text"].split()) <= 30 and shown < 15:
            nb = [cues[j]["speaker"] for j in (i - 1, i + 1) if 0 <= j < len(cues)]
            if all(x == "instructor" for x in nb):
                L.append(f"- {_fmt(c['start'])}: isolated unclear cue, \"{_snippet(c['text'])}\"")
                shown += 1
    if not ov.get("unsure") and not shown:
        L.append("- none")
    _write_private(path, "\n".join(L) + "\n")


# ---------------------------------------------------------------------------
# Sweep: prove no roster token or person name survives
# ---------------------------------------------------------------------------
SWEEP_KINDS = ("roster", "roster_nickname", "roster_fuzzy", "override_term", "known_person", "title_name",
               "given_name", "ambiguous", "cap_bigram")
REVIEW_ONLY_KINDS = {"ambiguous", "cap_bigram"}  # read by a person; not automatically names


def sweep_texts(texts, scrub: ScrubList, english: set[str], given: set[str], keep=(),
                frequent: set[str] | None = None) -> list[tuple[str, str]]:
    """Return [(kind, masked context)] for every surviving name-like token. Contexts never show the token."""
    keep = {_norm(k) for k in keep}
    tokens = scrub.exact | scrub.ids | scrub.extra
    index = {t: i for i, t in enumerate(sorted(tokens | scrub.nick | scrub.person))}
    phrase_re = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(p) for p in KNOWN_PERSON_PHRASES) + r")(?![\w-])",
                           re.IGNORECASE)
    given = given - INSTRUCTOR_FORMS
    hits = []
    for text in texts:
        text = blank_institution_terms(text)  # "Andrew ID", "andrew.cmu.edu" are not names (indexer/roster.py)
        for m in phrase_re.finditer(text):
            if not is_eponym(text, m.start(), m.end()):
                hits.append(("known_person", text[max(0, m.start() - 40):m.start()] + "<P>" + text[m.end():m.end() + 40]))
        title_starts = {m.start("n") for m in TITLE_RE.finditer(text)}
        for m in WORD_RE.finditer(text):
            tok = m.group(0)
            low = _norm(tok)
            if low in INSTRUCTOR_FORMS or (low in keep and low not in tokens):
                continue
            cap = tok[:1].isupper()
            acronym = tok.isupper() and 1 < len(tok) <= 5
            common = low in (frequent if frequent is not None else english)
            non_person = low in NON_PERSON_LOW
            sent_start = Scrubber._sentence_start(text, m.start())
            kind = None
            if low in tokens and not acronym:
                if non_person:
                    kind = "ambiguous" if cap else None
                elif not common and (cap or len(low) >= 4):
                    kind = "override_term" if low in scrub.extra and low not in scrub.exact else "roster"
                elif cap and not sent_start:
                    kind = "roster"
                elif cap:
                    kind = "ambiguous"
            elif low in scrub.person and (cap or not common) and not is_eponym(text, m.start(), m.end()):
                kind = "override_term"
            elif low in scrub.nick and cap and not acronym and not non_person and (not sent_start or not common):
                kind = "roster_nickname"
            elif cap and not acronym and not non_person and not is_eponym(text, m.start(), m.end()):
                if low in KNOWN_PERSON_LOW and (not common or not sent_start):
                    kind = "known_person"
                elif m.start() in title_starts and (not common or not sent_start):
                    kind = "title_name"
                elif low in given and (not sent_start or not common):
                    kind = "given_name"
                elif len(low) >= 5 and not common and scrub.fuzzy_pool and \
                        process.extractOne(low, scrub.fuzzy_pool, scorer=fuzz.ratio, score_cutoff=90):
                    kind = "roster_fuzzy"
            if kind is None and cap and not acronym and not non_person and not sent_start and low not in english:
                nxt = WORD_RE.match(text, m.end() + 1) if text[m.end():m.end() + 1] == " " else None
                if nxt and nxt.group(0)[:1].isupper() and _norm(nxt.group(0)) not in english \
                        and _norm(nxt.group(0)) not in NON_PERSON_LOW and not nxt.group(0).isupper():
                    kind = "cap_bigram"  # review signal: two unknown capitalised words ("Xyz Abc")
            if kind:
                ref = f"<R{index[low]}>" if low in index else "<N>"
                ctx = (text[max(0, m.start() - 40):m.start()] + ref + text[m.end():m.end() + 40]).replace("\n", " ")
                hits.append((kind, ctx))
    return hits


def sweep(archive: Path = ARCHIVE, out_dir: Path | None = None, verbose: bool = True) -> dict:
    """Scan every output for roster tokens, fuzzy variants and person-name patterns. Counts only."""
    out_dir = out_dir or archive / "_build" / "transcripts"
    english, given, people, scrub, keep, _ = build_context(archive)
    result = {"files": 0, **{k: 0 for k in SWEEP_KINDS}, "details": []}
    files = sorted(out_dir.rglob("s[0-9][0-9].json")) + sorted(out_dir.rglob("s[0-9][0-9].drive.md"))
    loaded = []
    for f in files:
        if f.suffix == ".json":
            texts = [c["text"] for c in json.loads(f.read_text())["cues"]]
        else:
            texts = f.read_text().splitlines()
        loaded.append((f, texts))
    frequent = {w for w in lowercase_vocab(t for _, ts in loaded for t in ts) if w in english}
    given = given - lowercase_vocab((t for _, ts in loaded for t in ts), 5)
    result["pg_language"] = 0  # texts the PG filter would still change (0 after a run)
    for f, texts in loaded:
        result["files"] += 1
        for kind, ctx in sweep_texts(texts, scrub, english, given, keep, frequent):
            result[kind] += 1
            result["details"].append({"file": f"{f.parent.name}/{f.name}", "kind": kind, "context": ctx})
        result["pg_language"] += sum(1 for t in texts if pg_smooth(t)[1])
    if verbose:
        print(f"files swept: {result['files']}")
        for k in SWEEP_KINDS:
            print(f"{k}: {result[k]}")
        print(f"pg_language: {result['pg_language']}")
    return result


def dump(archive: Path, course: str, session: int, start: int = 0, count: int = 100000) -> None:
    """Print a de-identified session for the review pass (reads the OUTPUT, never the raw transcript)."""
    p = archive / "_build" / "transcripts" / course / f"s{session:02d}.json"
    doc = json.loads(p.read_text())
    for i, c in enumerate(doc["cues"][start:start + count], start):
        print(f"{i}|{_fmt(c['start'])}|{c['start']:.0f}-{c['end']:.0f}|{c['speaker'][:5]}|{c['text']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--archive", type=Path, default=ARCHIVE)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run")
    sw = sub.add_parser("sweep")
    sw.add_argument("--details", action="store_true", help="print masked context for each hit")
    d = sub.add_parser("dump")
    d.add_argument("course")
    d.add_argument("session", type=int)
    d.add_argument("--start", type=int, default=0)
    d.add_argument("--count", type=int, default=100000)
    a = ap.parse_args(argv)
    if a.cmd == "run":
        process_all(a.archive)
    elif a.cmd == "sweep":
        r = sweep(a.archive)
        if a.details:
            for d_ in r["details"]:
                print(f"{d_['kind']}\t{d_['file']}\t{d_['context']}")
        return 1 if any(r[k] for k in SWEEP_KINDS if k not in REVIEW_ONLY_KINDS) or r["pg_language"] else 0
    elif a.cmd == "dump":
        dump(a.archive, a.course, a.session, a.start, a.count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
