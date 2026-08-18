"""The report as one file you can open, with its evidence attached.

A PDF is the deliverable and a page PNG is what a machine reads, but neither can
show you *why* a sentence is allowed to say what it says. The citation rule makes
every factual claim point at a bibliography entry, and `report-maker cite` files
an archived copy of the page that entry names — and until now nothing surfaced
either. The snapshot sat on disk, unread.

So this module writes `out/<report-id>.html`: the built pages on one tab, and on
the other, one card per source listing the claims that rest on it. Every `@key`
in those claims is a button, and pressing it shows the source it resolves to
together with a window of the archived text around the matching passage. That is
the whole point — the chain from a sentence to the bytes we took off the web is
one keypress long, and it stays that way in an email attachment.

Which means the file has to be genuinely self-contained. No stylesheet, no
script, no font and no image is fetched: the page images are inlined as `data:`
URIs and everything else is written into the document. It works from `file://`,
from a USB stick, and from a machine with no network at all — because an evidence
bundle that only renders while the CDN is up is not evidence.

Colour and type come from the report's own brand pack, the same values
`brand.tokens_typ` hands to Typst, so the export looks like the report it came
from. The dark variant is derived from those same inks and surfaces rather than
picked, because a second hand-written palette is a second palette to drift.

Everything that came from a report or from a fetched page is escaped on the way
in. A report may legitimately quote a source that contains markup, and this file
is opened in a browser.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path

from . import brand, check, snapshot, sources, vault
from .config import Config
from .workspace import Report, reports

# How much archived text to show around a match. Enough to read the sentence in
# its own paragraph, short enough that the popover does not become the page.
EXCERPT = 600

# Where a statement ends. `.` inside `9.8pt` or `example.com` is not a full stop,
# so a terminator only counts when whitespace or a closing bracket follows it.
TERMINATORS = ".!?"

# A line opening with one of these is structure, not the continuation of the
# sentence above it: a Typst heading, a call, or a comment.
BLOCK_STARTS = "=#/"


class HtmlError(RuntimeError):
    pass


@dataclass
class Claim:
    """One sentence that rests on at least one source.

    Held as alternating parts rather than as a string because the citation has
    to survive as a *thing* — it becomes the button that opens the evidence, and
    a `@key` recovered from rendered text by a second regex would be a second
    definition of what a citation is.
    """

    line: int
    parts: tuple[tuple[str, str], ...]  # ("text", run) | ("cite", key)

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(value for kind, value in self.parts if kind == "cite"))

    @property
    def text(self) -> str:
        return "".join(
            value if kind == "text" else f"@{value}" for kind, value in self.parts
        ).strip()


# ── reading the report ───────────────────────────────────────────────────────


def page_images(report: Report) -> list[Path]:
    """Page PNGs in reading order.

    Typst does not zero-pad, so `page-10` sorts before `page-2` lexically — the
    same numeric sort `pages.py` applies when it writes the index.
    """
    folder = report.pages_dir
    if not folder.is_dir():
        return []
    return sorted(
        folder.glob("page-*.png"),
        key=lambda path: int(re.sub(r"\D", "", path.stem) or 0),
    )


# ── claims ───────────────────────────────────────────────────────────────────


def claims(report: Report) -> list[Claim]:
    """Every sentence in the report that carries a citation, in file order.

    Works on the scrubbed source, so a `@key` inside a comment or a code block is
    not a claim, and cross-references to the document's own `<labels>` are
    dropped — `@fig-timing` points at a figure, not at evidence.
    """
    if not report.main.is_file():
        return []
    raw = report.main.read_text(encoding="utf-8")
    src = check.scrub(raw)
    defined = check.labels(raw)
    cites = [(key, index) for key, index in check.cited_keys(src) if key not in defined]

    call_spans = _call_spans(src)
    found: dict[tuple[int, int], Claim] = {}
    for key, index in cites:
        span = _statement(src, index)
        if not _has_prose(src[span[0] : span[1]]):
            # `source: [@example-page]` is a citation with no sentence around it.
            # The sentence it belongs to is the call's first content block — the
            # quotation in `claim(…)`, the caption in `srcfig(…)`.
            span = _content_block(src, index, call_spans) or span
        if span in found:
            continue  # a second key in a sentence already collected
        found[span] = Claim(
            line=check.line_of(src, index), parts=_parts(src, span, cites, key)
        )
    return [found[span] for span in sorted(found)]


def _parts(
    src: str, span: tuple[int, int], cites: list[tuple[str, int]], key: str
) -> tuple[tuple[str, str], ...]:
    """Split a statement into text runs and the citations between them."""
    start, end = span
    inside = [(k, i) for k, i in cites if start <= i < end]
    parts: list[tuple[str, str]] = []
    cursor = start
    for cited, index in inside:
        parts.append(("text", _flatten(src[cursor:index])))
        parts.append(("cite", cited))
        cursor = _cite_end(src, index)
    parts.append(("text", _flatten(src[cursor:end])))
    if not inside:
        # The statement was widened past its own citation — say which key it is,
        # or the sentence would arrive with nothing to press.
        parts.append(("text", " "))
        parts.append(("cite", key))
    kept = [part for part in parts if part[1]]
    if kept and kept[0][0] == "text":
        kept[0] = ("text", kept[0][1].lstrip())
    if kept and kept[-1][0] == "text":
        kept[-1] = ("text", kept[-1][1].rstrip())
    return tuple(part for part in kept if part[1])


def _cite_end(src: str, index: int) -> int:
    """Offset just past `@key` in the source. Trailing punctuation stays outside,
    exactly as `check.cited_keys` treats it — `@page.` cites `page`."""
    cursor = index + 1
    while cursor < len(src) and (src[cursor].isalnum() or src[cursor] in "_.:+-"):
        cursor += 1
    while cursor > index + 1 and src[cursor - 1] in ".:+-":
        cursor -= 1
    return cursor


def _flatten(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _has_prose(text: str) -> bool:
    """True when there are words here beyond the citations themselves."""
    return bool(re.search(r"[A-Za-z]{3,}", re.sub(r"@[\w.:+-]+", " ", text)))


def _statement(src: str, index: int) -> tuple[int, int]:
    return _scan_back(src, index), _scan_forward(src, index)


def _scan_back(src: str, index: int) -> int:
    cursor = index
    while cursor > 0:
        char = src[cursor - 1]
        if char in "[]":
            return cursor
        if char in TERMINATORS and src[cursor : cursor + 1].isspace():
            return cursor
        if char == "\n":
            if _blank_line_before(src, cursor - 1) or _opens_block(src, cursor):
                return cursor
        cursor -= 1
    return 0


def _scan_forward(src: str, index: int) -> int:
    cursor = index
    while cursor < len(src):
        char = src[cursor]
        if char in "[]":
            return cursor
        if char in TERMINATORS and _ends_sentence(src, cursor + 1):
            return cursor + 1
        if char == "\n":
            after = cursor + 1
            if _blank_line_after(src, cursor) or _opens_block(src, after):
                return cursor
        cursor += 1
    return len(src)


def _ends_sentence(src: str, after: int) -> bool:
    return after >= len(src) or src[after].isspace() or src[after] in "])},"


def _line_from(src: str, start: int) -> str:
    end = src.find("\n", start)
    return src[start:] if end < 0 else src[start:end]


def _opens_block(src: str, start: int) -> bool:
    return _line_from(src, start).lstrip()[:1] in tuple(BLOCK_STARTS)


def _blank_line_before(src: str, newline: int) -> bool:
    previous = src.rfind("\n", 0, newline)
    return not src[previous + 1 : newline].strip()


def _blank_line_after(src: str, newline: int) -> bool:
    return not _line_from(src, newline + 1).strip()


def _call_spans(src: str) -> list[tuple[int, int]]:
    """The argument list of every `name(…)` in the source, outermost usable for
    finding which call a stray citation is buried in."""
    spans = []
    for match in re.finditer(r"(?<![\w.-])[A-Za-z][\w-]*\s*\(", src):
        spans.append(check.call_span(src, match.end() - 1))
    return spans


def _content_block(
    src: str, index: int, call_spans: list[tuple[int, int]]
) -> tuple[int, int] | None:
    """The first `[…]` content block of the outermost call containing `index`."""
    enclosing = [span for span in call_spans if span[0] <= index < span[1]]
    if not enclosing:
        return None
    start, end = min(enclosing)
    cursor = start
    while cursor < end:
        if src[cursor] == "[":
            _, closing = check.call_span(src, cursor)
            block = (cursor + 1, closing - 1)
            return block if _has_prose(src[block[0] : block[1]]) else None
        cursor += 1
    return None


# ── excerpts ─────────────────────────────────────────────────────────────────


def excerpt(text: str, sentence: str, width: int = EXCERPT) -> str:
    """A window of archived text centred on the best match for `sentence`.

    Matching is by distinctive words rather than by `difflib`: the claim is a
    paraphrase far more often than a quotation, so the useful signal is *which
    part of the page talks about this*, and a whole-page sequence match costs
    seconds to answer a question word overlap answers instantly.
    """
    body = _flatten(text).strip()
    if len(body) <= width:
        return body
    wanted = _distinctive(sentence)
    haystack = body.lower()
    positions = sorted(
        {position for word in wanted for position in _occurrences(haystack, word)}
    )
    if not positions:
        return _trim(body, 0, width)

    half = width // 2
    best, score = positions[0], -1
    for position in positions:
        window = haystack[max(0, position - half) : position + half]
        hits = sum(1 for word in wanted if word in window)
        if hits > score:
            best, score = position, hits
    return _trim(body, max(0, best - half), width)


def _distinctive(sentence: str) -> list[str]:
    words = re.findall(r"[a-z0-9]{4,}", sentence.lower())
    return list(dict.fromkeys(word for word in words if word not in sources.STOPWORDS))


def _occurrences(haystack: str, word: str, limit: int = 40) -> list[int]:
    found: list[int] = []
    position = haystack.find(word)
    while position >= 0 and len(found) < limit:
        found.append(position)
        position = haystack.find(word, position + 1)
    return found


def _trim(body: str, start: int, width: int) -> str:
    """A window snapped out to whole words, with ellipses where it was cut."""
    end = min(len(body), start + width)
    if start > 0:
        start = body.find(" ", start) + 1 or start
    if end < len(body):
        cut = body.rfind(" ", start, end)
        end = cut if cut > start else end
    return ("… " if start > 0 else "") + body[start:end] + (" …" if end < len(body) else "")


# ── colour ───────────────────────────────────────────────────────────────────
#
# Every value below is the brand pack's own, or a mix of two of them. The
# fractions are structure — how far a surface moves towards its ink — and stay
# true for any pack; a hex code written here would be a palette this file owns,
# which is exactly what the brand pack exists to prevent.


def _rgb(value: str) -> tuple[float, float, float] | None:
    text = str(value).strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6 or any(char not in "0123456789abcdefABCDEF" for char in text):
        return None
    return tuple(int(text[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(part))):02X}" for part in rgb)


def _mix(base: str, other: str, amount: float) -> str:
    """`amount` of `other` mixed into `base`. Either colour unreadable → base."""
    first, second = _rgb(base), _rgb(other)
    if first is None or second is None:
        return base
    return _hex(tuple(a + (b - a) * amount for a, b in zip(first, second)))


def _luminance(value: str) -> float:
    rgb = _rgb(value)
    if rgb is None:
        return 0.0
    channels = []
    for part in rgb:
        c = part / 255
        channels.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(one: str, other: str) -> float:
    a, b = _luminance(one), _luminance(other)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def _readable(color: str, on: str, toward: str, target: float = 4.5) -> str:
    """Nudge `color` towards `toward` until it reads on `on`.

    An accent chosen to sit on paper is usually too dark for a dark background.
    Rather than pick a second accent, walk the pack's own colour towards the
    pack's own surface until the contrast clears AA.
    """
    for step in range(12):
        candidate = _mix(color, toward, step * 0.08)
        if _contrast(candidate, on) >= target:
            return candidate
    return _mix(color, toward, 0.88)


def palette(pack: dict) -> tuple[dict[str, str], dict[str, str]]:
    """The light and dark colour tokens for one brand pack."""
    c = pack["colors"]
    light = {
        "bg": c["surface"],
        "panel": c["surface-alt"],
        "ink": c["ink"],
        "ink-soft": c["ink-soft"],
        "ink-muted": c["ink-muted"],
        "rule": c["rule"],
        "rule-light": c["rule-light"],
        "accent": c["accent"],
        "accent-deep": c["accent-deep"],
        "accent-tint": c["accent-tint"],
        "positive": c["positive"],
    }

    ink, surface = c["ink"], c["surface"]
    bg = _mix(ink, surface, 0.08)
    accent = _readable(c.get("accent-bright", c["accent"]), bg, surface)
    dark = {
        "bg": bg,
        "panel": _mix(ink, surface, 0.15),
        "ink": _mix(surface, ink, 0.10),
        "ink-soft": _mix(surface, ink, 0.32),
        "ink-muted": _mix(surface, ink, 0.46),
        "rule": _mix(ink, surface, 0.26),
        "rule-light": _mix(ink, surface, 0.18),
        "accent": accent,
        "accent-deep": _mix(accent, surface, 0.25),
        "accent-tint": _mix(bg, accent, 0.20),
        "positive": _readable(c["positive"], bg, surface),
    }
    return light, dark


def _family(names, generic: str) -> str:
    if isinstance(names, str):
        names = [names]
    quoted = ", ".join(f'"{name}"' if " " in str(name) else str(name) for name in names)
    return f"{quoted}, {generic}"


def stylesheet(pack: dict) -> str:
    light, dark = palette(pack)
    sizes, space = pack["sizes"], pack["space"]

    def block(tokens: dict[str, str]) -> str:
        return "".join(f"    --{name}: {value};\n" for name, value in tokens.items())

    typography = {
        "font-display": _family(pack["fonts"]["display"], "serif"),
        "font-text": _family(pack["fonts"]["text"], "system-ui, sans-serif"),
        "font-mono": _family(pack["fonts"]["mono"], "monospace"),
        **{f"size-{name}": value for name, value in sizes.items()},
        **{f"space-{name}": value for name, value in space.items()},
    }

    return f"""
:root {{
{block(light)}{block(typography)}    color-scheme: light dark;
}}
@media (prefers-color-scheme: dark) {{
  :root {{
{block(dark)}  }}
}}

*, *::before, *::after {{ box-sizing: border-box; }}
html {{ background: var(--bg); }}
body {{
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: var(--font-text);
  font-size: var(--size-body);
  line-height: 1.55;
  -webkit-text-size-adjust: 100%;
}}
/* Tabs and popovers are hidden only once the head script has marked the page
   scripted. With scripting off — a stripped viewer, a print pipeline — the
   whole thing degrades to one long document with every excerpt on show, which
   is the right failure for an evidence bundle. */
.js .pop {{ display: none; }}
.js .pop.is-open {{ display: block; }}
.js [role="tabpanel"][data-active="false"] {{ display: none; }}

header.masthead {{
  position: sticky; top: 0; z-index: 5;
  background: var(--bg);
  border-bottom: 1px solid var(--rule);
  padding: var(--space-lg) var(--space-lg) 0;
}}
.wrap {{ max-width: 62rem; margin: 0 auto; }}
h1 {{
  font-family: var(--font-display);
  font-size: var(--size-h1);
  font-weight: 400;
  margin: 0 0 var(--space-xs);
}}
.dek {{ color: var(--ink-soft); margin: 0 0 var(--space-sm); }}
.meta {{
  color: var(--ink-muted);
  font-size: var(--size-small);
  margin: 0 0 var(--space-md);
}}
.meta span + span::before {{ content: " · "; }}

[role="tablist"] {{ display: flex; gap: var(--space-md); }}
[role="tab"] {{
  appearance: none; background: none; border: 0;
  border-bottom: 2px solid transparent;
  color: var(--ink-muted);
  font: inherit;
  padding: var(--space-sm) 0;
  cursor: pointer;
}}
[role="tab"][aria-selected="true"] {{ color: var(--ink); border-bottom-color: var(--accent); }}
[role="tab"]:focus-visible, .cite:focus-visible, a:focus-visible {{
  outline: 2px solid var(--accent); outline-offset: 2px;
}}

main {{ padding: var(--space-lg); }}
[role="tabpanel"] {{ max-width: 62rem; margin: 0 auto; }}

figure.page {{ margin: 0 0 var(--space-lg); }}
figure.page img {{
  display: block; width: 100%; height: auto;
  border: 1px solid var(--rule-light);
  background: var(--panel);
}}
figure.page figcaption {{
  color: var(--ink-muted);
  font-size: var(--size-micro);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  padding-top: var(--space-xs);
}}

.card {{
  border: 1px solid var(--rule-light);
  border-left: 3px solid var(--accent);
  background: var(--panel);
  padding: var(--space-md);
  margin: 0 0 var(--space-md);
}}
.card h2 {{
  font-family: var(--font-display);
  font-size: var(--size-h3);
  font-weight: 400;
  margin: 0 0 var(--space-xs);
}}
.key {{
  font-family: var(--font-mono);
  font-size: var(--size-micro);
  color: var(--accent-deep);
}}
dl.fields {{ display: grid; grid-template-columns: max-content 1fr; gap: 0 var(--space-md); margin: var(--space-sm) 0 0; }}
dl.fields dt {{
  color: var(--ink-muted);
  font-size: var(--size-micro);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}}
dl.fields dd {{ margin: 0; font-size: var(--size-small); overflow-wrap: anywhere; }}
a {{ color: var(--accent); }}
.state {{ font-size: var(--size-small); }}
.state.archived {{ color: var(--positive); }}
.state.missing {{ color: var(--ink-muted); }}
.sha {{ font-family: var(--font-mono); }}

ul.claims {{ list-style: none; margin: var(--space-md) 0 0; padding: 0; }}
ul.claims > li {{ border-top: 1px solid var(--rule-light); padding: var(--space-sm) 0; }}
.ln {{
  color: var(--ink-muted);
  font-family: var(--font-mono);
  font-size: var(--size-micro);
  display: block;
}}
.sentence {{ margin: 0; }}
.cite {{
  appearance: none;
  background: var(--accent-tint);
  border: 1px solid var(--rule);
  border-radius: 2px;
  color: var(--accent-deep);
  cursor: pointer;
  font: inherit;
  font-family: var(--font-mono);
  font-size: var(--size-micro);
  padding: 0 0.35em;
}}
a.cite {{ text-decoration: none; }}
.cite[aria-expanded="true"] {{ background: var(--accent); color: var(--bg); border-color: var(--accent); }}
.pop {{
  border: 1px solid var(--rule);
  background: var(--bg);
  margin-top: var(--space-sm);
  padding: var(--space-sm) var(--space-md);
  font-size: var(--size-small);
}}
.pop h3 {{ font-size: var(--size-small); font-weight: 600; margin: 0 0 var(--space-xs); }}
.pop .quote {{
  border-left: 2px solid var(--accent);
  color: var(--ink-soft);
  margin: var(--space-sm) 0 0;
  padding-left: var(--space-sm);
}}
.empty {{ color: var(--ink-muted); font-style: italic; }}
footer {{
  border-top: 1px solid var(--rule-light);
  color: var(--ink-muted);
  font-size: var(--size-micro);
  margin-top: var(--space-xl);
  padding: var(--space-md) var(--space-lg);
}}
@media print {{
  header.masthead {{ position: static; }}
  .js [role="tabpanel"][data-active="false"] {{ display: block; }}
}}
"""


# ── the document ─────────────────────────────────────────────────────────────


SCRIPT = """
(function () {
  var tabs = Array.prototype.slice.call(document.querySelectorAll('[role="tab"]'));
  function panelOf(tab) { return document.getElementById(tab.getAttribute('aria-controls')); }
  function select(chosen) {
    tabs.forEach(function (tab) {
      var on = tab === chosen;
      tab.setAttribute('aria-selected', on ? 'true' : 'false');
      tab.tabIndex = on ? 0 : -1;
      panelOf(tab).setAttribute('data-active', on ? 'true' : 'false');
    });
    chosen.focus();
  }
  tabs.forEach(function (tab, position) {
    tab.addEventListener('click', function () { select(tab); });
    tab.addEventListener('keydown', function (event) {
      var step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0;
      if (!step) return;
      event.preventDefault();
      select(tabs[(position + step + tabs.length) % tabs.length]);
    });
  });

  var cites = Array.prototype.slice.call(document.querySelectorAll('button.cite'));
  function closeAll() {
    cites.forEach(function (button) {
      button.setAttribute('aria-expanded', 'false');
      document.getElementById(button.getAttribute('aria-controls')).classList.remove('is-open');
    });
  }
  cites.forEach(function (button) {
    button.addEventListener('click', function () {
      var open = button.getAttribute('aria-expanded') === 'true';
      closeAll();
      if (open) return;
      button.setAttribute('aria-expanded', 'true');
      document.getElementById(button.getAttribute('aria-controls')).classList.add('is-open');
    });
  });
  document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') closeAll();
  });
})();
"""


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def _snapshot_state(record: dict | None) -> tuple[str, str]:
    """The snapshot line for a source card: (css class, text)."""
    if not record:
        return "missing", "not archived"
    fetched = str(record.get("fetched", "")).replace("T", " ")[:10] or "date unknown"
    digest = str(record.get("sha256", ""))
    tail = f" · sha256 {digest[:12]}…" if digest else ""
    return "archived", f"archived {fetched}{tail}"


def _sentence_html(claim: Claim, key: str, pop_id: str) -> str:
    """The sentence, with `key` as the button that opens its evidence.

    A sentence may rest on several sources, and it appears once under each. The
    other keys in it become links to their own cards rather than inert text, so
    a reader following one thread of evidence can always step onto another.
    """
    out = []
    for kind, value in claim.parts:
        if kind == "text":
            out.append(escape(value))
        elif value == key:
            out.append(
                f'<button type="button" class="cite" aria-expanded="false" '
                f'aria-controls="{pop_id}">@{escape(value)}</button>'
            )
        else:
            href = escape(f"#src-{value}", quote=True)
            out.append(f'<a class="cite" href="{href}">@{escape(value)}</a>')
    return "".join(out)


def _popover(pop_id: str, source: sources.Source, state: str, window: str | None) -> str:
    lines = [f'<div class="pop" id="{pop_id}">']
    lines.append(f"<h3>{escape(source.title or source.key)}</h3>")
    detail = " · ".join(
        part for part in (escape(source.author), escape(source.type), escape(state)) if part
    )
    lines.append(f'<p class="state">{detail}</p>')
    if source.url:
        lines.append(f'<p><a href="{escape(source.url, quote=True)}">{escape(source.url)}</a></p>')
    if window:
        lines.append(f'<p class="quote">{escape(window)}</p>')
    elif source.url:
        lines.append('<p class="empty">No snapshot — run <code>report-maker cite --refresh</code>.</p>')
    lines.append(f'<p><a href="#src-{escape(source.key, quote=True)}">Go to the source card</a></p>')
    lines.append("</div>")
    return "".join(lines)


def _source_card(
    report: Report,
    source: sources.Source,
    citing: list[Claim],
    record: dict | None,
    counter: list[int],
) -> str:
    css_state, state = _snapshot_state(record)
    archived = snapshot.read_text(report, source.key) if record else None

    parts = [f'<article class="card" id="src-{escape(source.key, quote=True)}">']
    parts.append(f"<h2>{escape(source.title or source.key)}</h2>")
    parts.append(f'<p class="key">@{escape(source.key)}</p>')

    fields: list[tuple[str, str]] = [("Type", escape(source.type))]
    if source.author:
        fields.append(("Author", escape(source.author)))
    if source.url:
        fields.append(
            ("URL", f'<a href="{escape(source.url, quote=True)}">{escape(source.url)}</a>')
        )
    if source.accessed:
        fields.append(("Accessed", escape(source.accessed)))
    fields.append(("Snapshot", f'<span class="state {css_state}">{escape(state)}</span>'))
    parts.append('<dl class="fields">')
    for label, value in fields:
        parts.append(f"<dt>{label}</dt><dd>{value}</dd>")
    parts.append("</dl>")

    if not citing:
        parts.append(
            '<p class="empty">Never cited — it reaches References as a reviewed '
            "source, and no sentence rests on it.</p>"
        )
    else:
        parts.append('<ul class="claims">')
        for claim in citing:
            counter[0] += 1
            pop_id = f"pop-{counter[0]}"
            window = excerpt(archived, claim.text) if archived else None
            parts.append(
                f'<li><span class="ln">line {claim.line}</span>'
                f'<p class="sentence">{_sentence_html(claim, source.key, pop_id)}</p>'
                f"{_popover(pop_id, source, state, window)}</li>"
            )
        parts.append("</ul>")

    parts.append("</article>")
    return "".join(parts)


def render(cfg: Config, report: Report) -> str:
    """The whole document, as a string."""
    images = page_images(report)
    if not images:
        raise HtmlError(
            f"no page images for {report.id} — run `report-maker pages "
            f"{report.id}` first, then export again\n"
            f"  expected: {report.pages_dir}"
        )

    pack = brand.load(cfg, vault.template(cfg, report.template_id()).brand_pack)
    meta = report.meta()
    entries = sources.parse(report.sources)
    body = claims(report)
    title = meta.get("title") or report.id

    head_meta = [
        value
        for value in (
            meta.get("kind"),
            meta.get("author"),
            meta.get("date-display"),
            meta.get("version"),
            meta.get("classification"),
        )
        if value
    ]
    head_meta.append(_plural(len(images), "page"))
    head_meta.append(_plural(len(entries), "source"))

    pages_html = "".join(
        f'<figure class="page"><img src="{_data_uri(path)}" '
        f'alt="Page {number} of {escape(title, quote=True)}, as rendered">'
        f"<figcaption>Page {number}</figcaption></figure>"
        for number, path in enumerate(images, start=1)
    )

    counter = [0]
    records = snapshot.records(report)
    cards = "".join(
        _source_card(
            report,
            source,
            [c for c in body if source.key in c.keys],
            records.get(source.key),
            counter,
        )
        for source in entries
    )
    if not cards:
        cards = (
            '<p class="empty">This report has no sources.yml, so there is nothing '
            "to show here.</p>"
        )

    subtitle = meta.get("subtitle", "")
    dek = f'<p class="dek">{escape(subtitle)}</p>' if subtitle else ""
    strip = "".join(f"<span>{escape(value)}</span>" for value in head_meta)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} — report-maker</title>
<script>document.documentElement.className = "js";</script>
<style>{stylesheet(pack)}</style>
</head>
<body>
<header class="masthead">
  <div class="wrap">
    <h1>{escape(title)}</h1>
    {dek}
    <p class="meta">{strip}</p>
    <div role="tablist" aria-label="Report views">
      <button type="button" role="tab" id="tab-pages" aria-controls="panel-pages" aria-selected="true">Pages</button>
      <button type="button" role="tab" id="tab-evidence" aria-controls="panel-evidence" aria-selected="false" tabindex="-1">Evidence</button>
    </div>
  </div>
</header>
<main>
  <section role="tabpanel" id="panel-pages" aria-labelledby="tab-pages" data-active="true">
    {pages_html}
  </section>
  <section role="tabpanel" id="panel-evidence" aria-labelledby="tab-evidence" data-active="false">
    {cards}
  </section>
</main>
<footer>
  <div class="wrap">{escape(report.id)} · {_plural(len(body), "cited statement")} · exported by
  report-maker. Every page image and every excerpt is embedded in this file; it
  makes no network requests.</div>
</footer>
<script>{SCRIPT}</script>
</body>
</html>
"""


# ── the command ──────────────────────────────────────────────────────────────


def export_one(cfg: Config, report: Report) -> Path:
    target = cfg.out / f"{report.id}.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(cfg, report), encoding="utf-8")
    size = target.stat().st_size / 1024
    print(f"  → {target.relative_to(cfg.root)} ({size:,.0f} KB)")
    return target


def export(cfg: Config, target: str | None = None) -> list[Path]:
    return [export_one(cfg, report) for report in reports(cfg, target)]
