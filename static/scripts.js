const verifyPages = async (ext) => {
    let greens = 0;
    let yellows = 0;

    let target = "";

    if(ext == "external"){
        const userSegements = window.location.href.split("@")
        const userName = userSegements[1];
        const userServer = userSegements[2]
        target = `https://${userServer}/@${userName}`

        const guess = document.getElementById("guess");
        guess.innerHTML = "<a href='" + target + "'>" + target + "</a>";
    } else{
        target = window.location.href;
    }


    const postItems = document.querySelectorAll('.post');
    postItems.forEach(async postItem => {
        // Get the anchor element inside the list item
        const linkElement = postItem.querySelector('a');

        // Get the verification icon span inside the list item
        const verificationIcon = postItem.querySelector('.verification-icon');    
        let allVerified = document.getElementById("all-verified");

        // Get the post link from the anchor element's href attribute
        const postLink = linkElement.getAttribute('href');     

        const endpointUrl = `/verify`;

        fetch(endpointUrl, 
            {
                method: "POST",
                headers: 
                {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify(
                    {
                        url: postLink,
                        target: target
                    })
            })
            .then(response => response.json())
            .then(data => {
                if(data.verified == 1){
                    verificationIcon.textContent = '🟢';
                    verificationIcon.title = "Page verified."
                    greens = greens + 1;
                } else if (data.verified == -1) {
                    verificationIcon.textContent = '🔴';
                    verificationIcon.title = "Page not verified."
                } else{                    
                    verificationIcon.textContent = '🟡';
                    verificationIcon.title = "Page partially verified."
                    yellows = yellows + 1;
                }
                let system = data.site;
                if(system == null)
                    system = "–"
                
                postItem.innerHTML += "<span class=\"site\">" + system + "</span>"

                let totals = greens + yellows;
                if(greens == postItems.length){
                    allVerified.innerHTML = "🟢"
                    allVerified.title = "All pages verified."
                } else if(totals == postItems.length){
                    allVerified.innerHTML = "🟡"
                    allVerified.title = "All pages at least partially verified."
                } else {
                    allVerified.innerHTML = "🔴"
                    allVerified.title = "Some pages not verified."
                }
            })
            .catch(error => {
                console.log(error);
            });


        // console.log(`Checking if ${postLink} contains reference to ${target}...`);
        // const response = await fetch(postLink);

        // if (response.status === 200) {
        //     const text = await response.text();
        //     const parser = new DOMParser();
        //     const doc = parser.parseFromString(text, 'text/html');
        //     const anchors = doc.querySelectorAll("a[href]");
        //     const links = doc.querySelectorAll("link[href]");
        //     const allElements = [...anchors, ...links];
        //     const found = allElements.some(element => element.getAttribute("href").includes(target));
        //     if(found){
        //         verificationIcon.textContent = '✅';
        //         founds = founds + 1;
        //     } else {
        //         verificationIcon.textContent = '❌';
        //     }

        //     const serverType = await getTypeSoftware(postLink);
        //     postItem.innerHTML += "<span class=\"site\">" + serverType + "</span>";

        // }  else {
        //     verificationIcon.textContent = '❌';
        // }
        // if(founds == postItems.length){
        //     allVerified.innerHTML = "✅"
        // } else{
        //     allVerified.innerHTML = "❌"
        // }
    });
};

function extractBaseUrl(url) {
    const parsedUrl = new URL(url);
    const baseUrl = `${parsedUrl.protocol}//${parsedUrl.hostname}`;
    return baseUrl;
}


async function getTypeSoftware(link) {
    const baseUrl = extractBaseUrl(link);
    const newLink = `${baseUrl}/.well-known/nodeinfo`;
    
    try {
        const response = await fetch(newLink);
        if (response.status === 200) {
            const responseData = await response.json();
            if ("links" in responseData) {
                for (const link of responseData.links) {
                    if ("href" in link) {
                        const typeResponse = await fetch(link.href);
                        if (typeResponse.status === 200) {
                            const typeData = await typeResponse.json();
                            return typeData.software.name;
                        } else {
                            console.error("Error code:", typeResponse.status);
                        }
                    }
                }
            }
        }
    } catch (error) {
        console.error("Error:", error);
    }
    
    return "";
}
