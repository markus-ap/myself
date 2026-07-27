# 0001 — All mutable state lives under `MYSELF_DATA`, never in the source tree

**Status:** Accepted
**Contributors:** Claude (agent decision — no human input on the technical choice)
**Topics:** deployment, activitypub, state, keys

## Context

The app was written to run from a checkout on Google App Engine, so every mutable
file was written straight into the working directory next to the code:
`users.json`, `oauth_clients.json`, `.secret_key`, and the whole of `actors/` —
user files, actor documents, followers/following/featured collections, outbox
notes, and each actor's **private signing key** (`<user>_private.pem`).

Moving to the naustet box means running from an image that is rebuilt on every
`make deploy`. Under that model the layout above quietly destroys the instance on
each deploy. Losing accounts would be bad enough, but losing the private keys is
not recoverable by redeploying: remote servers hold a cached `publicKeyPem` for
each actor, and every future delivery gets signed with a key that no longer
matches. Deployment would silently break federation.

## Decision

One data root, resolved once in `storage.py`:

```python
DATA_DIR = pathlib.Path(os.environ.get("MYSELF_DATA", HERE / "data"))
```

Everything mutable hangs off it via helpers (`user_file`, `actor_file`,
`messages_file`, `private_key_file`, `USERS_FILE`, `CLIENTS_FILE`,
`SECRET_KEY_FILE`) — no module opens a relative path any more. The container
mounts the host directory `/var/lib/myself` at `/data`; local development falls
back to `./data`, which is gitignored.

The content that used to be committed as live state now lives in `seed/` and is
strictly read-only: `storage.bootstrap()` copies it into an empty data root on
first boot and never overwrites a populated one. It runs on every boot and is
idempotent.

## Consequences

- `/var/lib/myself` is the backup unit for this service, and must be carried
  across any box move (recorded in `naustet-server`'s `MIGRATION.md` and
  `services/myself-social.md`). A rebuilt container is disposable; that directory
  is not.
- The bind mount is a host directory rather than a named Docker volume,
  specifically so it can be `tar`'d and `scp`'d without `docker run --volumes-from`
  gymnastics. It matches how `karusell-ui` handles `/var/lib/karusell`.
- Editing `seed/` after launch changes nothing on a running instance — that is
  the point, but it does mean seed fixes need a manual edit under
  `/var/lib/myself` as well.
- `update_user.py` (the offline maintenance script) reads the same root, so it
  must be run with `MYSELF_DATA` set to reach the real keys.
