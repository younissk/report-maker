"""The bibliography as data.

`sources.yml` is the evidence ledger of a report. Every `@key` in the prose has
to land on a block in it, and the References section is built from it whether or
not a citation ever reached the entry. That makes it the file the tooling reads
most often, and also the file a person is most likely to have hand-edited — with
comments above an entry explaining why the source is there at all.

So this module parses it, and refuses to be precious about it. The parser covers
the slice of YAML that hayagriva actually uses — block mappings nested to any
depth, plain and quoted scalars, `- ` sequences, `#` comments — and anything
outside that slice degrades to an entry with no fields rather than to an
exception. A bibliography that confuses the parser must still appear in the
sources panel, still count towards the key set, and still be editable; a
traceback would take all three away. `check.bib_keys` reaches for a regex for
exactly this reason, and `keys()` reproduces its behaviour, so the linter and the
sources panel can never disagree about what a key is.

Writing is deliberately narrow. `append`, `upsert` and `remove` rewrite the one
block they are named for and leave every other byte alone — comments, blank
lines and ordering included. The file belongs to the person who wrote it, and a
tool that silently reformats on save is a tool people stop running.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .workspace import Report

# A top-level key, in exactly the shape `check.bib_keys` recognises: a name at
# column zero with nothing after the colon. Keeping the two regexes identical is
# what guarantees `keys()` and the linter see the same bibliography.
TOP_KEY = re.compile(r"^([A-Za-z][\w.:+-]*):\s*$")

# A mapping entry inside a block. The colon must be followed by whitespace or the
# end of the line, or a bare `https://example.com` on its own line would parse as
# the key `https`.
ENTRY = re.compile(r"([A-Za-z][\w.-]*):(?:[ \t]+(.*))?$")

# A sequence item: `-` alone, or `- value`.
ITEM = re.compile(r"-(?:[ \t]+(.*))?$")

ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\", "/": "/"}

# Dropped when a key is derived from a title, because `@the-state-of-the-market`
# reads worse than `@state-market` and says no more.
STOPWORDS = frozenset(
    "a an and are as at be but by for from if in into is it its no not of on or "
    "over so than that the this to was were will with".split()
)

# How many tokens a generated key may carry, the site included. Three is the
# point where a key still reads as a phrase in the middle of a sentence.
KEY_TOKENS = 3

# Second-level domains that carry no identity of their own, so `example.co.uk`
# yields `example` rather than `co`.
GENERIC_LABELS = frozenset({"co", "com", "org", "net", "gov", "edu", "ac"})


@dataclass
class Source:
    """One bibliography entry, as parsed and as written.

    `fields` is the parsed value tree; `raw` is the exact block text it came
    from, so a caller that wants to move an entry without reformatting it has
    the original bytes to hand.
    """

    key: str
    fields: dict = field(default_factory=dict)
    line: int = 0
    raw: str = ""

    # ── what the rest of the engine actually asks for ────────────────────────
    #
    # Hayagriva allows several spellings for the same idea — an author may be a
    # string, a list or a mapping; an access date may hang off `url` or sit at
    # the top level. These properties are the one place that variety is resolved,
    # so no caller has to know about it.

    @property
    def type(self) -> str:
        value = self.fields.get("type")
        return value.strip() if isinstance(value, str) and value.strip() else "Misc"

    @property
    def title(self) -> str:
        return _display(self.fields.get("title"))

    @property
    def author(self) -> str:
        return _display(self.fields.get("author"))

    @property
    def url(self) -> str | None:
        value = self.fields.get("url")
        if isinstance(value, Mapping):
            value = value.get("value")
        text = _display(value).strip()
        return text or None

    @property
    def accessed(self) -> str | None:
        value = self.fields.get("url")
        if isinstance(value, Mapping) and _display(value.get("date")).strip():
            return _display(value["date"]).strip()
        text = _display(self.fields.get("date")).strip()
        return text or None

    def to_yaml(self) -> str:
        """The block as it would be written: key line, two-space indent, strings
        always double-quoted, trailing newline.

        Regenerated from `fields`, not echoed from `raw` — `upsert` exists to
        replace a block, so a caller that edited the fields must see the edit.
        The one exception is an entry the parser could not read: there is nothing
        to regenerate from, so the bytes it came with are handed back untouched.
        """
        if not self.fields:
            return _ends_with_newline(self.raw) if self.raw else f"{self.key}:\n"
        lines = [f"{self.key}:"]
        # `type` leads, the way hayagriva's own examples do; everything else
        # keeps the order it was given, which for a parsed entry is file order.
        names = (["type"] if "type" in self.fields else []) + [
            name for name in self.fields if name != "type"
        ]
        for name in names:
            _emit(lines, name, self.fields[name], 2)
        return "\n".join(lines) + "\n"


# ── reading ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _Block:
    """Where one entry sits in the file, and its body already tokenised."""

    key: str
    line: int  # 1-based line of the key
    start: int  # character offset of the key line
    end: int  # character offset just past the block's last line
    body: tuple[tuple[int, str], ...]  # (indent, text) per meaningful line


def _blocks(text: str) -> list[_Block]:
    """Split a bibliography into blocks, by character offset.

    A block runs from its key line to the last indented, non-blank line beneath
    it. Trailing blank lines and column-zero comments are left outside it: a
    comment above a key usually belongs to that key, and a rewriter that guesses
    otherwise is a rewriter that eats people's prose.
    """
    lines = text.splitlines(keepends=True)
    starts: list[int] = []
    offset = 0
    for line in lines:
        starts.append(offset)
        offset += len(line)
    contents = [line.rstrip("\n").rstrip("\r") for line in lines]

    found: list[_Block] = []
    for index, content in enumerate(contents):
        match = TOP_KEY.match(content)
        if not match:
            continue
        last = index
        cursor = index + 1
        while cursor < len(contents):
            line = contents[cursor]
            if not line.strip():
                cursor += 1  # a blank line inside a block, or the gap after it
                continue
            if not line[:1].isspace():
                break  # column zero — the next entry, or a comment introducing it
            last = cursor
            cursor += 1
        found.append(
            _Block(
                key=match.group(1),
                line=index + 1,
                start=starts[index],
                end=starts[last] + len(lines[last]),
                body=_body(contents, index + 1, last + 1),
            )
        )
    return found


def _body(contents: list[str], first: int, last: int) -> tuple[tuple[int, str], ...]:
    """(indent, text) for every line of a block body that carries meaning.

    Blank lines and whole-line comments are dropped here rather than in the
    grammar below, so the grammar never has to think about them.
    """
    out: list[tuple[int, str]] = []
    for line in contents[first:last]:
        text = line.rstrip()
        stripped = text.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append((len(text) - len(stripped), stripped))
    return tuple(out)


def _fields(body: Sequence[tuple[int, str]]) -> dict:
    """The value tree for one block, or `{}` when it cannot be read at all."""
    if not body:
        return {}
    try:
        parsed, _ = _mapping(body, 0, body[0][0])
    except Exception:  # noqa: BLE001 — a bibliography must never fail to load
        return {}
    return parsed


def _mapping(
    body: Sequence[tuple[int, str]], index: int, indent: int
) -> tuple[dict, int]:
    out: dict = {}
    while index < len(body):
        depth, text = body[index]
        if depth < indent:
            break
        if depth > indent:
            index += 1  # stray deeper line with no parent key — skip, do not fail
            continue
        match = ENTRY.match(text)
        if not match:
            index += 1  # not an entry (a stray `- item`, or a line with no colon)
            continue
        name, inline = match.group(1), match.group(2)
        scalar = _scalar(inline or "")
        if scalar == "" and index + 1 < len(body):
            below_depth, below_text = body[index + 1]
            if below_depth >= indent and ITEM.match(below_text):
                out[name], index = _sequence(body, index + 1, below_depth)
                continue
            if below_depth > indent:
                out[name], index = _mapping(body, index + 1, below_depth)
                continue
        out[name] = scalar
        index += 1
    return out, index


def _sequence(
    body: Sequence[tuple[int, str]], index: int, indent: int
) -> tuple[list, int]:
    items: list = []
    while index < len(body):
        depth, text = body[index]
        match = ITEM.match(text)
        if depth < indent or not match:
            break
        inline = (match.group(1) or "").strip()
        if not inline:
            # `-` alone: the item is whatever is indented beneath the dash.
            if index + 1 < len(body) and body[index + 1][0] > depth:
                value, index = _mapping(body, index + 1, body[index + 1][0])
                items.append(value)
            else:
                items.append("")
                index += 1
            continue
        if ENTRY.match(inline):
            # `- name: Ada` — a mapping item. Its remaining keys line up under
            # the first one, so re-present the dash line as a plain entry at that
            # column and let the mapping parser take it from there.
            column = depth + (len(text) - len(text[1:].lstrip()))
            shifted = list(body)
            shifted[index] = (column, inline)
            value, index = _mapping(shifted, index, column)
            items.append(value)
            continue
        items.append(_scalar(inline))
        index += 1
    return items, index


def _scalar(text: str) -> str:
    """One scalar value: plain, single-quoted or double-quoted."""
    text = text.strip()
    if not text or text.startswith("#"):
        return ""
    if text[0] in "\"'":
        return _quoted(text, text[0])
    # A plain scalar ends at a comment, but only when the `#` is preceded by
    # whitespace — otherwise `https://example.com/p#frag` loses its fragment.
    comment = re.search(r"\s#", text)
    return text[: comment.start()].strip() if comment else text


def _quoted(text: str, quote: str) -> str:
    out: list[str] = []
    index = 1
    while index < len(text):
        char = text[index]
        if quote == '"' and char == "\\" and index + 1 < len(text):
            out.append(ESCAPES.get(text[index + 1], text[index + 1]))
            index += 2
            continue
        if char == quote:
            if quote == "'" and text[index + 1 : index + 2] == "'":
                out.append("'")  # YAML doubles a quote to escape it
                index += 2
                continue
            return "".join(out)
        out.append(char)
        index += 1
    return "".join(out)  # unterminated — take what is there rather than raise


def _display(value) -> str:
    """Flatten a hayagriva value to something a person can read in a list."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Mapping):
        # A person is `name` (family) plus an optional `given-name`. Display
        # order is the spoken one; the References section does its own ordering.
        family, given = _display(value.get("name")), _display(value.get("given-name"))
        if family or given:
            return f"{given} {family}".strip()
        return ", ".join(part for part in map(_display, value.values()) if part)
    if isinstance(value, (list, tuple)):
        return ", ".join(part for part in map(_display, value) if part)
    return str(value)


def parse(path: Path) -> list[Source]:
    """Every entry in a `sources.yml`, in file order. A missing file is empty."""
    if not path.is_file():
        return []
    # Lenient decoding: a bibliography that is not quite UTF-8 should still list
    # its keys. The writers below read strictly, because they write back.
    return parse_text(path.read_text(encoding="utf-8", errors="replace"))


def parse_text(text: str) -> list[Source]:
    return [
        Source(
            key=block.key,
            fields=_fields(block.body),
            line=block.line,
            raw=text[block.start : block.end],
        )
        for block in _blocks(text)
    ]


def keys(path: Path) -> set[str]:
    """The key set, identical to what `check.bib_keys` would return."""
    return {source.key for source in parse(path)}


# ── writing ──────────────────────────────────────────────────────────────────


def _emit(lines: list[str], key: str, value, indent: int) -> None:
    """One field. Emits nothing at all when there is nothing to say.

    An absent field is absent, not `null`, and a container that emits no children
    is absent too — otherwise `to_yaml` would write `author:` with a blank value,
    which reads back as an empty string and the round trip stops holding.
    """
    pad = " " * indent
    if value is None:
        return
    if isinstance(value, bool):
        lines.append(f"{pad}{key}: {'true' if value else 'false'}")
    elif isinstance(value, (int, float)):
        lines.append(f"{pad}{key}: {value}")
    elif isinstance(value, Mapping):
        children: list[str] = []
        for name, item in value.items():
            _emit(children, name, item, indent + 2)
        if children:
            lines.append(f"{pad}{key}:")
            lines.extend(children)
    elif isinstance(value, (list, tuple)):
        children = []
        for item in value:
            _emit_item(children, item, indent + 2)
        if children:
            lines.append(f"{pad}{key}:")
            lines.extend(children)
    else:
        lines.append(f"{pad}{key}: {_quote(str(value))}")


def _emit_item(lines: list[str], item, indent: int) -> None:
    pad = " " * indent
    if isinstance(item, Mapping):
        block: list[str] = []
        for name, value in item.items():
            _emit(block, name, value, indent + 2)
        if not block:
            return
        # `- ` is exactly as wide as the indent step, so the first pair moves out
        # onto the dash and the rest of the item stays aligned under it.
        block[0] = f"{pad}- {block[0].lstrip()}"
        lines.extend(block)
    elif item is not None:
        lines.append(f"{pad}- {_quote(str(item))}")


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{escaped}"'


def _ends_with_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def url_field(url: str, accessed: str | None = None) -> str | dict:
    """The `url:` value hayagriva wants.

    A bare string when all we have is the address, and the `value:`/`date:` pair
    when we know when we looked — which is what makes the accessed date show up
    in References.
    """
    return {"value": url, "date": accessed} if accessed else url


def append(path: Path, source: Source) -> None:
    """Add an entry at the end. Idempotent: an existing key is left as it is.

    Idempotence is what lets `cite` be run twice on the same URL without the
    bibliography growing a duplicate.
    """
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if any(block.key == source.key for block in _blocks(text)):
        return
    if text and not text.endswith("\n"):
        text += "\n"
    if text.strip() and not text.endswith("\n\n"):
        text += "\n"  # one blank line between entries
    _write(path, text + source.to_yaml())


def upsert(path: Path, source: Source) -> None:
    """Replace an entry in place, or add it when it is not there yet.

    Only the block itself is rewritten. Everything around it — the comment above
    it, the entries either side, the blank lines — comes through byte-identical.
    """
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    for block in _blocks(text):
        if block.key == source.key:
            _write(path, text[: block.start] + source.to_yaml() + text[block.end :])
            return
    append(path, source)


def remove(path: Path, key: str) -> bool:
    """Delete an entry. False when there was nothing to delete.

    The block goes and nothing else does, so a blank separator line may be left
    behind. That is the deliberate trade: the alternative is a rewriter that
    reasons about which surrounding whitespace and comments "belonged" to the
    entry, and gets it wrong on somebody's carefully annotated file.
    """
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    for block in _blocks(text):
        if block.key == key:
            _write(path, text[: block.start] + text[block.end :])
            return True
    return False


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ── keys ─────────────────────────────────────────────────────────────────────


def _words(text: str) -> list[str]:
    return [word for word in re.split(r"[^a-z0-9]+", (text or "").lower()) if word]


def _host_label(url: str | None) -> str:
    """The part of a hostname worth naming a source after: `stripe`, not
    `docs.stripe.com`."""
    if not url:
        return ""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    labels = [label for label in host.removeprefix("www.").split(".") if label]
    if not labels:
        return ""
    if len(labels) >= 3 and labels[-2] in GENERIC_LABELS:
        chosen = labels[-3]  # example.co.uk → example
    elif len(labels) >= 2:
        chosen = labels[-2]
    else:
        chosen = labels[0]
    return "-".join(_words(chosen))


def slugify_key(title: str, url: str | None, taken: set[str]) -> str:
    """A citation key that reads well in prose and is not already in use.

    Site first, then the words of the title that carry meaning: `@stripe-pricing`
    tells a reader where the claim came from, `@source-4` does not.
    """
    host = _host_label(url)
    words = _words(title)
    meaningful = [word for word in words if word not in STOPWORDS] or words
    parts = [host] if host else []
    parts += [word for word in meaningful if word != host][: KEY_TOKENS - len(parts)]
    base = "-".join(parts)[:48].strip("-")
    if not base:
        base = "-".join(_words(url or "")[-1:]) or "source"
    if not base[:1].isalpha():
        # A key is also a Typst reference, and `@2026-review` is not one.
        base = f"ref-{base}"
    if base not in taken:
        return base
    suffix = 2
    while f"{base}-{suffix}" in taken:
        suffix += 1
    return f"{base}-{suffix}"


# ── JSON, for the CLI and the app ────────────────────────────────────────────


def to_json(
    sources: Sequence[Source],
    *,
    uses: Mapping[str, int] | None = None,
    snapshots: Mapping[str, Mapping] | None = None,
) -> list[dict]:
    """The `SourceRow` shape the app's sources panel consumes.

    Both `uses` and `snapshots` are passed in rather than looked up here: use
    counts need the report body, and snapshot records live next to the report.
    This module knows about neither, and should not learn.
    """
    rows_out: list[dict] = []
    for source in sources:
        record = (snapshots or {}).get(source.key)
        snapshot = None
        if isinstance(record, Mapping):
            snapshot = {
                "sha256": str(record.get("sha256", "")),
                "fetched": str(record.get("fetched", "")),
            }
        rows_out.append(
            {
                "key": source.key,
                "type": source.type,
                "title": source.title,
                "author": source.author,
                "url": source.url,
                "accessed": source.accessed,
                "line": source.line,
                "snapshot": snapshot,
                "uses": int((uses or {}).get(source.key, 0)),
            }
        )
    return rows_out


def use_counts(report: Report) -> dict[str, int]:
    """How often each key is actually cited in the report body.

    Zero is the interesting value: it is `W001`, an entry that is in References
    because it was reviewed but that no sentence rests on.
    """
    from . import check  # local: check may delegate its key set back to this module

    counts = {source.key: 0 for source in parse(report.sources)}
    if not report.main.is_file():
        return counts
    raw = report.main.read_text(encoding="utf-8")
    defined = check.labels(raw)
    for key, _index in check.cited_keys(check.scrub(raw)):
        if key in defined:
            continue  # a cross-reference to a figure in this document, not a citation
        counts[key] = counts.get(key, 0) + 1
    return counts


def rows(
    report: Report, *, snapshots: Mapping[str, Mapping] | None = None
) -> list[dict]:
    """One report's bibliography, ready to print as `sources --json`."""
    return to_json(
        parse(report.sources), uses=use_counts(report), snapshots=snapshots
    )
