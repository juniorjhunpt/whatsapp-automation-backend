import Redis from 'ioredis';
import pino from 'pino';

const logger = pino({ name: 'redis-client' });

const REDIS_URL = process.env.REDIS_URL || 'redis://127.0.0.1:6379';

export const redis = new Redis(REDIS_URL, {
  maxRetriesPerRequest: 3,
  retryStrategy: (times) => Math.min(times * 500, 5000),
  reconnectOnError: () => true,
});

// Subscriber client (separate connection — cannot publish on subscriber)
export const redisSub = new Redis(REDIS_URL, {
  maxRetriesPerRequest: 3,
  retryStrategy: (times) => Math.min(times * 500, 5000),
});

redis.on('connect', () => logger.info('Redis publisher connected'));
redis.on('error', (err) => logger.error({ err }, 'Redis publisher error'));
redisSub.on('connect', () => logger.info('Redis subscriber connected'));
redisSub.on('error', (err) => logger.error({ err }, 'Redis subscriber error'));

export async function publish(channel: string, data: object): Promise<void> {
  await redis.publish(channel, JSON.stringify(data));
}
