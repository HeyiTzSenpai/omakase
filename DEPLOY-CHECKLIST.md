# omakase Plus Deploy Checklist

Local branch: `plus-waitlist`
Status: local-only, awaiting user approval. Do not run these steps without explicit deploy approval.

## Before Deploy

1. Generate a production admin token:

   ```powershell
   python -c "import secrets; print(secrets.token_hex(32))"
   ```

2. Add a Vaultwarden secure note named `omakase-plus-admin` with that token.

3. Confirm the production posture:
   - `OMAKASE_PLUS_OPEN=false` for a waitlist-only launch.
   - `OMAKASE_PLUS_ADMIN_TOKEN=<production token>` in the server `.env`.
   - No `jhinx.dev` strings in the omakase repo source.

4. Push `plus-waitlist`, open a PR, and merge after user review.

## Deploy Recipe

```powershell
ssh heyi@192.168.50.103 'pct exec 101 -- bash -c "
  git clone --depth 1 https://github.com/HeyiTzSenpai/omakase.git /tmp/omakase-new && \
  mkdir -p /opt/stacks/omakase/.backups && \
  tar -czf /opt/stacks/omakase/.backups/pre-deploy-$(date +%Y%m%d-%H%M%S).tar.gz -C /opt/stacks/omakase --exclude=.backups . && \
  rsync -a --delete --exclude=.git --exclude=.backups --exclude=save-screenshot.ps1 /tmp/omakase-new/ /opt/stacks/omakase/ && \
  cd /opt/stacks/omakase && \
  echo OMAKASE_PLUS_ADMIN_TOKEN=<TOKEN> >> .env && \
  echo OMAKASE_PLUS_OPEN=false >> .env && \
  bash /opt/stacks/omakase-overlay/apply.sh && \
  docker compose up -d --build
"'
```

The overlay must run before the Docker build. It patches the source tree, and the build packages those patched files into the installed wheel.

## Verify Live

```powershell
curl.exe -s https://omakase.jhinx.dev/plus | Select-String "We're opening Plus soon"
curl.exe -s https://omakase.jhinx.dev/ | Select-String "jhinx.dev"
```

Then submit one test signup in the browser and confirm the row appears in CT 101's SQLite DB. If verification fails, restore from the `.backups/` tarball and halt.
