"""Do the pages we cited still say what we said they said?

A citation is a promise that a claim rests on something outside the report, and
the web breaks that promise quietly. A pricing page is edited, a press release is
withdrawn, a URL 404s two quarters after the audit shipped — and none of it shows
up in a build, because Typst compiles a `@key` exactly the same whether the page
behind it still exists or not.

`report-maker verify` re-fetches every source that was archived and says what
moved. It compares the sha256 of the bytes first, and when they differ it
measures how far apart the *extracted text* is, because a nav-bar tweak and a
rewritten argument both change the bytes and only the second one is worth a
person's attention.

A page changing is not a failure. The snapshot is still evidence and the report
is still defensible because of it — that is the entire reason the archive exists.
So `changed` is a warning, and only a dead link, a page that is now *gone*, sets
a non-zero exit code.

Nothing here ever mutates a stored snapshot. `--refresh` writes a new record and
moves the old one aside to `<key>.<fetched-date>.html`, because an archive you
are willing to overwrite is not an archive.

The fetcher is a parameter rather than a hard-wired call, so the tests run
without a network and `--offline` is a real mode rather than a lie: on a plane,
or in CI, every archived source reports `offline` and nothing is dialled.
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from . import snapshot, sources
from .config import Config
from .workspace import Report, reports

# The two statuses that mean the resource is *gone*, as opposed to meaning we
# were blocked, throttled or unlucky. Only these fail the command: a 403 from a
# bot wall and a 503 from an overloaded host say nothing about whether the page
# still exists, and a checker that cries dead-link at them is a checker people
# stop believing.
GONE_STATUSES = frozenset({404, 410})

# Worst first. Used for the summary line, so a run over eighty sources still
# reads top-down.
STATES = ("gone", "error", "changed", "unsnapshotted", "offline", "ok")


@dataclass
class Drift:
    """What one archived source is doing now, relative to when it was archived."""

    report: str
    key: str
    url: str
    state: str  # "ok" | "changed" | "gone" | "error" | "unsnapshotted" | "offline"
    detail: str
    fetched: str | None  # ISO datetime of the original snapshot
    similarity: float | None  # 0..1 over extracted text, when both texts exist


# ── comparing ────────────────────────────────────────────────────────────────


def text_similarity(old: str, new: str) -> float:
    """How alike two extracted texts are, 0..1.

    `quick_ratio` rather than `ratio`: it compares the multiset of characters
    instead of hunting for matching blocks, which is orders of magnitude cheaper
    on the hundred-kilobyte texts a real page yields, and precise enough for the
    only distinction that matters here — "they changed the footer" versus "they
    rewrote the page".

    It runs on the extracted text and never on the raw bytes. Bytes differ when a
    cache-busting query string moves in the markup; text differs when the
    argument the report rests on has been edited.
    """
    return difflib.SequenceMatcher(None, old, new).quick_ratio()


def _percent(value: float | None) -> str:
    """A similarity as a percentage, with the two rounding lies suppressed.

    A page whose bytes changed by one word is 99.7% similar, and printing that as
    "changed — 100% similar" reads as a contradiction and trains people to
    distrust the number. The same in reverse at the bottom of the range: a page
    replaced wholesale is not "0% similar" to its own alphabet.
    """
    if value is None:
        return "—"
    rounded = round(value * 100)
    if rounded >= 100 and value < 1.0:
        return ">99%"
    if rounded <= 0 and value > 0.0:
        return "<1%"
    return f"{rounded}%"


def _reason(exc: BaseException) -> str:
    text = str(exc).strip()
    return text or type(exc).__name__


def _stamp(record: Mapping) -> str:
    """The date part of a record's `fetched`, for saying so in a sentence.

    Display only. The archived *file* name is `snapshot.rotate`'s business, and
    only that function may decide it — see `_refresh` below.
    """
    text = str(record.get("fetched") or "").strip()
    match = re.match(r"\d{4}-\d{2}-\d{2}", text)
    if match:
        return match.group(0)
    cleaned = re.sub(r"[^0-9A-Za-z]+", "-", text).strip("-")
    return cleaned[:24] or "undated"


def _record(report: Report, key: str) -> dict | None:
    """The stored record, or None — including when it is there but unreadable.

    A corrupt record reads as no record, which reports `unsnapshotted` and tells
    the reader to archive it again. That is the right fix in both cases, and it
    is better than ending the pass on one bad JSON file.
    """
    try:
        return snapshot.read_record(report, key)
    except Exception:  # noqa: BLE001 — one bad file must not stop the run
        return None


def _archived_text(report: Report, key: str) -> str | None:
    try:
        return snapshot.read_text(report, key)
    except Exception:  # noqa: BLE001 — same reasoning as _record
        return None


# ── the archive ──────────────────────────────────────────────────────────────


def _refresh(report: Report, key: str, got: snapshot.Fetched) -> str:
    """Move the old snapshot aside, then write the new one.

    The move itself is `snapshot.rotate`, not a copy written here. That module
    owns where a snapshot lives — it maps a key to a filename, because a key may
    legally contain `:` and `+` and a filesystem may not — so a second
    implementation of the same naming in this module would sooner or later look
    for a file that was never written, conclude there was nothing to preserve,
    and let the write overwrite the evidence. Which is the one outcome the
    archive exists to prevent.

    Returns a phrase naming what was kept, for the line the user reads.
    """
    kept = snapshot.rotate(report, key)
    snapshot.write(report, key, got)
    if not kept:
        return " · archived"
    return f" · re-archived, the old copy kept as {kept[0].stem}.*"


# ── one source ───────────────────────────────────────────────────────────────


def _drift(
    report: Report,
    key: str,
    url: str,
    record: Mapping,
    *,
    fetch: snapshot.Fetcher,
    refresh: bool,
) -> Drift:
    """Re-fetch one archived source and say what it is doing."""
    stamp = _stamp(record)
    fetched_at = str(record.get("fetched") or "") or None

    def result(state: str, detail: str, similarity: float | None = None) -> Drift:
        return Drift(
            report=report.id,
            key=key,
            url=url,
            state=state,
            detail=detail,
            fetched=fetched_at,
            similarity=similarity,
        )

    try:
        got = fetch(url)
    except Exception as exc:  # noqa: BLE001 — one dead host must not end the pass
        # urllib raises HTTPError for a 4xx and carries the status on `.code`; a
        # fetcher that returns the response instead is handled just below. Both
        # shapes have to reach the same verdict, or `gone` would depend on which
        # one the fetcher happens to be.
        status = getattr(exc, "code", None)
        if status in GONE_STATUSES:
            return result("gone", f"HTTP {status} — the page is no longer there · {url}")
        return result("error", f"could not fetch — {_reason(exc)} · {url}")

    if got.status in GONE_STATUSES:
        return result("gone", f"HTTP {got.status} — the page is no longer there · {url}")
    if got.status >= 400:
        # Blocked, throttled or broken at their end. We learned nothing about the
        # page, and saying so is more useful than guessing.
        return result("error", f"HTTP {got.status} — could not read the page · {url}")

    if snapshot.sha256_of(got.body) == str(record.get("sha256") or ""):
        # Identical bytes are identical text, so there is nothing to measure.
        return result("ok", f"unchanged since {stamp}", 1.0)

    old_text = _archived_text(report, key)
    similarity = (
        text_similarity(old_text, snapshot.extract_text(got.body, got.content_type))
        if old_text is not None
        else None
    )
    detail = (
        f"changed since {stamp} — extracted text {_percent(similarity)} similar"
        if similarity is not None
        else f"changed since {stamp} — no archived text to compare against"
    )

    if refresh:
        detail += _refresh(report, key, got)
    return result("changed", detail, similarity)


# ── the pass ─────────────────────────────────────────────────────────────────


def verify(
    cfg: Config,
    target: str | None = None,
    *,
    fetch: snapshot.Fetcher = snapshot.http_fetch,
    offline: bool = False,
    refresh: bool = False,
) -> list[Drift]:
    """Re-check every archived source of every matching report.

    A source with no URL — a measurement we took, an interview, an internal
    document — has nothing to re-fetch and is not reported at all; it is not
    drifting, it is simply not a web page.

    A source with a URL but no snapshot reports `unsnapshotted` without touching
    the network. That is a fact about this vault, not about the web, and there is
    nothing to compare a fetch against anyway.
    """
    out: list[Drift] = []
    for report in reports(cfg, target):
        for source in sources.parse(report.sources):
            record = _record(report, source.key)
            # The bibliography is the authority on what the report claims to
            # cite, so its URL wins. When they disagree, re-fetching the cited
            # URL and finding it different from the archive is exactly the
            # signal a reader wants.
            url = source.url or str((record or {}).get("url") or "")
            if not url:
                continue
            if record is None:
                out.append(
                    Drift(
                        report=report.id,
                        key=source.key,
                        url=url,
                        state="unsnapshotted",
                        detail="no snapshot archived — run `report-maker cite --refresh`",
                        fetched=None,
                        similarity=None,
                    )
                )
                continue
            if offline:
                out.append(
                    Drift(
                        report=report.id,
                        key=source.key,
                        url=url,
                        state="offline",
                        detail=f"not checked — offline; archived {_stamp(record)}",
                        fetched=str(record.get("fetched") or "") or None,
                        similarity=None,
                    )
                )
                continue
            out.append(
                _drift(report, source.key, url, record, fetch=fetch, refresh=refresh)
            )
    return out


# ── output ───────────────────────────────────────────────────────────────────


def counts(drifts: Sequence[Drift]) -> dict[str, int]:
    """Every state, zeros included — a caller should never have to test for
    `undefined` before rendering a number."""
    tally = {state: 0 for state in STATES}
    for drift in drifts:
        tally[drift.state] = tally.get(drift.state, 0) + 1
    return tally


def to_json(drifts: Sequence[Drift]) -> dict:
    return {
        "drifts": [
            {
                "report": drift.report,
                "key": drift.key,
                "url": drift.url,
                "state": drift.state,
                "detail": drift.detail,
                "fetched": drift.fetched,
                "similarity": drift.similarity,
            }
            for drift in drifts
        ],
        "counts": counts(drifts),
    }


def report_drift(cfg: Config, drifts: Sequence[Drift]) -> int:
    """Print the drift, grouped by report. Returns the process exit code."""
    if not drifts:
        print("  no source with a URL — nothing to verify")
        return 0

    width = min(max(len(drift.key) for drift in drifts), 32)
    current: str | None = None
    for drift in drifts:
        if drift.report != current:
            current = drift.report
            print(f"  {(cfg.reports / current).relative_to(cfg.root)}")
        print(f"    {drift.state:<14} {drift.key:<{width}}  {drift.detail}")

    tally = counts(drifts)
    print(
        "\n  "
        + ", ".join(f"{tally[state]} {state}" for state in STATES if tally[state])
    )

    if not tally["gone"]:
        return 0
    # Said in full, because the instinct on a dead link is to quietly drop the
    # citation, and that is the one response that makes the report less true.
    dead = tally["gone"]
    print(
        f"\n  {dead} dead link{'' if dead == 1 else 's'}. The archived snapshot is "
        "still the evidence for what\n"
        "  the page said when it was read — cite that, rather than removing the "
        "claim."
    )
    return 1
