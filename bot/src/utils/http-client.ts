// Shared HTTP client with keep-alive, concurrency limiting, and exponential backoff
// Tuned for Raspberry Pi stability and low resource usage

import fetch, { type RequestInit, type Response } from 'node-fetch';
import http from 'http';
import https from 'https';
import Agent from 'agentkeepalive';
import { logger } from './logging.js';

// Env helpers
const toInt = (v: string | undefined, d: number) => {
  const n = parseInt(String(v ?? ''), 10);
  return Number.isFinite(n) ? n : d;
};

// Tunables (override via env on Pi)
const HTTP_MAX_SOCKETS = toInt(process.env.HTTP_MAX_SOCKETS, 4); // Pi Zero: very low
const HTTP_MAX_FREE = toInt(process.env.HTTP_MAX_FREE_SOCKETS, 2);
const HTTP_TIMEOUT = toInt(process.env.HTTP_TIMEOUT_MS, 60000);
const HTTP_FREE_TTL = toInt(process.env.HTTP_FREE_TTL_MS, 30000);
const HTTP_SOCKET_TTL = toInt(process.env.HTTP_SOCKET_TTL_MS, 60000);
const HTTP_MAX_CONCURRENT = toInt(process.env.HTTP_MAX_CONCURRENT, 2);
const HTTP_RETRIES = toInt(process.env.HTTP_RETRIES, 2);
const HTTP_RETRY_BASE_MS = toInt(process.env.HTTP_RETRY_BASE_MS, 1000);

// Keep-alive agents for HTTP/HTTPS
const httpAgent: http.Agent = new Agent({
  keepAlive: true,
  maxSockets: HTTP_MAX_SOCKETS,
  maxFreeSockets: HTTP_MAX_FREE,
  timeout: HTTP_TIMEOUT,
  freeSocketTimeout: HTTP_FREE_TTL,
  socketActiveTTL: HTTP_SOCKET_TTL
});

const httpsAgent: https.Agent = new Agent.HttpsAgent({
  keepAlive: true,
  maxSockets: HTTP_MAX_SOCKETS,
  maxFreeSockets: HTTP_MAX_FREE,
  timeout: HTTP_TIMEOUT,
  freeSocketTimeout: HTTP_FREE_TTL,
  socketActiveTTL: HTTP_SOCKET_TTL
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

// Timeout wrapper using AbortController
function withTimeout<T>(promise: Promise<T>, ms: number, label: string): Promise<T> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), ms);
  return (promise as any)(controller.signal)
    .finally(() => clearTimeout(timeout))
    .catch((err: any) => {
      if (err?.name === 'AbortError') {
        const e = new Error(`${label} timed out after ${ms}ms`);
        (e as any).code = 'ETIMEDOUT';
        throw e;
      }
      throw err;
    });
}

// Main fetch helper
export async function httpFetch(url: string, options: RequestInit & { timeoutMs?: number } = {}): Promise<Response> {
  const isHttps = url.startsWith('https://');
  const agent = isHttps ? httpsAgent : httpAgent;
  const timeoutMs = options.timeoutMs ?? HTTP_TIMEOUT;

  await limiter.acquire();
  try {
    return await withBackoff(async () => {
      const doFetch = (signal: AbortSignal) => fetch(url, { ...options, agent: agent as any, signal });
      // Use AbortController for timeout per attempt
      return await new Promise<Response>((resolve, reject) => {
        const controller = new AbortController();
        const t = setTimeout(() => controller.abort(), timeoutMs);
        doFetch(controller.signal)
          .then(res => { clearTimeout(t); resolve(res); })
          .catch(err => { clearTimeout(t); reject(err); });
      });
    });
  } finally {
    limiter.release();
  }
}

export function getHttpClientStats() {
  return {
    maxSockets: HTTP_MAX_SOCKETS,
    maxFreeSockets: HTTP_MAX_FREE,
    timeoutMs: HTTP_TIMEOUT,
    freeSocketTtlMs: HTTP_FREE_TTL,
    socketTtlMs: HTTP_SOCKET_TTL,
    maxConcurrent: HTTP_MAX_CONCURRENT,
    retries: HTTP_RETRIES
  };
}
