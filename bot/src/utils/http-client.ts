// Shared HTTP client: a capped keep-alive connection pool, concurrency
// limiting and exponential backoff, tuned for the Raspberry Pi.
//
// undici is Node's own HTTP client (the engine behind built-in fetch). Its
// Agent keeps the per-host socket cap the bot has had since the Pi's
// connection-tracking trouble; built-in fetch on its own has no cap.

import { Agent, fetch, type RequestInit, type Response } from 'undici';
import { logger } from './logging.js';

// Env helpers
const toInt = (v: string | undefined, d: number) => {
  const n = parseInt(String(v ?? ''), 10);
  return Number.isFinite(n) ? n : d;
};

// Tunables (override via env on Pi)
const HTTP_MAX_SOCKETS = toInt(process.env.HTTP_MAX_SOCKETS, 4); // per host; Pi: very low
const HTTP_TIMEOUT = toInt(process.env.HTTP_TIMEOUT_MS, 60000);
const HTTP_FREE_TTL = toInt(process.env.HTTP_FREE_TTL_MS, 30000);
const HTTP_SOCKET_TTL = toInt(process.env.HTTP_SOCKET_TTL_MS, 60000);
const HTTP_MAX_CONCURRENT = toInt(process.env.HTTP_MAX_CONCURRENT, 2);
const HTTP_RETRIES = toInt(process.env.HTTP_RETRIES, 2);
const HTTP_RETRY_BASE_MS = toInt(process.env.HTTP_RETRY_BASE_MS, 1000);

// One pool for every request the bot makes through httpFetch.
const dispatcher = new Agent({
  connections: HTTP_MAX_SOCKETS,
  keepAliveTimeout: HTTP_FREE_TTL,
  keepAliveMaxTimeout: HTTP_SOCKET_TTL,
  headersTimeout: HTTP_TIMEOUT,
  bodyTimeout: HTTP_TIMEOUT,
});

// Simple concurrency gate to avoid request storms
class RequestLimiter {
  private active = 0;
  private queue: Array<() => void> = [];

  constructor(private readonly maxConcurrent: number) {}

  async acquire(): Promise<void> {
    if (this.active < this.maxConcurrent) {
      this.active++;
      return;
    }
    await new Promise<void>(resolve => this.queue.push(resolve));
    this.active++;
  }

  release(): void {
    this.active = Math.max(0, this.active - 1);
    const next = this.queue.shift();
    if (next) next();
  }
}

const limiter = new RequestLimiter(HTTP_MAX_CONCURRENT);

// Exponential backoff with jitter
async function withBackoff<T>(fn: () => Promise<T>, retries = HTTP_RETRIES): Promise<T> {
  let attempt = 0;
  let lastError: any = null;
  while (attempt <= retries) {
    try {
      return await fn();
    } catch (err: any) {
      lastError = err;
      attempt++;
      if (attempt > retries) break;
      const jitter = Math.floor(Math.random() * 1000);
      const delay = Math.min(HTTP_RETRY_BASE_MS * 2 ** (attempt - 1) + jitter, 60000);
      logger.warn('HTTP backoff retry', { attempt, delayMs: delay, error: err?.message });
      await new Promise(res => setTimeout(res, delay));
    }
  }
  throw lastError || new Error('HTTP backoff exhausted');
}

// Main fetch helper
export async function httpFetch(url: string, options: RequestInit & { timeoutMs?: number; retries?: number } = {}): Promise<Response> {
  const { timeoutMs = HTTP_TIMEOUT, retries, ...init } = options;

  await limiter.acquire();
  try {
    return await withBackoff(async () => {
      try {
        // A fresh timeout per attempt.
        return await fetch(url, { ...init, dispatcher, signal: AbortSignal.timeout(timeoutMs) });
      } catch (err: any) {
        if (err?.name === 'TimeoutError' || err?.name === 'AbortError') {
          const e = new Error(`HTTP request timed out after ${timeoutMs}ms`);
          (e as any).code = 'ETIMEDOUT';
          throw e;
        }
        throw err;
      }
    }, retries ?? HTTP_RETRIES);
  } finally {
    limiter.release();
  }
}

export function getHttpClientStats() {
  return {
    maxSockets: HTTP_MAX_SOCKETS,
    timeoutMs: HTTP_TIMEOUT,
    freeSocketTtlMs: HTTP_FREE_TTL,
    socketTtlMs: HTTP_SOCKET_TTL,
    maxConcurrent: HTTP_MAX_CONCURRENT,
    retries: HTTP_RETRIES
  };
}
