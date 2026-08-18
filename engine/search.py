"""Everything the vault knows, findable.

A vault answers three questions a folder of PDFs cannot. *Which report said
this?* — the prose. *Where did we get it?* — the bibliography. And, since the
engine started archiving every cited page, *did any source I ever read mention
this?* — the snapshots. That last one is the reason this module exists now
rather than later: no other tool can answer it, because no other tool kept the
pages. A search over `reports/<id>/snapshots/<key>.txt` reaches into evidence
that has since been paywalled, rewritten or deleted.

Four kinds of document, indexed side by side so one query crosses all of them:

    report      the prose of main.typ, with the Typst markup blanked out
    source      one sources.yml entry — title, author, url, note
    snapshot    the extracted text of an archived page
    diagram     the label text inside a .mmd

Ranking is tf-idf and nothing more. Every term of the query must be present
(quotes make a phrase, `-word` excludes, `kind:` filters), matches are weighted
by `(1 + ln tf) · ln(1 + N/(1+df))`, a term in the title or key counts as if it
had appeared three times in the body, and the total is divided by the log of the
document length so a long snapshot cannot outrank a short entry by sheer bulk.
There is no stemming, no synonyms and no learned weights: a search that cannot
be explained is a search nobody trusts, and this one fits in a paragraph.

Offsets, not HTML. A hit carries the character offsets of the matched terms
*within its excerpt*, so an app can highlight them without searching again, and
so the same hit renders in a terminal, in the desktop shell and over JSON.
A report hit also carries its line in `main.typ` — the markup is blanked in
place rather than deleted, so every offset still maps to the line it came from,
and the editor can jump straight there. A snapshot has no line worth jumping to,
so it carries `offset` and leaves `line` as `None`.

The index is one JSON file at `.build/search/index.json`, rebuilt incrementally:
a file whose mtime and size are unchanged is never reopened, which is what keeps
an eighty-report vault with four hundred snapshots at a couple of seconds.
Document text is stored in the index so a query never touches the vault — but
only up to `TEXT_BUDGET` (40 MB), well under the point where a single JSON file
stops being a sensible container. Past that the largest documents fall back to
postings only: their term counts stay in the index and their text is re-read
from disk when a hit needs an excerpt. Ranking is unaffected either way.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from . import check
from . import sources as sources_mod
from .config import Config
from .workspace import Report, reports

KINDS = ("report", "source", "snapshot", "diagram")

# Snapshots live next to the report that cites them (see engine/snapshot.py).
# Only the layout is needed here, and search must keep working in a vault built
# by an engine that never archived anything — so the folder name is a constant
# rather than an import, and a missing folder is simply an empty one.
SNAPSHOT_DIR = "snapshots"

INDEX_VERSION = 4

# Beyond this much stored text the index stops being one comfortable JSON file,
# so the largest documents keep their postings and give up their text. See the
# module docstring.
TEXT_BUDGET = 40 * 1024 * 1024

TOKEN_RE = re.compile(r"\w+", re.UNICODE)
MIN_TOKEN = 2

# A term in the title or the key counts as this many body occurrences. Three is
# enough for `@stripe-pricing` to beat a report that says "pricing" twice, and
# small enough that a report *about* pricing still beats a source merely named
# for it.
TITLE_TF = 3.0

# Added once per matched quoted phrase, after length normalisation.
PHRASE_BOOST = 1.5

EXCERPT_WORDS = 30
EXCERPT_LEAD = 8  # words of run-up before the match, so the excerpt reads in context
AVG_CHARS = 7  # characters per word, trailing space included — window sizing only


class SearchError(RuntimeError):
    pass


@dataclass(frozen=True)
class Hit:
    """One match, ready to print or to render.

    `line` is 1-based and points into `path`; it is `None` for a snapshot, whose
    extracted text has no line worth jumping to — `offset` carries the character
    position in `snapshots/<key>.txt` instead. `marks` are (start, end) character
    offsets **into `excerpt`**, so a UI highlights by slicing, not by searching.
    """

    kind: str
    report: str
    key: str
    path: str  # vault-relative, POSIX
    line: int | None
    offset: int | None
    score: float
    excerpt: str
    marks: tuple[tuple[int, int], ...] = ()
    title: str = ""
    # When the archived copy was taken — snapshot hits only. It is the whole
    # reason the hit exists ("this page said that, on this date"), so it travels
    # with the hit rather than making the caller open the record to find out.
    fetched: str | None = None


@dataclass(frozen=True)
class Query:
    """A parsed query. Every term and phrase must match; excluded ones must not."""

    terms: tuple[str, ...] = ()
    phrases: tuple[tuple[str, ...], ...] = ()
    excluded: tuple[str, ...] = ()
    excluded_phrases: tuple[tuple[str, ...], ...] = ()
    kinds: frozenset[str] = frozenset()
    raw: str = ""

    @property
    def all_terms(self) -> tuple[str, ...]:
        """Terms to score on: the bare ones plus every token of every phrase."""
        seen: dict[str, None] = dict.fromkeys(self.terms)
        for phrase in self.phrases:
            seen.update(dict.fromkeys(phrase))
        return tuple(seen)

    @property
    def empty(self) -> bool:
        return not self.terms and not self.phrases


# ── tokenising ───────────────────────────────────────────────────────────────


def tokens(text: str) -> list[str]:
    return [
        match.group(0).casefold()
        for match in TOKEN_RE.finditer(text)
        if len(match.group(0)) >= MIN_TOKEN
    ]


def _spans(text: str) -> list[tuple[str, int, int]]:
    """(token, start, end) for every token, casefolded but positioned in the
    original string — the excerpt is cut from the original, the matching is done
    on the fold."""
    return [
        (match.group(0).casefold(), match.start(), match.end())
        for match in TOKEN_RE.finditer(text)
        if len(match.group(0)) >= MIN_TOKEN
    ]


@lru_cache(maxsize=256)
def phrase_re(phrase: tuple[str, ...]) -> re.Pattern:
    """A pattern matching `phrase` as adjacent tokens.

    "Adjacent" has to mean the same thing here as it does in the tokeniser, or a
    quoted phrase would answer a different question from the words around it.
    The separator therefore swallows exactly what the tokeniser drops:
    punctuation and whitespace, plus any word too short to be a token. So
    `"pricing page"` matches "pricing — page" and "pricing a page", and does not
    match "pricing on the page".

    Regex rather than a walk over the token stream because this runs against
    every candidate document, and tokenising a whole archived page to answer a
    yes/no question is the difference between a query that feels instant and one
    that does not.
    """
    gap = r"(?:\W|\b\w\b)+"
    return re.compile(
        r"\b" + gap.join(re.escape(word) for word in phrase) + r"\b",
        re.IGNORECASE | re.UNICODE,
    )


# ── query language ───────────────────────────────────────────────────────────

_QUERY_RE = re.compile(r'(-?)(?:"([^"]*)"|(\S+))')


def parse_query(text: str) -> Query:
    """`pricing "list price" -enterprise kind:snapshot` → a Query.

    Unknown `kind:` values are an error rather than a silent empty result: the
    difference between "no vault mentions this" and "you typed kind:snapshots"
    is exactly the difference a person needs to see.
    """
    terms: list[str] = []
    phrases: list[tuple[str, ...]] = []
    excluded: list[str] = []
    excluded_phrases: list[tuple[str, ...]] = []
    kinds: set[str] = set()

    for match in _QUERY_RE.finditer(text or ""):
        negated = match.group(1) == "-"
        quoted = match.group(2)
        word = match.group(3)

        if quoted is not None:
            phrase = tuple(tokens(quoted))
            if len(phrase) == 1:
                (excluded if negated else terms).append(phrase[0])
            elif phrase:
                (excluded_phrases if negated else phrases).append(phrase)
            continue

        if word and word.lower().startswith("kind:"):
            if negated:
                # `-kind:source` reads as "everything but sources", and honouring
                # it as a filter would do the opposite. Say so rather than guess.
                raise SearchError(
                    "kind: cannot be negated — name the kinds you do want, "
                    f"e.g. kind:report kind:snapshot ({', '.join(KINDS)})"
                )
            # A plural reads more naturally than the singular does — `kind:sources`
            # is what a person types — and only the four names below survive it.
            name = word.split(":", 1)[1].strip().lower().rstrip("s")
            if name not in KINDS:
                raise SearchError(
                    f"unknown kind {word.split(':', 1)[1]!r} — "
                    f"expected one of: {', '.join(KINDS)}"
                )
            kinds.add(name)
            continue

        parts = tokens(word or "")
        if len(parts) > 1:
            # `report-maker` tokenises to two words; treat it as the phrase the
            # person plainly meant rather than as two unrelated terms.
            (excluded_phrases if negated else phrases).append(tuple(parts))
        elif parts:
            (excluded if negated else terms).append(parts[0])

    return Query(
        terms=tuple(dict.fromkeys(terms)),
        phrases=tuple(phrases),
        excluded=tuple(dict.fromkeys(excluded)),
        excluded_phrases=tuple(excluded_phrases),
        kinds=frozenset(kinds),
        raw=text or "",
    )


# ── what a document is made of ───────────────────────────────────────────────
#
# Every extractor blanks what it drops instead of deleting it — a removed span
# becomes the same number of spaces, newlines intact — so a character offset in
# the indexed text is still a character offset in the file on disk, and a line
# number is a `count("\n")` away. Deleting would be simpler and would put the
# editor's cursor in the wrong place.


def _blank(match: re.Match) -> str:
    return re.sub(r"[^\n]", " ", match.group(0))


def report_text(raw: str) -> str:
    """The prose of a `main.typ`, with the machinery blanked out.

    What goes: comments and code blocks (`check.scrub` already does exactly
    this), statement lines, the `report.with(…)` metadata header — `meta()`
    reads it, and indexing it would make every report match "datetime" — plus
    `#helper` names, `@citations`, `<labels>`, project-absolute path strings,
    and argument names where a `(` or `,` proves they are arguments. What stays:
    every word a person wrote, including the ones inside `caption: [Tier
    comparison]` and `assessment[…]`.

    The scrub is deliberately shy. A stray identifier in the index costs one
    noisy hit; a swallowed sentence costs a search that quietly cannot find it.
    """
    src = check.scrub(raw)
    for start, end, _args in check.calls(src, "report.with"):
        src = src[:start] + re.sub(r"[^\n]", " ", src[start:end]) + src[end:]
    src = re.sub(
        r"^[ \t]*#(?:import|include|let|set|show)\b[^\n]*", _blank, src, flags=re.M
    )
    src = re.sub(r"#[A-Za-z][\w.-]*", _blank, src)  # #assess, #srcfig, #link …
    src = re.sub(r"(?<![\w@\\])@[A-Za-z][\w.:+-]*", _blank, src)  # citations
    src = re.sub(r"<[A-Za-z][\w.:-]*>", _blank, src)  # label definitions
    src = re.sub(r'"/[^"\n]*"', _blank, src)  # "/reports/…/sources.yml" and friends
    # An argument name, and only an argument name: prose writes "Note: like
    # this", never "(note: like this" or ", note: like this".
    src = re.sub(r"(?<=[(,])\s*[a-z][\w-]*\s*:", _blank, src)
    return src


# A mermaid line that is styling or wiring rather than something a person reads.
DIAGRAM_DIRECTIVES = frozenset(
    {"classDef", "class", "style", "linkStyle", "click", "direction"}
)

# Where a mermaid label can live. Overlapping matches are harmless: the same
# characters get copied twice.
LABEL_RES = tuple(
    re.compile(pattern)
    for pattern in (
        r'"([^"\n]*)"',
        r"\[([^\]\n]*)\]",
        r"\(([^)\n]*)\)",
        r"\{([^}\n]*)\}",
        r"\|([^|\n]*)\|",
    )
)


def diagram_text(raw: str) -> str:
    """Only the labels of a `.mmd`.

    A diagram is mostly wiring — `A --> B`, `classDef em-accent fill:…` — and
    indexing that would make every diagram match every colour role. The words a
    reader actually sees sit inside brackets, braces, pipes or quotes, so those
    are copied onto an otherwise blank canvas of the same shape.
    """
    canvas = ["\n" if char == "\n" else " " for char in raw]
    offset = 0
    for line in raw.splitlines(keepends=True):
        stripped = line.strip()
        head = stripped.split(" ", 1)[0] if stripped else ""
        if stripped and not stripped.startswith("%%") and head not in DIAGRAM_DIRECTIVES:
            for pattern in LABEL_RES:
                for match in pattern.finditer(line):
                    start, end = match.span(1)
                    canvas[offset + start : offset + end] = list(line[start:end])
        offset += len(line)
    # A label may carry markup of its own — `<br/>` is how mermaid wraps a line.
    return re.sub(r"<[^>\n]*>", _blank, "".join(canvas))


def source_text(source: sources_mod.Source) -> str:
    """The searchable face of a bibliography entry.

    Title, author, url and note, in that order and one per line. The offsets do
    not map back into `sources.yml` — the entry's own line does, and that is the
    only place worth jumping to.
    """
    note = source.fields.get("note")
    parts = [
        source.title,
        source.author,
        source.url or "",
        note if isinstance(note, str) else "",
    ]
    return "\n".join(part for part in parts if part)


def _title_text(*parts: str) -> str:
    """Title-weighted text: the words that name the thing, hyphens opened up so
    `2026-08-12-audit` and `stripe-pricing` contribute their words."""
    return " ".join(part.replace("-", " ").replace("_", " ") for part in parts if part)


# ── the files that make documents ────────────────────────────────────────────


@dataclass(frozen=True)
class _Source:
    """One file, and how to turn it into documents — but not yet.

    The thunk is the whole point of the incremental build: enumerating the vault
    must not read a file, so that an unchanged snapshot costs one `stat()`.

    `also` names a sibling whose changes count as changes to this file — the
    `.json` record beside a snapshot's `.txt`, which carries the page's title
    and its capture date. Refreshing an archive rewrites the record without
    necessarily touching the text, and an index that only watched the text would
    keep quoting last month's capture date.
    """

    path: Path
    rel: str
    extract: Callable[[], list[dict]]
    also: Path | None = None

    def stamp(self) -> tuple[float, int] | None:
        """(mtime, size) for the file and its sibling as one unit, or None when
        it has gone."""
        try:
            stat = self.path.stat()
        except OSError:
            return None
        mtime, size = stat.st_mtime, stat.st_size
        if self.also is not None and self.also.is_file():
            extra = self.also.stat()
            mtime, size = max(mtime, extra.st_mtime), size + extra.st_size
        return mtime, size


def _rel(cfg: Config, path: Path) -> str:
    try:
        return path.resolve().relative_to(cfg.root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _doc(
    kind: str,
    report: str,
    key: str,
    rel: str,
    title: str,
    text: str,
    *,
    line: int | None,
    named: str = "",
    fetched: str | None = None,
) -> dict:
    """One indexed document. `named` is extra title-weighted text — the report
    id, say — so `find acme` reaches a report filed under `clients/acme` even
    when the prose never says the word."""
    body = Counter(tokens(text))
    return {
        "id": f"{kind}:{rel}:{key}",
        "kind": kind,
        "report": report,
        "key": key,
        "path": rel,
        "title": title,
        "line": line,
        "fetched": fetched,
        "tf": dict(body),
        "ttf": dict(Counter(tokens(_title_text(title, key, named)))),
        "len": sum(body.values()),
        "text": text,
    }


def _files(cfg: Config) -> Iterator[_Source]:
    for report in reports(cfg):
        yield from _report_files(cfg, report)


def _report_files(cfg: Config, report: Report) -> Iterator[_Source]:
    if report.main.is_file():
        rel = _rel(cfg, report.main)

        def extract_main(report: Report = report, rel: str = rel) -> list[dict]:
            raw = report.main.read_text(encoding="utf-8", errors="replace")
            title = report.meta().get("title", "") or report.slug
            return [
                _doc(
                    "report",
                    report.id,
                    "",
                    rel,
                    title,
                    report_text(raw),
                    line=None,
                    named=report.id,
                )
            ]

        yield _Source(report.main, rel, extract_main)

    if report.sources.is_file():
        rel = _rel(cfg, report.sources)

        def extract_sources(report: Report = report, rel: str = rel) -> list[dict]:
            return [
                _doc(
                    "source",
                    report.id,
                    source.key,
                    rel,
                    source.title or source.key,
                    source_text(source),
                    line=source.line,
                )
                for source in sources_mod.parse(report.sources)
            ]

        yield _Source(report.sources, rel, extract_sources)

    for path in sorted(report.diagrams.glob("*.mmd")):
        rel = _rel(cfg, path)

        def extract_diagram(
            report: Report = report, path: Path = path, rel: str = rel
        ) -> list[dict]:
            raw = path.read_text(encoding="utf-8", errors="replace")
            return [
                _doc(
                    "diagram",
                    report.id,
                    path.stem,
                    rel,
                    path.stem,
                    diagram_text(raw),
                    line=None,
                )
            ]

        yield _Source(path, rel, extract_diagram)

    for path in sorted((report.folder / SNAPSHOT_DIR).glob("*.txt")):
        rel = _rel(cfg, path)

        def extract_snapshot(
            report: Report = report, path: Path = path, rel: str = rel
        ) -> list[dict]:
            text = path.read_text(encoding="utf-8", errors="replace")
            title, fetched = _snapshot_record(path)
            return [
                _doc(
                    "snapshot",
                    report.id,
                    path.stem,
                    rel,
                    title,
                    text,
                    line=None,
                    fetched=fetched,
                )
            ]

        yield _Source(path, rel, extract_snapshot, also=path.with_suffix(".json"))


def _snapshot_record(text_path: Path) -> tuple[str, str | None]:
    """The archived page's own title and capture date, from the record beside it.

    A `.txt` with no `.json` is still worth indexing — it is still evidence — so
    a missing or unreadable record degrades to the key and no date.
    """
    record = text_path.with_suffix(".json")
    if not record.is_file():
        return text_path.stem, None
    try:
        data = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return text_path.stem, None
    if not isinstance(data, dict):
        return text_path.stem, None
    title = data.get("title")
    fetched = data.get("fetched")
    return (
        title if isinstance(title, str) and title else text_path.stem,
        fetched if isinstance(fetched, str) and fetched else None,
    )


# ── the index ────────────────────────────────────────────────────────────────


def index_path(cfg: Config) -> Path:
    return cfg.build / "search" / "index.json"


def load_index(cfg: Config) -> dict | None:
    """The stored index, or None when there is nothing usable.

    A corrupt or older-format index is treated as absent rather than as an
    error: it is a cache in a generated directory, and the only sane response to
    a bad cache is to build a new one.
    """
    path = index_path(cfg)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != INDEX_VERSION:
        return None
    if data.get("vault") != str(cfg.root.resolve()):
        return None  # the vault was moved or copied; offsets and paths may not hold
    return data


def build_index(cfg: Config, *, force: bool = False) -> dict:
    """Bring `.build/search/index.json` up to date and return it.

    Incremental by file: a path whose mtime and size match the stored ones keeps
    its documents verbatim and is never opened. `force` reads everything again,
    which is the escape hatch for the one case stat cannot see — an edit that
    preserved both mtime and size.
    """
    old = None if force else load_index(cfg)
    old_files: dict = (old or {}).get("files", {})
    old_docs: dict = (old or {}).get("docs", {})

    files: dict[str, dict] = {}
    docs: dict[str, dict] = {}
    changed = False

    for source in _files(cfg):
        stamp = source.stamp()
        if stamp is None:
            continue
        mtime, size = stamp
        prior = old_files.get(source.rel)
        fresh = (
            prior is not None
            and prior.get("mtime") == mtime
            and prior.get("size") == size
            and all(doc_id in old_docs for doc_id in prior.get("docs", ()))
        )
        if fresh:
            files[source.rel] = prior
            for doc_id in prior["docs"]:
                docs[doc_id] = old_docs[doc_id]
            continue

        changed = True
        made = source.extract()
        files[source.rel] = {
            "mtime": mtime,
            "size": size,
            "docs": [doc["id"] for doc in made],
        }
        for doc in made:
            docs[doc["id"]] = doc

    if old is not None and not changed and set(docs) == set(old_docs):
        return old

    _apply_text_budget(docs)
    index = {
        "version": INDEX_VERSION,
        "vault": str(cfg.root.resolve()),
        "built": dt.datetime.now(dt.timezone.utc).isoformat(),
        "files": files,
        "docs": docs,
        "df": _document_frequencies(docs),
    }
    _write_index(cfg, index)
    return index


def _apply_text_budget(docs: dict[str, dict]) -> None:
    """Keep the stored text under `TEXT_BUDGET`, largest documents first to lose it.

    Postings — the term counts everything is ranked on — always stay. Only the
    text goes, and only for documents big enough that re-reading one to cut an
    excerpt is cheaper than carrying it in every load.
    """
    order = sorted(docs.values(), key=lambda doc: len(doc.get("text") or ""))
    spent = 0
    for doc in order:
        size = len(doc.get("text") or "")
        if spent + size <= TEXT_BUDGET:
            spent += size
        else:
            doc["text"] = None


def _document_frequencies(docs: dict[str, dict]) -> dict[str, int]:
    df: Counter[str] = Counter()
    for doc in docs.values():
        df.update(set(doc["tf"]) | set(doc["ttf"]))
    return dict(df)


def _write_index(cfg: Config, index: dict) -> None:
    path = index_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written beside the target and moved into place, so a killed build leaves
    # the previous index intact rather than a half-written one.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(index, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def _doc_text(cfg: Config, doc: dict) -> str:
    """The document's text: from the index, or from disk when the budget cut it."""
    stored = doc.get("text")
    if stored is not None:
        return stored
    path = cfg.root / doc["path"]
    if not path.is_file():
        return ""
    raw = path.read_text(encoding="utf-8", errors="replace")
    if doc["kind"] == "report":
        return report_text(raw)
    if doc["kind"] == "diagram":
        return diagram_text(raw)
    if doc["kind"] == "source":
        for source in sources_mod.parse_text(raw):
            if source.key == doc["key"]:
                return source_text(source)
        return ""
    return raw


# ── ranking ──────────────────────────────────────────────────────────────────


def _idf(df: int, total: int) -> float:
    return math.log(1 + total / (1 + df))


def _weight(doc: dict, query: Query, df: dict, total: int) -> float | None:
    """The tf-idf score, or None when the document fails the query.

    Failing is a boolean matter and comes first: every term present, every
    excluded term absent. Scoring only ever orders documents that already match,
    which is why a low score never means "nearly".
    """
    tf, ttf = doc["tf"], doc["ttf"]
    for term in query.excluded:
        if tf.get(term) or ttf.get(term):
            return None

    score = 0.0
    for term in query.all_terms:
        count = tf.get(term, 0) + TITLE_TF * ttf.get(term, 0)
        if not count:
            return None
        score += (1 + math.log(count)) * _idf(df.get(term, 0), total)
    return score / (1 + math.log(doc["len"] + 1))


def _phrases_ok(text: str, query: Query) -> int | None:
    """How many required phrases matched, or None when one did not."""
    for phrase in query.excluded_phrases:
        if phrase_re(phrase).search(text):
            return None
    for phrase in query.phrases:
        if not phrase_re(phrase).search(text):
            return None
    return len(query.phrases)


def find(
    cfg: Config,
    query: str,
    *,
    kinds: Sequence[str] | None = None,
    limit: int = 50,
    rebuild: bool = True,
) -> list[Hit]:
    """Search the vault. `kinds` narrows in code the way `kind:` narrows in the
    query string; given both, a document must satisfy both."""
    parsed = parse_query(query)
    if parsed.empty:
        raise SearchError(
            "nothing to search for — give at least one word, "
            'e.g. report-maker find pricing, or find "list price" kind:snapshot'
        )

    # None means "every kind"; an empty set means "no kind can satisfy both
    # filters", which is a legitimate — and empty — answer, not an absent filter.
    wanted: set[str] | None = set(parsed.kinds) or None
    if kinds:
        for name in kinds:
            if name not in KINDS:
                raise SearchError(
                    f"unknown kind {name!r} — expected one of: {', '.join(KINDS)}"
                )
        wanted = set(kinds) if wanted is None else wanted & set(kinds)

    index = build_index(cfg) if rebuild else load_index(cfg)
    if index is None:
        index = build_index(cfg)

    docs: dict = index["docs"]
    df: dict = index.get("df") or _document_frequencies(docs)
    total = max(len(docs), 1)

    phrasal = bool(parsed.phrases or parsed.excluded_phrases)
    texts: dict[str, str] = {}
    scored: list[tuple[float, dict]] = []
    for doc in docs.values():
        if wanted is not None and doc["kind"] not in wanted:
            continue
        score = _weight(doc, parsed, df, total)
        if score is None:
            continue
        matched = 0
        if phrasal:
            # The only reason to touch a document's text before it has placed in
            # the ranking: a phrase can still disqualify it. Everything else —
            # the excerpt, the marks, the line — waits for the top `limit`, so a
            # broad query does not read four hundred archived pages off disk.
            texts[doc["id"]] = text = _doc_text(cfg, doc)
            checked = _phrases_ok(text, parsed)
            if checked is None:
                continue
            matched = checked
        scored.append((score + PHRASE_BOOST * matched, doc))

    scored.sort(key=lambda row: (-row[0], row[1]["path"], row[1]["key"]))
    return [
        _hit(doc, texts.get(doc["id"]) or _doc_text(cfg, doc), score, parsed)
        for score, doc in scored[: max(limit, 0)]
    ]


def _hit(doc: dict, text: str, score: float, query: Query) -> Hit:
    excerpt, marks, offset = excerpt_for(text, query.all_terms)
    line = doc.get("line")
    if line is None and doc["kind"] in ("report", "diagram"):
        # Report and diagram text is blanked in place, never deleted, so the
        # offset of the matched word is still its offset in the file itself.
        line = text.count("\n", 0, offset) + 1 if text else 1
    return Hit(
        kind=doc["kind"],
        report=doc["report"],
        key=doc["key"],
        path=doc["path"],
        line=line,
        offset=offset if doc["kind"] == "snapshot" else None,
        score=round(score, 4),
        excerpt=excerpt,
        marks=marks,
        title=doc.get("title", ""),
        fetched=doc.get("fetched"),
    )


@lru_cache(maxsize=256)
def _terms_re(terms: tuple[str, ...]) -> re.Pattern:
    return re.compile(
        r"\b(?:" + "|".join(re.escape(term) for term in terms) + r")\b",
        re.IGNORECASE | re.UNICODE,
    )


def excerpt_for(
    text: str, terms: Sequence[str], *, width: int = EXCERPT_WORDS
) -> tuple[str, tuple[tuple[int, int], ...], int]:
    """The best ~`width`-word window of `text`, its marks, and the anchor.

    The anchor is the character offset **in `text`** of the first matched word
    in that window — the position a line number is counted to and the one a
    snapshot hit reports, so both point at the match rather than at the run-up
    to it.

    "Best" is the window covering the most *distinct* query terms, then the most
    occurrences, then the earliest — so a paragraph using two of the words beats
    one that repeats a single word. Candidate windows are measured in characters
    (`AVG_CHARS` per word) and only the winner is tokenised: an archived page
    runs to thousands of words, and tokenising all of them to quote thirty is
    the difference between a search that keeps up with typing and one that does
    not. Whitespace inside the winner is collapsed as the excerpt is built,
    which is what keeps a report excerpt readable when a blanked-out helper call
    sat in the middle of the sentence.
    """
    wanted = frozenset(terms)
    reach = width * AVG_CHARS
    found = (
        [
            (match.start(), match.group(0).casefold())
            for match in _terms_re(tuple(sorted(wanted))).finditer(text)
        ]
        if text and wanted
        else []
    )

    start = 0
    if found:
        # Both window edges only ever move forward, so the candidates are swept
        # with two pointers and a running tally rather than re-counted for each
        # one — a page that says the word two hundred times is otherwise
        # quadratic, and a page that says the word two hundred times is exactly
        # the page a search is most likely to return.
        best, low, high = (-1, -1, 0), 0, 0
        tally: Counter[str] = Counter()
        for position, _term in found:
            first = max(0, position - EXCERPT_LEAD * AVG_CHARS)
            while high < len(found) and found[high][0] < first + reach:
                tally[found[high][1]] += 1
                high += 1
            while found[low][0] < first:
                tally[found[low][1]] -= 1
                if not tally[found[low][1]]:
                    del tally[found[low][1]]
                low += 1
            rank = (len(tally), high - low, -first)
            if rank > best:
                best, start = rank, first
        while start > 0 and text[start - 1].isalnum():
            start -= 1  # never open the excerpt on half a word

    spans = _spans(text[start : start + reach * 2])
    window = spans[:width]
    if not window:
        return "", (), start

    head, tail = window[0][1], window[-1][2]
    anchor = start + next((at for token, at, _e in window if token in wanted), head)
    parts: list[str] = ["…"] if start > 0 else []
    length = len(parts[0]) if parts else 0
    marks: list[tuple[int, int]] = []
    cursor = head
    for token, begin, end in window:
        gap = re.sub(r"\s+", " ", text[start + cursor : start + begin])
        parts.append(gap)
        length += len(gap)
        word = text[start + begin : start + end]
        if token in wanted:
            marks.append((length, length + len(word)))
        parts.append(word)
        length += len(word)
        cursor = end
    if start + tail < len(text.rstrip()):
        parts.append("…")
    return "".join(parts), tuple(marks), anchor


# ── output ───────────────────────────────────────────────────────────────────


def to_json(hits: Sequence[Hit]) -> dict:
    return {
        "count": len(hits),
        "hits": [
            {
                "kind": hit.kind,
                "report": hit.report,
                "key": hit.key,
                "path": hit.path,
                "line": hit.line,
                "offset": hit.offset,
                "score": hit.score,
                "excerpt": hit.excerpt,
                "marks": [[start, end] for start, end in hit.marks],
                "title": hit.title,
                "fetched": hit.fetched,
            }
            for hit in hits
        ],
    }


def report_hits(cfg: Config, hits: Sequence[Hit]) -> int:
    """Print hits, grep-style. Exit 1 when nothing matched, so a shell can test it."""
    if not hits:
        print("  no matches")
        return 1
    for hit in hits:
        where = hit.path + (f":{hit.line}" if hit.line else "")
        label = f"{hit.kind}{'/' + hit.key if hit.key else ''}"
        print(f"\n  {label:<28} {where}")
        if hit.title:
            print(f"    {hit.title}")
        if hit.excerpt:
            print(f"    {hit.excerpt}")
    files = len({hit.path for hit in hits})
    print(f"\n  {len(hits)} hit(s) in {files} file(s)")
    return 0
