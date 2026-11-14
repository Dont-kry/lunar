
const API_URL = "/api/features/";

const featureList = document.getElementById("featureList");
const featureForm = document.getElementById("featureForm");

async function loadFeatures() {
    const response = await fetch(API_URL);
    const features = await response.json();

    featureList.innerHTML = "";

    features.forEach(feature => {
        const div = document.createElement("div");
        div.className = "feature";

        const title = document.createElement("h3");
        title.textContent = feature.name;

        const description = document.createElement("p");
        description.textContent = feature.description;

        const button = document.createElement("button");
        button.textContent = "Delete";
        button.className = "delete-btn";
        button.onclick = () => deleteFeature(feature.id);

        div.append(title, description, button);
        featureList.appendChild(div);
    });
}

featureForm.addEventListener("submit", async (event) => {
    event.preventDefault();

    const name = document.getElementById("name").value.trim();
    const description = document.getElementById("description").value.trim();

    const response = await fetch(API_URL, {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "X-CSRFToken": getCookie("csrftoken")
        },
        body: JSON.stringify({ name, description })
    });

    const data = await response.json();

    if (response.ok) {
        featureForm.reset();
        loadFeatures();
    } else {
        console.error(data);
        alert(JSON.stringify(data));
    }
});

async function deleteFeature(id) {
    if (!confirm("Delete this feature?")) return;

    const response = await fetch(`${API_URL}${id}/`, {
        method: "DELETE",
        headers: {
            "X-CSRFToken": getCookie("csrftoken")
        }
    });

    if (response.ok) {
        loadFeatures();
    }
}

function getCookie(name) {
    const cookies = document.cookie.split(";");

    for (let cookie of cookies) {
        const [key, value] = cookie.trim().split("=");
        if (key === name) {
            return decodeURIComponent(value);
        }
    }

    return null;
}

loadFeatures();
