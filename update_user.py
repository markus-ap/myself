from urllib.parse import urlparse, urlunparse
import hashlib, requests, hmac, base64, json, time, base64
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes

import storage

def create_signature(key, data):
    if isinstance(key, str):
        key = key.encode('utf-8')
    signature = hmac.new(key, data.encode('utf-8'), hashlib.sha256)
    return base64.b64encode(signature.digest())

def send_signed_request(url, payload, private_key, host):
    # Calculate the digest of the JSON data using SHA-256
    digest = hashlib.sha256(json.dumps(payload).encode('utf-8')).digest()
    digest_b64 = base64.b64encode(digest).decode('utf-8')
    
    date = time.strftime('%a, %d %b %Y %H:%M:%S GMT', time.gmtime())
    
    private_key = serialization.load_pem_private_key(private_key, password=None, backend=default_backend())
    signed_string = f"(request-target): post /inbox\nhost: {host}\ndate: {date}\ndigest: SHA-256={digest_b64}"

    signature = private_key.sign(
        signed_string.encode('utf-8'),
        padding.PKCS1v15(),
        hashes.SHA256()
    )

    signature_b64 = base64.b64encode(signature).decode('utf-8')
    signature_header = f'keyId="https://myself.social/b/markus#publicKey",headers="(request-target) host date digest",signature="{signature_b64}"'

    headers = {
        "Host": host,
        "Date": date, 
        "Digest": f"SHA-256={digest_b64}",
        "Signature": signature_header 
    }


    try:
        response = requests.post(url, json=payload, headers=headers)
        return response
    except:
        print(f"Failed on request to {url}")
        return

def get_followers(user: str):
    all_followers = []

    i = 1
    while True:
        url = f"{user}/followers.json?page={i}"
        result = requests.get(url).text

        followers = json.loads(result)["orderedItems"]
        if len(followers) < 1: break
        all_followers.extend(followers)

        i += 1

    return all_followers

def get_follower_servers(user: str):
    servers = set()
    all_followers = set()

    i = 1
    while True:
        url = f"{user}/followers.json?page={i}"
        result = requests.get(url)
        try:
            result.raise_for_status()
            result = result.text
            result = json.loads(result)        
        except:
            return servers, all_followers
        

        followers = result["orderedItems"]
        if len(followers) < 1: break
        
        all_followers = all_followers.union(set(followers))

        for follower in followers:            
            domain = urlparse(follower).netloc
            servers.add(domain)
            if len(servers) > 100:
                return servers, all_followers

        i += 1

    return servers, all_followers

def followers_followers(user: str = "https://skvip.lol/users/markus"):
    all_servers = set()
    servers, followers = get_follower_servers(user)    
    all_servers = all_servers.union(servers)

    i = 1
    for follower in followers:
        print(f"{i} / {len(followers)} : {follower}")
        i += 1
        servers, _ = get_follower_servers(follower)
        all_servers = all_servers.union(servers)
        print(f"Servers: {len(all_servers)}")
        if len(all_servers) > 1000: break
    
    result = {
        "servers": list(all_servers)        
    }
    
    storage.write_json(storage.FOLLOWERS_INFO_FILE, result, indent=None)

def update_user(user: str):
    private_key = storage.private_key_file(user).read_bytes()

    user = f"https://myself.social/b/{user}.json"
    user = json.loads(requests.get(user).text)

    payload = {
        "@context": [
            "https://www.w3.org/ns/activitystreams",            
        
        ],
        "id": "https://myself.social/b/markus",
        "type": "Update",
        "actor": "https://myself.social/b/markus",

        "object": user
    }

    servers = storage.read_json(storage.FOLLOWERS_INFO_FILE)["servers"]
    server_count = len(servers)

    hosts = ["skvip.lol", "prosa.skvip.lol", "pixelfed.babb.no", "bookwyrm.social"]
    for host in hosts:
        url = f"https://{host}/inbox"
        print(f"0/{server_count}", url, end="\t")
        response = send_signed_request(url, payload, private_key, host)
        print(response.status_code)

    index = 1
    for host in servers:
        url = f"https://{host}/inbox"
        print(f"{index}/{server_count}", url, end="\t")
        index += 1
        response = send_signed_request(url, payload, private_key, host)
        if response is None: continue
        print(response.status_code)

def delete_message():
    private_key = storage.private_key_file("markus").read_bytes()

    followers = get_followers("https://skvip.lol/users/markus")
    servers = set([ urlparse(follower).netloc for follower in followers ])

    host = "skvip.lol"
    url = f"https://{host}/inbox"

    for domain in servers:
        url = f"https://{domain}/inbox"
        for note_id in ["110696475316418926", "110788424322406752", "110853199595369994"]:
            payload = {
                "@context": "https://www.w3.org/ns/activitystreams",
                "type": "Delete",
                "actor": "https://myself.social/b/markus",
                "object": f"https://myself.social/b/markus/statuses/{note_id}"
            }

            response = send_signed_request(url, payload, private_key, host)
            if response is None: continue
            print(url, note_id, response.status_code)

def follower_servers(user: str):
    followers = get_followers(user)
    return set([urlparse(follower).netloc for follower in followers])

def broadcast_messages(user: str):
    notes = storage.read_json(storage.messages_file(user))
    private_key = storage.private_key_file(user).read_bytes()

    for server in ["skvip.lol"]: # follower_servers("https://skvip.lol/users/markus"):
        url = f"https://{server}/inbox"
        for note_id, note in notes.items():
            payload = {
                "@context": "https://www.w3.org/ns/activitystreams",
                "id": note_id,
                "to": "https://www.w3.org/ns/activitystreams#Public",
                "actor" :note["attributedTo"],
                "type": "Update",
                "object": note
            }
            response = send_signed_request(url, payload, private_key, server)
            if response is None:
                print("Failed to send.", url, note_id)
                continue
            print(url, note_id, response.status_code)

if __name__ == '__main__':    
    #broadcast_messages("markus")
    update_user("markus")
