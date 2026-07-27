# myself.social

An ActivityPub **passport**: one handle that vouches for all your others. An account
here holds the links to your accounts elsewhere, verifies them (`rel="me"`
back-links, respecting the target's `robots.txt`), and publishes the result as a
real fediverse actor — webfinger, actor document, inbox, outbox, followers.

Flask + gunicorn. `app.py` is the site and the ActivityPub surface, `accounts.py`
account provisioning and the Mastodon OAuth flow, `federation.py` HTTP-signature
verification/signing/delivery, `storage.py` where state lives, `update_user.py` an
offline maintenance script.

<!-- Deployment & box context — KEEP this block. It is what a single-repo agent
     needs to work on this service safely without seeing the rest of the box. -->

## Deployment (naustet box)

| Key | Value |
|-----|-------|
| Slug / dir | `myself` → `/srv/myself` |
| Domain | `myself.social`, `www.myself.social` (apex, not a `msge.no` subdomain) |
| Host port | 4024 (bound `172.18.0.1:4024`) |
| Runtime | Docker Compose |
| Deploy | `cd /srv/myself && make deploy` |
| State | `/var/lib/myself` → `/data` — **back this up** |

**Central ingress — do NOT manage TLS/routing here.** Caddy (TLS + reverse proxy
for every domain) is central in **`github.com/mmsge/naustet-server`**. Do not add a
Caddy service to this repo. To change routing or the domain, edit that repo.

**Conventions this repo must follow:**
- Lives at `/srv/myself`; the Compose project name is pinned (`name: myself`) so a
  directory rename can never orphan named volumes.
- Publish the port on `172.18.0.1:4024` or `0.0.0.0:4024` — never `127.0.0.1`
  (central Caddy dials it at `172.18.0.1:4024`).
- Set `mem_limit` (the box is 3.7 GB / 2 vCPU) and don't run heavy builds on it.
- Expose `/healthz`.
- Serve `robots.txt` + `sitemap.xml` at the root (absolute URLs, correct
  content-types, COPY'd into the image), plus as much structured/interop metadata
  as fits. See `naustet-server/NEW-SERVICE.md` → "Web standards & discoverability".
- Every HTML page carries **git-derived creation/modification metadata** (the
  `meta name="date"`/`last-modified` pair, `article:published_time`/
  `article:modified_time`, JSON-LD `dateCreated`/`datePublished`/`dateModified`;
  `<lastmod>` in the sitemap). `scripts/generate-page-dates.sh` derives it on the
  checkout at `make deploy` (the image has no `.git`) into the gitignored
  `page-dates.json`; `load_page_dates()` falls back to boot time when it's absent.
  Keep the box checkout a **full clone** — a shallow one collapses both dates.
  See naustet-server ADR 0015.

**Things specific to this service that will bite you:**

- **State is not in the repo.** Accounts, actor documents, outboxes and the actors'
  **private signing keys** live under `MYSELF_DATA` (`/var/lib/myself` on the box),
  never in the checkout. `seed/` is read-only first-boot content, not live data.
  Wiping the data directory rotates every signing key. See ADR 0001 and 0002.
- **`ProxyFix` is load-bearing.** Caddy terminates TLS and proxies plain HTTP, and
  `request.base_url` ends up as the `id` of federated documents. Without the
  middleware those ids are `http://` — wrong identity, not just an ugly URL.
- **The basic-auth gate is path-scoped** to `/signup` + `/auth/*`. Gating the whole
  domain would take webfinger, the actor documents and the inboxes with it and
  break federation. Same reason `robots.txt`/`sitemap.xml` stay public. See ADR 0003.
- **Never put `encode gzip` on a streaming endpoint** if one is ever added here —
  box-wide rule, see naustet-server ADR 0001. Nothing streams today.

**Live data & full picture:** the box exposes an MCP at **`https://mcp.msge.no/mcp`**
(OAuth). Call `get_service("myself")`, `list_services`, `port_map`, `conventions`
for authoritative, live answers. The static reference lives in `mmsge/naustet-server`
(`Caddyfile` = the port map; `services/myself-social.md` = this service's doc).

## Decision records

Non-obvious knowledge — an incident whose root cause is hard to re-derive, or a
deliberate choice between real alternatives — lives in `docs/decision-records/` as
append-only ADRs (`NNNN-short-slug.md`; update the README index). This is the same
practice used across every service on the box. **Create one when:**

- **An incident** occurs whose cause is a footgun someone could reintroduce —
  record the symptom, the trap, and the fix.
- **A specific decision** is made — a convention or config trade-off, why X instead
  of the obvious Y.

Skip routine changes with no trap and no alternative worth remembering. Records are
immutable once accepted; to change one, add a new record and mark the old
`Superseded by NNNN`. The **Contributors** field must make clear whether Markus was
*asked and answered* (name him only then) or an **agent decided on its own** (name
the agent). Add an optional `**Topics:**` line (comma-separated tags) so the record
surfaces in the pooled cross-service view at `adr.msge.no`. Decisions about central
ingress/routing/the box go in `naustet-server`; decisions about this service go
here. See `docs/decision-records/README.md`.
