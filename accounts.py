"""Account provisioning and Mastodon OAuth sign-up helpers.

Sign-up with Mastodon uses the instance's dynamic app registration
(POST /api/v1/apps), the standard authorization-code flow, and
verify_credentials to prove the person controls the Mastodon account.
"""

import json, os, re, time
from urllib.parse import urlencode

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

APP_NAME = "myself.social"
APP_WEBSITE = "https://myself.social"
# read:accounts is enough to call verify_credentials; never ask for more
OAUTH_SCOPES = "read:accounts"
OAUTH_TIMEOUT = 10
CLIENTS_FILE = "oauth_clients.json"

_hostname_re = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")
_username_re = re.compile(r"^[a-zA-Z0-9_]{1,64}$")


def normalize_instance(value: str):
    """Accept 'example.social', 'user@example.social' or '@user@example.social'
    and return the bare host, or None if it doesn't look like one."""
    value = (value or "").strip().lower().lstrip("@")
    if "@" in value:
        value = value.rsplit("@", 1)[1]
    value = value.removeprefix("https://").removeprefix("http://").split("/")[0]

    host, _, port = value.partition(":")
    if port and not port.isdigit():
        return None
    if host in ("localhost", "127.0.0.1") or _hostname_re.match(host):
        return value
    return None


def instance_url(host: str) -> str:
    # plain http only for local development instances
    if host.startswith(("localhost", "127.0.0.1")):
        return f"http://{host}"
    return f"https://{host}"


def valid_username(username: str) -> bool:
    return bool(_username_re.match(username or ""))


def get_oauth_client(host: str, redirect_uri: str, headers: dict) -> dict:
    """Return cached OAuth client credentials for an instance, registering a
    new app on it the first time (or when the redirect URI changed)."""
    clients = json.loads(open(CLIENTS_FILE).read()) if os.path.exists(CLIENTS_FILE) else {}
    client = clients.get(host)
    if client and client.get("redirect_uri") == redirect_uri:
        return client

    response = requests.post(f"{instance_url(host)}/api/v1/apps", data={
        "client_name": APP_NAME,
        "redirect_uris": redirect_uri,
        "scopes": OAUTH_SCOPES,
        "website": APP_WEBSITE,
    }, headers=headers, timeout=OAUTH_TIMEOUT)
    response.raise_for_status()
    data = response.json()

    client = {"client_id": data["client_id"], "client_secret": data["client_secret"],
              "redirect_uri": redirect_uri}
    clients[host] = client
    open(CLIENTS_FILE, "w").write(json.dumps(clients, indent=4))
    return client


def authorize_url(host: str, client: dict, state: str) -> str:
    query = urlencode({
        "client_id": client["client_id"],
        "redirect_uri": client["redirect_uri"],
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state,
    })
    return f"{instance_url(host)}/oauth/authorize?{query}"


def exchange_code(host: str, client: dict, code: str, headers: dict) -> str:
    response = requests.post(f"{instance_url(host)}/oauth/token", data={
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "redirect_uri": client["redirect_uri"],
        "grant_type": "authorization_code",
        "code": code,
        "scope": OAUTH_SCOPES,
    }, headers=headers, timeout=OAUTH_TIMEOUT)
    response.raise_for_status()
    return response.json()["access_token"]


def fetch_credentials(host: str, token: str, headers: dict) -> dict:
    response = requests.get(f"{instance_url(host)}/api/v1/accounts/verify_credentials",
                            headers={**headers, "Authorization": f"Bearer {token}"},
                            timeout=OAUTH_TIMEOUT)
    response.raise_for_status()
    return response.json()


def revoke_token(host: str, client: dict, token: str, headers: dict):
    """Best-effort revocation — we only needed the token for one call and
    should not leave it usable."""
    try:
        requests.post(f"{instance_url(host)}/oauth/revoke", data={
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "token": token,
        }, headers=headers, timeout=OAUTH_TIMEOUT)
    except requests.RequestException:
        pass


def generate_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
    public_pem = key.public_key().public_bytes(serialization.Encoding.PEM,
                                               serialization.PublicFormat.SubjectPublicKeyInfo)
    return private_pem, public_pem


def _write(path: str, document: dict):
    open(path, "w", encoding="utf8").write(json.dumps(document, indent=4, ensure_ascii=False))


def _link_attachment(link: str, verified_at):
    domain = link.split("//")[-1].split("/")[0]
    return {
        "type": "PropertyValue",
        "name": domain,
        "value": f"<a href=\"{link}\" target=\"_blank\" rel=\"nofollow noopener noreferrer me\">{link}</a>",
        "verified_at": verified_at,
    }


def provision_account(username: str, instance_base: str, links=None, verified_links=None,
                      display_name=None, summary=""):
    """Create everything a user needs on disk: signing keypair, user file,
    ActivityPub actor document, its collections, and an empty outbox store.

    verified_links maps link URL -> ISO timestamp for links whose ownership
    was proven (e.g. via Mastodon OAuth login) rather than crawled.
    """
    links = links or []
    verified_links = verified_links or {}
    actor_id = f"{instance_base}/b/{username}"
    published = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    private_pem, public_pem = generate_keypair()
    with open(f"./actors/{username}_private.pem", "wb") as key_file:
        key_file.write(private_pem)

    _write(f"./actors/{username}.jsonld", {
        "@id": actor_id,
        "profile": actor_id,
        "outbox": f"{actor_id}/outbox",
        "links": links,
        "verified_links": verified_links,
        "followers": [],
    })

    _write(f"./actors/{username}_actor.jsonld", {
        "@context": ["https://www.w3.org/ns/activitystreams", "https://w3id.org/security/v1"],
        "@id": actor_id,
        "id": actor_id,
        "type": "Person",
        "preferredUsername": username,
        "name": display_name or username,
        "summary": summary,
        "url": f"{instance_base}/@{username}",
        "published": published,
        "inbox": f"{actor_id}/inbox",
        "outbox": f"{actor_id}/outbox",
        "followers": f"{actor_id}/followers",
        "following": f"{actor_id}/following",
        "featured": f"{actor_id}/collections/featured",
        "manuallyApprovesFollowers": False,
        "discoverable": True,
        "attachment": [_link_attachment(link, verified_links.get(link)) for link in links],
        "publicKey": {
            "id": f"{actor_id}#publicKey",
            "owner": actor_id,
            "publicKeyPem": public_pem.decode("utf8"),
        },
    })

    for kind in ("followers", "following"):
        _write(f"./actors/{username}_actor_{kind}.jsonld", {
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": f"{actor_id}/{kind}",
            "type": "OrderedCollection",
            "totalItems": 0,
            "first": f"{actor_id}/{kind}?page=1",
        })

    _write(f"./actors/{username}_actor_featured.jsonld", {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{actor_id}/collections/featured",
        "type": "OrderedCollection",
        "totalItems": 0,
        "orderedItems": [],
    })

    _write(f"./actors/messages/{username}.jsonld", {})
