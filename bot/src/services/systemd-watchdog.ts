import { spawn } from 'node:child_process';
import { logger } from '../utils/logging.js';


type Environment = NodeJS.ProcessEnv;
type Notify = () => void;


export function watchdogEnabled(environment: Environment = process.env): boolean {
    if (!environment.NOTIFY_SOCKET || !environment.WATCHDOG_USEC) return false;
    if (!environment.WATCHDOG_PID) return true;
    const watchdogPid = Number(environment.WATCHDOG_PID);
    return Number.isInteger(watchdogPid) && watchdogPid === process.pid;
}


export function notifySystemd(): void {
    const child = spawn(
        '/usr/bin/systemd-notify', ['--no-block', 'WATCHDOG=1'],
        { stdio: 'ignore' });
    child.once('error', (error) => {
        logger.warn('Could not notify systemd watchdog', { error: error.message });
    });
    child.once('exit', (code) => {
        if (code !== 0) {
            logger.warn('systemd watchdog notification failed', { code });
        }
    });
    child.unref();
}


export class SystemdWatchdog {
    private readonly enabled: boolean;
    private readonly notify: Notify;

    constructor(environment: Environment = process.env, notify: Notify = notifySystemd) {
        this.enabled = watchdogEnabled(environment);
        this.notify = notify;
    }

    progress(): boolean {
        if (!this.enabled) return false;
        this.notify();
        return true;
    }
}
