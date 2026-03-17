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
const express_1 = __importDefault(require("express"));
const pino_1 = __importDefault(require("pino"));
const redis_client_1 = require("./redis-client");
const connection_manager_1 = require("./connection-manager");
const logger = (0, pino_1.default)({ name: 'whatsapp-service' });
const app = (0, express_1.default)();
app.use(express_1.default.json());
const PORT = parseInt(process.env.PORT || '3001', 10);
// ─── REST API ────────────────────────────────────────────────────────────────
app.get('/health', (_req, res) => {
    res.json({ status: 'ok', activeInstances: (0, connection_manager_1.getInstances)().filter((i) => i.status === 'connected').length });
});
app.get('/instances', (_req, res) => {
    res.json((0, connection_manager_1.getInstances)());
});
app.post('/instances/:id/connect', async (req, res) => {
    try {
        await (0, connection_manager_1.connectInstance)(req.params.id);
        res.json({ ok: true, instanceId: req.params.id });
    }
    catch (err) {
        res.status(500).json({ error: err.message });
    }
});
app.post('/instances/:id/disconnect', async (req, res) => {
    try {
        await (0, connection_manager_1.disconnectInstance)(req.params.id);
        res.json({ ok: true });
    }
    catch (err) {
        res.status(500).json({ error: err.message });
    }
});
app.get('/instances/:id', (req, res) => {
    const status = (0, connection_manager_1.getInstanceStatus)(req.params.id);
    if (!status)
        return res.status(404).json({ error: 'Instance not found' });
    res.json(status);
});
// ─── REDIS COMMAND LISTENER ───────────────────────────────────────────────────
async function startRedisListener() {
    await redis_client_1.redisSub.subscribe('whatsapp:command', 'whatsapp:outgoing');
    redis_client_1.redisSub.on('message', async (channel, rawMessage) => {
        try {
            const data = JSON.parse(rawMessage);
            if (channel === 'whatsapp:command') {
                logger.info({ channel, data }, 'Received command');
                if (data.action === 'connect') {
                    await (0, connection_manager_1.connectInstance)(data.instanceId);
                }
                else if (data.action === 'disconnect') {
                    await (0, connection_manager_1.disconnectInstance)(data.instanceId);
                }
            }
            if (channel === 'whatsapp:outgoing') {
                const msg = data;
                const { instanceId, to, message } = msg;
                logger.info({ instanceId, to }, 'Sending outgoing message');
                // Find socket
                const instances = (0, connection_manager_1.getInstances)();
                const inst = instances.find((i) => i.id === instanceId);
                if (!inst || inst.status !== 'connected') {
                    logger.warn({ instanceId }, 'Instance not connected, cannot send');
                    return;
                }
                // Dynamic import to get socket — access through manager function
                const { getSocket, markRecentlySent } = await Promise.resolve().then(() => __importStar(require('./connection-manager')));
                const sock = getSocket ? getSocket(instanceId) : null;
                if (sock) {
                    const toJid = to.includes('@') ? to : `${to}@s.whatsapp.net`;
                    // Marcar JID como "enviado recentemente" antes de enviar — evita loop
                    if (markRecentlySent)
                        markRecentlySent(instanceId, toJid);
                    const sentMsg = await sock.sendMessage(toJid, { text: message });
                    await redis_client_1.redis.publish('whatsapp:sent', JSON.stringify({
                        instanceId,
                        to,
                        messageId: sentMsg?.key?.id,
                    }));
                }
            }
        }
        catch (err) {
            logger.error({ err, channel }, 'Error processing Redis message');
        }
    });
}
// ─── BOOTSTRAP ───────────────────────────────────────────────────────────────
async function main() {
    logger.info('Starting WhatsApp Service...');
    // Subscribe to Redis commands
    await startRedisListener();
    // Reconnect any existing sessions
    await (0, connection_manager_1.reconnectExistingSessions)();
    app.listen(PORT, () => {
        logger.info({ port: PORT }, 'WhatsApp Service HTTP server running');
    });
}
main().catch((err) => {
    logger.fatal({ err }, 'Fatal error starting service');
    process.exit(1);
});
