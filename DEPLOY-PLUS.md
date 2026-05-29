# Deploy Omakase Plus (private — LAN only)

Deploy to CT 101 (docker-edge LXC, 192.168.50.141) on the optiplex node.

## One-time setup

```bash
# SSH to optiplex, then enter CT 101
ssh root@192.168.50.103
pct enter 101

# Create the stack directory
mkdir -p /opt/stacks/omakase-plus/data
# Container runs as UID 1000 (the `omakase` user from the Dockerfile);
# the bind-mounted data dir must be writable by that UID or SQLite will
# fail with "unable to open database file" on first signup.
chown -R 1000:1000 /opt/stacks/omakase-plus/data
cd /opt/stacks/omakase-plus

# Create .env file (fill in real values)
cat > .env << 'EOF'
OMAKASE_PLUS_MASTER_KEY=<generate: python -c "import secrets; print(secrets.token_hex(32))">
OMAKASE_SEED_EMAIL=<your email>
OMAKASE_SEED_PASSWORD=<strong password>
ANILIST_CLIENT_ID=
ANILIST_CLIENT_SECRET=
EOF

# Copy compose-plus.yaml from the repo
# (rsync from your workstation or git clone)
git clone --depth 1 -b plus-mvp https://github.com/HeyiTzSenpai/omakase.git /tmp/omakase-plus-src
cp /tmp/omakase-plus-src/compose-plus.yaml .

# Build and start
docker compose -f compose-plus.yaml up -d --build
```

## Seed your account

```bash
docker exec omakase-plus python -m omakase.plus.admin seed-user
```

## Access

Open `http://192.168.50.141:8766/plus/login` from any device on your LAN.

## Update

```bash
cd /opt/stacks/omakase-plus

if [ ! -d /tmp/omakase-plus-src/.git ]; then
  rm -rf /tmp/omakase-plus-src
  git clone --depth 1 -b plus-mvp https://github.com/HeyiTzSenpai/omakase.git /tmp/omakase-plus-src
else
  git -C /tmp/omakase-plus-src pull --ff-only origin plus-mvp
fi

cp /tmp/omakase-plus-src/compose-plus.yaml .
docker compose -f compose-plus.yaml up -d --build
```

## Notes

- Port 8766 is LAN-only. On the homelab it is also available through the LAN-only NPM host `anime.jhinx.dev`.
- `OMAKASE_PLUS_PRIVATE=true` gates all Plus routes
- Data persists in `/opt/stacks/omakase-plus/data/`
- The public demo at omakase.jhinx.dev (port 8765) is unaffected
