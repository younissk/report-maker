"""`report-maker cite` — one URL becomes a citable source.

The citation rule asks for a `@key` behind every fact, and the friction in
following it is almost entirely clerical: open the page, copy the title, guess a
key nobody has used, write six lines of hayagriva, and hope the page is still
there in a year. This command does all of that in one line, and archives the
bytes while it is there — see `snapshot.py` for why that matters.

    report-maker cite acme/2026-08-12-audit https://acme.example/pricing
      → reports/acme/2026-08-12-audit/sources.yml (acme-pricing)
      Cite it with: @acme-pricing

The last line is the point. A tool that adds an entry and leaves you to go and
look up what it called it has moved the clerical work rather than removed it.

Two behaviours are deliberate. Citing the same URL twice does nothing — it
recognises the existing entry, keeps its key, and returns it, so re-running a
half-finished command is always safe. And nothing is ever *inferred* into the
bibliography: a page with no author gets no `author:` field, because an invented
attribution is a worse failure than a missing one.
"""

from __future__ import annotations

import datetime as dt
import re
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

from . import snapshot, sources
from .config import Config
from .sources import Source
from .workspace import Report, reports


class CiteError(RuntimeError):
    pass


# ── the report, the URL, the key ─────────────────────────────────────────────


def _one_report(cfg: Config, target: str) -> Report:
    """A citation belongs to exactly one bibliography, so a folder is not enough."""
    found = reports(cfg, target)
    if len(found) == 1:
        return found[0]
    listed = "\n    ".join(report.id for report in found)
    raise CiteError(
        f"{target!r} matches {len(found)} reports — a source is added to one "
        f"bibliography, so name one:\n    {listed}"
    )


def normalise(url: str) -> str:
    """Accept what a person actually pastes, refuse what must never be fetched.

    A bare `acme.example/pricing` gets `https://`, because that is what was
    meant. Anything that names a non-web scheme is refused here as well as in
    `snapshot`, so `--no-snapshot` cannot smuggle a `file://` URL into a
    bibliography where `verify` would later fetch it.
    """
    url = url.strip()
    if not url:
        raise CiteError("no URL given")
    parts = urlsplit(url)
    # A dot in what looks like a scheme means it is really `host:port` — urlsplit
    # reads `acme.example:8000/x` as the scheme `acme.example`.
    if not parts.scheme or "." in parts.scheme:
        url = "https://" + url.lstrip("/")
        parts = urlsplit(url)
    if parts.scheme.lower() not in snapshot.WEB_SCHEMES:
        raise CiteError(
            f"{url!r} is not a web address — only http and https can be cited "
            "and archived."
        )
    return url


def _canonical(url: str) -> str:
    """A comparison form, for deciding whether a URL is already in the file.

    The scheme is left out on purpose: a site that has moved to https should not
    produce a second bibliography entry for a page already cited over http.
    """
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    port = f":{parts.port}" if parts.port else ""
    path = parts.path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{host}{port}{path}{query}"


def _already_cited(existing: list[Source], url: str) -> Source | None:
    wanted = _canonical(url)
    for source in existing:
        if source.url and _canonical(source.url) == wanted:
            return source
    return None


def _entry_type(content_type: str, explicit: str | None) -> str:
    """`Web` unless we can see it is something else. A PDF at a URL is a
    document that happens to be published on the web, and reads better in
    References as a Report."""
    if explicit:
        return explicit
    mime = content_type.split(";")[0].strip().lower()
    return "Report" if mime == "application/pdf" else "Web"


def _iso_date(value: str) -> str | None:
    """A date Hayagriva will accept, or nothing at all.

    A page announces its publication date in whatever shape its CMS favours — an
    MDN article says `2026-03-22T23:36:38.000Z`, an RSS lineage says
    `Tue, 22 Mar 2026 23:36:38 GMT`, some pages manage only a year. Hayagriva
    parses `YYYY`, `YYYY-MM` and `YYYY-MM-DD` and refuses everything else, so
    writing the raw string through means a successful `cite` can leave a report
    that no longer builds — the one failure a convenience command must never
    cause. Anything that cannot be reduced to those three shapes is dropped: a
    missing date costs a reader nothing, and an invented one is exactly the
    small fabrication this tool exists to refuse.
    """
    if not value:
        return None
    text = str(value).strip()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if match:
        return "-".join(match.groups())
    match = re.match(r"^(\d{4})-(\d{2})$", text)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    try:
        return parsedate_to_datetime(text).date().isoformat()
    except (TypeError, ValueError, IndexError):
        pass
    match = re.match(r"^(\d{4})$", text)
    return match.group(1) if match else None


def _fields(
    url: str, meta: dict, content_type: str, accessed: str, type_: str | None
) -> dict:
    fields: dict = {"type": _entry_type(content_type, type_)}
    # No title found is not a reason to write a blank one: the URL is what we
    # know, so the URL is what the entry says until a person improves it.
    fields["title"] = meta.get("title") or url
    if meta.get("author"):
        fields["author"] = meta["author"]
    if meta.get("site"):
        fields["publisher"] = meta["site"]
    published = _iso_date(meta.get("published", ""))
    if published:
        fields["date"] = published
    fields["url"] = sources.url_field(url, accessed)
    return fields


# ── the command ──────────────────────────────────────────────────────────────


def cite(
    cfg: Config,
    target: str,
    url: str,
    *,
    key: str | None = None,
    type_: str | None = None,
    no_snapshot: bool = False,
    fetch: snapshot.Fetcher = snapshot.http_fetch,
) -> Source:
    """Fetch a URL, archive it, and add it to one report's bibliography."""
    report = _one_report(cfg, target)
    url = normalise(url)
    existing_sources = sources.parse(report.sources)
    taken = {source.key for source in existing_sources}

    # The idempotent path, taken before the network: an entry for this URL that
    # already has its snapshot needs nothing at all.
    existing = _already_cited(existing_sources, url)
    archived = existing is not None and snapshot.read_record(report, existing.key)
    if existing is not None and (no_snapshot or archived):
        print(f"  · {url} is already cited as @{existing.key}")
        _print_key(existing.key)
        return existing

    if key is not None and existing is None and key in taken:
        raise CiteError(
            f"@{key} is already used in {report.sources.name} for a different "
            "source — choose another key, or let cite pick one."
        )

    fetched = fetch(url)
    final = fetched.final_url or url
    meta = snapshot.extract_meta(fetched.body, fetched.content_type)

    if existing is not None:
        # The entry is there, the archive was not. Fill in the missing half and
        # leave the person's own wording of the entry exactly as they wrote it —
        # including its key, which is why an explicit `key` is ignored here.
        print(f"  · {url} is already cited as @{existing.key} — archiving it now")
        record = snapshot.write(report, existing.key, fetched)
        _print_snapshot(cfg, report, existing.key, record)
        _print_status(fetched.status)
        _print_key(existing.key)
        return existing

    key = key or sources.slugify_key(meta.get("title", ""), final, taken)
    record = None if no_snapshot else snapshot.write(report, key, fetched)
    # The accessed date and the snapshot must agree, or References claims a date
    # the archive cannot support.
    accessed = str(record["fetched"])[:10] if record else dt.date.today().isoformat()

    source = Source(
        key=key,
        fields=_fields(final, meta, fetched.content_type, accessed, type_),
    )
    sources.append(report.sources, source)

    print(f"  → {report.sources.relative_to(cfg.root)} ({key})")
    if record is not None:
        _print_snapshot(cfg, report, key, record)
    else:
        print("    (no snapshot — nothing archived, so quotes cannot be checked)")
    _print_status(fetched.status)
    _print_key(key)
    return source


def _print_snapshot(cfg: Config, report: Report, key: str, record: dict) -> None:
    path = snapshot.raw_path(report, key)
    print(
        f"  → {path.relative_to(cfg.root)} "
        f"(sha256 {str(record['sha256'])[:12]}…, {record['bytes']:,} bytes)"
    )


def _print_status(status: int) -> None:
    if status >= 400:
        # Not an error here. "No pricing on any reviewed page" is a citable
        # finding, and this is the evidence for it.
        print(
            f"    (the server answered {status} — archived as evidence of what the "
            "URL serves today)"
        )


def _print_key(key: str) -> None:
    print()
    print(f"  Cite it with: @{key}")
