// Offline by default. Explicitly budgeted --live calls cannot publish posts.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
process.env.ENABLE_FILE_LOGS = 'false';
process.env.ENABLE_CONSOLE_LOGS = 'true';
process.env.LOG_LEVEL = 'error';
const { replay, recordedResponder, comparisonPage, digest } = await import('./audition-core.mjs');
const { buildGeminiStructuredGenerationConfig } = await import('../dist/services/ai-commentary.js');
const args = process.argv.slice(2), [inputPath, outputDirectory] = args.splice(0, 2), options = {};
while (args.length) {
    const key = args.shift();
    if (key === '--live')
        options.live = true;
    else if (['--baseline', '--responses', '--model', '--max-calls', '--ledger'].includes(key) && args[0] && !args[0].startsWith('--'))
        options[key.slice(2)] = args.shift();
    else
        throw new Error(`Unknown or incomplete argument: ${key}`);
}
if (!inputPath || !outputDirectory)
    throw new Error('Usage: audition.mjs cases.json output-directory [--baseline run.json] [--responses run.json] [--live --model NAME --max-calls N --ledger FILE]');
const read = p => JSON.parse(fs.readFileSync(p, 'utf8').replace(/^\uFEFF/, ''));
const input = read(inputPath), baseline = options.baseline ? read(options.baseline) : null, responses = options.responses ? read(options.responses) : { calls: [] };
if (responses.fixtureSha && responses.fixtureSha !== digest(input))
    throw new Error('Responses use a different fixture set');
if (baseline && baseline.fixtureSha !== digest(input))
    throw new Error('Baseline uses a different fixture set');
const output = path.resolve(outputDirectory);
fs.mkdirSync(output, { recursive: true });
if ([inputPath, options.baseline, options.responses].filter(Boolean).map(p => path.resolve(p)).includes(path.join(output, 'run.json')))
    throw new Error('Refusing to overwrite input or baseline');
if (options.ledger && [inputPath, options.baseline, options.responses, path.join(output, 'run.json'), path.join(output, 'checkpoint.json'), path.join(output, 'index.html')].filter(Boolean).map(p => path.resolve(p)).includes(path.resolve(options.ledger)))
    throw new Error('Budget ledger must be separate from inputs and reports');
const atomic = (p, d) => { fs.writeFileSync(p + '.tmp', JSON.stringify(d, null, 2)); fs.renameSync(p + '.tmp', p); };
const here = path.dirname(fileURLToPath(import.meta.url));
const promptVersion = digest(['ai-commentary', 'story-brief', 'story-subject', 'subject-history', 'posting-fallback', 'editorial-commentary-policy'].map(n => fs.readFileSync(path.join(here, '../dist/services', n + '.js'), 'utf8')).join('\n'));
let ledger, lock, spent = 0, replayed = 0, fatal;
const model = options.model || responses.model || baseline?.model || 'unconfigured', limit = Number(options['max-calls']);
try {
    if (options.live) {
        if (!process.env.AI_API_KEY || !options.model || !options.ledger || !Number.isSafeInteger(limit) || limit < 1 || limit > 1000)
            throw new Error('Live mode needs AI_API_KEY, --model, --ledger and an explicit 1–1000 call limit');
        lock = fs.openSync(options.ledger + '.lock', 'wx');
        ledger = fs.existsSync(options.ledger) ? read(options.ledger) : { version: 1, model, limit, fixtureSha: digest(input), calls: [] };
        if (ledger.model !== model || ledger.limit !== limit || ledger.fixtureSha !== digest(input))
            throw new Error('Ledger approval does not match this run');
    }
    const saved = recordedResponder(responses);
    const respond = async (request) => {
        const cached = ledger?.calls.find(c => c.id === request.id && c.promptSha === request.promptSha);
        if (cached) {
            if (!cached.complete) {
                fatal = `Uncertain prior request ${request.id}; inspect before retrying`;
                return null;
            }
            replayed++;
            return { text: cached.text, usage: cached.usage, error: cached.error, httpStatus: cached.httpStatus, quotaExceeded: cached.quotaExceeded };
        }
        let recorded;
        try {
            recorded = await saved(request);
        }
        catch (e) {
            fatal = e.message;
            return null;
        }
        if (recorded) {
            replayed++;
            return recorded;
        }
        if (!options.live)
            return null;
        if (ledger.calls.length >= limit) {
            fatal = 'Approved model-call budget exhausted';
            return null;
        }
        const entry = { ...request, complete: false };
        ledger.calls.push(entry);
        atomic(options.ledger, ledger);
        spent++;
        try {
            const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent`, {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'x-goog-api-key': process.env.AI_API_KEY },
                body: JSON.stringify({ contents: [{ parts: [{ text: request.prompt }] }], generationConfig: buildGeminiStructuredGenerationConfig(request.schema, request.temperature, request.thinkingLevel) }),
                signal: AbortSignal.timeout(Math.max(1, Math.min(request.timeoutMs || 75000, 75000)))
            });
            if (!response.ok) {
                entry.httpStatus = response.status;
                entry.quotaExceeded = response.status === 429;
                throw new Error(`Provider HTTP ${response.status}`);
            }
            const payload = await response.json();
            entry.text = (payload.candidates?.[0]?.content?.parts || []).filter(p => typeof p.text === 'string' && !p.thought).map(p => p.text).join('\n').trim();
            entry.usage = payload.usageMetadata || null;
            if (!entry.text)
                throw new Error('Provider returned no text');
        }
        catch (e) {
            entry.error = e.message.includes('Provider') ? e.message : 'Provider request failed or timed out';
        }
        entry.complete = true;
        atomic(options.ledger, ledger);
        return { text: entry.text, usage: entry.usage, error: entry.error, httpStatus: entry.httpStatus, quotaExceeded: entry.quotaExceeded };
    };
    const run = await replay(input, respond, { model, onCase: (result, results) => {
            atomic(path.join(output, 'checkpoint.json'), { fixtureSha: digest(input), model, promptVersion, results, calls: results.flatMap(r => r.calls) });
            console.log(`${result.id}: ${result.status} (${result.calls.length} requests)`);
        } });
    Object.assign(run, { promptVersion, liveCallsThisRun: spent, ledgerCalls: ledger?.calls.length ?? responses.ledgerCalls ?? 0, replayedCalls: replayed, fatal, provenance: input.provenance,
        baselineReview: 'Model output awaiting maintainer review; passing checks is not editorial approval.' });
    atomic(path.join(output, 'run.json'), run);
    fs.writeFileSync(path.join(output, 'index.html'), comparisonPage(run, baseline), 'utf8');
    console.log(JSON.stringify({ complete: run.complete, liveCalls: spent, ledgerCalls: run.ledgerCalls, replayed, output }));
    if (fatal) {
        console.error(fatal);
        process.exitCode = 1;
    }
}
finally {
    if (lock !== undefined) {
        fs.closeSync(lock);
        fs.unlinkSync(options.ledger + '.lock');
    }
}
