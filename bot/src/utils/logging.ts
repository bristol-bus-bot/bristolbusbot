// Environment-driven logging and performance metrics.

import { DateTime } from 'luxon';
import winston from 'winston';
import path from 'path';
import fs from 'fs';

// Target timezone for consistent logging
export const TARGET_TIMEZONE = 'Europe/London';

// -------- ENV helpers --------------------------------------------------------
const envBool = (v: string | undefined, def: boolean) =>
  v === undefined ? def : /^(1|true|yes|on)$/i.test(v);

const envInt = (v: string | undefined, def: number) => {
  const n = parseInt(String(v ?? ''), 10);
  return Number.isFinite(n) ? n : def;
};

const envSize = (v: string | undefined, defBytes: number) => {
  if (!v) return defBytes;
  const s = v.trim().toLowerCase();
  if (/^\d+$/.test(s)) return parseInt(s, 10);         // bytes
  if (s.endsWith('k')) return parseInt(s) * 1024;
  if (s.endsWith('m')) return parseInt(s) * 1024 * 1024;
  if (s.endsWith('g')) return parseInt(s) * 1024 * 1024 * 1024;
  return defBytes;
};

// -------- Types --------------------------------------------------------------
interface PerformanceMetrics {
  operation: string;
  startTime: number;
  endTime?: number;
  duration?: number;
  success: boolean;
  error?: Error;
  metadata?: Record<string, any>;
}

interface LoggerConfig {
  level: string;
  maxFileSize: number;
  maxFiles: number;
  enableConsole: boolean;
  enableFile: boolean;
  logDirectory: string;
  summaryMode?: boolean;
  timezone: string;
}

// -------- Default config (env-aware) ----------------------------------------
const defaultConfig: LoggerConfig = {
  level: process.env.LOG_LEVEL || 'info',                         // e.g. 'warn' on Pi
  maxFileSize: envSize(process.env.LOG_MAX_SIZE, 10 * 1024 * 1024), // '5m' works too
  maxFiles: envInt(process.env.LOG_MAX_FILES, 5),
  enableConsole: envBool(process.env.ENABLE_CONSOLE_LOGS, true),
  enableFile: envBool(process.env.ENABLE_FILE_LOGS, true),        // set false to kill file logs
  logDirectory: process.env.LOG_DIR || './logs',
  summaryMode: envBool(process.env.SUMMARY_MODE, false),          // or pass --summary in CLI
  timezone: TARGET_TIMEZONE
};

// -------- Formats ------------------------------------------------------------
const jsonFormat = winston.format.combine(
  winston.format.timestamp({
    format: () => DateTime.now().setZone(TARGET_TIMEZONE).toISO() ?? ''
  }),
  winston.format.errors({ stack: true }),
  winston.format.json(),
  winston.format.printf(({ timestamp, level, message, stack, ...meta }) => {
    const logObject: Record<string, any> = { timestamp, level, message, ...meta };
    if (stack) logObject.stack = stack;
    return JSON.stringify(logObject);
  })
);

const consoleFormat = winston.format.combine(
  winston.format.colorize(),
  winston.format.timestamp({
    format: () => DateTime.now().setZone(TARGET_TIMEZONE).toFormat('yyyy-MM-dd HH:mm:ss')
  }),
  winston.format.printf(({ timestamp, level, message, ...meta }) => {
    const metaString = Object.keys(meta).length > 0 ? ` ${JSON.stringify(meta)}` : '';
    return `${timestamp} [${level}] ${message}${metaString}`;
  })
);

// -------- Logger factory -----------------------------------------------------
function createLogger(config: LoggerConfig = defaultConfig): winston.Logger {
  // Ensure log directory exists if file logging enabled
  if (config.enableFile && !fs.existsSync(config.logDirectory)) {
    fs.mkdirSync(config.logDirectory, { recursive: true });
  }

  const transports: winston.transport[] = [];

  if (config.enableConsole) {
    transports.push(
      new winston.transports.Console({
        level: config.level,
        format: consoleFormat
      })
    );
  }

  if (config.enableFile) {
    transports.push(
      new winston.transports.File({
        filename: path.join(config.logDirectory, 'app.log'),
        level: config.level,
        maxsize: config.maxFileSize,
        maxFiles: config.maxFiles,
        tailable: true,
        format: jsonFormat
      }),
      new winston.transports.File({
        filename: path.join(config.logDirectory, 'error.log'),
        level: 'error',
        maxsize: config.maxFileSize,
        maxFiles: config.maxFiles,
        tailable: true,
        format: jsonFormat
      })
    );
  }

  return winston.createLogger({
    level: config.level,
    format: jsonFormat, // base format (not used by console transport which has its own)
    defaultMeta: {
      service: 'bristol-bus-bot',
      version: '1.0.0',
      hostname: process.env.HOSTNAME || 'unknown',
      pid: process.pid
    },
    transports,
    exceptionHandlers: config.enableFile ? [
      new winston.transports.File({
        filename: path.join(config.logDirectory, 'exceptions.log'),
        format: jsonFormat
      })
    ] : [],
    rejectionHandlers: config.enableFile ? [
      new winston.transports.File({
        filename: path.join(config.logDirectory, 'rejections.log'),
        format: jsonFormat
      })
    ] : []
  });
}

// Global logger instance (env-aware)
export const logger = createLogger();

// -------- Summary mode -------------------------------------------------------
let summaryModeEnabled = !!defaultConfig.summaryMode;

export function setSummaryMode(enabled: boolean): void {
  summaryModeEnabled = enabled;
  if (enabled) {
    logger.info('📊 Summary logging mode enabled - showing key metrics only');
  }
}

// Only logs when SUMMARY mode is ON
export function logSummary(level: 'info'|'warn'|'error', message: string, meta?: any): void {
  if (summaryModeEnabled) (logger as any)[level](message, meta);
}

// Only logs when SUMMARY mode is OFF
export function logDetailed(level: 'info'|'warn'|'error', message: string, meta?: any): void {
  if (!summaryModeEnabled) (logger as any)[level](message, meta);
}

// Always logs regardless of summary mode (errors, critical events)
export function logAlways(level: 'info'|'warn'|'error', message: string, meta?: any): void {
  (logger as any)[level](message, meta);
}

// -------- Performance timer --------------------------------------------------
export class PerformanceTimer {
  private startTime: number;
  private operation: string;
  private logger: winston.Logger;
  private metadata: Record<string, any>;

  constructor(operation: string, loggerInstance: winston.Logger, metadata: Record<string, any> = {}) {
    this.operation = operation;
    this.logger = loggerInstance;
    this.metadata = metadata;
    this.startTime = Date.now();

    // This is debug; with LOG_LEVEL=warn it won’t print
    this.logger.debug('Performance timer started', {
      operation: this.operation,
      ...this.metadata
    });
  }

  complete(additionalMetadata: Record<string, any> = {}): void {
    const endTime = Date.now();
    const duration = endTime - this.startTime;

    // Only in detailed mode
    logDetailed('info', 'Performance timer completed', {
      operation: this.operation,
      duration: `${duration}ms`,
      success: true,
      ...this.metadata,
      ...additionalMetadata
    });

    this.recordMetrics({
      operation: this.operation,
      startTime: this.startTime,
      endTime,
      duration,
      success: true,
      metadata: { ...this.metadata, ...additionalMetadata }
    });
  }

  fail(error: Error, additionalMetadata: Record<string, any> = {}): void {
    const endTime = Date.now();
    const duration = endTime - this.startTime;

    // Always log errors
    logAlways('error', 'Performance timer failed', {
      operation: this.operation,
      duration: `${duration}ms`,
      success: false,
      error: error.message,
      stack: error.stack,
      ...this.metadata,
      ...additionalMetadata
    });

    this.recordMetrics({
      operation: this.operation,
      startTime: this.startTime,
      endTime,
      duration,
      success: false,
      error,
      metadata: { ...this.metadata, ...additionalMetadata }
    });
  }

  getElapsed(): number {
    return Date.now() - this.startTime;
  }

  private recordMetrics(metrics: PerformanceMetrics): void {
    // Record metrics in the structured application log.
    this.logger.debug('Performance metrics recorded', {
      type: 'performance_metrics',
      ...metrics
    });
  }
}

// -------- Utility helpers ----------------------------------------------------
export class LoggingUtils {
  static createOperationLogger(operation: string, metadata: Record<string, any> = {}): winston.Logger {
    return logger.child({ operation, ...metadata });
  }

  static logHttpRequest(req: any, res: any, duration: number): void {
    logger.info('HTTP request', {
      method: req.method,
      url: req.url,
      statusCode: res.statusCode,
      duration: `${duration}ms`,
      userAgent: req.get?.('user-agent'),
      ip: req.ip,
      contentLength: res.get?.('content-length')
    });
  }

  static logDatabaseOperation(operation: string, table: string, duration: number, error?: Error): void {
    if (error) {
      logger.error('Database operation failed', {
        operation, table, duration: `${duration}ms`,
        error: error.message, stack: error.stack
      });
    } else {
      logger.debug('Database operation completed', {
        operation, table, duration: `${duration}ms`
      });
    }
  }

  static logApiCall(service: string, endpoint: string, duration: number, success: boolean, error?: Error): void {
    const base = { service, endpoint, duration: `${duration}ms`, success };
    if (error) {
      logger.error('API call failed', { ...base, error: error.message, stack: error.stack });
    } else {
      logger.info('API call completed', base);
    }
  }

  static logSystemMetrics(metrics: Record<string, any>): void {
    logger.info('System metrics', {
      type: 'system_metrics',
      timestamp: DateTime.now().setZone(TARGET_TIMEZONE).toISO(),
      ...metrics
    });
  }

  static logBusinessEvent(event: string, data: Record<string, any>): void {
    logger.info('Business event', {
      type: 'business_event',
      event,
      timestamp: DateTime.now().setZone(TARGET_TIMEZONE).toISO(),
      ...data
    });
  }

  static logSecurityEvent(event: string, data: Record<string, any>): void {
    logger.warn('Security event', {
      type: 'security_event',
      event,
      timestamp: DateTime.now().setZone(TARGET_TIMEZONE).toISO(),
      ...data
    });
  }

  static safelog(obj: any, sensitiveKeys: string[] = ['password', 'apiKey', 'token', 'secret']): any {
    if (typeof obj !== 'object' || obj === null) return obj;
    const cleaned: Record<string, any> = { ...obj };
    for (const key of sensitiveKeys) if (key in cleaned) cleaned[key] = '[REDACTED]';
    return cleaned;
  }
}

// -------- Express middlewares ------------------------------------------------
export function requestLoggingMiddleware(req: any, res: any, next: any): void {
  const startTime = Date.now();
  logger.debug('HTTP request started', {
    method: req.method,
    url: req.url,
    userAgent: req.get?.('user-agent'),
    ip: req.ip
  });
  const originalSend = res.send;
  res.send = function (body: any) {
    const duration = Date.now() - startTime;
    LoggingUtils.logHttpRequest(req, res, duration);
    return originalSend.call(this, body);
  };
  next();
}

export function errorLoggingMiddleware(error: Error, req: any, res: any, next: any): void {
  logger.error('Express error', {
    error: error.message,
    stack: error.stack,
    method: req.method,
    url: req.url,
    userAgent: req.get?.('user-agent'),
    ip: req.ip
  });
  next(error);
}

// -------- Graceful shutdown --------------------------------------------------
export function setupGracefulShutdown(): void {
  const gracefulShutdown = (signal: string) => {
    logger.info('Received shutdown signal', { signal });
    logger.on('finish', () => { process.exit(0); });
    // @ts-ignore end() is on NodeJS.WritableStream types used by winston
    (logger as any).end?.();
  };
  process.on('SIGTERM', () => gracefulShutdown('SIGTERM'));
  process.on('SIGINT', () => gracefulShutdown('SIGINT'));
}

// -------- Runtime configuration ---------------------------------------------
export function configureLogger(config: Partial<LoggerConfig>): void {
  logger.info('Logger configuration updated', { newConfig: LoggingUtils.safelog(config) });
}

// Default export
export { logger as default };
