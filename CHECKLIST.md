# New VPS

## Secure SSH access
```bash
ssh-copy-id vivek@HOST                       # from your machine
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/; s/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config
sudo systemctl reload ssh
sudo ufw allow OpenSSH && sudo ufw enable    # tunnel needs no inbound ports
```

## Install uv
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh && exec $SHELL
```

## Install node
```bash
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo bash - && sudo apt install -y nodejs
```

## Install gh
```bash
sudo apt install -y gh && gh auth login
```

## Clone + test
```bash
gh repo clone waivek/crk-main ~/crk-main && cd ~/crk-main
uv sync && uv run pytest tools/todo/tests -q
```

## uv dependencies
Installed by `uv sync` (from `pyproject.toml`):
- `flask`, `flask-cors`, `gunicorn`: web app
- `requests`, `bs4`, `box`: cookie scrapers
- `pytest` (dev)

## Copy data (optional)
```bash
rsync -a OLD_HOST:crk-main/tools/todo/data/ ~/crk-main/tools/todo/data/
```

## Run gunicorn
`/etc/systemd/system/crk.service`:
```ini
[Service]
User=vivek
WorkingDirectory=/home/vivek/crk-main
ExecStart=/home/vivek/.local/bin/uv run gunicorn --bind 127.0.0.1:5183 --workers 1 --access-logfile - crk-main-api:app
Restart=on-failure
[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl enable --now crk              # restart: sudo systemctl restart crk
```

## Install cloudflared
```bash
curl -fsSLo /tmp/cf.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb && sudo dpkg -i /tmp/cf.deb
cloudflared tunnel login && cloudflared tunnel create crk && cloudflared tunnel route dns crk crk.stardews.com
```
`/etc/cloudflared/config.yml`:
```yaml
tunnel: crk
credentials-file: /home/vivek/.cloudflared/<TUNNEL-ID>.json
ingress:
  - hostname: crk.stardews.com
    service: http://localhost:5183
  - service: http_status:404
```
```bash
sudo cloudflared service install
```
