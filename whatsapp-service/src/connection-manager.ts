import makeWASocket, {
  DisconnectReason,
  fetchLatestBaileysVersion,
  useMultiFileAuthState,
  isJidBroadcast,
  isJidGroup,
  WASocket,
  proto,
} from '@whiskeysockets/baileys';
import QRCode from 'qrcode';
import pino from 'pino';
import path from 'path';
import fs from 'fs';
import { publish, redis } from './redis-client';
import { IncomingMessage, InstanceStatus } from './types';

const logger = pino({ name: 'connection-manager', level: 'info' });
const SESSIONS_DIR = path.resolve(process.env.SESSIONS_DIR || './sessions');
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY_MS = 30_000;

interface InstanceEntry {
  socket: WASocket;
  status: InstanceStatus['status'];
  phone?: string;
  reconnectAttempts: number;
  reconnectTimer?: ReturnType<typeof setTimeout>;
}

const instances = new Map<string, InstanceEntry>();

export function getSocket(instanceId: string): WASocket | undefined {
  return instances.get(instanceId)?.socket;
}

export function getInstances(): InstanceStatus[] {
  return Array.from(instances.entries()).map(([id, entry]) => ({
    id,
    status: entry.status,
    phone: entry.phone,
  }));
}

export function getInstanceStatus(instanceId: string): InstanceStatus | null {
  const entry = instances.get(instanceId);
  if (!entry) return null;
  return { id: instanceId, status: entry.status, phone: entry.phone };
}

export async function connectInstance(instanceId: string): Promise<void> {
  if (instances.has(instanceId)) {
    const entry = instances.get(instanceId)!;
    if (entry.status === 'connected' || entry.status === 'connecting') {
      logger.info({ instanceId }, 'Instance already connecting/connected');
      return;
    }
  }

  logger.info({ instanceId }, 'Starting WhatsApp connection');
  await _createSocket(instanceId, 0);
}

export async function disconnectInstance(instanceId: string): Promise<void> {
  const entry = instances.get(instanceId);
  if (!entry) return;

  if (entry.reconnectTimer) clearTimeout(entry.reconnectTimer);
  entry.socket.end(undefined);
  instances.delete(instanceId);

  // Remove session files so QR is required next time
  const sessionPath = path.join(SESSIONS_DIR, instanceId);
  if (fs.existsSync(sessionPath)) {
    fs.rmSync(sessionPath, { recursive: true, force: true });
  }

  await publish('whatsapp:status', { instanceId, status: 'disconnected' });
  logger.info({ instanceId }, 'Instance disconnected and session cleared');
}

export async function reconnectExistingSessions(): Promise<void> {
  if (!fs.existsSync(SESSIONS_DIR)) {
    fs.mkdirSync(SESSIONS_DIR, { recursive: true });
    return;
  }
  const dirs = fs.readdirSync(SESSIONS_DIR, { withFileTypes: true })
    .filter((d) => d.isDirectory())
    .map((d) => d.name);

  for (const instanceId of dirs) {
    logger.info({ instanceId }, 'Auto-reconnecting saved session');
    // Small delay to avoid hammering at startup
    await new Promise((r) => setTimeout(r, 2000));
    await connectInstance(instanceId);
  }
}

async function _createSocket(instanceId: string, reconnectAttempt: number): Promise<void> {
  const sessionPath = path.join(SESSIONS_DIR, instanceId);
  fs.mkdirSync(sessionPath, { recursive: true });

  const { state, saveCreds } = await useMultiFileAuthState(sessionPath);
  const { version } = await fetchLatestBaileysVersion();

  const sock = makeWASocket({
    version,
    auth: state,
    printQRInTerminal: false,
    logger: pino({ level: 'silent' }) as any,
    browser: ['WA Hub', 'Chrome', '120.0.0'],
    connectTimeoutMs: 60_000,
    defaultQueryTimeoutMs: 60_000,
    keepAliveIntervalMs: 25_000,
    generateHighQualityLinkPreview: false,
  });

  const entry: InstanceEntry = {
    socket: sock,
    status: 'connecting',
    reconnectAttempts: reconnectAttempt,
  };
  instances.set(instanceId, entry);

  // Persist credentials whenever they change
  sock.ev.on('creds.update', saveCreds);

  // Handle connection state changes
  sock.ev.on('connection.update', async (update) => {
    const { connection, lastDisconnect, qr } = update;

    // QR code received — convert to base64 PNG and publish
    if (qr) {
      logger.info({ instanceId }, 'QR code received');
      try {
        const base64 = await QRCode.toDataURL(qr, {
          type: 'image/png',
          width: 300,
          margin: 2,
          color: { dark: '#000000', light: '#FFFFFF' },
        });
        entry.status = 'qr';
        await publish('whatsapp:qr', { instanceId, qr: base64 });
        await publish('whatsapp:status', { instanceId, status: 'qr' });
      } catch (err) {
        logger.error({ instanceId, err }, 'Failed to generate QR image');
      }
    }

    if (connection === 'open') {
      const jid = sock.user?.id ?? '';
      const phone = jid.split(':')[0].replace('@s.whatsapp.net', '');
      entry.status = 'connected';
      entry.phone = phone;
      entry.reconnectAttempts = 0;
      logger.info({ instanceId, phone }, 'WhatsApp connected!');
      await publish('whatsapp:status', { instanceId, status: 'connected', phone });
    }

    if (connection === 'close') {
      const code = (lastDisconnect?.error as any)?.output?.statusCode;
      const loggedOut = code === DisconnectReason.loggedOut;
      logger.warn({ instanceId, code, loggedOut }, 'Connection closed');

      entry.status = 'disconnected';
      await publish('whatsapp:status', { instanceId, status: 'disconnected' });

      if (loggedOut) {
        // Remove session so QR is needed again
        const sessionPath2 = path.join(SESSIONS_DIR, instanceId);
        if (fs.existsSync(sessionPath2)) {
          fs.rmSync(sessionPath2, { recursive: true, force: true });
        }
        instances.delete(instanceId);
        return;
      }

      // Try reconnecting
      if (entry.reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
        entry.reconnectAttempts++;
        logger.info({ instanceId, attempt: entry.reconnectAttempts }, `Reconnecting in ${RECONNECT_DELAY_MS / 1000}s`);
        await publish('whatsapp:status', { instanceId, status: 'reconnecting' });
        entry.reconnectTimer = setTimeout(() => {
          _createSocket(instanceId, entry.reconnectAttempts);
        }, RECONNECT_DELAY_MS);
      } else {
        logger.error({ instanceId }, 'Max reconnect attempts reached');
        instances.delete(instanceId);
      }
    }
  });

  // Handle incoming messages
  sock.ev.on('messages.upsert', async ({ messages, type }) => {
    if (type !== 'notify') return;

    for (const msg of messages) {
      try {
        await _handleIncomingMessage(instanceId, msg);
      } catch (err) {
        logger.error({ instanceId, err }, 'Error handling message');
      }
    }
  });
}

async function _handleIncomingMessage(instanceId: string, msg: proto.IWebMessageInfo): Promise<void> {
  // Ignore own messages, broadcasts, status
  if (msg.key.fromMe) return;
  const from = msg.key.remoteJid ?? '';
  if (!from || isJidBroadcast(from) || from === 'status@broadcast') return;

  const isGroup = isJidGroup(from);
  const groupId = isGroup ? from : null;

  // Extract text content
  const text =
    msg.message?.conversation ||
    msg.message?.extendedTextMessage?.text ||
    msg.message?.imageMessage?.caption ||
    msg.message?.videoMessage?.caption ||
    '';

  if (!text.trim()) return;

  // Get sender name
  const fromName =
    msg.pushName ||
    (isGroup ? msg.key.participant?.split('@')[0] : from.split('@')[0]) ||
    'Unknown';

  const payload: IncomingMessage = {
    instanceId,
    from: isGroup ? (msg.key.participant ?? from) : from,
    fromName,
    message: text.trim(),
    messageType: Object.keys(msg.message ?? {})[0] ?? 'text',
    timestamp: (msg.messageTimestamp as number) ?? Math.floor(Date.now() / 1000),
    isGroup,
    groupId,
    messageId: msg.key.id ?? '',
  };

  // Deduplicação — ignorar se o mesmo messageId já foi publicado nos últimos 60s
  if (msg.key.id) {
    const dedupeKey = `dedup:${instanceId}:${msg.key.id}`;
    const alreadySeen = await redis.get(dedupeKey);
    if (alreadySeen) {
      logger.debug({ instanceId, msgId: msg.key.id }, 'Duplicate message ignored');
      return;
    }
    await redis.set(dedupeKey, '1', 'EX', 60);
  }

  logger.info({ instanceId, from: payload.from, isGroup }, 'Incoming message');
  await publish('whatsapp:incoming', payload);
}
