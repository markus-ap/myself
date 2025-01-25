from flask import Flask, request, jsonify, render_template, redirect, url_for
# from flask_login import UserMixin, login_user, login_required, logout_user, current_user
from flask_cors import CORS
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urlunparse
import json, requests, bcrypt, uuid, os

dev_ip = "192.168.68.107:5000"  
domain = "myself.social"
instance = f"https://{domain}"
myself_headers = {"User-Agent": "myself.social"}
timeout = 1

app = Flask(__name__, static_folder="static")
app.secret_key = "very-secret"

# login_manager = LoginManager()
# login_manager.init_app(app)

# class User(UserMixin):
#     pass

def get_users():
    return json.loads(open("users.json", "r", encoding="utf8").read())

def write_users(users: dict):
    open("users.json", "w", encoding="utf8").write(json.dumps(users, indent=4))

# @login_manager.user_loader
def load_user(username: str):
    if username in get_users():
        user = User()
        user.id = username
        return user

CORS(app)

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

    stored_salt = user["salt"].encode("utf8")
    stored_hash = user["password"].encode("utf8")

    hashed_password = bcrypt.hashpw(password.encode('utf-8'), salt=stored_salt)

    if hashed_password == stored_hash:
        return True
    return False

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
    salt = bcrypt.gensalt()
    password = password.encode("utf8")
    hashed_password = bcrypt.hashpw(password, salt)
    users[username] = {'password': hashed_password.decode("utf8"), "salt": salt.decode("utf8")}
    write_users(users)

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        register_user(username, password)
        
        return redirect(url_for('login'))  # Redirect to login page after successful registration
    
    return render_template('signup.html')



@app.route("/")
def main():
    return render_template("index.html")

@app.route("/test")
def test():
    return render_template("test.html")

@app.route("/b/<actor>/outbox", methods=["GET"])
def outbox(actor: str):
    users = get_users()
    if actor not in users:
        return jsonify({"Error": "User not found."})
    
    notes = json.loads(open(f"./actors/messages/{actor}.jsonld", "r", encoding="utf8").read())
    pages = int(len(notes) / 5)
    if pages == 0: pages = 1

    resource = request.args.get("page")
    if not resource:
        return {
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": request.url,
            "type": "OrderedCollection",
            "totalItems": len(notes),
            "first": f"{request.url}?page=1",
            "last": f"{request.url}?page={pages}"
        }

    return jsonify({"Error": "Unknown error"}), 400

@app.route("/b/<actor>/inbox", methods=["POST"])
def inbox(actor: str):    
    print(f"Inbox call for {actor}")
    request_body = json.loads(request.data.decode('utf-8'))
    print(request_body)
    if "type" in request_body:
        response, status  = resolve_inbox_type(request_body)
        return jsonify(response), status

    return jsonify({"Error": "Unsupported request"}), 400

def resolve_inbox_type(request: dict):
    users = get_users()
    username = request["object"].split("/")[-1]
    if username not in users: 
        return jsonify({"Error": "Failed to find user"}), 404
    
    user = json.loads(open(f"./actors/{username}.jsonld", "r", encoding="utf8").read())

    match request["type"]:
        case "Follow":
            print(f"{request['actor']} is requesting to follow.")
            user.setdefault("followers", [])
            user["followers"].append(request["actor"])
            
            follower = request["actor"]

            accept = {
                "@context": "https://www.w3.org/ns/activitystreams",
                "id": f"{user['profile']}#accepts/followers/{uuid.uuid4()}",
                "type": "Accept",
                "actor": follower,
                "object": request
            }

            open(f"./actors/{username}.jsonld", "w", encoding="utf8").write(json.dumps(user, indent=4))
            return accept, 200
        case "Delete":
            print(f"{request['id']} was requested for deletion...")

            return {"status": "ok"}, 200
        case _:
            return {"error": "Unsupported activity type"}, 400

@app.route("/b/<actor>/following")
def following(actor: str):
    return redirect(f"/b/{actor}/following.json")

@app.route("/b/<actor>/followers")
def followers(actor: str):
    return redirect(f"/b/{actor}/followers.json")

@app.route("/b/<actor>/following.json")
def following_json(actor: str):   
    users = get_users()
    if actor not in users:
        return jsonify({"Error": "User not found."}), 400
    
    data = json.loads(open(f"./actors/{actor}_actor_following.jsonld", "r").read())
    
    return jsonify(data), 200

@app.route("/b/<actor>/followers.json")
def followers_json(actor: str):   
    users = get_users()
    if actor not in users:
        return jsonify({"Error": "User not found."}), 400
    
    data = json.loads(open(f"./actors/{actor}_actor_followers.jsonld", "r").read())
    
    return jsonify(data), 200

@app.route("/create", methods=["POST"])
def create():
    data = request.json
    links = data["links"]

    items, all_verified = verify_pages(links)

    return render_template("user.html", verified = all_verified, posts=items, name=data["username"])

@app.route("/b/<user>/o/<note_id>")
def user_post(user: str, note_id: str):
    users = get_users()
    if user not in users:
        return jsonify({"Error": "User not found."}), 400
    
    actor = get_actor(user)
    if note_id == "0":
        return  {
            "@context": "https://www.w3.org/ns/activitystreams",
            "id": f"https://myself.social/b/{user}/o/0",
            "type": "Note",
            "published": actor["published"],
            "attributedTo": f"https://myself.social/b/{user}",
            "content": "<p>Hei, verda!</p> <p>Eg har blitt født.</p>",
            "to": "https://www.w3.org/ns/activitystreams#Public"
        }
    
    notes = json.loads(open(f"./actors/messages/{user}.jsonld", "r", encoding="utf8").read())
    if request.url in notes:
        return notes[request.url]

    return jsonify({"Error": "Note not found."}), 400


@app.route("/b/<user>.json")
def actor_redirect(user: str):
    return redirect(f"/b/{user}")

@app.route("/b/<user>")
def actor(user: str):
    user_agent = request.headers.get('User-Agent').lower()

    browser_agents = ["mozilla", "chrome", "applewebkit"]
    for agent in browser_agents: 
        if agent in user_agent:
            return redirect(f"/@{user}")
    
    users = get_users()
    if user not in users:
        return jsonify({"Error": "User not found."}), 400
    
    data = get_actor_jsonld(user)
    
    return jsonify(data), 200

@app.route("/b/<user>/collections/featured")
def featured(user: str):
    users = get_users()
    if user not in users:
        return jsonify({"Error": "User not found."}), 400
    
    data = json.loads(open(f"./actors/{user}_actor_featured.jsonld", "r").read())
    data["orderedItems"] = json.loads(open(f"./actors/messages/{user}.jsonld", "r", encoding="utf8").read())
    data["totalItems"] = len(data["orderedItems"])
    
    return jsonify(data), 200

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
        return jsonify({"Error": "User not found."}), 400
    
    data = get_actor_jsonld(actor)
    return jsonify(data), 200

@app.route("/@<actor>")
def profile(actor: str):   
    print(f"Checking if '{actor}' exists.")
    users = get_users()
    if actor not in users:
        print(f"'{actor}' does not exist.")
        return render_template("no_user.html", name=f"@{actor}@{domain}", shortname=f"@{actor}")
    
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
        return {"link": link, "verified": verified, "site": "myself"}

    item = {"link": link, "verified": False, "site": None}
    print(f"Checking if {link} contains reference to {target}...")
    try:
        with requests.Session() as session:
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
                "href": f"{domain}/nodeinfo/2.0"
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
    if user_instance not in instance:
        return jsonify({'error': 'Invalid instance'}), 400
    if "acct:" not in username:
        return jsonify({"error": "Invalid acct IRI."}), 400
    
    username = username.replace("acct:", "")
    
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
    
    return jsonify(response), 200

def get_user(user: str):
    return json.loads(open(f"./actors/{user}.jsonld", "r").read())

def get_actor(actor: str):
    return json.loads(open(f"./actors/{actor}_actor.jsonld", "r").read())

def save_user(user: dict):
    open(f"./actors/{actor}.jsonld", "w").write(json.dumps(user))

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8081)
