"use strict";
var __createBinding = (this && this.__createBinding) || (Object.create ? (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    var desc = Object.getOwnPropertyDescriptor(m, k);
    if (!desc || ("get" in desc ? !m.__esModule : desc.writable || desc.configurable)) {
      desc = { enumerable: true, get: function() { return m[k]; } };
    }
    Object.defineProperty(o, k2, desc);
}) : (function(o, m, k, k2) {
    if (k2 === undefined) k2 = k;
    o[k2] = m[k];
}));
var __setModuleDefault = (this && this.__setModuleDefault) || (Object.create ? (function(o, v) {
    Object.defineProperty(o, "default", { enumerable: true, value: v });
}) : function(o, v) {
    o["default"] = v;
});
var __importStar = (this && this.__importStar) || (function () {
    var ownKeys = function(o) {
        ownKeys = Object.getOwnPropertyNames || function (o) {
            var ar = [];
            for (var k in o) if (Object.prototype.hasOwnProperty.call(o, k)) ar[ar.length] = k;
            return ar;
        };
        return ownKeys(o);
    };
    return function (mod) {
        if (mod && mod.__esModule) return mod;
        var result = {};
        if (mod != null) for (var k = ownKeys(mod), i = 0; i < k.length; i++) if (k[i] !== "default") __createBinding(result, mod, k[i]);
        __setModuleDefault(result, mod);
        return result;
    };
})();
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.markRecentlySent = markRecentlySent;
exports.getSocket = getSocket;
exports.getInstances = getInstances;
exports.getInstanceStatus = getInstanceStatus;
exports.connectInstance = connectInstance;
exports.disconnectInstance = disconnectInstance;
exports.reconnectExistingSessions = reconnectExistingSessions;
const baileys_1 = __importStar(require("@whiskeysockets/baileys"));
const qrcode_1 = __importDefault(require("qrcode"));
const pino_1 = __importDefault(require("pino"));
const path_1 = __importDefault(require("path"));
const fs_1 = __importDefault(require("fs"));
const redis_client_1 = require("./redis-client");
const logger = (0, pino_1.default)({ name: 'connection-manager', level: 'info' });
const SESSIONS_DIR = path_1.default.resolve(process.env.SESSIONS_DIR || './sessions');
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY_MS = 30000;
// Mapa de JIDs para os quais o bot enviou mensagem recentemente (anti-loop)
const recentlySentMap = new Map();
const SENT_COOLDOWN_MS = 15000; // 15 segundos
function markRecentlySent(instanceId, jid) {
    recentlySentMap.set(`${instanceId}:${jid}`, Date.now());
}
function _wasRecentlySent(instanceId, jid) {
    const key = `${instanceId}:${jid}`;
    const ts = recentlySentMap.get(key);
    if (!ts)
        return false;
    if (Date.now() - ts < SENT_COOLDOWN_MS)
        return true;
    recentlySentMap.delete(key);
    return false;
}
const instances = new Map();
function getSocket(instanceId) {
    return instances.get(instanceId)?.socket;
}
function getInstances() {
    return Array.from(instances.entries()).map(([id, entry]) => ({
        id,
        status: entry.status,
        phone: entry.phone,
    }));
}
function getInstanceStatus(instanceId) {
    const entry = instances.get(instanceId);
    if (!entry)
        return null;
    return { id: instanceId, status: entry.status, phone: entry.phone };
}
async function connectInstance(instanceId) {
    if (instances.has(instanceId)) {
        const entry = instances.get(instanceId);
        if (entry.status === 'connected' || entry.status === 'connecting') {
            logger.info({ instanceId }, 'Instance already connecting/connected');
            return;
        }
    }
    logger.info({ instanceId }, 'Starting WhatsApp connection');
    await _createSocket(instanceId, 0);
}
async function disconnectInstance(instanceId) {
    const entry = instances.get(instanceId);
    if (!entry)
        return;
    if (entry.reconnectTimer)
        clearTimeout(entry.reconnectTimer);
    entry.socket.end(undefined);
    instances.delete(instanceId);
    // Remove session files so QR is required next time
    const sessionPath = path_1.default.join(SESSIONS_DIR, instanceId);
    if (fs_1.default.existsSync(sessionPath)) {
        fs_1.default.rmSync(sessionPath, { recursive: true, force: true });
    }
    await (0, redis_client_1.publish)('whatsapp:status', { instanceId, status: 'disconnected' });
    logger.info({ instanceId }, 'Instance disconnected and session cleared');
}
async function reconnectExistingSessions() {
    if (!fs_1.default.existsSync(SESSIONS_DIR)) {
        fs_1.default.mkdirSync(SESSIONS_DIR, { recursive: true });
        return;
    }
    const dirs = fs_1.default.readdirSync(SESSIONS_DIR, { withFileTypes: true })
        .filter((d) => d.isDirectory())
        .map((d) => d.name);
    for (const instanceId of dirs) {
        logger.info({ instanceId }, 'Auto-reconnecting saved session');
        // Small delay to avoid hammering at startup
        await new Promise((r) => setTimeout(r, 2000));
        await connectInstance(instanceId);
    }
}
async function _createSocket(instanceId, reconnectAttempt) {
    const sessionPath = path_1.default.join(SESSIONS_DIR, instanceId);
    fs_1.default.mkdirSync(sessionPath, { recursive: true });
    const { state, saveCreds } = await (0, baileys_1.useMultiFileAuthState)(sessionPath);
    const { version } = await (0, baileys_1.fetchLatestBaileysVersion)();
    const sock = (0, baileys_1.default)({
        version,
        auth: state,
        printQRInTerminal: false,
        logger: (0, pino_1.default)({ level: 'silent' }),
        browser: ['WA Hub', 'Chrome', '120.0.0'],
        connectTimeoutMs: 60000,
        defaultQueryTimeoutMs: 60000,
        keepAliveIntervalMs: 25000,
        generateHighQualityLinkPreview: false,
    });
    const entry = {
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
                const base64 = await qrcode_1.default.toDataURL(qr, {
                    type: 'image/png',
                    width: 300,
                    margin: 2,
                    color: { dark: '#000000', light: '#FFFFFF' },
                });
                entry.status = 'qr';
                await (0, redis_client_1.publish)('whatsapp:qr', { instanceId, qr: base64 });
                await (0, redis_client_1.publish)('whatsapp:status', { instanceId, status: 'qr' });
            }
            catch (err) {
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
            await (0, redis_client_1.publish)('whatsapp:status', { instanceId, status: 'connected', phone });
        }
        if (connection === 'close') {
            const code = lastDisconnect?.error?.output?.statusCode;
            const loggedOut = code === baileys_1.DisconnectReason.loggedOut;
            logger.warn({ instanceId, code, loggedOut }, 'Connection closed');
            entry.status = 'disconnected';
            await (0, redis_client_1.publish)('whatsapp:status', { instanceId, status: 'disconnected' });
            if (loggedOut) {
                // Remove session so QR is needed again
                const sessionPath2 = path_1.default.join(SESSIONS_DIR, instanceId);
                if (fs_1.default.existsSync(sessionPath2)) {
                    fs_1.default.rmSync(sessionPath2, { recursive: true, force: true });
                }
                instances.delete(instanceId);
                return;
            }
            // Try reconnecting
            if (entry.reconnectAttempts < MAX_RECONNECT_ATTEMPTS) {
                entry.reconnectAttempts++;
                logger.info({ instanceId, attempt: entry.reconnectAttempts }, `Reconnecting in ${RECONNECT_DELAY_MS / 1000}s`);
                await (0, redis_client_1.publish)('whatsapp:status', { instanceId, status: 'reconnecting' });
                entry.reconnectTimer = setTimeout(() => {
                    _createSocket(instanceId, entry.reconnectAttempts);
                }, RECONNECT_DELAY_MS);
            }
            else {
                logger.error({ instanceId }, 'Max reconnect attempts reached');
                instances.delete(instanceId);
            }
        }
    });
    // Handle incoming messages
    sock.ev.on('messages.upsert', async ({ messages, type }) => {
        if (type !== 'notify')
            return;
        for (const msg of messages) {
            try {
                await _handleIncomingMessage(instanceId, msg);
            }
            catch (err) {
                logger.error({ instanceId, err }, 'Error handling message');
            }
        }
    });
}
async function _handleIncomingMessage(instanceId, msg) {
    // Ignore own messages, broadcasts, status
    if (msg.key.fromMe)
        return;
    const from = msg.key.remoteJid ?? '';
    if (!from || (0, baileys_1.isJidBroadcast)(from) || from === 'status@broadcast')
        return;
    // Anti-loop: ignorar se enviámos mensagem para este JID nos últimos 15s
    if (_wasRecentlySent(instanceId, from)) {
        logger.debug({ instanceId, from }, 'Anti-loop: ignoring message from recently-sent JID');
        return;
    }
    const isGroup = (0, baileys_1.isJidGroup)(from);
    const groupId = isGroup ? from : null;
    // Extract text content
    const text = msg.message?.conversation ||
        msg.message?.extendedTextMessage?.text ||
        msg.message?.imageMessage?.caption ||
        msg.message?.videoMessage?.caption ||
        '';
    if (!text.trim())
        return;
    // Get sender name
    const fromName = msg.pushName ||
        (isGroup ? msg.key.participant?.split('@')[0] : from.split('@')[0]) ||
        'Unknown';
    const payload = {
        instanceId,
        from: isGroup ? (msg.key.participant ?? from) : from,
        fromName,
        message: text.trim(),
        messageType: Object.keys(msg.message ?? {})[0] ?? 'text',
        timestamp: msg.messageTimestamp ?? Math.floor(Date.now() / 1000),
        isGroup,
        groupId,
        messageId: msg.key.id ?? '',
    };
    // Deduplicação — ignorar se o mesmo messageId já foi publicado nos últimos 60s
    if (msg.key.id) {
        const dedupeKey = `dedup:${instanceId}:${msg.key.id}`;
        const alreadySeen = await redis_client_1.redis.get(dedupeKey);
        if (alreadySeen) {
            logger.debug({ instanceId, msgId: msg.key.id }, 'Duplicate message ignored');
            return;
        }
        await redis_client_1.redis.set(dedupeKey, '1', 'EX', 60);
    }
    logger.info({ instanceId, from: payload.from, isGroup }, 'Incoming message');
    await (0, redis_client_1.publish)('whatsapp:incoming', payload);
}
