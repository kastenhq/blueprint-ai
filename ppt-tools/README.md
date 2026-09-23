# ppt-tools — blueprint concept decks

Every blueprint ships a short slide deck next to its `README.md`. The README is the
reference you read while you build; the deck is what you put on screen to explain the
**concept** before anyone opens a YAML file — the pattern chosen, why it was chosen,
and what it does and does not protect.

This directory is the **shared toolkit**. It is the canonical copy: do not copy it into
a blueprint directory. Each blueprint keeps only its own *content*:

```
ppt-tools/                              <- you are here (shared, one copy)
  veeam_deck.py                         the Deck class and slide builders
  build.py                              the build runner
  README.md
<blueprint>-example/
  ppt-tools/deck.py                     content only, ~150-250 lines
  <slug>.pptx                           the built deck, committed
```

This mirrors how the repository already handles container images — one canonical copy
at the root for anything shared ([images/kasten-tools/](../images/kasten-tools/)), a
local copy only when it genuinely cannot be shared.

---

## Build

The toolkit needs `python-pptx`, `Pillow`, and one SVG rasterizer. `cairosvg` is the
easiest to get; `rsvg-convert` is faster if you already have it.

```bash
python3 -m venv /tmp/deck-venv
/tmp/deck-venv/bin/pip install python-pptx Pillow cairosvg

# all decks
/tmp/deck-venv/bin/python ppt-tools/build.py

# one deck
/tmp/deck-venv/bin/python ppt-tools/build.py cnpg-example

# build all and fail on any layout warning (for CI)
/tmp/deck-venv/bin/python ppt-tools/build.py --check
```

Output looks like this — one line per deck, then any warnings indented underneath:

```
cnpg-example/cnpg.pptx  11 slides, 16 icons  [OK]
datastage-example/datastage.pptx  12 slides, 19 icons  [1 warning(s)]
    ! [datastage] text still overflows at 8.5pt (needs 1.42", box 1.10"): 'The engine …'
```

### The Veeam template bundle

Slide backgrounds, the logo, the theme, the ES Build Neutral fonts and the 65 SVG icons
all come from the `veeam-pptx` skill bundle, which is **not** in this repository. The
toolkit looks for it at `~/.claude/skills/veeam-pptx` and you can point it elsewhere:

```bash
export VEEAM_PPTX_SKILL=/path/to/veeam-pptx
```

Without the bundle the build stops with a clear message rather than producing an
off-brand deck.

> The fonts are installed into your user font directory on first run so that a local
> preview renders correctly. This does **not** embed them in the `.pptx` — a recipient
> without ES Build Neutral sees Calibri, which is the accepted fallback.

---

## Writing a deck

A deck is a sequence of builder calls. Nothing is positioned by hand: each builder owns
its own layout, keeps every shape inside the Veeam content zone, and shrinks text to fit.

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'ppt-tools'))
from veeam_deck import Deck

d = Deck('cnpg', 'cnpg-example')       # -> cnpg-example/cnpg.pptx
d.title("CNPG BACKUP WITH KASTEN", "Cold backup through the operator's own API")
d.agenda(["The workload", "The pattern", "Blueprint actions", "Restore"])
...
d.save()
```

### The builders

| Builder | Slide it makes | Use it for |
|---|---|---|
| `title(title, subtitle)` | Dark cover | Always the first slide |
| `agenda(items)` | Split agenda | Always the second slide |
| `hero(line1, highlight, body, pills, icon, polarity)` | Dark hero | One big point: the risk, the key claim, the punchline |
| `cards(title, subtitle, items, style, note)` | Light card row, 2-4 cards | Parallel points: limitations, prerequisites, capabilities |
| `flow(title, subtitle, stages, note)` | Light flow, 3-5 stages with arrows | The architecture — what writes where, and what Kasten snapshots |
| `table_slide(title, subtitle, headers, rows, side, cards)` | Light table | Versions, the blueprint actions table, comparisons |
| `split_dark(eyebrow, white, grad, steps, left_body)` | Dark split, 2-3 numbered points | A recommendation, a two-part argument |
| `steps_dark(eyebrow, title, steps)` | Dark 2x2 icon grid | The deploy-and-run sequence |
| `two_dark(eyebrow, title, panels)` | Dark, two numbered panels | A distinction that gets confused (for example, two meanings of "restore") |
| `ending(line, body)` | Dark closing | Always the last slide |

`polarity='neg'` switches a hero or a split title from the green/cyan positive gradient
to the yellow/red negative one. Use it for risks and warnings, never decoratively.
`style='negative'` does the same for a card row.

### What the toolkit enforces so you do not have to

- **Content zone** — every shape stays inside the Veeam safe area. Violations are
  reported by slide number after the build.
- **Text fitting** — each text box is measured against the real ES Build Neutral metrics
  and the font size steps down until the text fits. If it still does not fit at the
  floor, you get a warning naming the slide and the text, so you shorten the copy
  instead of shipping a clipped slide.
- **Consistent titles** — every content slide title is 28pt, light or dark.
- **Icons** — always a real bundled SVG, embedded as vector with a visible raster
  fallback. An unmatched keyword falls back to a checkmark rather than an empty plate.
- **Icon and plate colours** — always from the same family, derived from the keyword.
- **No metadata leaks** — author, agency, SharePoint path, add-in binding and first-slide
  thumbnail are all stripped from the saved file on every build.
- **Opens on the slides**, not in Slide Master view.

### Choosing icons

`icon='database'` is a keyword search over the 65 bundled icons, so plain words work:
`shield`, `lock`, `cloud`, `upload`, `server`, `folder`, `history`, `loop`, `rocket`,
`error`, `close`, `virus`, `scan`, `settings`, `checkbox`, `time`, `search`, `sparkling`.
An unmatched keyword returns a checkmark — if you see checkmarks where you did not want
them, pick a different word.

---

## Rules for the content

**No customer information.** These decks are published in a public repository and are
meant to be presented to anyone. Never include a customer or prospect name, a real
cluster or namespace that identifies one, an internal hostname or URL, a support case or
ticket number, a real user name or email, or any figure given in confidence. Where a
blueprint was built for a named engagement, describe the *workload*, not the customer.

**Keep it to the concept.** Ten to twelve slides. The deck answers "what is this and why
is it built this way", not "how do I run it" — the README already does that, better.
Resist copying command lines onto slides.

**Say the mechanism, not just the conclusion.** The same rule the blueprint READMEs
follow. "Kasten discovers PVCs before the prehook runs, so a PVC created in the hook
never reaches the restore point" is useful; "permanent PVCs are better" is not.

**Plain English.** Most readers are not native English speakers and are often reading
under pressure. Avoid idiom. See the word list in [AGENTS.md](../AGENTS.md).

**Keep it in step with the README.** The deck is generated from `deck.py`, but `deck.py`
is written by hand from the README — nothing keeps them in sync automatically. When a
blueprint changes materially, update `deck.py` and rebuild in the same commit.
