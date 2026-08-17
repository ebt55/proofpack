# Adding or changing a form / checklist

The mapping *form → checklist → which items are website-verifiable → how to check each*
is **pure configuration**. Nothing about a category is hard-coded in the pipeline: adding a
new pre-approval form type means adding one YAML file to `checklists/` — no code changes.

## Add a new category

1. Copy the closest existing file in `checklists/` (e.g. `membership.yaml`).
2. Set the header fields:

```yaml
category: art_supplies            # machine id — also what extraction must output
display_name: Art Supplies
form_title_hints:                 # phrases from the form's printed title
  - "Art Supplies Pre-approval"
adults_only: false                # true adds a deterministic 18+ check on the form's age
fee_caps:                         # deterministic caps checked against the form's fees
  - id: cap_budget_year
    label: "Capped at $500 per budget year"
    max_amount: 500
    unit_keywords: []             # [] = matches any fee; else e.g. ["month"], ["course"]
notes:
  - "Free-text program rules shown at the bottom of the report."
exclusion_list: []                # shown to the agent; item categories that must be flagged
exclusion_keywords: []            # deterministic backstop matched against the requested item
```

3. Add the checklist items. Every YES/NO row on the form should appear once, with a `kind`:

```yaml
items:
  - id: published_fees            # snake_case, unique within the file
    form_question: "Does the item have published fees?"     # as printed on the form
    requirement: "A public price for the item is visible"   # phrased so 'met' = good
    kind: website                 # website | internal | document
    check_hint: >                 # instructions the research agent follows for this item
      Find a public dollar price on the provider's site. "Contact us" is not a
      published fee.

  - id: budget_approved
    form_question: "Is this category approved in the budget?"
    kind: internal                # the tool will list it as Internal — never guessed
    internal_reason: "Participant budget data lives in internal systems."

  - id: staff_letter
    form_question: "Are staff background-screened? (letter)"
    kind: document                # reported as Needs Document
    internal_reason: "Proven by a letter from the provider, not the website."
```

4. Register the category id in `preapproval/extraction.py` (`KNOWN_CATEGORIES`) and add it to
   the `CategoryName` literal in `preapproval/models.py` — one string in each place. This is
   the only code the change touches, and it exists so extraction fails loudly on typos
   instead of silently mis-routing a form.

5. Run `python -m pytest tests/` — the config tests validate that every website item has a
   `requirement` and `check_hint`, and every internal item has an `internal_reason`.

## The three `kind` values — the crux

| kind       | Who answers it | Where it shows in the report |
|------------|----------------|------------------------------|
| `website`  | The research agent, with mandatory screenshot evidence | "Website verification" (Found / Not Found / Needs Review) |
| `internal` | Nobody — deliberately | "Internal items — for the reviewer", never guessed |
| `document` | Nobody — needs paperwork | "Requires a document" |

When in doubt, mark an item `internal`. A wrong "Found" is the worst outcome the tool can
produce; an unnecessary "Internal" just means the reviewer checks one more thing by hand.

## Change an existing checklist

Edit the YAML. Typical changes:
- **Cap amount changed by the program** → edit `max_amount` (and `label`).
- **New exclusion** → add to `exclusion_list` (agent guidance) and, if a keyword can catch it,
  `exclusion_keywords` (deterministic backstop).
- **Sharper agent guidance** → edit the item's `check_hint`. Hints are the highest-leverage
  place to encode reviewer knowledge ("prices are usually on the Schedule page").

## Appeals of other categories

`appeal.yaml` currently re-runs the Community Classes checks (the only appeal form provided).
If appeals of other categories appear later, add `appeal_<category>.yaml` files following the
same pattern: copy the base category's website items and add the `appeal:` framing block.
