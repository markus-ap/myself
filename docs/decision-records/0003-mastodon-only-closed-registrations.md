# 0003 — Sign-up is Mastodon OAuth only, and closed at launch

**Status:** Accepted
**Contributors:** Markus (asked & decided: Mastodon-only, registrations closed) + Claude (proposed/implemented)
**Topics:** auth, registration, activitypub

## Context

Two sign-up paths existed side by side. `POST /signup` took a username and
password, bcrypt-hashed it, and appended an entry to `users.json` — and stopped
there. It never called `provision_account()`, so no keypair, actor document,
collections or outbox were written. The account existed just enough to be found
by `get_users()` and then 500 on `/@<user>` when the missing actor file was
opened. The Mastodon OAuth path (`/auth/mastodon`) is the one that produces a
real, federating actor.

Meanwhile `nodeinfo/2.0` advertised `openRegistrations: false` while `/signup`
was reachable by anyone.

## Decision

- The password path is removed: the `POST` branch of `/signup`, `register_user`,
  `validate_login`, `generate_random_string`, and the commented-out Flask-Login
  scaffolding. `flask_login` and `bcrypt` are dropped from `requirements.txt`.
  `/signup` is `GET`-only and renders the Mastodon form.
- Registrations stay **closed** for launch. Central Caddy applies basic-auth to
  `/signup` and `/auth/*` only — a path matcher, not a site-wide gate.
  `openRegistrations: false` is now truthful.
- The dead Datastore stubs and their routes (`/db/<actor>`, `/db/add/<actor>`)
  went with the same pass; they called an unimplemented client and 500'd.

## Consequences

- Proving control of an existing fediverse account is the only way in, which is
  the right shape for a passport: the first verified link comes free, from the
  OAuth response, without a crawl.
- The auth gate must stay path-scoped. Gating the whole domain would take
  `/.well-known/webfinger`, `/b/<actor>` and the inboxes with it and break
  federation outright — the exact trap `robots.txt` and `sitemap.xml` also have to
  stay outside of.
- Opening registrations later is a Caddy change (drop the `@private` handle) plus
  flipping `openRegistrations`; no application change.
- Existing seeded accounts carry no password field, so nothing was invalidated by
  removing the check.
