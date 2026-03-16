# WA Hub — Backend

Backend completo para automação de WhatsApp com IA.

## Stack
- **Backend**: FastAPI (Python 3.11+)
- **WhatsApp**: Baileys (Node.js)
- **Banco**: SQLite
- **Fila**: Redis
- **Frontend**: Next.js no Vercel (separado)

## Subir com Docker

```bash
# 1. Clonar
git clone https://github.com/juniorjhunpt/whatsapp-automation-backend.git
cd whatsapp-automation-backend

# 2. Configurar
cp .env.example .env

# 3. Subir tudo
docker compose up -d --build

# 4. Verificar
docker compose ps
curl http://localhost:8000/api/health
curl http://localhost:3001/health
```

## Portas
| Serviço | Porta |
|---------|-------|
| Backend FastAPI | 8000 |
| WhatsApp Service | 3001 |
| Redis | 6379 |

## Liberar portas na VPS
```bash
ufw allow 8000/tcp
ufw allow 3001/tcp
ufw reload
```

## Configurar Frontend (Vercel)
Em Settings → Environment Variables:
```
NEXT_PUBLIC_API_URL=http://SEU_IP:8000
NEXT_PUBLIC_WS_URL=ws://SEU_IP:8000
```

## Comandos úteis
```bash
docker compose logs -f          # Ver logs em tempo real
docker compose restart          # Reiniciar tudo
docker compose down             # Parar tudo
docker compose up -d --build    # Reconstruir e subir
```
