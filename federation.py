"""HTTP-signature verification, signing and delivery helpers for ActivityPub.

Implements the draft-cavage HTTP signature scheme (rsa-sha256, PKCS#1 v1.5)
that the fediverse uses for server-to-server deliveries.
"""

import base64, hashlib, json, time
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

ACTIVITY_CONTENT_TYPE = "application/activity+json"
ACTIVITY_ACCEPT = 'application/activity+json, application/ld+json; profile="https://www.w3.org/ns/activitystreams"'

# how far a signed Date header may differ from our clock (bounds replay)
DATE_TOLERANCE = 3600
# a signature that doesn't cover these can be replayed against another
# target or with a different body, so deliveries must sign all of them
REQUIRED_SIGNED_HEADERS = {"(request-target)", "date", "digest"}


class SignatureError(Exception):
    def __init__(self, reason: str, actor_gone: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.actor_gone = actor_gone


def parse_signature_header(value: str) -> dict:
    params = {}
    for part in value.split(","):
        key, _, val = part.strip().partition("=")
        params[key] = val.strip('"')
    return params


def fetch_actor(url: str, headers: dict, timeout: int) -> dict:
    response = requests.get(url, headers={**headers, "Accept": ACTIVITY_ACCEPT}, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_public_key(key_id: str, headers: dict, timeout: int):
    """Fetch the actor document a keyId points at; returns (pem, owner id)."""
    url = key_id.split("#")[0]
    try:
        response = requests.get(url, headers={**headers, "Accept": ACTIVITY_ACCEPT}, timeout=timeout)
    except requests.RequestException as error:
        raise SignatureError(f"could not fetch signing key: {error}")

    if response.status_code in (404, 410):
        raise SignatureError("signing actor is gone", actor_gone=True)
    if response.status_code != 200:
        raise SignatureError(f"key fetch returned HTTP {response.status_code}")

    try:
        document = response.json()
    except ValueError:
        raise SignatureError("key document is not valid JSON")

    key = document.get("publicKey", document)
    if isinstance(key, list):
        key = key[0]
    pem = key.get("publicKeyPem") if isinstance(key, dict) else None
    if not pem:
        raise SignatureError("no publicKeyPem in key document")

    owner = key.get("owner") or document.get("id") or url
    return pem, owner


def verify_request(method: str, path: str, headers, body: bytes, fetch_headers: dict, timeout: int) -> str:
    """Verify the HTTP signature on an inbox delivery.

    Returns the id of the actor owning the signing key. Raises SignatureError
    if the request is unsigned, stale, or the signature doesn't match.
    """
    header = headers.get("Signature")
    if not header:
        raise SignatureError("missing Signature header")

    params = parse_signature_header(header)
    for field in ("keyId", "signature"):
        if field not in params:
            raise SignatureError(f"Signature header missing '{field}'")

    signed_headers = params.get("headers", "date").split()
    missing = REQUIRED_SIGNED_HEADERS - set(signed_headers)
    if missing:
        raise SignatureError(f"signature must cover: {' '.join(sorted(missing))}")

    digest = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
    if headers.get("Digest", "") not in (f"SHA-256={digest}", f"sha-256={digest}"):
        raise SignatureError("Digest header does not match request body")

    try:
        sent = parsedate_to_datetime(headers.get("Date", "")).timestamp()
    except (TypeError, ValueError):
        raise SignatureError("missing or malformed Date header")
    if abs(time.time() - sent) > DATE_TOLERANCE:
        raise SignatureError("Date header outside acceptable time window")

    lines = []
    for name in signed_headers:
        if name == "(request-target)":
            lines.append(f"(request-target): {method.lower()} {path}")
        else:
            value = headers.get(name)
            if value is None:
                raise SignatureError(f"signed header '{name}' not present in request")
            lines.append(f"{name}: {value}")
    signing_string = "\n".join(lines).encode("utf8")

    pem, owner = fetch_public_key(params["keyId"], fetch_headers, timeout)
    try:
        public_key = serialization.load_pem_public_key(pem.encode("utf8"))
        public_key.verify(base64.b64decode(params["signature"]), signing_string,
                          padding.PKCS1v15(), hashes.SHA256())
    except (InvalidSignature, ValueError):
        raise SignatureError("signature does not match")

    return owner


def deliver(activity: dict, inbox_url: str, key_id: str, private_key_pem: bytes,
            headers: dict, timeout: int) -> requests.Response:
    """POST a signed activity to a remote inbox."""
    body = json.dumps(activity).encode("utf8")
    digest = base64.b64encode(hashlib.sha256(body).digest()).decode("ascii")
    date = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime())
    parsed = urlparse(inbox_url)

    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    signing_string = (f"(request-target): post {parsed.path or '/'}\n"
                      f"host: {parsed.netloc}\n"
                      f"date: {date}\n"
                      f"digest: SHA-256={digest}")
    signature = base64.b64encode(private_key.sign(signing_string.encode("utf8"),
                                                  padding.PKCS1v15(), hashes.SHA256())).decode("ascii")
    signature_header = (f'keyId="{key_id}",algorithm="rsa-sha256",'
                        f'headers="(request-target) host date digest",signature="{signature}"')

    return requests.post(inbox_url, data=body, timeout=timeout, headers={
        **headers,
        "Date": date,
        "Digest": f"SHA-256={digest}",
        "Signature": signature_header,
        "Content-Type": ACTIVITY_CONTENT_TYPE,
    })
