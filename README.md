# ShowMe

You ask your coding agent for a report. It thinks for a while, builds you a genuinely nice HTML page with charts and tables and everything, and then proudly tells you it lives at `./output/report.html`.

Now what? If you're lucky, you paste the path into a browser and it works. Usually you're not lucky. The agent is running on a remote box that has no browser, so you scp the file down, and the charts break because they were in a sibling `assets/` folder. So you tar the whole directory instead, unpack it, dig for the entry point, and finally look at the thing. Then the agent revises it and you do all of that again. Then a teammate asks to see it.

ShowMe is the fix for that. Run one small server of your own, and your agent can end the job with a link instead of a file path:

> *"Build me an HTML report on last quarter's latency regressions, and show me with a link."*
>
> *"Here you go: https://showme.example.com/p/4f2a9c/"*

One click, from any machine, including your phone. Revise the report later and the URL stays the same. Make it private and it picks up a passcode. Everything sits on your own box, so nobody else is holding your data.

Under the hood it's deliberately small: a local CLI uploads a directory containing `index.html` plus whatever assets it needs, the server stores it, and you get a durable link back. One owner, one server, content you trust.

Licensed under the [MIT License](LICENSE).

## Features

- Publish a complete webpage directory, including images and other static assets.
- Replace a page's content later without changing its URL or access settings.
- Public pages accessible to anyone with the URL.
- Passcode-protected private pages.
- Switch a page between public and private without changing its URL.
- Archive, restore, and manually delete pages.
- Rename pages and add searchable notes without changing their published content or URL.
- Download any page's original HTML and assets as an editable ZIP archive.
- Treat the server as the source of truth with disposable local workspaces by default.
- Pull an existing page into a retained local project when you want to keep its source.
- Search names, notes, page IDs, and machines; filter by status or publishing machine.
- Single-administrator dashboard with no external identity provider.
- One API token per local machine, with individual revocation.
- Global Codex skill and optional global publishing preference.
- Reversible local installation.

## Security and trust model

> **ShowMe serves uploaded HTML and JavaScript without sanitizing it. Only publish content you created or trust. Do not let untrusted people or agents upload pages, and do not publish unknown website bundles. Malicious uploaded JavaScript may act through a dashboard session because published pages and the dashboard share an origin.**

The public internet can reach the login page, but passwords are stored with Argon2id, dashboard sessions are server-side, state-changing dashboard forms use CSRF tokens, repeated login failures are rate-limited, and cookies are `HttpOnly` and `SameSite=Strict` (`Secure` in HTTPS mode). HTTPS is strongly recommended whenever the service crosses an untrusted network.

This project deliberately accepts the same-origin limitation above to keep personal deployment simple. It is not a multi-tenant hosting service.

## Server setup

Prerequisites:

- A machine with a static public IP.
- TCP port 80 open for IP/HTTP use, or ports 80 and 443 for domain/HTTPS use.
- Docker with Docker Compose.

Clone and launch:

```bash
git clone YOUR_REPOSITORY_URL showme
cd showme
cp .env.example .env
```

The checked-in defaults work immediately on the Docker host at `http://127.0.0.1`:

```dotenv
SHOWME_HOST=127.0.0.1
SHOWME_SCHEME=http
SHOWME_SECURE_COOKIES=false
```

To reach ShowMe directly through a static IP, replace `SHOWME_HOST` with that IP. A domain is not required:

```dotenv
SHOWME_HOST=203.0.113.10
SHOWME_SCHEME=http
SHOWME_SECURE_COOKIES=false
SHOWME_MAX_UPLOAD_MB=50
```

To use automatic HTTPS, point a domain at the server and use:

```dotenv
SHOWME_HOST=showme.example.com
SHOWME_SCHEME=https
SHOWME_SECURE_COOKIES=true
SHOWME_MAX_UPLOAD_MB=50
```

Then start the stack:

```bash
docker compose up -d --build
docker compose logs showme
```

Caddy obtains and renews the HTTPS certificate automatically when domain/HTTPS mode is selected. The ShowMe log prints a one-time setup URL. Open it and create the administrator password. No API token is created during setup; create one yourself from **API tokens** in the dashboard when you are ready to connect a machine.

HTTP is plaintext. If the IP is exposed to the public internet, credentials, API tokens, and page traffic are not encrypted. Prefer domain/HTTPS or put the IP service behind a private network or VPN.

The setup URL is valid only while no administrator exists. Its token is stored in the private Docker data volume and is deleted after setup.

## Local installation

On every machine that should publish pages, clone the repository and run:

```bash
./install.sh
```

The server prompt defaults to `http://127.0.0.1`. It also accepts a bare IP such as `203.0.113.10` and automatically treats it as HTTP; an explicit `http://` or `https://` is always respected.

The installer:

- Installs `showme` at `~/.local/bin/showme`.
- Saves the server URL and API token in `~/.config/showme/config.json` with mode `0600`.
- Installs the global Codex skill at `~/.agents/skills/showme-publish`.
- Optionally adds a marked ShowMe preference to `~/.codex/AGENTS.md`.
- Installs a standalone uninstaller.

For unattended setup:

```bash
./install.sh \
  --server https://showme.example.com \
  --token 'TOKEN_FROM_THE_DASHBOARD' \
  --codex-default
```

Passing a token on the command line can place it in shell history. Interactive setup is preferred on shared machines.

If `~/.local/bin` is not on `PATH`, add it to the shell profile and restart the shell. Restart Codex if the newly installed skill does not appear immediately.

Verify the installation:

```bash
showme doctor
```

When the preference is enabled—or when you explicitly invoke or pin `$showme-publish`—the installed instructions treat publishing as part of completing an HTML report. Codex should upload the finished artifact and return its clickable ShowMe link without requiring a second request to publish it.

## Publishing

Every site must have this basic shape:

```text
report/
├── index.html
└── assets/
    └── chart.png
```

Use relative asset paths such as `./assets/chart.png`. Symbolic links are rejected.

Publish publicly:

```bash
showme publish ./report --title "Benchmark report"
```

Publish privately and let the server generate a passcode:

```bash
showme publish ./report --title "Private analysis" --private
```

The generated passcode is shown once. A specific passcode can be supplied with `--passcode`, although letting ShowMe generate one is preferred.

For reliable agent automation, add `--json`:

```bash
showme publish ./report --title "Benchmark report" --json
```

Add searchable context explaining why the report was created:

```bash
showme publish ./report --title "Benchmark report" \
  --description "Created to compare regional latency regressions and prioritize follow-up work."
```

For descriptions that are easier to prepare as text files, use `--description-file PATH`. Descriptions are limited to 2,000 characters, appear on the dashboard, and are included in dashboard search. The installed Codex skill generates a concise description from the report request automatically.

## Managing pages

All operations are available from the dashboard. Common CLI commands are:

```bash
showme list
showme update PAGE_ID ./updated-report
showme visibility PAGE_ID private
showme visibility PAGE_ID public
showme archive PAGE_ID
showme restore PAGE_ID
showme delete PAGE_ID
showme open PAGE_ID
showme details PAGE_ID --description "Created to investigate August latency regressions."
```

Use **Edit details** in a page's dashboard menu to change its name or description. The name is also used as the published page's browser-tab title; ShowMe applies it when serving the page and does not rewrite the uploaded HTML file or body. Descriptions are dashboard-only metadata and are included in search.

`showme details` changes the same dashboard metadata without replacing page content. It accepts `--title`, `--description`, or `--description-file`. Content updates preserve the existing description unless one of the description options is supplied explicitly.

Use **Download source** in the same menu to retrieve the original uploaded directory as a ZIP. The archive contains a single named folder with `index.html` and all relative assets, ready to edit locally and publish again. Downloads require an administrator dashboard session, including for public pages.

Changing a public page to private generates a new passcode unless `--passcode` is supplied. Changing it back to public removes the existing passcode. Archiving disables the URL but retains the files. Deletion permanently removes the record and files after confirmation.

To replace a page's HTML and assets while preserving its URL, visibility, passcode, and archive state:

```bash
showme update PAGE_ID ./updated-report
showme update https://showme.example.com/p/PAGE_ID/ ./updated-report --title "Revised report"
```

The new directory is validated and then swapped into place atomically. A rejected or interrupted upload leaves the existing page content in place.

### Remote-first workspaces

The installed Codex skill creates reports in ShowMe-managed temporary workspaces. After a successful publish or update, it removes that workspace; after a failure, it retains the workspace and reports its path for recovery. ShowMe refuses to clean ordinary directories.

The underlying commands are also available directly:

```bash
showme workspace create
showme workspace pull PAGE_ID
showme workspace clean /path/printed/by/showme
```

To keep an editable local project, pull it to an explicit new directory:

```bash
showme pull PAGE_ID ./retained-report
cd ./retained-report
# edit index.html and its assets
showme update .
```

`showme pull` writes a `.showme.json` manifest linking the project to its server page. ShowMe excludes this manifest, its workspace marker, and `.showme-description.txt` drafts from uploaded assets. A page ID or full ShowMe URL can be used wherever `PAGE_ID` appears above.

## API tokens

Open **API tokens** in the dashboard to create a separate token for each computer. The token name becomes that machine's label on newly published pages, which makes the dashboard's machine filter useful. Setup never creates one automatically. Raw tokens are displayed once with a **Copy token** button; only their SHA-256 hashes are stored by the server. Revoking one machine's token does not affect other machines or existing pages.

The local CLI owns the credential. The Codex skill instructs agents not to read or print the configuration file.

## Administrator recovery

There is no email recovery dependency. From the Docker host, generate a one-time recovery URL:

```bash
docker compose exec showme python -m showme_server.admin reset-password
```

Opening that URL lets you set a new password and logs out existing dashboard sessions.

## Local removal

From any directory after installation:

```bash
showme uninstall
```

Or from a repository clone:

```bash
./uninstall.sh
```

The uninstaller removes the CLI, global skill, and only the marked ShowMe block in the global `AGENTS.md`. It asks whether to retain the saved server URL and API token.

Non-interactive variants:

```bash
showme uninstall --keep-config
showme uninstall --all
```

Local removal never deletes remote pages or stops the remote server.

## Server operations

Upgrade after pulling a new version:

```bash
docker compose up -d --build
```

Then update each previously installed publishing machine without changing its saved server, API token, or Codex preference:

```bash
git pull
./install.sh --upgrade
```

Restart Codex after upgrading so it loads the revised publishing skill. Existing reports and URLs do not need to be re-uploaded; their editable source is already stored in the persistent server volume.

Back up the persistent application volume while the app is stopped, or copy `/data` from the `showme` container. It contains SQLite metadata, uploaded sites, and server secrets.

Stop the server while preserving data:

```bash
docker compose down
```

Permanently remove the server and all published pages:

```bash
docker compose down -v
```

The `-v` operation is destructive and cannot be undone without a backup.

## Development checks

Use the machine's Python environment, install the development requirements, and run:

```bash
pip install -r requirements-dev.txt
pytest -q
SHOWME_HOST=showme.example.com docker compose config -q
```

Build the production image with:

```bash
docker build -t showme .
```
