/**
 * Link verification for the passport page.
 *
 * Each linked account starts as a "Checking…" chip and settles into one of
 * four states. Meaning is never carried by colour alone: every state writes a
 * visible word into .status__text and a distinct glyph into .status__mark, and
 * the colour is a third, redundant signal applied from CSS via data-state.
 */

const STATES = {
    verified: { mark: "✓", text: "Verified" },
    partial:  { mark: "!", text: "Partly verified" },
    failed:   { mark: "✕", text: "Not verified" },
    error:    { mark: "?", text: "Check failed" },
    checking: { mark: "…", text: "Checking…" }
};

/**
 * Profile pictures are fetched from whatever server the actor lives on, so a
 * dead or moved image is routine rather than exceptional. Rather than leave a
 * broken-image box, fall back to the generic portrait placeholder.
 */
const guardPortrait = () => {
    const portrait = document.getElementById("portrait");
    const fallback = document.getElementById("portrait-fallback");
    if (!portrait || !fallback) {
        return;
    }

    const swap = () => {
        portrait.hidden = true;
        fallback.hidden = false;
    };

    portrait.addEventListener("error", swap);
    // This script is deferred, so a fast failure may already have happened
    if (portrait.complete && portrait.naturalWidth === 0) {
        swap();
    }
};

guardPortrait();

const verifyPages = async (ext) => {
    let greens = 0;
    let yellows = 0;
    let settled = 0;

    let target = "";

    if (ext == "external") {
        const userSegements = window.location.href.split("@");
        const userName = userSegements[1];
        const userServer = userSegements[2];
        target = `https://${userServer}/@${userName}`;

        // createElement, not innerHTML: userName and userServer come straight
        // from the URL and would otherwise be interpolated into markup unescaped
        const guess = document.getElementById("guess");
        const guessLink = document.createElement("a");
        guessLink.href = target;
        guessLink.textContent = target;
        guess.replaceChildren(guessLink);
    } else {
        target = window.location.href;
    }

    const postItems = document.querySelectorAll(".post");
    const allVerified = document.getElementById("all-verified");

    /**
     * Set a chip's state. Writes the visible word, the decorative glyph, the
     * data-state hook the stylesheet colours from, and the hover tooltip.
     * `detail` overrides the default wording when there is more to say.
     */
    const setStatus = (element, state, detail) => {
        const preset = STATES[state];
        const label = detail || preset.text;

        element.dataset.state = state;
        element.title = label;
        element.querySelector(".status__mark").textContent = preset.mark;
        element.querySelector(".status__text").textContent = label;
    };

    const addSiteLabel = (postItem, system) => {
        // textContent (not innerHTML): the server type string comes from a
        // remote server's nodeinfo and must not be parsed as HTML
        const siteSpan = document.createElement("span");
        siteSpan.className = "site";
        siteSpan.textContent = system;
        postItem.appendChild(siteSpan);
    };

    /**
     * The summary is only written once every link has settled. Updating it on
     * each response made it flicker red -> amber -> green as results streamed
     * in, which the aria-live region would then announce every time.
     */
    const settle = () => {
        settled = settled + 1;
        if (settled < postItems.length) {
            return;
        }

        if (greens == postItems.length) {
            setStatus(allVerified, "verified", "All accounts verified");
        } else if (greens + yellows == postItems.length) {
            setStatus(allVerified, "partial", "All accounts at least partly verified");
        } else {
            setStatus(allVerified, "failed", "Some accounts could not be verified");
        }
    };

    if (postItems.length === 0) {
        setStatus(allVerified, "partial", "No accounts to check");
        return;
    }

    postItems.forEach(async postItem => {
        const linkElement = postItem.querySelector("a");
        const chip = postItem.querySelector(".status");

        // Links whose ownership was proven by logging in with the account
        // (e.g. Mastodon OAuth) are verified without a network round-trip
        if (postItem.dataset.preverified) {
            setStatus(chip, "verified", "Verified at sign-in");
            addSiteLabel(postItem, "mastodon login");
            greens = greens + 1;
            settle();
            return;
        }

        const postLink = linkElement.getAttribute("href");

        fetch("/verify", {
            method: "POST",
            headers: {
                "Content-Type": "application/json"
            },
            body: JSON.stringify({
                url: postLink,
                target: target
            })
        })
            .then(response => response.json())
            .then(data => {
                if (data.verified == 1) {
                    setStatus(chip, "verified");
                    greens = greens + 1;
                } else if (data.verified == -1) {
                    setStatus(chip, "failed");
                } else {
                    setStatus(chip, "partial");
                    yellows = yellows + 1;
                }
                addSiteLabel(postItem, data.site == null ? "–" : data.site);
                settle();
            })
            .catch(error => {
                // Without this the chip sat on "Checking…" for ever, telling
                // nobody — sighted or otherwise — that the request had died
                console.log(error);
                setStatus(chip, "error");
                settle();
            });
    });
};
