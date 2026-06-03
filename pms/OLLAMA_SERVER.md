# Ollama on server (not on your laptop)

PMS calls Ollama over HTTP using `OLLAMA_BASE_URL` in `pms/.env`.

## Why Swagger shows 502 from your PC

If Ollama works in the browser **on the server** at `http://127.0.0.1:11434` but Django on your **laptop** uses `http://192.168.1.240:11434` and gets *connection refused*, Ollama is only bound to **localhost on the server**. Other machines cannot connect until you expose port 11434 on the LAN.

## Fix on the Ollama server (rama / 192.168.1.240)

### Linux (systemd)

```bash
sudo systemctl edit ollama
```

Add:

```ini
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

### Or one-off

```bash
export OLLAMA_HOST=0.0.0.0:11434
ollama serve
```

### Firewall

```bash
sudo ufw allow 11434/tcp
# or firewalld equivalent
```

### Verify **from your laptop** (not only on the server)

```powershell
Test-NetConnection 192.168.1.240 -Port 11434
Invoke-RestMethod http://192.168.1.240:11434/api/tags
```

`TcpTestSucceeded` must be **True** and `/api/tags` must list `gemma4:e2b`.

Then on the laptop:

```bash
cd pms
python manage.py test_ollama
```

Restart Django and call `GET /api/v1/admin/ai/health`.

## `.env` for server-only Ollama

```env
OLLAMA_BASE_URL=http://192.168.1.240:11434
OLLAMA_MODEL=gemma4:e2b
OLLAMA_TIMEOUT=180
```

## If PMS Django runs on the **same** machine as Ollama

Use localhost **only in that server’s** `.env` (not on your dev laptop):

```env
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma4:e2b
```

## Dev laptop cannot reach LAN Ollama

Use an SSH tunnel:

```bash
ssh -L 11434:127.0.0.1:11434 user@192.168.1.240
```

Then on the laptop:

```env
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=gemma4:e2b
```

Django talks to localhost; SSH forwards to server Ollama.
