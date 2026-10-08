# Contributing to hs-bloom

Thanks for taking a look. This is a small, focused project: a self-hosted
markets terminal that runs on free data. The bar for a change is that it keeps
that promise — free, keyless where possible, and polite to upstreams.

## Ground rules

1. **No paid or key-gated sources, and no brokerage accounts.** FRED's free key
   is the only credential a user should ever need, and the aim is to drop even
   that if a keyless equivalent appears. An optional integration that most
   users cannot enable is not a feature — it is a tax on everyone who reads the
   code. An optional Interactive Brokers path was built and then removed on
   exactly these grounds: it delivered data flagged just as delayed as the free
   source, while threading a parameter through every fetcher signature.
2. **Be polite to upstreams**, and treat these three as hard rules — a PR that
   breaks one will not be merged:
   - **Never impersonate a browser.** Use `http.USER_AGENT`. If an endpoint
     only answers a fake Chrome UA, the answer is no.
   - **Never defeat a bot check.** CAPTCHAs and proof-of-work challenges are a
     refusal; drop the source rather than working around it. Stooq was removed
     for this.
   - **Never fetch a path `robots.txt` disallows.** Check it first. This is why
     there is no UK gilt row.

   Beyond that: sensible cadence, no retry storms, no parallel hammering. Read
   "Data sources and polite use" in the README before adding a fetcher.
3. **Tests never touch the network.** Every fetcher takes an injected
   `get_text` / `get_bytes`. Tests feed recorded fixtures. `make test` must
   pass offline.

## Setup

```bash
cd collector
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
cd .. && make test
```

## Adding a data source

A new source is usually four things:

1. **A fetcher module** in `collector/src/collector/fetchers/`. Keep it pure:
   take the HTTP callable as an argument, return parsed data, let the caller
   write to the store. Document the endpoint's quirks in the module docstring
   — that is where the hard-won knowledge lives (see `boe.py`, `cboe.py`).
2. **A recorded fixture** in `collector/tests/fixtures/`. Trim it to something
   representative; don't commit megabytes. Redact anything account-specific.
3. **A test** in `collector/tests/`, covering the happy path plus at least one
   malformed or empty response.
4. **Config wiring** in `config.yaml` — a source key on a `series` /
   `cycle_series` entry, and a cadence if it is a new job.

Prefer adding a series to `config.yaml` over writing code. Most additions to
the cycle tabs need no Python at all.

## Adding a series to an existing source

Add one line to `cycle_series` with exactly one source key, then reference its
`id` from a `cycle_tabs` row. Use `transform` (`yoy`, `diff`, `pct_prev`) for
derived values and `valid_range` to guard against corrupt feed values.

## Conventions

- Python 3.12, standard library first, no formatter enforced — match the
  surrounding style.
- Comments explain *why*, especially when the reason is an upstream quirk. A
  comment recording "this endpoint 403s without a browser UA" or "the weekly
  series never aligns for a YoY transform" is worth more than restating code.
- A failure in one instrument must never kill a whole run. Catch per-item, log,
  collect errors, and raise only if everything failed.
- Preserve "stale beats gone": on failure, keep the last-known value with its
  old timestamp rather than blanking the panel.

## Before opening a PR

```bash
make test            # required, offline
make smoke           # optional, needs a running stack and hits real upstreams
```

Mention in the PR whether you ran `make smoke`, since the unit suite cannot
catch an upstream that changed shape.

## Security

Never commit `.env`, `secrets/`, PEM files, or a populated `bloom.db` — all are
gitignored, please keep it that way. If you find a credential in the repo or
its history, open an issue without including the value.
