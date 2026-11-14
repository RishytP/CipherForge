"""Build the sensitivity-classification regression dataset.

Run:  python tests/fixtures/build_sensitivity_dataset.py

Each example lists detected entities as (entity_type, substring, occurrence,
expected_sensitivity, note). The builder computes exact character offsets so
labels cannot drift from the text, exactly like ``build_dataset.py``.

``expected_sensitivity`` is one of PROTECT / KEEP / REVIEW and encodes the
*right* decision for that span in that sentence:

* PROTECT -- personal / sensitive data that must be anonymised.
* KEEP    -- public organisation / place / generic reference that must stay.
* REVIEW  -- genuinely ambiguous; the engine should not decide confidently.

All data is synthetic. No real personal information is used.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Tuple

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
C = "CREDENTIAL"

PROTECT = "PROTECT"
KEEP = "KEEP"
REVIEW = "REVIEW"

# (entity_type, substring, occurrence, expected_sensitivity, note)
Label = Tuple[str, str, int, str, str]


def ex(id_: str, text: str, entities: List[Label], categories: List[str],
       notes: str = "") -> dict:
    return {
        "id": id_,
        "text": text,
        "entities": [
            {"entity_type": t, "text": s, "occurrence": occ,
             "expected_sensitivity": sens, "note": note}
            for t, s, occ, sens, note in entities
        ],
        "categories": categories,
        "notes": notes,
    }


EXAMPLES: List[dict] = [
    # ---------------- the required regression examples ----------------
    ex("rq01", "How do I get a 1 crore package at Microsoft?",
       [(O, "Microsoft", 0, KEEP, "Public company; a salary question, not personal data.")],
       ["required", "org_public"]),
    ex("rq02", "I want to work at Google.",
       [(O, "Google", 0, KEEP, "Public company; career intent, not personal data.")],
       ["required", "org_public"]),
    ex("rq03", "Microsoft announced a new product.",
       [(O, "Microsoft", 0, KEEP, "Public company as a news subject.")],
       ["required", "org_public"]),
    ex("rq04", "My friend studies at IIT Delhi.",
       [(L, "IIT Delhi", 0, KEEP, "Public institution; third-party reference.")],
       ["required", "location_public"]),
    ex("rq05", "I live in Delhi.",
       [(L, "Delhi", 0, PROTECT, "Speaker's own location via 'I live in'.")],
       ["required", "location_personal"]),
    ex("rq06", "My email is rahul@example.com.",
       [(E, "rahul@example.com", 0, PROTECT, "Contact detail of the speaker.")],
       ["required", "contact"]),
    ex("rq07", "My password is Microsoft123.",
       [(C, "Microsoft123", 0, PROTECT, "Credential value; protect the secret, not the word.")],
       ["required", "credential"]),
    ex("rq08", "Rahul Sharma works at Microsoft.",
       [(P, "Rahul Sharma", 0, PROTECT, "Named individual tied to the speaker's circle."),
        (O, "Microsoft", 0, KEEP, "Public employer name.")],
       ["required", "mixed"]),
    ex("rq09", "I work for an undisclosed client.",
       [],
       ["required", "no_entity"],
       notes=(
           "No named entity to classify. The engine must not invent or "
           "anonymise anything here; 'undisclosed client' is already private."
       )),
    ex("rq10", "Send my personal records to Microsoft.",
       [(O, "Microsoft", 0, PROTECT, "Recipient in a personal-data transfer frame.")],
       ["required", "personal_transfer"]),

    # ---------------- informal / lowercase / punctuation ----------------
    ex("in01", "hey so im rahul, ping me at 98 765-43210",
       [(P, "rahul", 0, PROTECT, "Self-introduction."),
        (N, "98 765-43210", 0, PROTECT, "Contact number.")],
       ["informal", "contact"]),
    ex("in02", "my no is 98765 43210, call after 6",
       [(N, "98765 43210", 0, PROTECT, "Contact number.")],
       ["informal", "contact"]),
    ex("in03", "i live in pune and work at wipro",
       [(L, "pune", 0, PROTECT, "Speaker's own location."),
        (O, "wipro", 0, KEEP, "Public employer name.")],
       ["informal", "lowercase"]),
    ex("in04", "lol my email is... meera.k@mail.com ig",
       [(E, "meera.k@mail.com", 0, PROTECT, "Contact detail.")],
       ["informal", "contact"]),
    ex("in05", "gimme a call on 98765   43210!!",
       [(N, "98765   43210", 0, PROTECT, "Contact number, odd spacing.")],
       ["informal", "contact"]),

    # ---------------- credentials and government ids ----------------
    ex("cr01", "my api key: sk-abcd1234efgh",
       [(C, "sk-abcd1234efgh", 0, PROTECT, "API secret.")],
       ["credential"]),
    ex("cr02", "the otp is 482913 dont share it",
       [(C, "482913", 0, PROTECT, "One-time code.")],
       ["credential"]),
    ex("id01", "my aadhaar is 2345 6789 0124",
       [(AD, "2345 6789 0124", 0, PROTECT, "Government identity number.")],
       ["indian_ids"]),
    ex("id02", "PAN ABCPE1234F belongs to me",
       [(PAN, "ABCPE1234F", 0, PROTECT, "Government identity number.")],
       ["indian_ids"]),

    # ---------------- public organisations (KEEP) ----------------
    ex("or01", "The company Apple reported strong earnings.",
       [(O, "Apple", 0, KEEP, "Public company as a news subject.")],
       ["org_public"]),
    ex("or02", "Infosys hires thousands of engineers every year.",
       [(O, "Infosys", 0, KEEP, "Public company.")],
       ["org_public"]),
    ex("or03", "Acme Corp won the contract last year.",
       [(O, "Acme Corp", 0, KEEP, "Unknown vendor; not personal data.")],
       ["org_public", "informal"]),
    ex("or04", "Send the invoice to Acme Corp.",
       [(O, "Acme Corp", 0, KEEP, "Business document, not personal data.")],
       ["org_public"]),

    # ---------------- public places (KEEP) ----------------
    ex("lo01", "we visited Goa last summer",
       [(L, "Goa", 0, KEEP, "Third-party / holiday reference.")],
       ["location_public"]),
    ex("lo02", "my friend studies at IIT Bombay",
       [(L, "IIT Bombay", 0, KEEP, "Public institution, third party.")],
       ["location_public"]),

    # ---------------- personal locations (PROTECT) ----------------
    ex("lo03", "I stay in Chennai with my family.",
       [(L, "Chennai", 0, PROTECT, "Speaker's own location.")],
       ["location_personal"]),
    ex("lo04", "my address is 24, MG Road",
       [(A, "24, MG Road", 0, PROTECT, "Identifying street address.")],
       ["location_personal", "address"]),

    # ---------------- ambiguous (REVIEW) ----------------
    ex("am01", "Rahul is a character in this story.",
       [(P, "Rahul", 0, REVIEW, "Fictional reference; not a real person.")],
       ["ambiguous"]),
    ex("am02", "The meeting is in Delhi tomorrow.",
       [(L, "Delhi", 0, REVIEW, "A place with no personal tie; ambiguous whether it identifies anyone.")],
       ["ambiguous", "location_public"]),

    # ---------------- personal transfer frames (PROTECT) ----------------
    ex("tr01", "Please send my medical records to Dr. Mehta.",
       [(P, "Mehta", 0, PROTECT, "Named recipient in a medical-data frame.")],
       ["personal_transfer", "ambiguous"],
       notes="The 'Dr.' title may be split off; the surname is the sensitive span."),
    ex("tr02", "email my bank details to Priya at work",
       [(P, "Priya", 0, PROTECT, "Recipient in a bank-details frame.")],
       ["personal_transfer", "informal"]),
]


def _locate(text: str, substring: str, occurrence: int) -> Tuple[int, int]:
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
                "expected_sensitivity": label["expected_sensitivity"],
                "note": label["note"],
            })
        labels.sort(key=lambda x: x["start"])
        examples.append({
            "id": e["id"],
            "text": e["text"],
            "entities": labels,
            "categories": e["categories"],
            "notes": e["notes"],
        })

    return {
        "version": "1.0",
        "description": (
            "Sensitivity-classification regression set. Every span is synthetic. "
            "expected_sensitivity is the correct PROTECT/KEEP/REVIEW decision in "
            "context, not just the entity type."
        ),
        "count": len(examples),
        "examples": examples,
    }


if __name__ == "__main__":
    payload = build()
    out = Path(__file__).resolve().parent / "sensitivity_dataset.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    spans = sum(len(e["entities"]) for e in payload["examples"])
    print(f"wrote {out} with {len(payload['examples'])} examples / {spans} labelled spans")
