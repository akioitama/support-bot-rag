const API_BASE = (function () {
    const host = window.location.hostname;
    if (host === "localhost" || host === "127.0.0.1" || host === "::1") {
        return "http://localhost:8000";
    }
    return "http://" + host + ":8000";
})();

async function api(path, options = {}) {
    const response = await fetch(API_BASE + path, {
        credentials: "include",
        headers: {
            "Content-Type": "application/json",
            ...(options.headers || {}),
        },
        ...options,
    });
    return response;
}

async function readError(response) {
    try {
        const data = await response.json();
        if (typeof data.detail === "string") {
            return data.detail;
        }
        if (Array.isArray(data.detail)) {
            return data.detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
        }
        return "Request failed";
    } catch (error) {
        return response.statusText || "Request failed";
    }
}

async function getCurrentUser() {
    try {
        const response = await api("/api/users/me");
        if (response.status === 401) {
            return null;
        }
        if (!response.ok) {
            return null;
        }
        return response.json();
    } catch (error) {
        return null;
    }
}

function displayName(user) {
    const name = `${user.first_name || ""} ${user.last_name || ""}`.trim();
    return name || user.email;
}

function renderNav(user, currentPage) {
    const nav = document.getElementById("nav");
    if (!nav) {
        return;
    }

    const links = [];
    links.push(
        currentPage === "chat"
            ? '<span class="current">Chat</span>'
            : '<a href="chat.html">Chat</a>'
    );
    if (user && user.role === "admin") {
        links.push(
            currentPage === "admin"
                ? '<span class="current">Admin</span>'
                : '<a href="admin.html">Admin</a>'
        );
    }
    links.push('<a href="' + API_BASE + '/auth/logout' + (user && user.role === "admin" ? "?next=admin" : "") + '">Logout</a>');
    nav.innerHTML = links.join("");
}

function appendChatItem(container, who, text) {
    const item = document.createElement("div");
    item.className = "chat-item" + (who === "You" ? " user" : "");
    const label = document.createElement("div");
    label.className = "who";
    label.textContent = who;
    const body = document.createElement("div");
    body.textContent = text;
    item.appendChild(label);
    item.appendChild(body);
    container.appendChild(item);
    container.scrollTop = container.scrollHeight;
}

async function initIndexPage() {
    const user = await getCurrentUser();
    if (!user) {
        window.location.href = "login.html";
        return;
    }
    window.location.href = user.role === "admin" ? "admin.html" : "chat.html";
}

async function initUserLoginPage() {
    const params = new URLSearchParams(window.location.search);
    const errorBox = document.getElementById("error");
    if (errorBox && params.get("error") === "admin_account") {
        errorBox.textContent = "This email is an admin account. Use Admin login instead.";
    }

    const loginBtn = document.getElementById("login-btn");
    if (loginBtn) {
        loginBtn.setAttribute("href", API_BASE + "/auth/login");
    }

    if (params.get("error")) {
        return;
    }

    const user = await getCurrentUser();
    if (user) {
        window.location.href = user.role === "admin" ? "admin.html" : "chat.html";
    }
}

async function initAdminLoginPage() {
    const params = new URLSearchParams(window.location.search);
    const errorBox = document.getElementById("error");
    if (errorBox && params.get("error") === "not_admin") {
        errorBox.textContent = "This email is not an admin. Use User login for chat.";
    }

    const adminBtn = document.getElementById("admin-login-btn");
    if (adminBtn) {
        adminBtn.setAttribute("href", API_BASE + "/auth/login-admin");
    }

    if (params.get("error")) {
        return;
    }

    const user = await getCurrentUser();
    if (user) {
        window.location.href = user.role === "admin" ? "admin.html" : "chat.html";
    }
}

async function initChatPage() {
    const user = await getCurrentUser();
    if (!user) {
        window.location.href = "login.html";
        return;
    }

    renderNav(user, "chat");
    document.getElementById("welcome").textContent = "Welcome, " + displayName(user);

    const box = document.getElementById("messages");
    const input = document.getElementById("message");
    const sendButton = document.getElementById("send");
    const errorBox = document.getElementById("error");

    const historyResponse = await api("/api/chat/history");
    if (historyResponse.ok) {
        const history = await historyResponse.json();
        history.forEach(function (item) {
            appendChatItem(box, "You", item.message);
            appendChatItem(box, "Assistant", item.response);
        });
    } else {
        errorBox.textContent = await readError(historyResponse);
    }

    async function sendMessage() {
        const message = input.value.trim();
        if (!message) {
            return;
        }

        errorBox.textContent = "";
        sendButton.disabled = true;
        appendChatItem(box, "You", message);
        input.value = "";

        try {
            const response = await api("/api/chat", {
                method: "POST",
                body: JSON.stringify({ message: message }),
            });
            if (!response.ok) {
                errorBox.textContent = await readError(response);
                appendChatItem(box, "Assistant", "(No response — see error above)");
                return;
            }
            const data = await response.json();
            appendChatItem(box, "Assistant", data.response);
        } catch (error) {
            errorBox.textContent = "Could not reach the server.";
        } finally {
            sendButton.disabled = false;
            input.focus();
        }
    }

    sendButton.addEventListener("click", sendMessage);
    input.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            sendMessage();
        }
    });
}

async function initAdminPage() {
    const user = await getCurrentUser();
    if (!user) {
        window.location.href = "admin-login.html";
        return;
    }
    if (user.role !== "admin") {
        document.getElementById("admin-content").innerHTML =
            "<h1>Admin access required</h1>" +
            "<p class=\"muted\">This page is only for admin users.</p>" +
            "<p><a class=\"button\" href=\"admin-login.html\">Go to admin login</a></p>" +
            "<p><a href=\"chat.html\">Back to chat</a></p>";
        return;
    }

    renderNav(user, "admin");
    await loadUsers();
}

async function loadUsers() {
    const errorBox = document.getElementById("error");
    const tbody = document.getElementById("users-body");
    errorBox.textContent = "";

    const response = await api("/api/admin/users");
    if (!response.ok) {
        errorBox.textContent = await readError(response);
        return;
    }

    const users = await response.json();
    tbody.innerHTML = "";
    users.forEach(function (item) {
        const row = document.createElement("tr");
        const name = `${item.first_name || ""} ${item.last_name || ""}`.trim() || "-";

        row.innerHTML = "<td></td><td></td><td></td><td></td>";
        row.children[0].textContent = item.email;
        row.children[1].textContent = name;
        const roleBadge = document.createElement("span");
        roleBadge.className = "role" + (item.role === "admin" ? " admin" : "");
        roleBadge.textContent = item.role;
        row.children[2].appendChild(roleBadge);
        row.children[3].textContent = String(item.chat_count || 0);
        tbody.appendChild(row);
    });
}
