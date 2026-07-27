"""Where myself.social keeps the things it must not lose.

Everything mutable — accounts, actor documents, outboxes, OAuth client
registrations, the session key and above all the actors' private signing keys —
lives under a single data root outside the source tree. In a container the app
directory is rebuilt on every deploy, and an ActivityPub actor whose key is gone
cannot sign anything remote servers will accept, so losing this directory is not
recoverable by redeploying. See docs/decision-records/0001.

Set MYSELF_DATA to point it somewhere (the container mounts /data); it defaults
to ./data next to the source for local development.
"""

import json
import os
import pathlib
import shutil

HERE = pathlib.Path(__file__).parent
DATA_DIR = pathlib.Path(os.environ.get("MYSELF_DATA", HERE / "data"))
SEED_DIR = HERE / "seed"

ACTORS_DIR = DATA_DIR / "actors"
MESSAGES_DIR = ACTORS_DIR / "messages"
USERS_FILE = DATA_DIR / "users.json"
CLIENTS_FILE = DATA_DIR / "oauth_clients.json"
SECRET_KEY_FILE = DATA_DIR / ".secret_key"
FOLLOWERS_INFO_FILE = DATA_DIR / "followers_info.json"


def user_file(username: str) -> pathlib.Path:
    return ACTORS_DIR / f"{username}.jsonld"


def actor_file(username: str, collection: str = None) -> pathlib.Path:
    suffix = f"_actor_{collection}" if collection else "_actor"
    return ACTORS_DIR / f"{username}{suffix}.jsonld"


def messages_file(username: str) -> pathlib.Path:
    return MESSAGES_DIR / f"{username}.jsonld"


def private_key_file(username: str) -> pathlib.Path:
    return ACTORS_DIR / f"{username}_private.pem"


def read_json(path: pathlib.Path):
    return json.loads(path.read_text(encoding="utf8"))


def write_json(path: pathlib.Path, document, indent: int = 4):
    path.write_text(json.dumps(document, indent=indent, ensure_ascii=False), encoding="utf8")


def bootstrap():
    """Make an empty data root usable, without ever overwriting a populated one.

    Idempotent: safe to run on every boot, which is exactly when it runs.
    """
    MESSAGES_DIR.mkdir(parents=True, exist_ok=True)

    if not USERS_FILE.exists() and SEED_DIR.exists():
        shutil.copyfile(SEED_DIR / "users.json", USERS_FILE)
        for source in (SEED_DIR / "actors").rglob("*.jsonld"):
            target = ACTORS_DIR / source.relative_to(SEED_DIR / "actors")
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)

    _ensure_signing_keys()


def _ensure_signing_keys():
    """Give every account a signing key it actually holds.

    The seeded actor document ships a publicKeyPem whose private half was never
    committed (*.pem is gitignored), so without this the server advertises a key
    it cannot sign with and every delivery is silently dropped. Generating a new
    pair and republishing it is safe: remote servers refetch the actor when a
    signature fails to verify against their cached key. See ADR 0002.
    """
    # imported here so storage stays importable without the crypto dependency
    from accounts import generate_keypair

    if not USERS_FILE.exists():
        return

    for username in read_json(USERS_FILE):
        key_path = private_key_file(username)
        if key_path.exists():
            continue

        private_pem, public_pem = generate_keypair()
        key_path.write_bytes(private_pem)
        print(f"Generated a new signing key for '{username}'")

        actor_path = actor_file(username)
        if not actor_path.exists():
            continue
        actor = read_json(actor_path)
        actor_id = actor.get("id") or actor.get("@id")
        actor["publicKey"] = {
            "id": f"{actor_id}#publicKey",
            "owner": actor_id,
            "publicKeyPem": public_pem.decode("utf8"),
        }
        write_json(actor_path, actor)
