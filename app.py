from flask import Flask, Response, request, jsonify, render_template, redirect, url_for, session
from flask_cors import CORS
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlunparse
from urllib import robotparser
from werkzeug.middleware.proxy_fix import ProxyFix
import json, requests, uuid, os, time, threading, secrets

import accounts
import federation
import storage

dev_ip = "192.168.68.107:5000"
domain = "myself.social"
instance = f"https://{domain}"
user_agent = f"myself.social/0.1 (+{instance}/)"
myself_headers = {"User-Agent": user_agent}
timeout = 3

# Where OAuth callbacks land; must be the public URL of this deployment.
base_url = os.environ.get("BASE_URL", instance)

storage.bootstrap()

def load_secret_key():
    """A per-process random key breaks session cookies (OAuth state) across
    gunicorn workers, so persist one to disk unless SECRET_KEY is set."""
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    try:
        with open(storage.SECRET_KEY_FILE, "x") as key_file:
            key_file.write(os.urandom(32).hex())
    except FileExistsError:
        pass
    return storage.SECRET_KEY_FILE.read_text().strip()

app = Flask(__name__, static_folder="static")
app.secret_key = load_secret_key()
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400

# Central Caddy terminates TLS and proxies over plain HTTP, so without this the
# request URLs Flask builds come out as http:// — and those URLs end up as the
# `id` of federated documents (outbox pages, note lookups), where the scheme is
# part of the identity. Exactly one proxy sits in front of us.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

# users.json is re-read only when its mtime changes, instead of on every request
_users_cache = {"mtime": None, "data": None}

def get_users():
    mtime = os.path.getmtime(storage.USERS_FILE)
    if _users_cache["mtime"] != mtime:
        _users_cache["data"] = storage.read_json(storage.USERS_FILE)
        _users_cache["mtime"] = mtime
    return _users_cache["data"]

def write_users(users: dict):
    storage.write_json(storage.USERS_FILE, users)

CORS(app)

def ap_jsonify(data: dict, status: int = 200):
    """ActivityPub documents must be served as activity+json, not plain json."""
    return Response(json.dumps(data), status=status, content_type=federation.ACTIVITY_CONTENT_TYPE)

@app.route("/healthz")
def healthz():
    return Response("ok", content_type="text/plain; charset=utf-8")

@app.route("/signup")
def signup():
    """Sign-up is Mastodon-only: proving control of an existing fediverse
    account is the whole point of a passport, and it is the only sign-up path
    that produces a real actor. Caddy basic-auth gates this route and /auth/*
    while registrations are closed."""
    return render_template('signup.html')

@app.route("/auth/mastodon", methods=["POST"])
def mastodon_auth_start():
    host = accounts.normalize_instance(request.form.get("instance", ""))
    if host is None:
        return render_template("signup.html", error="That doesn't look like a Mastodon handle or server."), 400

    redirect_uri = f"{base_url}/auth/mastodon/callback"
    try:
        client = accounts.get_oauth_client(host, redirect_uri, myself_headers)
    except (requests.RequestException, KeyError, ValueError) as error:
        print(f"Failed to register OAuth app on {host}: {error}")
        return render_template("signup.html", error=f"Could not talk to {host}. Is it a Mastodon server?"), 502

    state = secrets.token_urlsafe(32)
    session["oauth_state"] = state
    session["oauth_instance"] = host
    return redirect(accounts.authorize_url(host, client, state))

@app.route("/auth/mastodon/callback")
def mastodon_auth_callback():
    state = session.pop("oauth_state", None)
    host = session.pop("oauth_instance", None)
    if not state or not host or request.args.get("state") != state:
        return render_template("signup.html", error="The login attempt expired or was tampered with. Please try again."), 400
    if "code" not in request.args:
        # the user denied the authorization request on their home server
        return redirect("/signup")

    redirect_uri = f"{base_url}/auth/mastodon/callback"
    try:
        client = accounts.get_oauth_client(host, redirect_uri, myself_headers)
        token = accounts.exchange_code(host, client, request.args["code"], myself_headers)
        credentials = accounts.fetch_credentials(host, token, myself_headers)
        accounts.revoke_token(host, client, token, myself_headers)
    except (requests.RequestException, KeyError, ValueError) as error:
        print(f"Mastodon OAuth against {host} failed: {error}")
        return render_template("signup.html", error=f"Signing in with {host} failed. Please try again."), 502

    return create_mastodon_account(host, credentials)

def create_mastodon_account(host: str, credentials: dict):
    """Create (or sign back into) the account belonging to an OAuth-verified
    Mastodon identity. The Mastodon profile link starts out verified, since
    the person just proved control of that account by logging into it."""
    mastodon_username = credentials.get("username", "")
    if not accounts.valid_username(mastodon_username):
        return render_template("signup.html", error="Your Mastodon username can't be used here."), 400

    mastodon_url = credentials.get("url") or f"{accounts.instance_url(host)}/@{mastodon_username}"
    acct = f"{mastodon_username}@{host}"

    users = get_users()
    for name, entry in users.items():
        if entry.get("mastodon", {}).get("acct") == acct:
            session["user"] = name
            return redirect(f"/@{name}")

    username = mastodon_username
    if username in users:
        username = f"{mastodon_username}_{host.replace('.', '_').replace(':', '_')}"
        if username in users:
            return render_template("signup.html", error="A username for this account is already taken."), 409

    verified_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    accounts.provision_account(username, instance,
                               links=[mastodon_url],
                               verified_links={mastodon_url: verified_at},
                               display_name=credentials.get("display_name") or username)

    users[username] = {"mastodon": {"acct": acct, "url": mastodon_url,
                                    "instance": host, "verified_at": verified_at}}
    write_users(users)

    session["user"] = username
    return redirect(f"/@{username}")



def load_page_dates():
    """Site created/modified from git history, written onto the checkout by
    scripts/generate-page-dates.sh at deploy time — the image has no .git. Falls
    back to boot time when the file is absent (e.g. a bare `docker build`).
    Box-wide convention: naustet-server ADR 0015."""
    try:
        dates = json.loads((storage.HERE / "page-dates.json").read_text())
        return {"created": dates["created"], "modified": dates["modified"]}
    except (OSError, ValueError, KeyError):
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return {"created": now, "modified": now}

PAGE_DATES = load_page_dates()

@app.context_processor
def inject_page_dates():
    return {"page_dates": PAGE_DATES, "instance": instance}

@app.route("/")
def main():
    return render_template("index.html")

@app.route("/robots.txt")
def robots_txt():
    return app.send_static_file("robots.txt")

@app.route("/sitemap.xml")
def sitemap_xml():
    """Generated, not static: the public page set is one profile per account, so
    a checked-in sitemap would go stale the moment somebody signs up."""
    urls = [(f"{instance}/", "daily")]
    urls += [(f"{instance}/@{username}", "weekly") for username in sorted(get_users())]

    entries = "".join(
        f"\n  <url>\n    <loc>{loc}</loc>\n"
        f"    <lastmod>{PAGE_DATES['modified']}</lastmod>\n"
        f"    <changefreq>{freq}</changefreq>\n  </url>"
        for loc, freq in urls
    )
    document = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                f"{entries}\n</urlset>\n")
    return Response(document, content_type="application/xml; charset=utf-8")

# Cached robots.txt rules for the remote sites we fetch during verification,
# so repeated verifications don't re-download robots.txt on every request.
_robots_cache = {}
ROBOTS_CACHE_TTL = 3600

def robots_allowed(url: str, session: requests.Session) -> bool:
    """Check the target site's robots.txt before fetching a page (RFC 9309:
    4xx or missing robots.txt means allowed, 5xx means disallowed)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    base = f"{parsed.scheme}://{parsed.netloc}"

    cached = _robots_cache.get(base)
    if cached is None or time.time() - cached[1] > ROBOTS_CACHE_TTL:
        parser = robotparser.RobotFileParser()
        try:
            response = session.get(f"{base}/robots.txt", headers=myself_headers, timeout=timeout)
            if response.status_code == 200:
                parser.parse(response.text.splitlines())
            elif response.status_code >= 500:
                parser.disallow_all = True
            else:
                parser.allow_all = True
        except requests.RequestException:
            parser.allow_all = True
        _robots_cache[base] = (parser, time.time())
        cached = _robots_cache[base]

    return cached[0].can_fetch(user_agent, url)

def as_create_activity(note: dict):
    """Outbox items are activities, not bare objects (ActivityPub §5.1)."""
    return {
        "id": f"{note['id']}/activity",
        "type": "Create",
        "actor": note.get("attributedTo"),
        "published": note.get("published"),
        "to": note.get("to"),
        "object": note
    }

@app.route("/b/<actor>/outbox", methods=["GET"])
def outbox(actor: str):
    users = get_users()
    if actor not in users:
        return jsonify({"Error": "User not found."}), 404

    notes = storage.read_json(storage.messages_file(actor))
    page_size = 5
    pages = max(1, -(-len(notes) // page_size))
    base = request.base_url

    page_param = request.args.get("page")
    if not page_param:
        return ap_jsonify({
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": base,
            "type": "OrderedCollection",
            "totalItems": len(notes),
            "first": f"{base}?page=1",
            "last": f"{base}?page={pages}"
        })

    try:
        page = int(page_param)
    except ValueError:
        return jsonify({"Error": "page must be a number"}), 400
    if page < 1 or page > pages:
        return jsonify({"Error": "Page not found."}), 404

    ordered = list(notes.values())
    ordered.reverse()  # newest first, matching other fediverse outboxes
    start = (page - 1) * page_size

    document = {
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{base}?page={page}",
        "type": "OrderedCollectionPage",
        "partOf": base,
        "orderedItems": [as_create_activity(note) for note in ordered[start:start + page_size]]
    }
    if page < pages: document["next"] = f"{base}?page={page + 1}"
    if page > 1: document["prev"] = f"{base}?page={page - 1}"
    return ap_jsonify(document)

@app.route("/b/<actor>/inbox", methods=["POST"])
def inbox(actor: str):
    print(f"Inbox call for {actor}")
    users = get_users()
    if actor not in users:
        return jsonify({"Error": "User not found."}), 404

    body = request.get_data()
    try:
        activity = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return jsonify({"Error": "Body is not valid JSON"}), 400
    if "type" not in activity or "actor" not in activity:
        return jsonify({"Error": "Unsupported request"}), 400

    try:
        key_owner = federation.verify_request(request.method, request.path, request.headers,
                                              body, myself_headers, timeout)
    except federation.SignatureError as error:
        # A Delete for an already-deleted actor can't be verified any more
        # (the key is gone with the account); acknowledge it so remote
        # servers stop retrying, but act on nothing else.
        if activity["type"] == "Delete" and error.actor_gone:
            return "", 202
        print(f"Rejected inbox delivery for '{actor}': {error.reason}")
        return jsonify({"Error": f"Signature verification failed: {error.reason}"}), 401

    if activity["actor"] != key_owner:
        return jsonify({"Error": "Activity actor does not match the signing key's owner"}), 401

    response, status = resolve_inbox_type(actor, activity)
    return jsonify(response), status

def resolve_inbox_type(username: str, activity: dict):
    user = get_user(username)
    local_actor = get_actor(username)
    actor_id = local_actor.get("id", f"{instance}/b/{username}")

    match activity["type"]:
        case "Follow":
            if activity.get("object") != actor_id:
                return {"Error": "Follow object does not match this inbox"}, 400

            follower = activity["actor"]
            print(f"{follower} is requesting to follow.")
            followers = user.setdefault("followers", [])
            if follower not in followers:
                followers.append(follower)
                save_user(username, user)

            accept = {
                "@context": "https://www.w3.org/ns/activitystreams",
                "id": f"{actor_id}#accepts/follows/{uuid.uuid4()}",
                "type": "Accept",
                "actor": actor_id,
                "object": activity
            }
            # the Accept only takes effect once it is POSTed to the
            # follower's inbox; returning it in the response is not delivery
            threading.Thread(target=deliver_to_actor, args=(username, follower, accept), daemon=True).start()
            return accept, 202
        case "Undo":
            inner = activity.get("object")
            if isinstance(inner, dict) and inner.get("type") == "Follow":
                follower = activity["actor"]
                print(f"{follower} is unfollowing.")
                if follower in user.get("followers", []):
                    user["followers"].remove(follower)
                    save_user(username, user)
                return {"status": "ok"}, 202
            return {"error": "Unsupported activity type"}, 400
        case "Delete":
            print(f"{activity.get('id')} was requested for deletion...")
            if activity["actor"] in user.get("followers", []):
                user["followers"].remove(activity["actor"])
                save_user(username, user)
            return {"status": "ok"}, 202
        case _:
            return {"error": "Unsupported activity type"}, 400

def deliver_to_actor(username: str, remote_actor_url: str, activity: dict):
    """Sign an activity with the user's key and POST it to the remote
    actor's inbox. Skips quietly when no private key exists on disk."""
    key_path = storage.private_key_file(username)
    if not key_path.exists():
        print(f"No private key for '{username}'; cannot deliver {activity['type']} to {remote_actor_url}")
        return

    local_actor = get_actor(username)
    actor_id = local_actor.get("id", f"{instance}/b/{username}")
    key_id = local_actor.get("publicKey", {}).get("id", f"{actor_id}#publicKey")

    try:
        remote = federation.fetch_actor(remote_actor_url, myself_headers, timeout)
        inbox_url = remote.get("inbox")
        if not inbox_url:
            print(f"Remote actor {remote_actor_url} has no inbox.")
            return
        response = federation.deliver(activity, inbox_url, key_id, key_path.read_bytes(),
                                      myself_headers, timeout)
        print(f"Delivered {activity['type']} to {inbox_url}: {response.status_code}")
    except (requests.RequestException, ValueError) as error:
        print(f"Failed to deliver {activity['type']} to {remote_actor_url}: {error}")

def serve_collection(actor: str, kind: str):
    """Serve the followers/following collection, with live membership from
    the user file and a single OrderedCollectionPage behind ?page=1."""
    users = get_users()
    if actor not in users:
        return jsonify({"Error": "User not found."}), 404

    data = storage.read_json(storage.actor_file(actor, kind))
    members = get_user(actor).get(kind, [])
    data["totalItems"] = len(members)

    page_param = request.args.get("page")
    if not page_param:
        return ap_jsonify(data)
    if page_param != "1":
        return jsonify({"Error": "Page not found."}), 404

    return ap_jsonify({
        "@context": "https://www.w3.org/ns/activitystreams",
        "id": f"{data['id']}?page=1",
        "type": "OrderedCollectionPage",
        "partOf": data["id"],
        "totalItems": len(members),
        "orderedItems": members
    })

@app.route("/b/<actor>/following")
@app.route("/b/<actor>/following.json")
def following(actor: str):
    return serve_collection(actor, "following")

@app.route("/b/<actor>/followers")
@app.route("/b/<actor>/followers.json")
def followers(actor: str):
    return serve_collection(actor, "followers")

@app.route("/create", methods=["POST"])
def create():
    data = request.json
    links = data["links"]
    target = data.get("target", f"{instance}/@{data['username']}")

    items, all_verified = verify_pages(links, target)

    # shortname feeds the <title> and the Open Graph tags in meta.html; without
    # it they render as " | myself.social" and "Profile of  at myself.social."
    return render_template("user.html", verified = all_verified, posts=items,
                           name=data["username"], shortname=f"@{data['username']}")

@app.route("/b/<user>/o/<note_id>")
def user_post(user: str, note_id: str):
    users = get_users()
    if user not in users:
        return jsonify({"Error": "User not found."}), 404

    actor = get_actor(user)
    if note_id == "0":
        return ap_jsonify({
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": f"https://myself.social/b/{user}/o/0",
            "type": "Note",
            "published": actor["published"],
            "attributedTo": f"https://myself.social/b/{user}",
            "content": "<p>Hei, verda!</p> <p>Eg har blitt født.</p>",
            "to": "https://www.w3.org/ns/activitystreams#Public"
        })

    notes = storage.read_json(storage.messages_file(user))
    if request.url in notes:
        return ap_jsonify(notes[request.url])

    return jsonify({"Error": "Note not found."}), 404


@app.route("/b/<user>.json")
def actor_redirect(user: str):
    return redirect(f"/b/{user}")

@app.route("/b/<user>")
def actor(user: str):
    # content negotiation first (the spec-defined mechanism); the old
    # User-Agent sniffing only remains as a fallback for clients that
    # send no useful Accept header
    accept = request.headers.get("Accept", "")
    wants_activity = "activity+json" in accept or "ld+json" in accept

    if not wants_activity:
        user_agent = (request.headers.get("User-Agent") or "").lower()
        browser_agents = ["mozilla", "chrome", "applewebkit"]
        if "text/html" in accept or any(agent in user_agent for agent in browser_agents):
            return redirect(f"/@{user}")

    users = get_users()
    if user not in users:
        return jsonify({"Error": "User not found."}), 404

    data = get_actor_jsonld(user)

    return ap_jsonify(data)

@app.route("/b/<user>/collections/featured")
def featured(user: str):
    users = get_users()
    if user not in users:
        return jsonify({"Error": "User not found."}), 404

    data = storage.read_json(storage.actor_file(user, "featured"))
    # orderedItems must be a list of objects, not the id->note mapping
    data["orderedItems"] = list(storage.read_json(storage.messages_file(user)).values())
    data["totalItems"] = len(data["orderedItems"])

    return ap_jsonify(data)

def get_actor_jsonld(actor: str):
    data = storage.read_json(storage.actor_file(actor))
    return data
    user_data = storage.read_json(storage.user_file(actor))

    attachments = []

    for link in user_data["links"]:
        domain = urlparse(link).netloc
        if "myself.social" in domain: type_server = "myself"
        else: 
            try:
                type_server = get_type_software(link)
            except Exception as exeption:
                print(f"Failed to connect to '{link}'.")
                print(exeption)
                type_server = "failed to connect"

        if type_server != None and type_server not in domain: 
            domain = f"{domain} ({type_server})"

        attachments.append({
            "type": "PropertyValue",
            "name": domain,
            "value": f"<a href=\"{link}\" target=\"_blank\" rel=\"nofollow noopener noreferrer me\">{link}</a>",
            "verified_at": None
        })

    data["attachment"] = attachments
    data["fields"] = attachments
    return data

@app.route("/@<actor>.json")
def actor_json(actor: str):
    users = get_users()
    if actor not in users:
        return jsonify({"Error": "User not found."}), 404

    data = get_actor_jsonld(actor)
    return ap_jsonify(data)

@app.route("/@<actor>")
def profile(actor: str):   
    print(f"Checking if '{actor}' exists.")
    users = get_users()
    if actor not in users:
        print(f"'{actor}' does not exist.")
        return render_template("no_user.html", name=f"@{actor}@{domain}", shortname=f"@{actor}"), 404
    
    print(f"'{actor}' does exist.")
    user_file = storage.read_json(storage.user_file(actor))
    actor_jsonld = get_actor_jsonld(actor)

    links = user_file["links"]
    verified_links = user_file.get("verified_links", {})
    items = [{"link": link, "verified": False, "site": None,
              "preverified": link in verified_links} for link in links]
    
    return render_template("user.html", posts=items, name=f"@{actor}@{domain}", shortname=f"@{actor}", actor = actor_jsonld, user=None)#current_user)

@app.route("/@<actor>@<server>")
def external_profile(actor: str, server: str):
    user, links = fetch_external_profile(actor, server)
    if user is None:
        return render_template("failed.html", name=f"@{actor}@{server}", shortname=f"@{actor}")
    
    return render_template("user.html", posts=links, name=f"@{actor}@{server}", shortname=f"@{actor}", actor = user, external = "external")
    

def fetch_external_profile(actor: str, server: str):
    response = requests.get(f"https://{server}/.well-known/webfinger?resource=acct:{actor}@{server}")
    if response.status_code != 200:
        return None, None
    
    response = json.loads(response.text)
    if "links" not in response: return None

    attachments = []
    user = None
    for link in response["links"]:
        if link["rel"] == "self":
            user_info = requests.get(f"{link['href']}.json")
            if user_info.status_code == 200:
                user = json.loads(user_info.text)
                attachments = user["attachment"]
                break
    links = []
    for attachment in attachments:
        value = attachment["value"]
        soup = BeautifulSoup(value, "html.parser")
        anchors = soup.find_all("a", href=True)

        links.extend([{"link": element["href"]} for element in anchors])
    
    return user, links

def verify_internal_user(user: str, target: str):
    user = get_actor_jsonld(user)
    
    for attachment in user["attachment"]:
        value = attachment["value"]
        soup = BeautifulSoup(value, "html.parser")
        anchors = soup.find_all("a", href=True)

        links = [element["href"] for element in anchors]
        if target in links:
            return True
    return False
        

def verify_page(link, target):
    #target = "https://myself.social/@markus" < This is for local debugging
    
    target = urlparse(target)
    target = target.netloc + target.path
    
    if dev_ip in target:
        target = target.replace(dev_ip, domain)
    if domain in link:
        verified = verify_internal_user(link.split("@")[-1], target)
        return {"link": link, "verified": 1 if verified else -1, "site": "myself"}

    item = {"link": link, "verified": False, "site": None}
    print(f"Checking if {link} contains reference to {target}...")
    try:
        with requests.Session() as session:
            if not robots_allowed(link, session):
                print(f"robots.txt of '{link}' disallows fetching; skipping verification.")
                item["site"] = "robots.txt disallows"
                return item

            response = session.get(link, headers=myself_headers, timeout=timeout)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                anchors = soup.find_all("a", href=True)
                links = soup.find_all("link", href=True)
                all_elements = anchors + links
                hits = [element for element in all_elements if target in element["href"]]
                if len(hits) < 1: 
                    found = -1
                else:
                    true_hit = any("me" in element.get("rel", []) for element in hits)
                    if true_hit: 
                        found = 1
                    else:
                        found = 0
                #found = any(target in element["href"] for element in all_elements)
                item["verified"] = found

            type_site = get_type_software(link, session)
            item["site"] = type_site
            return item
    except:
        print(f"Failed to check '{link}'")
        return item

def verify_pages(links, target):
    items = []
    
    for link in links:
        items.append(verify_page(link, target))

    all_verified = all(item["verified"] for item in items)
    return items, all_verified


def extract_base_url(url):
    parsed_url = urlparse(url)
    base_url = urlunparse((parsed_url.scheme, parsed_url.netloc, '', '', '', ''))
    return base_url

def get_type_software(link: str, session = None):
    print(f"Checking server type of '{link}'")
    if domain in link: return "myself"

    link = f"{extract_base_url(link)}/.well-known/nodeinfo"
    if session is None:
        session = requests.Session()

    try:
        if not robots_allowed(link, session):
            print(f"robots.txt disallows fetching '{link}'.")
            return None

        response = session.get(link, headers = myself_headers, timeout=timeout)
        if response.status_code == 200:
            response = json.loads(response.text)
            if "links" in response:
                for link in response["links"]:
                    if "href" in link:
                            type_response = session.get(link["href"], headers = myself_headers, timeout=timeout)
                            if type_response.status_code == 200:
                                response = json.loads(type_response.text)
                                return  response["software"]["name"]
                            else:
                                print("Error code:", response)
    except Exception as exeption:
        print(f"Failed to connect to '{link}'.")
        print(exeption)
        return None


@app.route("/k/<tag>")
def tags(tag: str):
    return redirect("/")

@app.route("/.well-known/nodeinfo")
def nodeinfo():
    return jsonify({
        "links": [
            {
                "rel": "http://nodeinfo.diaspora.software/ns/schema/2.0",
                "href": f"{instance}/nodeinfo/2.0"
            }
        ]
    }), 200

@app.route("/nodeinfo/2.0")
def nodeinfo2():
    return jsonify({
        "version": "2.0",
        "software": {
            "name": "passetditt",
            "version": "0.0.1"
        },
        "protocols": [
            "activitypub"
        ],
        "services": {
            "outbound": [],
            "inbound": []
        },
        "usage": {
            "users": {
                "total": 1,
                "activeMonth": 1,
                "activeHalfyear": 31
            },
            "localPosts": 1
        },
        "openRegistrations": False,
        "metadata": {}
    }), 200

@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json()
    target = data.get("target")
    url = data.get("url")

    if not url:
        return jsonify({"Error": "Missing url parameter"}), 400
    
    else:
        return jsonify(verify_page(url, target)), 200


@app.route('/.well-known/webfinger', methods=['GET'])
def webfinger():
    resource = request.args.get('resource')
    if not resource:
        return jsonify({'error': 'Missing resource parameter'}), 400
    
    user_parts = resource.split('@')
    if len(user_parts) != 2:
        return jsonify({'error': 'Invalid resource format'}), 400

    username, user_instance = user_parts
    # exact host match; the old substring test ("lol" in instance, …)
    # accepted resources for domains this server is not authoritative for
    if user_instance != domain:
        return jsonify({'error': 'Invalid instance'}), 400
    if not username.startswith("acct:"):
        return jsonify({"error": "Invalid acct IRI."}), 400

    username = username.replace("acct:", "", 1)
    
    users = get_users()
    if username not in users:
        return jsonify({'error': 'User not found'}), 404
    
    actor = get_actor(username)

    response = {
        "subject": f"acct:{username}@{domain}",
        "aliases": [
            f"{instance}/@{username}",
            f"{instance}/b/{username}"
        ],
        "links": [
            {
                "rel": "http://webfinger.net/rel/profile-page",
                "type": "text/html",
                "href": f"{instance}/@{username}"
            },
            {
                "rel": "self",
                "type": "application/activity+json",
                "href": f"{instance}/b/{username}"
            }
        ]
    }

    # not every account has an avatar (e.g. freshly provisioned ones)
    if "icon" in actor:
        response["links"].append({
            "rel": "http://webfinger.net/rel/avatar",
            "type": actor["icon"]["mediaType"],
            "href": actor["icon"]["url"]
        })

    # RFC 7033: JRDs are served as application/jrd+json
    return Response(json.dumps(response), content_type="application/jrd+json")

def get_user(user: str):
    return storage.read_json(storage.user_file(user))

def get_actor(actor: str):
    return storage.read_json(storage.actor_file(actor))

def save_user(username: str, user: dict):
    storage.write_json(storage.user_file(username), user)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8081)
