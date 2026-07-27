# myself.social

A passport for ActivityPub based services — one handle that vouches for all the
others. An account holds the links to your accounts elsewhere on the web, verifies
them by looking for a `rel="me"` back-link (respecting the target's `robots.txt`),
and publishes the result as a real fediverse actor: webfinger, actor document,
inbox, outbox and followers.

Live at **https://myself.social**.

## Running it

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # SECRET_KEY, BASE_URL
MYSELF_DATA=./data .venv/bin/flask --app app run --port 8080
```

Or the way the box runs it:

```sh
docker compose up -d --build    # http://172.18.0.1:4024
```

## Layout

| Path | What |
|------|------|
| `app.py` | The site and the ActivityPub surface (routes) |
| `accounts.py` | Account provisioning + the Mastodon OAuth sign-up flow |
| `federation.py` | HTTP-signature verification, signing and delivery |
| `storage.py` | Where state lives, and first-boot seeding |
| `update_user.py` | Offline maintenance script (broadcast profile updates) |
| `seed/` | Read-only first-boot content for an empty data directory |

**State is not in the repo.** Accounts, actor documents, outboxes and the actors'
private signing keys live under `MYSELF_DATA` — `/var/lib/myself` on the box,
`./data` locally. That directory is the backup unit; losing it rotates every
signing key. See `docs/decision-records/0001` and `0002`.

Registrations are closed at launch: sign-up is Mastodon-only and sits behind
basic-auth at the proxy (`docs/decision-records/0003`).

Deployment lives in `CLAUDE.md`; the box's central Caddy config is in
[`mmsge/naustet-server`](https://github.com/mmsge/naustet-server).
