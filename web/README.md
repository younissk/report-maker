# web/

report-maker in a browser. Land on the page, get a working vault a second
later, write a report, build it, and send somebody a link that carries the
evidence with it — no account, no install, nothing to configure.

Two products share one server:

| mode | what it is | where the work lives |
|---|---|---|
| **try** | the default. A session vault seeded from the `base` starter, gone in 24 hours | a temp dir on the server, swept |
| **github** | connect a repository, work on it, commit and push back | your repository. The server keeps a session record and nothing else |

Google Drive is deliberately absent. `sync`, `diff` and the version timeline all
lean on git, and Drive has no atomic commits, no history and no diff —
connecting it would mean giving up three features that already exist.

```bash
python3 -m web                          # http://127.0.0.1:8787
python3 -m web --port 9000 --root ~/rm-web
make web                                # the same thing, from the repo root
npm --prefix web/client run build       # the frontend it serves
```

Python 3.11+ and the standard library. No Flask, no FastAPI, no `requests`,
nothing to install — which is the entire point of the choice. Every route
spends its time waiting on a `report-maker` subprocess rather than on a
framework, so a framework would buy routing and middleware that fits in three
hundred lines here, at the price of turning deployment into a dependency tree.

## What it is not

It is not where the logic lives. The paragraph in [`app/README.md`](../app/README.md)
governs this directory word for word: every question about a vault — what
reports exist, what a build produces, whether the citation rule holds, whether a
source still says what it said — is answered by shelling out to
`report-maker`, exactly as a terminal would.

```
browser  ──HTTP──▶  web/server  ──spawn──▶  report-maker -C <session vault> …
```

Nothing in `web/` parses a report, evaluates the citation rule, or computes what
a CLI command already prints. Where the web layer needs to know a path — where
the PDF landed, where the page images are, which file is a report's `main.typ` —
it reads `out/manifest.json`, which the engine writes and which names all of
them relative to the vault. Even the manifest's own location comes from the line
`report-maker manifest` prints. Nothing here assumes `out/`, because
`report-maker.toml` can move it.

`engine/` is not modified by this build. Where the web layer needs something the
engine cannot do, it is written down under [What is not closed](#what-is-not-closed)
rather than patched around.

## Layout

```
web/
  __main__.py        `python3 -m web` — three lines, so there is no launcher
  server/
    app.py           HTTP: sockets, cookies, static files, logging. No vaults.
    routes.py        which engine command answers which URL. No sockets.
    engine.py        the only door to a subprocess: -C containment, denylist,
                     the 60-second deadline, the output budget
    security.py      every guard: path containment, the SSRF pre-flight,
                     quotas, the rate limiter, the CSP
    sessions.py      the store: a vault per session, a record, a sweeper
    github.py        OAuth, the hardened clone, `sync` passed through verbatim
    share.py         `all --html`, the self-containment check, the public page
  client/            Vite + React 19 + Tailwind v4, built to client/dist
  tests/             unittest, no third-party runner
```

## Running it

| flag | default | what it does |
|---|---|---|
| `--host` | `127.0.0.1` | anything else prints a warning naming what is exposed |
| `--port` | `8787` | |
| `--root` | a per-run temp dir | where `sessions/` and `shares/` live |
| `--client` | `web/client/dist` | the built frontend; without one, the API still answers |
| `--tls` | off | this server is reached over HTTPS: `Secure` on the cookie, HSTS on |

Every flag has an environment variable, because a container takes environment
and not argv:

| variable | what it sets |
|---|---|
| `RM_WEB_HOST`, `RM_WEB_PORT` | the bind |
| `RM_WEB_ROOT` | the session and share store. **Set this in production** — the default is a temp dir that a reboot removes |
| `RM_WEB_CLIENT` | the built frontend |
| `RM_WEB_SECURE_COOKIE` | `1` when this server is behind TLS |
| `RM_WEB_ENGINE` | an explicit `bin/report-maker`, when it is not in this checkout or on `PATH` |
| `RM_WEB_DIAGRAMS` | `1` to allow `report-maker diagrams`. Off by default — see below |
| `RM_WEB_SHARE_TTL_HOURS` | how long a share link lives (default 720 — thirty days; `0` keeps them for ever) |
| `RM_WEB_GITHUB_CLIENT_ID` | OAuth app id. Without it, GitHub mode is off and the UI says so |
| `RM_WEB_GITHUB_CLIENT_SECRET` | OAuth app secret |
| `RM_WEB_GITHUB_CALLBACK` | where GitHub sends the browser back |
| `RM_WEB_GITHUB_TOKEN` | a single-user, self-hosted alternative to OAuth. A server-side variable, never a field in the UI |

`GET /api/health` answers `{ok, version, diagrams, github}` and never a path, so
a container probe can use it without publishing this machine's layout.

## The store

```
RM_WEB_ROOT/
  sessions/<session-id>/
    vault/          the vault — report-maker.toml lives here
    session.json    {id, label, created, last_seen, mode, repo?, branch?, quota, token?}
  shares/<token>.html   a published bundle, and <token>.json beside it
```

The session id is `secrets.token_urlsafe(32)`, delivered as an `HttpOnly`,
`SameSite=Lax`, `Secure`-when-TLS cookie and **never in a URL and never in a
response body**. That last part is a deliberate departure from the obvious
design: `POST /api/session` returns a `label` and the quota, not the id and not
the vault path. Returning either would hand the credential to JavaScript and
make `HttpOnly` a decoration — and the vault path *contains* the id. Nothing in
the UI needs either; the cookie proves which session the browser holds, and
200-versus-401 tells it whether it holds one.

A session is idle for 24 hours and it is gone, vault and all, swept by a
background thread. Shares outlive the session that made them, because the whole
point of a share is that it still opens after you have closed the tab.

## Security posture

Every item the spec calls a requirement, and where it is enforced. A reviewer
should be able to find each one by reading a single function.

| | requirement | where |
|---|---|---|
| 1 | loopback by default; anything else prints a warning | `app.parse`, `app._warn_exposed` |
| 2 | every path from a request resolved and confirmed inside the session vault | `security.within`, called through `routes.Bridge.within`; static files through the same function |
| 3 | Typst reads only under `--root` | the engine's, relied on. **Unverified here** — see below |
| 4 | SSRF pre-flight before `cite` | `security.check_url`, in `routes.cite`. **Partial** — see below |
| 5 | `template install` disabled | `engine.guard`, at the bridge, so no route can forget it |
| 6 | diagrams off unless `RM_WEB_DIAGRAMS=1` | `engine.guard` |
| 7 | disk 50 MB, 60 s per command, 200 commands/hour, 20 reports | `security.enforce`, through `routes.Bridge` |
| 8 | 60 requests/minute, 5 sessions/hour, per source address | `security.RateLimiter`, in `app.Handler._serve` |
| 9 | 24-hour session TTL, swept | `sessions.sweep`, `app.sweeper` |
| 10 | no secret reaches the browser | `sessions.Session.to_json`, `github.connection` |
| 11 | user text escaped wherever it is rendered | `security.esc` — and the API returns JSON, which the frontend renders |
| 12 | CSP with no inline script but a nonce, and no external host | `security.security_headers`, applied in `app.Handler._page` |

Beyond the list:

- **The vault boundary is checked twice.** `security.within` refuses an absolute
  path, refuses `..` outright rather than normalising it away, walks every
  component looking for a symlink whose target leaves the vault, and *then*
  resolves and compares. Both sides are resolved before comparison — on macOS a
  session under `/var` lives behind the `/private/var` symlink, and a string
  compare would refuse every legitimate path. Containment is decided with
  `target == root or root in target.parents`, never a string prefix, so
  `…/vault-evil` is not inside `…/vault`.
- **URL segments are decoded exactly once**, after the split, in `routes.match`.
  Decoding the whole path first turns `%2F` into a separator that was never
  sent; decoding twice turns `%252e%252e` into `..` one layer too late.
- **`-C` is the server's, never the request's.** The bridge refuses `-C`,
  `-C<value>`, `--vault` and `--vault=` in any argument, and it resolves the
  vault and requires it to be a strict descendant of `sessions/` before it
  spawns anything at all. With no sessions root declared, nothing spawns.
- **Request bodies are capped at 2 MB.** The disk quota refuses the *next* write
  once a vault is full, so the overshoot is bounded by one body — which only
  holds if a body is bounded.
- **Writes another site started are refused.** The cookie is `SameSite=Lax`,
  and `Sec-Fetch-Site` is checked as a second lock. There are no CORS headers
  anywhere in this server: the API is for the page this server itself serves.
- **No absolute path leaves the process.** Several engine commands print the
  vault path inside their own JSON — `check --json` opens with it — and a
  build's stderr is full of them. Every JSON body is walked on the way out and
  the server's roots removed. Raw bodies (a PDF, a page image, an HTML bundle)
  are passed through untouched, because they are the user's own artefacts.
- **One 401 shape.** Missing, malformed, unknown, tampered with, expired,
  swept — every reason a session did not open produces the identical body. A
  caller does the same thing about all of them and a stranger must not be able
  to tell which it was.
- **The log line has no id and no token.** The query string is dropped whole,
  because `?code=` on the GitHub callback is an authorization code; a share
  path logs as `/s/…`, because the token *is* the authorisation.

### What is off by default, and why

**Diagrams.** `report-maker diagrams` drives mermaid, which drives a headless
Chrome, once per diagram. That is the largest attack surface in the whole tool
and a trivial denial of service. `RM_WEB_DIAGRAMS=1` turns it on for an operator
who knows what they are hosting. `--prepare` renders nothing and would have been
safe to allow; it is refused anyway, because a security boundary with a carve-out
in it is one every reader has to reason about.

**`template install` and `template update`.** Both fetch arbitrary git
repositories — `install` from a URL in the request, `update` from URLs recorded
in the vault, which in GitHub mode arrived from somebody's repo. Same hole, two
doors, both shut. `templates` and `template new` still work.

**Pushing.** Never without an explicit action in that request, never `--force`,
and every refusal `engine/gitsync.py` makes is passed through word for word.
Those refusals each name the command that fixes them; a web layer that summarised
them into "push failed" would teach the writer to reach for `--force` unaided,
which is the outcome that module exists to prevent.

### What is not closed

Written down rather than implied to be solved.

1. **`cite` is vetted but not pinned.** `security.check_url` resolves the
   hostname and judges *every* address it answers with — loopback, link-local,
   private, multicast, reserved, the cloud metadata endpoints, and a final
   `not is_global` catch-all that also covers carrier-grade NAT. But the fetch
   itself happens inside `engine/snapshot.py`, which resolves the name a second
   time, and `engine/` is not ours to edit. A hostile name server that answers
   differently between the two lookups — a classic DNS rebind — still gets
   through. `security.safe_opener` closes this for fetches the web layer makes
   itself; it cannot close it for the engine's. **The fix is engine-side**, and
   either shape works: `cite --pinned-address <ip>` (connect to the vetted
   literal, keep the hostname for `Host` and SNI, re-check every redirect), or
   `cite --from-file` (the web layer fetches through `safe_opener` and hands
   over the archive).

   Redirects used to be the larger half of this and are no longer open.
   `engine/snapshot.py` follows a hop checking only its *scheme*, so a public
   host answering `302 Location: http://169.254.169.254/latest/meta-data/`
   fetched the metadata endpoint and archived the credentials into
   `snapshots/`, where the caller reads them back through
   `GET /reports/:id/file` — no hostile name server, no timing, one header.
   `security.trace` now walks the chain first through `safe_opener`, vetting
   and pinning every hop, and the engine is handed the terminal URL rather than
   the one that was typed. What remains is the same second-lookup window as
   above: the engine fetches again, and a server willing to answer one request
   with a page and the next with a redirect gets one hop through. Same engine
   fix closes both.

   The other half was a parser disagreement. `getaddrinfo("0177.0.0.1")` on
   macOS answers 177.0.0.1 — a public address — while `inet_aton`, glibc and
   curl read the same string as octal and answer 127.0.0.1. `check_url` now
   judges a host under *both* readings, so a spelling that any parser in the
   chain resolves to somewhere non-routable is refused.
2. **An online `verify` is refused for a whole vault.** It re-fetches every
   archived source, which is `cite` again once per entry with nobody looking at
   the URLs. Naming one report is allowed: its `sources.yml` is read first and
   every URL in it is put through the same pre-flight. Item 1 still applies to
   each of them.
3. **Typst's `--root` is the vault, and it follows symlinks.** Verified rather
   than assumed, both halves. `engine/build.py` and `engine/pages.py` pass
   `--root <cfg.root>` and never a parent, and typst refuses `read("/../etc/…")`
   and `read("../../etc/…")` with "would escape the project root" — probed, not
   read off the source.

   What it does *not* refuse is a symlink. A link inside the vault pointing at
   `/etc` makes `#raw(read("/leakdir/passwd"))` compile, and the contents land
   in the PDF the session then downloads. So typst's sandbox holds only while
   no symlink reaches a session vault, and today none can: `file_write` writes
   text to a path `security.within` has already refused to resolve through a
   link, the engine creates none, and every `git` invocation in `github.py`
   carries `-c core.symlinks=false`, which checks a repository's symlinks out
   as ordinary files holding their target as text.

   **That is a standing constraint on anything added here.** An upload route, an
   archive extractor, or a `git` call that loses `core.symlinks=false` would
   reopen it — and the failure would not look like a path bug, it would look
   like a report with somebody's `/etc/passwd` typeset into it.
4. **`all` renders diagrams on its own, and the server hides Node from it.**
   `guard` refuses `report-maker diagrams`, and that was never the whole
   surface: `report-maker all` renders diagrams as its second step, so
   seventeen bytes of mermaid in `diagrams/` and one press of Build used to buy
   an `npm install` of 190 packages and a headless Chrome — measured at 460 MB
   arriving inside a 50 MB quota, which no check on the *next* write can undo.
   The engine has no `--no-diagrams` and is not ours to edit, so with
   `RM_WEB_DIAGRAMS` unset `engine._env` removes every directory holding
   `node`, `npm` or `npx` from the subprocess `PATH`. `diagrams.ensure_cli`
   then raises, `cmd_all` prints `skipped: …`, and the build carries on to
   typst. Removed rather than shimmed, deliberately: a *failing* npm raises
   where a *missing* one does not, and that would crash the build instead of
   skipping it. The cost is the engine's own skip message — "Install Node.js,
   then re-run" — which is not the writer's decision to act on, so
   `POST /reports/:id/build` returns `diagrams: false` beside it and the page
   says the true thing.
5. **One process only.** The rate limiter and the per-session command tally are
   in memory. Run this under two workers and every limit doubles. Both would
   need a shared store first.
6. **Shares grow.** They are immutable by design, so nothing overwrites one and
   deleting the file is the only way to revoke a link. `RM_WEB_SHARE_TTL_HOURS`
   sweeps them; set it to `0` and mind the disk yourself.
7. **An error body is scrubbed of this machine's layout, and that is a
   subtraction rather than a guarantee.** `app._roots` removes the session
   vault, the sessions store, `RM_WEB_ROOT`, the shares directory, the engine
   checkout, the home directory and the interpreter tree — nominal *and*
   resolved, because Homebrew's `python@3.14` prefix is a symlink into `Cellar`
   and a traceback prints the target while `sys.prefix` reports the link — plus
   the session id as a last net. It is a list, so a path shape nobody
   anticipated survives it. The one that matters most is covered twice: the
   vault prefix carries the session id, and the id is redacted on its own after.

   This was open. `_serve` took the session out of `_dispatch`'s *return value*,
   and a handler that raises never returns one, so every refusal went out with
   no vault prefix to strip and no id to redact — and an engine traceback names
   the vault it ran in, whose path *is* the session id. The one response nobody
   looks at published the cookie that `HttpOnly` exists to keep out of reach.
   The session is now published to the response path the moment it opens.

8. **`X-Forwarded-For` is not trusted.** The rate limiter keys on the socket
   address. Behind a proxy that means one bucket for the whole proxy — so the
   proxy has to do the limiting too. Trusting the header instead would turn the
   limiter off for anyone who read this file.

## The API

JSON everywhere. Errors are `{"error": {"code", "message", "detail"}}` with a
real status; assert on `code`, which is stable, and never on wording.

```
POST   /api/session                 create (try mode) -> {label, mode, quota, …}
GET    /api/session                 the session, its quota and its GitHub state
DELETE /api/session                 destroy it, and the vault with it

GET    /api/reports                 list --json
POST   /api/reports                 {title, group?, template?, kind?, author?} -> new
GET    /api/reports/:id             the manifest entry, plus the folder's files
GET    /api/reports/:id/file?path=  file text
PUT    /api/reports/:id/file?path=  write file text
POST   /api/reports/:id/build       all <id> -> {ok, code, stdout, stderr, artefacts}
GET    /api/reports/:id/pdf         the PDF bytes
GET    /api/reports/:id/pages       the page index, as URLs
GET    /api/reports/:id/page/:n     one page PNG   ← this is what a phone reads
GET    /api/reports/:id/html        the self-contained bundle

GET    /api/check?target=           check --json --score
GET    /api/score?target=           score --json
GET    /api/sources/:id             sources <id> --json
POST   /api/sources/:id/cite        {url} — SSRF pre-flight first
GET    /api/verify?target=&online=  verify --json --offline unless asked
GET    /api/todos?target=&open=     todos --json
POST   /api/todos/:id               {text} | {line, done}
GET    /api/notes/:id               notes --json
PUT    /api/notes/:id               write notes.md
GET    /api/find?q=&kind=&limit=    find --json
GET    /api/templates               templates --json
POST   /api/templates/install       403, always. See requirement 5
GET    /api/brand?pack=             brand show --json
PUT    /api/brand                   {brand} — write the pack, then stage

GET    /api/git/state               sync --status --json
POST   /api/git/sync                {message, push} -> sync; 409 on a refusal
GET    /api/github/status           whether the button should exist. No session
GET    /api/github/authorize        -> {url}, with a state bound to this session
GET    /api/github/callback         GitHub sending the browser back
GET    /api/github/repos            the repositories this token can reach
POST   /api/github/connect          {repo, branch} — shallow clone into the vault
POST   /api/github/init             init a cloned repo that is not yet a vault

POST   /api/share/:id               publish -> {url, token, report, created}
GET    /s/:token                    PUBLIC. No cookie, no session, no auth
GET    /api/health                  for a container probe
```

`GET /s/:token` is the only public route, and it is the reason to send anyone a
link. The page it serves already carries the evidence: every citation resolves to
the archived page as it was on the date it was cited, with its sha256. Shares are
immutable — re-sharing mints a new token — and the bundle is checked to be
genuinely self-contained before it is published. A single remote image in it
would tell a third party the name of everyone who opened your link, silently.

A build runs synchronously under the 60-second deadline. There is no job queue,
on purpose: a build that overruns answers **504 with the engine's stderr**, which
is a true thing a person can act on, rather than a job id that has to be polled.

## Mobile

Read the **page PNGs**, never an embedded PDF. iOS Safari cannot usefully show a
PDF in an iframe, and `out/pages/<id>/*.png` already exists — the mobile reader
is not a workaround, it is the artefact that was there all along.
`GET /api/reports/:id/pages` returns URLs rather than paths for exactly this.

## Deploying

`web/Dockerfile` builds a `python:3.12-slim` image with the typst binary, a
non-root user and a healthcheck; `docker-compose.yml` runs it in one command,
and `fly.toml` and `render.yaml` are worked examples. Three things matter
wherever it lands:

1. **Set `RM_WEB_ROOT`** to a real volume. The default is a per-run temp dir,
   which is right for a laptop and wrong for anything that restarts.
2. **Terminate TLS in front, and set `RM_WEB_SECURE_COOKIE=1`.** Without it the
   session cookie has no `Secure` flag and travels in the clear.
3. **Leaving `npm` off the PATH is belt to the server's braces**, not the
   guard itself: with `RM_WEB_DIAGRAMS` unset the server already hides Node
   from every engine subprocess. See item 4 above.

Binding to anything but loopback prints a warning naming what the port can do —
create a vault on your disk, run typst on source a stranger wrote, make this
server fetch a URL of their choosing, and publish a page it will serve to
anyone. Quotas and a pre-flight are not the same thing as an authenticated
service. Put something that knows who your users are in front of it before it is
reachable from the internet.

## Verifying a change

```bash
python3 -m unittest discover -s web/tests        # the whole web suite
python3 -m unittest web.tests.test_api           # the API alone
make test                                        # the engine's 711, untouched
```

`test_api.py` drives the real `ThreadingHTTPServer` on an ephemeral port against
a real session and a real engine — nothing is stubbed — and walks the whole loop:
create a session, scaffold a report, write it, build it, check it, read the page
images, share it, and open the share with no cookie at all. Beside it sit the
refusals that have to hold while it does: an unknown session id, an expired one,
a traversal in `?path=`, a traversal in a static path, a symlink out of the
vault, `template install`, seven SSRF targets, both quotas, an oversized body and
a cross-site write.

The build tests skip without `typst`, because a machine is allowed not to have
it and a suite that cannot run teaches nobody anything. Everything else runs
anywhere.
