# Oracle Cloud Always Free — Deployment Guide

## What you'll have

A single Oracle Cloud ARM VM running both app containers and Caddy, accessible over HTTPS. $0/month, no expiry, no credit card charges.

```
https://YOUR-IP.nip.io
    └── Caddy (host process, auto-TLS via Let's Encrypt)
            ├── /api/*  →  localhost:8000  (api container)
            └── /*      →  localhost:5173  (web container)

~/app/data/ on 50 GB boot disk  (SQLite, receipts, WhatsApp session)
```

---

## Why Oracle Cloud

This app requires a **persistent process** for the WhatsApp WebSocket (neonize). Serverless platforms — Lambda, Cloud Run, App Runner — terminate idle processes, which breaks the WhatsApp connection. Only a long-running VM works.

| Platform | RAM | Egress free | Cost | Expiry |
|----------|-----|-------------|------|--------|
| **Oracle Cloud ARM A1** | **24 GB** | **10 TB/month** | **$0** | **Never** |
| GCP e2-micro | 1 GB | 1 GB/month | $0 | Never |
| AWS t2.micro | 1 GB | 15 GB/month | $0 | 12 months only |

GCP's e2-micro (1 GB) is too tight once Docker + FastAPI + neonize are loaded. Oracle's ARM A1 (24 GB) runs the full stack with room to spare.

`neonize` ships a `py3-none-any` wheel — no platform-specific binary, ARM64 compatible.

---

## Staying free — the guardrails

**Always Free resources this deployment uses:**

| Resource | Used | Always Free limit |
|----------|------|------------------|
| VM.Standard.A1.Flex | 4 OCPU + 24 GB | 4 OCPU + 24 GB total |
| Boot volume | 50 GB standard | 200 GB total |
| Public IP (reserved) | 1 | 2 (free even when unattached) |
| Egress | < 1 GB/month | 10 TB/month |

**How Oracle billing works:** Oracle does not auto-upgrade you. Charges require you to manually click "Upgrade to Pay As You Go" in the console. Always Free resources remain free regardless of what else you do in the account.

**Do not create:** load balancers, additional VMs beyond the A1 pool, block volumes beyond 200 GB total.

---

## Prerequisites

- Oracle Cloud free tier account (credit card required for identity verification only)
- OCI CLI configured and authenticated: `oci iam user get --user-id YOUR_OCID` returns 200
- SSH key at `~/.ssh/id_ed25519` (created by `ssh-keygen -t ed25519` if missing)
- `rsync` and `ssh` on your Mac (pre-installed on macOS)

---

## One-time VM setup

### 1. Create the VM

**Via the retry script (recommended):**

`scripts/create-vm.sh` wraps the OCI CLI launch call in a loop that retries every 60 s until Oracle has capacity — the most reliable way to handle the ARM supply constraint.

```bash
# Collect these four values first (commands in the script header show how):
export OCI_TENANCY=ocid1.tenancy.oc1..xxx
export OCI_SUBNET=ocid1.subnet.oc1.ca-montreal-1.xxx
export OCI_IMAGE=ocid1.image.oc1.ca-montreal-1.xxx   # Ubuntu 22.04 aarch64
export OCI_AD="OSct:CA-MONTREAL-1-AD-1"
export OCI_REGION=ca-montreal-1

bash scripts/create-vm.sh
```

The script prints progress on every attempt and, when it succeeds, prints the public IP and the exact `make bootstrap` / `make deploy` commands to run next.

> **ARM capacity errors are normal.** Oracle ARM A1 free tier frequently returns `"Out of host capacity"`. This is a supply issue on Oracle's side — not an error you caused.

**If your home region has persistent capacity issues:** Always Free VMs can only be created in the home region, and the home region is locked after account creation. The workaround is a new Oracle Cloud account (different email, or `+alias` on the same Gmail) with a better home region. Regions with historically better A1 availability: `eu-frankfurt-1` (Frankfurt), `ap-singapore-1` (Singapore), `us-ashburn-1` or `us-phoenix-1` (US — 3 availability domains each, giving more retry chances). Set `OCI_REGION` accordingly when running `create-vm.sh`.

**Via OCI console:**
- Shape: VM.Standard.A1.Flex → 4 OCPU, 24 GB
- Image: Canonical Ubuntu 22.04 Minimal aarch64
- Boot volume: 50 GB
- Networking: let Oracle create a default VCN
- SSH key: paste contents of `~/.ssh/id_ed25519.pub`
- Do **not** set a fault domain — leave it for Oracle to choose

**Open ports in the OCI Security List (VCN firewall):**

Navigate to: VCN → Security Lists → Default Security List → Add Ingress Rules:
- Source `0.0.0.0/0`, Protocol TCP, Port 80
- Source `0.0.0.0/0`, Protocol TCP, Port 443

### 2. Bootstrap the VM (once)

```bash
make bootstrap ORACLE_IP=x.x.x.x
```

This installs: Docker, Docker Compose plugin, Caddy, `netfilter-persistent`, opens ports 80/443 in the OS iptables (Oracle Ubuntu blocks them by default), configures 2 GB swap, and sets up Caddy as a systemd service reading `DOMAIN` from `~/app/.env`.

After it completes, **log out and back in** to pick up the `docker` group membership.

### 3. Prepare the production `.env`

```bash
cp .env.prod.example .env.prod
# Edit .env.prod — replace ORACLE_IP throughout, fill in secrets:
#   DOMAIN=1.2.3.4.nip.io
#   WEB_ORIGIN=https://1.2.3.4.nip.io
#   GOOGLE_REDIRECT_URI=https://1.2.3.4.nip.io/api/google/callback
#   CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-...   (or ANTHROPIC_API_KEY)

scp .env.prod ubuntu@x.x.x.x:~/app/.env
```

The `.env` file is intentionally excluded from `make deploy` so prod secrets are never overwritten by your local dev values.

### 4. First deploy

```bash
make deploy ORACLE_IP=x.x.x.x      # rsync + make restart on VM
ssh ubuntu@x.x.x.x "sudo systemctl restart caddy"
```

The first Docker build takes 15–20 minutes on the ARM VM (Poetry + PyMuPDF + neonize). Subsequent deploys reuse the Docker layer cache and complete in 1–3 minutes.

When done, open `https://YOUR-IP.nip.io`. The browser should reach the app over HTTPS.

### 5. Post-deploy one-time config

**WhatsApp:** Settings → WhatsApp → Link account → scan QR within 20 s.  
See [whatsapp-setup.md](../whatsapp-setup.md) for the full pairing guide.

**Google Drive/Sheets:** If you use Google sync, add `https://YOUR-IP.nip.io/api/google/callback` to the authorized redirect URIs in your Google Cloud Console OAuth client, then reconnect in Settings → Google sync.  
See [google-drive-setup.md](../google-drive-setup.md).

---

## Day-to-day operations

```bash
# Deploy a code change from your Mac
make deploy ORACLE_IP=x.x.x.x

# Tail logs
ssh ubuntu@x.x.x.x "cd ~/app && make logs"
ssh ubuntu@x.x.x.x "cd ~/app && make logs-api"

# Restart containers (e.g. after editing .env on the VM)
ssh ubuntu@x.x.x.x "cd ~/app && make restart"

# Container status
ssh ubuntu@x.x.x.x "cd ~/app && make status"
```

On the VM itself, all standard `make` targets work identically to local: `make start`, `make stop`, `make restart`, `make logs`, `make logs-api`, `make cleanup`.

---

## Backup

All persistent data lives in `~/app/data/`: `expense.db`, `receipts/`, and `whatsapp/` (the WhatsApp session — losing this requires re-pairing).

**Simplest: rsync to your Mac on demand**
```bash
rsync -az ubuntu@x.x.x.x:~/app/data/ ./data-backup/
```

**Automated: daily cron on the VM to OCI Object Storage (10 GB free tier)**
```bash
# SSH to VM, then add to crontab -e:
0 2 * * * oci os object put --bucket-name expense-backup \
  --file ~/app/data/expense.db \
  --name "expense-$(date +\%Y\%m\%d).db" 2>/dev/null
```

---

## Troubleshooting

**ARM A1 "Out of host capacity"**  
Supply issue on Oracle's side. Use `scripts/create-vm.sh` — it retries automatically every 60 s. If your home region has persistent congestion, create a new Oracle account with a better home region (e.g. `eu-frankfurt-1`, `us-ashburn-1`). Always Free VMs are locked to the home region; the home region itself cannot be changed on an existing account.

**Caddy not serving HTTPS**  
Check `sudo systemctl status caddy`. If `DOMAIN` is missing from the env, Caddy falls back to `localhost`. Verify `~/app/.env` contains `DOMAIN=...` and run `sudo systemctl restart caddy`.

**Port 80 or 443 unreachable**  
Two independent firewalls must both allow the port:
1. OCI Security List (VCN → Security Lists → ingress rules)
2. OS iptables (`sudo iptables -L INPUT -n --line-numbers`)

Run `sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80 -j ACCEPT` and repeat for 443, then `sudo netfilter-persistent save`.

**`make deploy` fails with "Host key verification failed"**  
SSH to the VM manually first to accept the host key: `ssh ubuntu@x.x.x.x`.

**Docker build runs out of memory**  
The 2 GB swap configured by bootstrap.sh handles this for normal builds. If a build still OOMs, run `make restart` instead (reuses existing images without rebuilding).

**WhatsApp QR expired**  
QR codes expire ~20 s after issue. Settings → Refresh QR. See [whatsapp-setup.md](../whatsapp-setup.md).

**OCI CLI returns 401 after setup**  
The most common cause is a fingerprint mismatch between `~/.oci/config` and the API key uploaded in Oracle Cloud. Verify: compute `openssl rsa -pubout -outform DER -in ~/.oci/oci_api_key.pem -passin pass:YOUR_PASSPHRASE | openssl dgst -md5 -c` and confirm it matches the fingerprint shown under Profile → User Settings → API Keys in the console.
