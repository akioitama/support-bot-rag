# Support Bot RAG

A local support bot that answers from uploaded PDFs (RAG), with WorkOS login and admin user management.

This is an MVP. The goal is working functionality, not a polished product

Normal users log in and chat. Admins upload support PDFs. The bot retrieves matching chunks, then Ollama answers only from that text. If nothing relevant is found, it says so instead of using general knowledge. Authentication is WorkOS. Embeddings and chat run locally with Ollama. Data is stored in SQLite.

## Features

- FastAPI backend
- WorkOS authentication (AuthKit)
- User management
- Admin and normal user roles
- AI support chatbot that answers from uploaded PDFs
- Local Ollama chat model and embedding model (no paid AI API)
- Background PDF processing with visible status
- SQLite database
- Chat history

## Requirements

You need:

- Python 3.11 or newer
- A WorkOS account and AuthKit application
- [Ollama](https://ollama.com) installed locally
- A local Ollama chat model such as `llama3.2`
- A local Ollama embedding model such as `nomic-embed-text`

## Project Structure

```text
project-root/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models/
│   ├── schemas/
│   ├── routes/
│   ├── services/
│   └── dependencies/
├── frontend/
├── uploads/
├── make_admin.py
├── .env.example
├── requirements.txt
└── README.md
```

## Installation

From the project root:

```text
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

On macOS or Linux:

```text
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Copy the example environment file:

```text
copy .env.example .env
```

On macOS or Linux:

```text
cp .env.example .env
```

Then fill in the WorkOS values described below.

## Environment Variables

The `.env` file should contain:

```env
WORKOS_API_KEY=
WORKOS_CLIENT_ID=
WORKOS_REDIRECT_URI=http://localhost:8000/auth/callback

DATABASE_URL=sqlite:///./app.db

OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.2
OLLAMA_EMBED_MODEL=nomic-embed-text

FRONTEND_URL=http://localhost:3000

ADMIN_EMAILS=

SESSION_SECRET=
```

- `WORKOS_API_KEY` and `WORKOS_CLIENT_ID` are required for login.
- `WORKOS_REDIRECT_URI` must match the redirect URI configured in the WorkOS dashboard. Keep this on the backend (`:8000`).
- `FRONTEND_URL` is the separate frontend origin. After login, WorkOS returns to the backend, then the backend sends the browser to this frontend URL.
- `ADMIN_EMAILS` is a comma-separated list of emails that receive admin rights. Example: `ADMIN_EMAILS=you@gmail.com`
- `SESSION_SECRET` is used to sign the session cookie. Leave it empty for local development and the app will generate one at startup. Set a long random value if you want sessions to survive server restarts.
- Do not commit the real `.env` file. It is ignored by git.

The app can start without WorkOS or Ollama configured. Login will explain that WorkOS is required. Chat will return a clear error if Ollama is not running.

## WorkOS Setup

This app uses WorkOS AuthKit for real authentication. There is no fake login.

1. Create an account at [https://dashboard.workos.com](https://dashboard.workos.com).
2. Create a WorkOS application, or open the existing one for this project.
3. Enable **AuthKit** / **User Management**.
4. Copy the **Client ID** from the application page into `WORKOS_CLIENT_ID`.
5. Open **API Keys**, copy the secret API key, and put it in `WORKOS_API_KEY`.
6. Open **Redirects** and add:

```text
http://localhost:8000/auth/callback
```

7. Make sure `WORKOS_REDIRECT_URI` in `.env` is exactly the same value.
8. In AuthKit, enable at least one sign-in method (for example email + password or a social provider).

Do not put real API keys in this README or in git.

After configuration:

- User login: [http://localhost:3000/login.html](http://localhost:3000/login.html) → `GET /auth/login` → chat
- Admin login: [http://localhost:3000/admin-login.html](http://localhost:3000/admin-login.html) → `GET /auth/login-admin` → admin page
- WorkOS redirects back to `/auth/callback`.
- Emails listed in `ADMIN_EMAILS` get role `admin`. Everyone else gets role `user`.

## Ollama Setup

The AI is free and runs on your machine. It does not use OpenAI, Anthropic, Gemini, or any paid AI API.

1. Install Ollama from [https://ollama.com/download](https://ollama.com/download).
2. Make sure Ollama is running. On Windows, the installer usually starts it automatically. You can also run:

```text
ollama serve
```

3. Download the chat model and the embedding model:

```text
ollama pull llama3.2
ollama pull nomic-embed-text
```

4. Confirm the model is available:

```text
ollama list
```

You should see `llama3.2` and `nomic-embed-text` in the list.

5. Start the backend, then start the frontend in a second terminal.

If you want a different local model, pull it and set `OLLAMA_MODEL` in `.env`.

## Run the Application

Use **two terminals**. Use `localhost` in the browser (not `127.0.0.1`) so the login cookie works.

### Terminal 1 — backend (port 8000)

From the project root, with the virtual environment activated:

```text
uvicorn app.main:app --reload
```

### Terminal 2 — frontend (port 3000)

From the project root, in a new terminal:

```text
python -m http.server 3000 --directory frontend
```

Then open:

- App / user login: [http://localhost:3000/login.html](http://localhost:3000/login.html)
- Admin login: [http://localhost:3000/admin-login.html](http://localhost:3000/admin-login.html)
- Chat: [http://localhost:3000/chat.html](http://localhost:3000/chat.html)
- Admin: [http://localhost:3000/admin.html](http://localhost:3000/admin.html)
- API docs: [http://localhost:8000/docs](http://localhost:8000/docs)
- Health check: [http://localhost:8000/health](http://localhost:8000/health)

## Create the First Admin

Put the admin email in `.env`:

```text
ADMIN_EMAILS=you@gmail.com
```

Restart the backend, then sign in on the **Admin login** page with that same WorkOS email. That account gets admin rights and opens the user management page.

You can still promote an existing user with:

```text
python make_admin.py user@example.com
```

## How to Test the Normal User Flow

1. Start Ollama and confirm `llama3.2` is installed.
2. Start the backend in one terminal: `uvicorn app.main:app --reload`.
3. Start the frontend in a second terminal: `python -m http.server 3000 --directory frontend`.
4. Open [http://localhost:3000/login.html](http://localhost:3000/login.html).
5. Click **Login** and complete WorkOS authentication.
6. You should land on the chat page with role `user`.
7. Send a message. If no PDF is relevant, the bot says it could not find that in the uploaded support documents.
8. Refresh the page. Previous messages should still appear.
9. Open [http://localhost:8000/docs](http://localhost:8000/docs) and call `GET /api/admin/users` while logged in as a normal user. The API should return `403 Forbidden`.

## How to Test the Admin Flow

1. Log in through WorkOS.
2. Promote that user:

```text
python make_admin.py user@example.com
```

3. Refresh or log in again.
4. Open **Admin**.
5. View all users.
6. Upload support PDFs. Status should move from `pending` to `processing` to `ready`.

## How to Test AI Chat

1. Ollama must be running, with `llama3.2` and `nomic-embed-text`.
2. Log in.
3. On the chat page, check the document status line (ready / processing / pending).
4. After PDFs are `ready`, ask something that is in those files.
5. Ask something not in the documents, such as `What is the capital of France?` The bot should say it could not find that in the uploaded support documents.
6. If Ollama is stopped, the chat API returns `503`.

## API Routes

### Public

- `GET /health`
- `GET /auth/login`
- `GET /auth/callback`
- `GET /auth/logout`

### Authenticated

- `GET /api/users/me`
- `GET /api/documents/status`
- `POST /api/chat`
- `GET /api/chat/history`

### Admin only

- `GET /api/admin/users`
- `GET /api/admin/users/{user_id}`
- `PATCH /api/admin/users/{user_id}/role`
- `GET /api/admin/documents`
- `POST /api/admin/documents`
- `DELETE /api/admin/documents/{document_id}`

Admin authorization is enforced in the backend. Hiding the Admin page in the frontend is not the security control.

## Roles

- `user`: log in, view profile, use AI chat, see document processing status, view own chat history, log out
- `admin`: everything a user can do, plus view all registered users and upload support PDFs

A user cannot read another user's chat history. History is loaded from the authenticated session, not from a user ID sent by the browser.
