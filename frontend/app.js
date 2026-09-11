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

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
}

function formatInline(text) {
    return escapeHtml(text).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
}

function formatAssistantHtml(text) {
    const lines = String(text || "").replace(/\r\n/g, "\n").split("\n");
    const html = [];
    let listType = null;

    function closeList() {
        if (listType) {
            html.push(listType === "ul" ? "</ul>" : "</ol>");
            listType = null;
        }
    }

    lines.forEach(function (rawLine) {
        const line = rawLine.trim();
        if (!line) {
            closeList();
            return;
        }

        const bullet = line.match(/^[-*]\s+(.+)$/);
        const numbered = line.match(/^\d+\.\s+(.+)$/);
        const heading = line.match(/^\*\*(.+)\*\*:?\s*$/) || line.match(/^#{1,3}\s+(.+)$/);

        if (heading && !bullet) {
            closeList();
            html.push("<h3>" + formatInline(heading[1].replace(/:$/, "")) + "</h3>");
            return;
        }
        if (bullet) {
            if (listType !== "ul") {
                closeList();
                html.push("<ul>");
                listType = "ul";
            }
            html.push("<li>" + formatInline(bullet[1]) + "</li>");
            return;
        }
        if (numbered) {
            if (listType !== "ol") {
                closeList();
                html.push("<ol>");
                listType = "ol";
            }
            html.push("<li>" + formatInline(numbered[1]) + "</li>");
            return;
        }
        closeList();
        html.push("<p>" + formatInline(line) + "</p>");
    });
    closeList();
    return html.join("") || "<p></p>";
}

function appendChatItem(container, who, text) {
    const item = document.createElement("div");
    item.className = "chat-item" + (who === "You" ? " user" : " assistant");
    const label = document.createElement("div");
    label.className = "who";
    label.textContent = who;
    const body = document.createElement("div");
    body.className = "msg";
    if (who === "You") {
        body.textContent = text;
    } else {
        body.innerHTML = formatAssistantHtml(text);
    }
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
    loadDocumentStatus();
    setInterval(loadDocumentStatus, 4000);

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
    await loadDocuments();
    const uploadForm = document.getElementById("upload-form");
    if (uploadForm) {
        uploadForm.addEventListener("submit", uploadDocument);
    }
    setInterval(loadDocuments, 2000);
}

async function loadDocumentStatus() {
    const box = document.getElementById("doc-status");
    if (!box) {
        return;
    }
    try {
        const response = await api("/api/documents/status");
        if (!response.ok) {
            box.textContent = "Could not load document status.";
            return;
        }
        const data = await response.json();
        box.textContent =
            "Documents: " +
            data.ready +
            " ready, " +
            data.processing +
            " processing, " +
            data.pending +
            " pending, " +
            data.failed +
            " failed.";
    } catch (error) {
        box.textContent = "Could not load document status.";
    }
}

async function uploadDocument(event) {
    event.preventDefault();
    const input = document.getElementById("pdf-file");
    const statusBox = document.getElementById("upload-status");
    const errorBox = document.getElementById("error");
    if (!input || !input.files || !input.files[0]) {
        statusBox.textContent = "Choose a PDF first.";
        return;
    }

    const formData = new FormData();
    formData.append("file", input.files[0]);
    statusBox.textContent = "Uploading...";
    errorBox.textContent = "";

    try {
        const response = await fetch(API_BASE + "/api/admin/documents", {
            method: "POST",
            credentials: "include",
            body: formData,
        });
        if (!response.ok) {
            statusBox.textContent = "";
            errorBox.textContent = await readError(response);
            return;
        }
        input.value = "";
        statusBox.textContent = "Uploaded. Processing will start in the background.";
        await loadDocuments();
    } catch (error) {
        statusBox.textContent = "";
        errorBox.textContent = "Could not reach the server.";
    }
}

async function loadDocuments() {
    const tbody = document.getElementById("documents-body");
    if (!tbody) {
        return;
    }

    const response = await api("/api/admin/documents");
    if (!response.ok) {
        return;
    }

    const documents = await response.json();
    tbody.innerHTML = "";
    documents.forEach(function (item) {
        const row = document.createElement("tr");
        row.innerHTML = "<td></td><td></td><td></td><td></td><td class=\"actions\"></td>";
        row.children[0].textContent = item.filename;
        const badge = document.createElement("span");
        badge.className = "status " + (item.status || "");
        badge.textContent = item.status;
        row.children[1].appendChild(badge);
        row.children[2].textContent = String(item.chunk_count || 0);
        row.children[3].textContent = item.error_message || "";
        const deleteButton = document.createElement("button");
        deleteButton.type = "button";
        deleteButton.className = "danger";
        deleteButton.textContent = "Delete";
        deleteButton.addEventListener("click", function () {
            deleteDocument(item.id, item.filename);
        });
        row.children[4].appendChild(deleteButton);
        tbody.appendChild(row);
    });
}

async function deleteDocument(documentId, filename) {
    const errorBox = document.getElementById("error");
    const statusBox = document.getElementById("upload-status");
    if (!window.confirm("Delete " + filename + "? Chat will no longer use this file.")) {
        return;
    }
    errorBox.textContent = "";
    const response = await api("/api/admin/documents/" + documentId, { method: "DELETE" });
    if (!response.ok) {
        errorBox.textContent = await readError(response);
        return;
    }
    if (statusBox) {
        statusBox.textContent = "Deleted " + filename + ".";
    }
    await loadDocuments();
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
