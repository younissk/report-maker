"""`python3 -m web` — the whole deployment story.

Deliberately three lines. Everything a flag does lives in `web.server.app`,
where it can be called from a test with an argument list instead of a process;
this file exists so that starting the server needs no launcher, no process
manager and no dependency install — which is the entire reason the API is
standard library only.
"""

from web.server.app import main

raise SystemExit(main())
