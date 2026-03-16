#!/bin/bash
# WA Hub — Backup diário automático para GitHub
# Corre à meia-noite via cron
# Guarda: código + base de dados + sessões WhatsApp

set -e

PROJECT_DIR="/root/whatsapp-automation-backend"
BACKUP_BRANCH="backups"
DATE=$(date +%Y-%m-%d)
TIMESTAMP=$(date +%Y-%m-%d_%H-%M)
BACKEND_CONTAINER="whatsapp-automation-backend-backend-1"
WA_CONTAINER="whatsapp-automation-backend-whatsapp-service-1"

log() { echo "[$(date '+%H:%M:%S')] $1"; }

log "=== Backup WA Hub: $TIMESTAMP ==="

cd "$PROJECT_DIR"

# 1. Garantir que estamos no branch main actualizado
git fetch origin
git checkout main
git pull origin main

# 2. Mudar para branch de backups (cria se não existir)
git checkout "$BACKUP_BRANCH" 2>/dev/null || git checkout -b "$BACKUP_BRANCH"
git merge main --no-edit 2>/dev/null || true

# 3. Criar pasta de backup do dia
BACKUP_DIR="backup-data/$DATE"
mkdir -p "$BACKUP_DIR"

# 4. Fazer dump da base de dados SQLite
log "A copiar base de dados..."
docker cp "$BACKEND_CONTAINER:/app/data/wahub.db" "$BACKUP_DIR/wahub.db" 2>/dev/null && \
  log "✅ DB copiada" || log "⚠️  DB não encontrada (possível 1ª execução)"

# 5. Fazer backup das sessões WhatsApp
log "A copiar sessões WhatsApp..."
docker exec "$WA_CONTAINER" tar -czf /tmp/sessions-backup.tar.gz -C /app sessions 2>/dev/null && \
  docker cp "$WA_CONTAINER:/tmp/sessions-backup.tar.gz" "$BACKUP_DIR/sessions.tar.gz" 2>/dev/null && \
  log "✅ Sessões copiadas" || log "⚠️  Sessões não encontradas"

# 6. Gerar relatório do backup
cat > "$BACKUP_DIR/info.txt" << EOF
Backup: $TIMESTAMP
Containers:
$(docker ps --format "  {{.Names}}: {{.Status}}" 2>/dev/null)
DB size: $(du -sh "$BACKUP_DIR/wahub.db" 2>/dev/null | cut -f1 || echo "N/A")
Sessions: $(du -sh "$BACKUP_DIR/sessions.tar.gz" 2>/dev/null | cut -f1 || echo "N/A")
EOF

# 7. Guardar no Git com tag de data
git add -A
git commit -m "backup: $TIMESTAMP" 2>/dev/null || log "Nada para commitar"

# Criar tag com data (sobrescreve se já existe)
git tag -f "backup-$DATE"

# 8. Push para GitHub
log "A enviar para GitHub..."
git push origin "$BACKUP_BRANCH" --force
git push origin "backup-$DATE" --force

# 9. Limpar backups com mais de 30 dias
log "A limpar backups antigos..."
cd "$PROJECT_DIR/backup-data"
ls -d 20*/ 2>/dev/null | sort | head -n -30 | xargs -r rm -rf

git -C "$PROJECT_DIR" add -A
git -C "$PROJECT_DIR" commit -m "cleanup: backups > 30 dias" 2>/dev/null || true
git -C "$PROJECT_DIR" push origin "$BACKUP_BRANCH" --force 2>/dev/null || true

# 10. Voltar ao branch main
git -C "$PROJECT_DIR" checkout main

log "=== ✅ Backup concluído: $TIMESTAMP ==="
