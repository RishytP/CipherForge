"""Build the synthetic evaluation dataset.

Run:  python tests/fixtures/build_dataset.py

Each example lists its PII spans as (entity_type, substring, occurrence). The
builder computes exact character offsets so labels can never drift from the
text. Occurrence defaults to 0 (first unused occurrence).

All data here is synthetic. No real personal information is used.

Gold labels represent what a careful human annotator would mark as PII *in
context*. Examples that are deliberately ambiguous carry ``expect_uncertain``:
the span is still labelled, but the engine is expected to flag it for review
rather than treat it as certain. This lets evaluation measure both detection
quality and honest ambiguity handling without gaming thresholds.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

# (entity_type, substring, occurrence)
Label = Tuple[str, str, int]

P = "PERSON"
E = "EMAIL_ADDRESS"
N = "PHONE_NUMBER"
L = "LOCATION"
O = "ORGANIZATION"
A = "ADDRESS"
Z = "POSTAL_CODE"
IP = "IP_ADDRESS"
U = "URL"
AD = "IN_AADHAAR"
PAN = "IN_PAN"
D = "DATE_TIME"


def ex(id_: str, text: str, entities: List[Label], categories: List[str],
       notes: str = "", expect_uncertain: bool = False, expect_none: bool = False) -> dict:
    return {
        "id": id_,
        "text": text,
        "entities": [{"entity_type": t, "text": s, "occurrence": occ} for t, s, occ in entities],
        "categories": categories,
        "notes": notes,
        "expect_uncertain": expect_uncertain,
        "expect_none": expect_none,
    }


EXAMPLES: List[dict] = [
    # ---------------- properly formatted ----------------
    ex("pf01", "Hello, my name is Rahul Sharma and my email address is rahul.sharma@example.com.",
       [(P, "Rahul Sharma", 0), (E, "rahul.sharma@example.com", 0)], ["properly_formatted"]),
    ex("pf02", "Please contact Dr. Ananya Iyer at ananya.iyer@hospital.org or +91 98765 43210.",
       [(P, "Ananya Iyer", 0), (E, "ananya.iyer@hospital.org", 0), (N, "+91 98765 43210", 0)],
       ["properly_formatted", "indian_phone"]),
    ex("pf03", "You can reach Vikram at vikram.menon@acme.co.in or on his mobile 98450 12345.",
       [(P, "Vikram", 0), (E, "vikram.menon@acme.co.in", 0), (N, "98450 12345", 0)],
       ["properly_formatted", "indian_phone"]),
    ex("pf04", "My postal address is 14, Brigade Road, Bengaluru 560001.",
       [(A, "14, Brigade Road", 0), (L, "Bengaluru", 0), (Z, "560001", 0)],
       ["properly_formatted", "indian_address"]),

    # ---------------- missing punctuation ----------------
    ex("mp01", "my name is priya and my number is 9876543210 call me anytime",
       [(P, "priya", 0), (N, "9876543210", 0)], ["missing_punctuation", "indian_phone"]),
    ex("mp02", "im arjun my email is arjun@gmail.com",
       [(P, "arjun", 0), (E, "arjun@gmail.com", 0)], ["missing_punctuation"]),
    ex("mp03", "hi this is kavya pls note my no 9900112233 thanks",
       [(P, "kavya", 0), (N, "9900112233", 0)], ["missing_punctuation", "abbreviations", "indian_phone"]),
    ex("mp04", "sending you the details now my address is 22 Park Street Kolkata 700016",
       [(A, "22 Park Street", 0), (L, "Kolkata", 0), (Z, "700016", 0)], ["missing_punctuation"]),

    # ---------------- excessive punctuation ----------------
    ex("ep01", "OMG!!! my number is 98765-43210!!! call me!!!",
       [(N, "98765-43210", 0)], ["excessive_punctuation", "indian_phone"]),
    ex("ep02", "hey!!! this is neha... reach me at neha@yahoo.co.in ...",
       [(P, "neha", 0), (E, "neha@yahoo.co.in", 0)], ["excessive_punctuation"]),
    ex("ep03", "so...... my mail is rohit@@ no wait, rohit.k@gmail.com :)",
       [(E, "rohit.k@gmail.com", 0)], ["excessive_punctuation"]),

    # ---------------- abbreviations / informal shorthand ----------------
    ex("ab01", "btw my no is 9876543210, ping me",
       [(N, "9876543210", 0)], ["abbreviations", "informal", "indian_phone"]),
    ex("ab02", "my add is 24 MG road, near the old school",
       [(A, "24 MG road", 0)], ["abbreviations", "informal", "indian_address"]),
    ex("ab03", "u can mail me at sid@outlook.com tbh",
       [(E, "sid@outlook.com", 0)], ["abbreviations", "informal"]),
    ex("ab04", "ig my whatsapp is 9898989898 only",
       [(N, "9898989898", 0)], ["abbreviations", "informal", "indian_phone"]),

    # ---------------- inconsistent spacing ----------------
    ex("sp01", "my   number  is   98 765-43210",
       [(N, "98 765-43210", 0)], ["inconsistent_spacing", "indian_phone"]),
    ex("sp02", "call me at 98765   43210",
       [(N, "98765   43210", 0)], ["inconsistent_spacing", "indian_phone"]),
    ex("sp03", "email :  tanvi . rao @ example . com",
       [(E, "tanvi . rao @ example . com", 0)], ["inconsistent_spacing"]),
    ex("sp04", "my pin code is 5 6 0 0 0 1",
       [(Z, "5 6 0 0 0 1", 0)], ["inconsistent_spacing"]),

    # ---------------- misspellings / typos ----------------
    ex("ty01", "my nme is rahul and my emial is rahul@gmail.com",
       [(P, "rahul", 0), (E, "rahul@gmail.com", 0)], ["misspellings"]),
    ex("ty02", "my numbr is 9876543210 plz cal",
       [(N, "9876543210", 0)], ["misspellings", "indian_phone"]),
    ex("ty03", "my adress is 7 Lake View Road",
       [(A, "7 Lake View Road", 0)], ["misspellings", "indian_address"]),

    # ---------------- informal conversational ----------------
    ex("in01", "hey so im rahul... u can reach me at rahul@gmail.com ig",
       [(P, "rahul", 0), (E, "rahul@gmail.com", 0)], ["informal", "missing_punctuation"]),
    ex("in02", "my no is 98 765-43210, call after 6",
       [(N, "98 765-43210", 0)], ["informal", "indian_phone"]),
    ex("in03", "I told Ananya (the one from Delhi lol) to use my number",
       [(P, "Ananya", 0), (L, "Delhi", 0)], ["informal", "ambiguous"]),
    ex("in04", "lol ok so my email is... wait... it's dev.p@proton.me",
       [(E, "dev.p@proton.me", 0)], ["informal", "excessive_punctuation"]),
    ex("in05", "yo my number changed, it's 9123456780 now, save it",
       [(N, "9123456780", 0)], ["informal", "indian_phone"]),

    # ---------------- mixed sentence structures ----------------
    ex("mx01", "Hi, I'm Meera. I work at Infosys. Email: meera.k@infosys.com.",
       [(P, "Meera", 0), (O, "Infosys", 0), (E, "meera.k@infosys.com", 0)], ["mixed_structures"]),
    ex("mx02", "The document should go to Rahul, not me. His email is rahul@example.com",
       [(P, "Rahul", 0), (E, "rahul@example.com", 0)], ["mixed_structures", "third_person"]),
    ex("mx03", "Mr. Sharma said that Rahul should contact him, not me.",
       [(P, "Sharma", 0), (P, "Rahul", 0)], ["mixed_structures", "third_person", "ambiguous"],
       notes="Two distinct people; engine may under-detect the bare surname.", expect_uncertain=True),
    ex("mx04", "Send it to R.S. at the office, not my personal mail.",
       [(P, "R.S.", 0)], ["mixed_structures", "ambiguous"],
       notes="Initial-style name; should be detected but flagged uncertain.",
       expect_uncertain=True),

    # ---------------- ambiguous names ----------------
    ex("am01", "Rahul is a character in this story.",
       [(P, "Rahul", 0)], ["ambiguous", "fiction"],
       notes="Fictional reference; span is a name but should be flagged uncertain.",
       expect_uncertain=True),
    ex("am02", "For example, say the user's name is John.",
       [(P, "John", 0)], ["ambiguous", "fiction"],
       notes="Illustrative placeholder name.", expect_uncertain=True),
    ex("am03", "My friend Rahul is coming over today.",
       [(P, "Rahul", 0)], ["ambiguous"], notes="Real person, third-person framing."),
    ex("am04", "The company Apple reported strong earnings.",
       [(O, "Apple", 0)], ["ambiguous", "false_positive_risk"],
       notes="Organisation, not a person.", expect_uncertain=True),

    # ---------------- multiple entities ----------------
    ex("mu01", "Reach me at 9876543210 or sneha.rao@company.in, I live in Pune.",
       [(N, "9876543210", 0), (E, "sneha.rao@company.in", 0), (L, "Pune", 0)],
       ["multiple_entities", "indian_phone"]),
    ex("mu02", "Hi I am Arjun Mehta, my PAN is ABCPE1234F and my Aadhaar is 234567890124.",
       [(P, "Arjun Mehta", 0), (PAN, "ABCPE1234F", 0), (AD, "234567890124", 0)],
       ["multiple_entities", "indian_ids"],
       notes="Synthetic PAN and Aadhaar-like numbers chosen to pass format checksums."),
    ex("mu03", "Email aisha@corp.com or call 98200 11223, address 5 Hill Road Mumbai 400001.",
       [(E, "aisha@corp.com", 0), (N, "98200 11223", 0), (A, "5 Hill Road", 0),
        (L, "Mumbai", 0), (Z, "400001", 0)], ["multiple_entities", "indian_phone"]),
    ex("mu04", "Server 10.0.0.5 belongs to ops; ping admin@internal.net if it is down.",
       [(IP, "10.0.0.5", 0), (E, "admin@internal.net", 0)], ["multiple_entities"]),

    # ---------------- repeated entities ----------------
    ex("re01", "Rahul told me his number is 9876543210. Rahul said call anytime.",
       [(P, "Rahul", 0), (P, "Rahul", 1), (N, "9876543210", 0)],
       ["repeated_entities", "indian_phone"]),
    ex("re02", "Email me at a@b.com, again a@b.com, and once more a@b.com.",
       [(E, "a@b.com", 0), (E, "a@b.com", 1), (E, "a@b.com", 2)], ["repeated_entities"]),
    ex("re03", "Rahul Sharma and Rahul are the same person; Rahul's email is rahul.s@x.com.",
       [(P, "Rahul Sharma", 0), (P, "Rahul", 0), (P, "Rahul", 1), (E, "rahul.s@x.com", 0)],
       ["repeated_entities", "coreference"],
       notes="Shorter name should share the placeholder of the longer name."),

    # ---------------- false-positive cases (expect no PII) ----------------
    ex("fp01", "I bought 2 apples and 3 oranges from the market.",
       [], ["false_positive"], expect_none=True),
    ex("fp02", "The train 12951 leaves at 5 in the evening.",
       [], ["false_positive"], expect_none=True),
    ex("fp03", "Call 100 for police and 108 for an ambulance.",
       [], ["false_positive"], notes="Emergency numbers are not personal data.", expect_none=True),
    ex("fp04", "Section 420 of the Indian Penal Code deals with cheating.",
       [], ["false_positive"], expect_none=True),
    ex("fp05", "The score was 98 out of 100 in the final exam.",
       [], ["false_positive"], expect_none=True),
    ex("fp06", "Order 4587 was shipped on Monday.",
       [], ["false_positive"], expect_none=True),
    ex("fp07", "The office is near the old school on MG Road.",
       [], ["false_positive"],
       notes="Generic location reference with no identifying detail.", expect_none=True),
    ex("fp08", "I live near the old school on MG Road.",
       [], ["false_positive", "ambiguous"],
       notes="Could contain location info but no specific address; borderline.",
       expect_none=True),

    # ---------------- Indian names ----------------
    ex("id01", "Hi, I am Suresh Kumar and you can call me at 9876543210.",
       [(P, "Suresh Kumar", 0), (N, "9876543210", 0)], ["indian_names", "indian_phone"]),
    ex("id02", "Lakshmi Narayanan will present today; reach her at lakshmi.n@iitm.ac.in.",
       [(P, "Lakshmi Narayanan", 0), (E, "lakshmi.n@iitm.ac.in", 0)], ["indian_names"]),
    ex("id03", "My name is Ramesh Iyer. My phone is 044-23456789.",
       [(P, "Ramesh Iyer", 0), (N, "044-23456789", 0)], ["indian_names", "indian_phone"]),
    ex("id04", "Sneha and her brother Karthik live in Chennai.",
       [(P, "Sneha", 0), (P, "Karthik", 0), (L, "Chennai", 0)], ["indian_names", "multiple_entities"]),
    ex("id05", "My Aadhaar number is 2345 6789 0124 and I live in Jaipur.",
       [(AD, "2345 6789 0124", 0), (L, "Jaipur", 0)], ["indian_ids", "indian_address"],
       notes="Synthetic Aadhaar-like number chosen to pass the Verhoeff checksum."),
    ex("id06", "PAN card ABCPE1234F belongs to me, my email is me@here.in.",
       [(PAN, "ABCPE1234F", 0), (E, "me@here.in", 0)], ["indian_ids"]),

    # ---------------- Indian phone formats ----------------
    ex("ph01", "reach me on +91-98765-43210",
       [(N, "+91-98765-43210", 0)], ["indian_phone"]),
    ex("ph02", "my mobile is 09876543210",
       [(N, "09876543210", 0)], ["indian_phone"]),
    ex("ph03", "landline 022-2345-6789 works best",
       [(N, "022-2345-6789", 0)], ["indian_phone"]),
    ex("ph04", "call 91 9876543210 tomorrow",
       [(N, "91 9876543210", 0)], ["indian_phone"]),
    ex("ph05", "whatsapp me on 9876543210 or 9876543211",
       [(N, "9876543210", 0), (N, "9876543211", 0)], ["indian_phone", "multiple_entities"]),

    # ---------------- obfuscated / tricky formats ----------------
    ex("ob01", "my mail is rahul (at) gmail (dot) com",
       [(E, "rahul (at) gmail (dot) com", 0)], ["obfuscated"]),
    ex("ob02", "write to me at first.last at gmail dot com",
       [(E, "first.last at gmail dot com", 0)], ["obfuscated"],
       notes="Spaced 'at'/'dot' obfuscation."),
    ex("ob03", "my ip is 192.168.0.101",
       [(IP, "192.168.0.101", 0)], ["structured"]),

    # ---------------- URLs and mixed ----------------
    ex("ur01", "check my profile at https://example.com/users/rahul",
       [(U, "https://example.com/users/rahul", 0)], ["url"]),
    ex("ur02", "docs are at http://10.1.2.3:8080/portal",
       [(U, "http://10.1.2.3:8080/portal", 0)], ["url"],
       notes="The IP inside a URL is part of the URL and is not separately surfaced."),

    # ---------------- negation ----------------
    ex("ng01", "That is not my number, mine is 9876543210.",
       [(N, "9876543210", 0)], ["negation"], notes="'not my number' refers to another number."),
    ex("ng02", "Don't call 9876543210, that's my old number.",
       [(N, "9876543210", 0)], ["negation", "indian_phone"]),

    # ---------------- more informal / realistic ----------------
    ex("rl01", "so anyway my email is basically zoya.k@mail.com lol",
       [(E, "zoya.k@mail.com", 0)], ["informal"]),
    ex("rl02", "name: aditya, ph: 9833004455, city: Nagpur",
       [(P, "aditya", 0), (N, "9833004455", 0), (L, "Nagpur", 0)],
       ["informal", "abbreviations", "indian_phone"]),
    ex("rl03", "hii im tanya frm delhi, numbr 9812345678, mail tanya@x.co",
       [(P, "tanya", 0), (L, "delhi", 0), (N, "9812345678", 0), (E, "tanya@x.co", 0)],
       ["informal", "misspellings", "multiple_entities", "indian_phone"]),
    ex("rl04", "pls share it with Deepak (the designer) not with me",
       [(P, "Deepak", 0)], ["informal", "third_person"]),
    ex("rl05", "my alternate contact is ritu@work.in / 9765432109",
       [(E, "ritu@work.in", 0), (N, "9765432109", 0)], ["informal", "indian_phone"]),
]


def _locate(text: str, substring: str, occurrence: int) -> Tuple[int, int]:
    """Find the ``occurrence``-th occurrence of ``substring`` (0-based).

    Occurrences are counted over the raw text in order of appearance, so gold
    spans may legitimately overlap or contain one another (e.g. "Rahul" inside
    "Rahul Sharma").
    """
    start = 0
    for _ in range(occurrence + 1):
        idx = text.find(substring, start)
        if idx == -1:
            raise ValueError(
                f"substring {substring!r} occurrence {occurrence} not found in {text!r}"
            )
        start = idx + 1
    return idx, idx + len(substring)


def build() -> dict:
    examples = []
    for e in EXAMPLES:
        labels = []
        for label in e["entities"]:
            start, end = _locate(e["text"], label["text"], label["occurrence"])
            labels.append({
                "entity_type": label["entity_type"],
                "start": start,
                "end": end,
                "text": label["text"],
            })
        labels.sort(key=lambda x: x["start"])
        examples.append({
            "id": e["id"],
            "text": e["text"],
            "entities": labels,
            "categories": e["categories"],
            "notes": e["notes"],
            "expect_uncertain": e["expect_uncertain"],
            "expect_none": e["expect_none"],
        })

    return {
        "version": "1.0",
        "description": (
            "Synthetic, fully fictional PII dataset for CipherForge evaluation. "
            "No real personal data. Gold spans are human-annotated PII in context."
        ),
        "matching_note": (
            "A prediction matches a gold span when entity types are equal and the "
            "spans overlap. Exact-boundary matches are reported separately."
        ),
        "count": len(examples),
        "examples": examples,
    }


def main() -> None:
    out = Path(__file__).with_name("synthetic_dataset.json")
    out.write_text(json.dumps(build(), indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out} with {len(EXAMPLES)} examples")


if __name__ == "__main__":
    main()
