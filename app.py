from flask import Flask, Response, request, jsonify, render_template, redirect, url_for
# from flask_login import UserMixin, login_user, login_required, logout_user, current_user
from flask_cors import CORS
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlunparse
from urllib import robotparser
import json, requests, bcrypt, uuid, os, time, threading

import federation

dev_ip = "192.168.68.107:5000"
domain = "myself.social"
instance = f"https://{domain}"
user_agent = f"myself.social/0.1 (+{instance}/)"
myself_headers = {"User-Agent": user_agent}
timeout = 3

app = Flask(__name__, static_folder="static")
app.secret_key = os.environ.get("SECRET_KEY", os.urandom(32).hex())
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 86400

# login_manager = LoginManager()
# login_manager.init_app(app)

# class User(UserMixin):
#     pass

# users.json is re-read only when its mtime changes, instead of on every request
_users_cache = {"mtime": None, "data": None}

def get_users():
    mtime = os.path.getmtime("users.json")
    if _users_cache["mtime"] != mtime:
        _users_cache["data"] = json.loads(open("users.json", "r", encoding="utf8").read())
        _users_cache["mtime"] = mtime
    return _users_cache["data"]

def write_users(users: dict):
    open("users.json", "w", encoding="utf8").write(json.dumps(users, indent=4))

# @login_manager.user_loader
def load_user(username: str):
    if username in get_users():
        user = User()
        user.id = username
        return user

CORS(app)

def ap_jsonify(data: dict, status: int = 200):
    """ActivityPub documents must be served as activity+json, not plain json."""
    return Response(json.dumps(data), status=status, content_type=federation.ACTIVITY_CONTENT_TYPE)

def get_ds_client():
    pass #return datastore.Client.from_service_account_info(json.loads(os.environ["GOOGLE_APPLICATION_CREDENTIALS"]))

def get_actor_from_db(actor: str):
    ds = get_ds_client()
    query = ds.query(kind="actor")
    query.add_filter("@id", "=", f"https://myself.social/b/{actor}")
    actors = list(query.fetch())
    if len(actors) != 1: return None
    return actors[0]

def update_actor_in_db(actor: dict):
    pass
    # client = get_ds_client()
    # key = client.key("actor")
    # actor = datastore.Entity(key=key)    
    # actor.update(actor)
    # client.put(actor)

@app.route("/db/<actor>", methods=["GET"])
def db(actor: str):
    actor = get_actor_from_db(actor)
    if actor is None:
        return jsonify({"Error": "Actor not found"}), 404
    return jsonify(actor), 200

@app.route("/db/add/<actor>", methods=["POST"])
def dbadd(actor: str):
    actor = request.json
    update_actor_in_db(actor)
    return jsonify({"OK": "Nice"}), 200


# @app.route("/login", methods = ["GET", "POST"])
# def login():
#     if request.method == "POST":
#         username = request.form["username"]
#         password = request.form["password"]
#         if validate_login(username, password):
#             user = load_user(username)
#             login_user(user)
#             return redirect(f"/@{username}")
#     return render_template("login.html")

def validate_login(username: str, password: str):
    users = get_users()
    if username not in users: return False
    user = users[username]

    stored_hash = user["password"].encode("utf8")

    return bcrypt.checkpw(password.encode("utf-8"), stored_hash)

# @app.route("/check", methods=["GET"])
# @login_required
# def check():
#     return f"Hello, {current_user.id}!"


# @app.route('/logout')
# @login_required
# def logout():
#     logout_user()
#     return redirect(url_for('login'))

def generate_random_string(length):
    import random, string
    characters = string.ascii_letters + string.digits + string.punctuation 
    return  ''.join(random.choice(characters) for _ in range(length))
    
def register_user(username: str, password: str):
    users = get_users()
    if username in users:
        return False
    salt = bcrypt.gensalt()
    password = password.encode("utf8")
    hashed_password = bcrypt.hashpw(password, salt)
    users[username] = {'password': hashed_password.decode("utf8"), "salt": salt.decode("utf8")}
    write_users(users)
    return True

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        if not register_user(username, password):
            return render_template('signup.html', error="That username is already taken."), 409

        # The /login route is currently disabled, so land on the front page instead
        return redirect("/")

    return render_template('signup.html')



@app.route("/")
def main():
    return render_template("index.html")

@app.route("/robots.txt")
def robots_txt():
    return app.send_static_file("robots.txt")

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

@app.route("/test")
def test():
    return render_template("test.html")

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

    notes = json.loads(open(f"./actors/messages/{actor}.jsonld", "r", encoding="utf8").read())
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
    key_path = f"./actors/{username}_private.pem"
    if not os.path.exists(key_path):
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
        response = federation.deliver(activity, inbox_url, key_id, open(key_path, "rb").read(),
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

    data = json.loads(open(f"./actors/{actor}_actor_{kind}.jsonld", "r").read())
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

    return render_template("user.html", verified = all_verified, posts=items, name=data["username"])

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

    notes = json.loads(open(f"./actors/messages/{user}.jsonld", "r", encoding="utf8").read())
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

    data = json.loads(open(f"./actors/{user}_actor_featured.jsonld", "r").read())
    # orderedItems must be a list of objects, not the id->note mapping
    data["orderedItems"] = list(json.loads(open(f"./actors/messages/{user}.jsonld", "r", encoding="utf8").read()).values())
    data["totalItems"] = len(data["orderedItems"])

    return ap_jsonify(data)

def get_actor_jsonld(actor: str):
    data = json.loads(open(f"./actors/{actor}_actor.jsonld", "r", encoding="utf8").read())
    return data
    user_data = json.loads(open(f"./actors/{actor}.jsonld", encoding="utf8").read())

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
    user_file = json.loads(open(f"./actors/{actor}.jsonld", "r").read())
    actor_jsonld = get_actor_jsonld(actor)

    links = user_file["links"]
    items = [{"link": link, "verified": False, "site": None} for link in links]
    
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
            },
            {
                "rel": "http://webfinger.net/rel/avatar",
                "type": actor["icon"]["mediaType"],
                "href": actor["icon"]["url"]
            }
        ]
    }

    # RFC 7033: JRDs are served as application/jrd+json
    return Response(json.dumps(response), content_type="application/jrd+json")

def get_user(user: str):
    return json.loads(open(f"./actors/{user}.jsonld", "r").read())

def get_actor(actor: str):
    return json.loads(open(f"./actors/{actor}_actor.jsonld", "r").read())

def save_user(username: str, user: dict):
    open(f"./actors/{username}.jsonld", "w").write(json.dumps(user))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8081)
