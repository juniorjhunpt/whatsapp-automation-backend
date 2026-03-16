import express from 'express';
import pino from 'pino';
import { redisSub, redis } from './redis-client';
import {
  connectInstance,
  disconnectInstance,
  getInstances,
  getInstanceStatus,
  reconnectExistingSessions,
} from './connection-manager';
import { OutgoingMessage } from './types';

const logger = pino({ name: 'whatsapp-service' });
const app = express();
app.use(express.json());

const PORT = parseInt(process.env.PORT || '3001', 10);

// ─── REST API ────────────────────────────────────────────────────────────────

app.get('/health', (_req, res) => {
  res.json({ status: 'ok', activeInstances: getInstances().filter((i) => i.status === 'connected').length });
});

app.get('/instances', (_req, res) => {
  res.json(getInstances());
});

app.post('/instances/:id/connect', async (req, res) => {
  try {
    await connectInstance(req.params.id);
    res.json({ ok: true, instanceId: req.params.id });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/instances/:id/disconnect', async (req, res) => {
  try {
    await disconnectInstance(req.params.id);
    res.json({ ok: true });
  } catch (err: any) {
    res.status(500).json({ error: err.message });
  }
});

app.get('/instances/:id', (req, res) => {
  const status = getInstanceStatus(req.params.id);
  if (!status) return res.status(404).json({ error: 'Instance not found' });
  res.json(status);
});

// ─── REDIS COMMAND LISTENER ───────────────────────────────────────────────────

async function startRedisListener() {
  await redisSub.subscribe('whatsapp:command', 'whatsapp:outgoing');

  redisSub.on('message', async (channel, rawMessage) => {
    try {
      const data = JSON.parse(rawMessage);

      if (channel === 'whatsapp:command') {
        logger.info({ channel, data }, 'Received command');
        if (data.action === 'connect') {
          await connectInstance(data.instanceId);
        } else if (data.action === 'disconnect') {
          await disconnectInstance(data.instanceId);
        }
      }

      if (channel === 'whatsapp:outgoing') {
        const msg: OutgoingMessage = data;
        const { instanceId, to, message } = msg;
        logger.info({ instanceId, to }, 'Sending outgoing message');

        // Find socket
        const instances = getInstances();
        const inst = instances.find((i) => i.id === instanceId);
        if (!inst || inst.status !== 'connected') {
          logger.warn({ instanceId }, 'Instance not connected, cannot send');
          return;
        }

        // Dynamic import to get socket — access through manager function
        const { getSocket } = await import('./connection-manager') as any;
        const sock = getSocket ? getSocket(instanceId) : null;
        if (sock) {
          const toJid = to.includes('@') ? to : `${to}@s.whatsapp.net`;
          const sentMsg = await sock.sendMessage(toJid, { text: message });
          await redis.publish('whatsapp:sent', JSON.stringify({
            instanceId,
            to,
            messageId: sentMsg?.key?.id,
          }));
        }
      }
    } catch (err) {
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
  await reconnectExistingSessions();

  app.listen(PORT, () => {
    logger.info({ port: PORT }, 'WhatsApp Service HTTP server running');
  });
}

main().catch((err) => {
  logger.fatal({ err }, 'Fatal error starting service');
  process.exit(1);
});
