# Known issues

Problems found while setting up and running Agent Company, with their status.
Update this file when one is fixed or a new one is found.

Status: **open** (not solved), **mitigated** (worked around, risk remains), **fixed**.

## GitHub and Git

| # | Problem | Status |
|---|---------|--------|
| G1 | Every push from the website used the server's single GitHub login (`gh`, account `thomaschang0912`), whatever user or project. Pushing to a repository that account cannot write to fails with 403. This is how pushing this repository to `MexcSell/Agent_Company` failed on 2026-11-04. | fixed for this repository (deploy key, see G2); website projects: see G3 |
| G2 | This repository pushes through its own deploy key (`~/.ssh/id_ed25519_agent_company`, set in `.git/config` only). The key lives on the server; whoever has root can push with it. | mitigated |
| G3 | Users have a Git name, email and branch (Users → Git), but no GitHub credentials. Work is committed under the right person but pushed with the server login. | fixed: each user has their own GitHub token or SSH key (Git settings); see `gitsync.py` |
| G4 | Whose settings a push uses. Decision: automatic push after approval uses the user who ordered the work; a manual Sync uses the person who clicked it. Without a credential of their own, the server login is used and the result says so. | by design |
| G5 | Only administrators can set Git settings, so every user has to ask an administrator to change their name, email or branch. | fixed: users edit their own (Git button next to their name) |

## Connection (HTTPS / HTTP)

| # | Problem | Status |
|---|---------|--------|
| C1 | In HTTP mode the device secret crosses the network unencrypted on every visit. An eavesdropper can sign in as that user permanently. Sealing the cookie does not help against this. HTTPS is the default for this reason. | open (by design, HTTP is optional) |
| C2 | The certificate comes from the company's own CA. Every computer has to install the CA once; without it browsers warn and Chrome turns its cache off, which makes pages slow. | mitigated (download and instructions on Settings → Connection) |
| C3 | The server certificate is renewed only when the server starts (it is valid 825 days, renewed when under 30 days remain). A server that runs for more than two years without a restart could serve an expired certificate. | open (low) |
| C4 | Tabs opened before a scheme switch run the old JavaScript and do not follow the hand-off; they must be reloaded once. `claude.js` stops polling on 401. | open (low) |
| C5 | No HSTS on purpose: it would make the HTTP option unusable for the HSTS lifetime. Do not add it while the HTTP option exists. | by design |
| C6 | During the first switch to nginx, the old server process picked up the new `.env` while still holding port 4900, so nginx briefly proxied to itself. | fixed (`transport.apply` refuses PORT == PUBLIC_PORT) |
| C7 | nginx (user `www-data`) cannot read `/home/luca` (mode 750), so it cannot serve `/static` from disk. | mitigated (static files are proxied through the app with cache headers) |
| C8 | `systemctl reload nginx` returns before the new workers serve, so a browser switching at that moment could hit the old mode. | fixed (`transport._wait_ready`) |

## Accounts and access

| # | Problem | Status |
|---|---------|--------|
| A1 | The first account becomes administrator (with a root shell in the Workspace). With the port open to the internet, a stranger could have claimed it. | fixed (first account only from localhost) |
| A2 | Creating that first account needs a port forward (SSH or VS Code) to `localhost:4900`. | by design |
| A3 | Settings and the Users page are admin-only. `Luca` is not an administrator, so that browser does not show them. Fix: as `Admin`, Users → *Make admin* on `Luca`. (Earlier notes wrongly said this button was missing.) | open (needs an administrator's click) |
| A4 | `localStorage` is per origin (scheme + host + port), so the same browser on `http://`, `https://`, `localhost` and the IP address counts as different computers. The hand-off covers HTTP ↔ HTTPS only. | open (low) |

## Server (outside this project)

| # | Problem | Status |
|---|---------|--------|
| S1 | The firewall (ufw) is inactive and the server has a public IP. Besides Agent Company, these listen on every interface: Samba (139, 445), nginx (80, 8081), 3000, 4800, 8000, 8082, k3s (6443, 10250), Cockpit (9090). Check that each is meant to be public, or block it at the hosting provider's firewall. | open |
