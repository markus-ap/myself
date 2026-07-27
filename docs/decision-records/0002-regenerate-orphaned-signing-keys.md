# 0002 — Regenerate signing keys that have no private half, on first boot

**Status:** Accepted
**Contributors:** Markus (asked & decided: regenerate rather than hunt for the old key) + Claude (proposed/implemented)
**Topics:** activitypub, http-signatures, keys, deployment

## Context

`actors/markus_actor.jsonld` has been committed with a `publicKey.publicKeyPem`
since the App Engine days. The matching private key was never committed —
`*.pem` is gitignored — and no copy survives. So the repo describes an actor that
advertises a key it cannot sign with.

The effect is quiet rather than loud. `deliver_to_actor()` checks for the key
file and, finding none, logs and returns; nothing raises. A remote Follow would
be accepted into the followers list and the `Accept` would simply never be
delivered, leaving the follow pending forever on the other side.

## Decision

`storage._ensure_signing_keys()` runs as part of `bootstrap()` on every boot. For
each account in `users.json` with no `<user>_private.pem` in the data root it
generates a fresh 2048-bit RSA pair, writes the private half, and rewrites
`publicKey` (`id`, `owner`, `publicKeyPem`) in that account's actor document.

The alternative — treating the published key as authoritative and blocking launch
until the original `.pem` turned up — was rejected: the key is gone, and nothing
of value is attached to it.

## Consequences

- Any remote server that cached the old `publicKeyPem` will fail to verify the
  first signature it sees from us, refetch the actor, and pick up the new key.
  That is the normal key-rotation path in the fediverse, and at this stage the
  cached audience is one instance (`skvip.lol`).
- Because generation is keyed on *absence*, an accidental deploy against an empty
  `/var/lib/myself` would rotate every key on the instance. This is the sharp
  edge of ADR 0001's data directory, and the reason it is the backup unit.
- Accounts created through Mastodon OAuth are unaffected — `provision_account()`
  has always written a real keypair at creation time.
- `seed/actors/markus_actor.jsonld` still carries the orphaned public key. It is
  never served: the copy under `MYSELF_DATA` is what the app reads, and it is
  rewritten on first boot.
