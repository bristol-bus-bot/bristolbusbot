// Actual writer replay. No publisher, live database or social credentials.
import { createHash } from 'node:crypto';
import { Settings } from 'luxon';
import { AICommentary, GeminiRequestError } from '../dist/services/ai-commentary.js';
import { observationIssue } from '../dist/services/story-brief.js';
import { observationPost, reservePost } from '../dist/services/posting-fallback.js';
export const digest = v => createHash('sha256').update(typeof v === 'string' ? v : JSON.stringify(v)).digest('hex');
export function validateCases(input) {
    if (input.version !== 1 || !Array.isArray(input.cases) || !input.cases.length || input.cases.length > 200)
        throw new Error('Expected version 1 and 1–200 cases');
    const ids = new Set();
    for (const item of input.cases) {
        if (!item.id || ids.has(item.id) || !item.event || !Number.isFinite(Date.parse(item.now)))
            throw new Error('Cases need unique IDs, an event and a valid frozen time');
        ids.add(item.id);
    }
}
export async function replay(input, respond, { onCase = () => { }, model = 'recorded' } = {}) {
    validateCases(input);
    const state = { recentPosts: [...(input.recentPosts || [])], enrichmentDataStatus: {} };
    const ai = new AICommentary({ model, timeout: 75000 }, state, {});
    ai.editorialContext = { recordPost() { } }; // Isolated, in-memory editorial accounting.
    ai.getSubjectHistory().publishedCount = input.publishedCount || 0;
    const prepare = ai.prepareWriterCandidate.bind(ai), prompt = ai.buildSingleWriterPrompt.bind(ai);
    const realNow = Date.now, luxonNow = Settings.now, results = [];
    try {
        for (const item of input.cases) {
            const start = realNow(), frozen = Date.parse(item.now);
            Date.now = () => frozen + realNow() - start; // Keep elapsed request deadlines real.
            Settings.now = () => frozen;
            const result = { id: item.id, now: item.now, evidence: item, drafts: [], calls: [], historyBefore: {
                    publishedCount: ai.getSubjectHistory().publishedCount,
                    subjects: structuredClone(ai.getSubjectHistory().history), recentPosts: [...state.recentPosts]
                } };
            let pending = false, transportFailure;
            ai.buildSingleWriterPrompt = (...args) => { result.subject = structuredClone(args[5]); return prompt(...args); };
            ai.prepareWriterCandidate = (...args) => {
                const checked = prepare(...args);
                result.drafts.push({ post: checked.post, mechanical: checked.issues.length ? 'FAIL' : 'PASS', issues: checked.issues, verifier: 'not_run' });
                return checked;
            };
            ai.requestGeminiStructured = async (text, schema, temperature, thinkingLevel, timeoutMs) => {
                const kind = schema.properties?.verdict ? 'verifier' : result.drafts.length ? 'repair' : 'writer';
                const request = { id: `${item.id}:${result.calls.length + 1}`, caseId: item.id, kind,
                    prompt: text, schema, temperature, thinkingLevel, timeoutMs,
                    promptSha: digest({ prompt: text, schema, temperature, thinkingLevel }) };
                const call = { ...request };
                result.calls.push(call);
                let response;
                try {
                    response = await respond(request);
                }
                catch (error) {
                    transportFailure = error;
                    throw error;
                }
                if (!response) {
                    pending = true;
                    call.pending = true;
                    throw new Error('Awaiting approved response');
                }
                Object.assign(call, response);
                if (response.error)
                    throw new GeminiRequestError(response.error, response.httpStatus, response.quotaExceeded);
                if (kind === 'verifier' && result.drafts.length) {
                    try {
                        result.drafts.at(-1).verifier = JSON.parse(response.text);
                    }
                    catch {
                        result.drafts.at(-1).verifier = 'invalid_json';
                    }
                }
                return response.text;
            };
            const gate = observationIssue(item.event, frozen);
            const output = gate ? null : await ai.callSingleWriterGemini({ event: item.event,
                weatherContext: item.weatherContext, trafficContext: item.trafficContext }, 0, item.hook || null);
            if (transportFailure)
                throw transportFailure;
            result.gate = gate;
            if (pending) {
                result.status = 'awaiting_response';
                result.post = null;
            }
            else if (output) {
                result.status = 'verified';
                result.post = output.text;
                result.subject = output.metadata.subject;
            }
            else {
                // The fixture eligibility stands in for the publisher's final live recheck.
                result.status = gate ? 'reserve_fallback' : 'observation_fallback';
                result.post = gate ? reservePost(frozen) : observationPost(item.event);
                result.subject = 'fallback';
            }
            result.repaired = result.calls.some(c => c.kind === 'repair');
            if (!pending)
                ai.recordPublished(result.post, `audition:${item.id}`);
            results.push(result);
            await onCase(result, results);
            if (pending)
                break; // Later prompts depend on this case's publication history.
        }
    }
    finally {
        Date.now = realNow;
        Settings.now = luxonNow;
    }
    return { version: 1, fixtureSha: digest(input), model, results, calls: results.flatMap(r => r.calls),
        complete: results.length === input.cases.length && !results.some(r => r.status === 'awaiting_response') };
}
export function recordedResponder(recording) {
    const calls = new Map((recording.calls || []).map(c => [c.id, c]));
    return async (request) => {
        const saved = calls.get(request.id);
        if (!saved || saved.pending)
            return null;
        if (saved.promptSha !== request.promptSha)
            throw new Error(`Recorded response does not match prompt: ${request.id}`);
        return { text: saved.text, usage: saved.usage, error: saved.error, httpStatus: saved.httpStatus, quotaExceeded: saved.quotaExceeded };
    };
}
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export function comparisonPage(candidate, baseline) {
    if (baseline && baseline.fixtureSha !== candidate.fixtureSha)
        throw new Error('Baseline uses a different fixture set');
    const previous = new Map((baseline?.results || []).map(r => [r.id, r]));
    const counts = run => JSON.stringify((run?.results || []).reduce((a, r) => ({ ...a, [r.status]: (a[r.status] || 0) + 1 }), {}));
    const post = row => row ? `<p class="status">${esc(row.status)} · ${esc(row.subject)}${row.repaired ? ' · repaired' : ''}</p><p class="post">${esc(row.post || 'Awaiting model response')}</p><details><summary>Draft checks and verifier verdicts</summary><pre>${esc(JSON.stringify({ gate: row.gate, drafts: row.drafts }, null, 2))}</pre></details>` : '<p>No baseline recorded</p>';
    return `<!doctype html><html lang="en-GB"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>Busbot audition</title><style>body{font:16px/1.5 system-ui;background:#f4f3ee;color:#202a32;margin:0}main{max-width:1100px;margin:auto;padding:24px}.meta,.status{color:#526572;font-size:.9rem}article{background:white;border:1px solid #d4dce0;border-radius:12px;margin:20px 0;padding:20px}.pair{display:grid;grid-template-columns:1fr 1fr;gap:24px}.post{font-size:1.15rem}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:.8rem;background:#eef2f4;padding:12px}summary{cursor:pointer}h3{font-size:1rem}@media(max-width:650px){.pair{grid-template-columns:1fr}main{padding:12px}}</style><main><h1>Bristol Bus Bot audition</h1><p>Fixed evidence, real writer checks, no publishing.</p><p class="meta">Model ${esc(candidate.model)} · Candidate ${esc(candidate.promptVersion)} · Baseline ${esc(baseline?.promptVersion || 'not recorded')}<br>Fixture ${esc(candidate.fixtureSha)}<br>Live calls this invocation: ${candidate.liveCallsThisRun || 0}; budget ledger total: ${candidate.ledgerCalls || 0}; cached responses replayed: ${candidate.replayedCalls || 0}.</p><p>Baseline: ${esc(counts(baseline))}<br>Candidate: ${esc(counts(candidate))}</p><p>Any scenario context is labelled. Fallbacks have no model verifier verdict. This tests writing, not live event selection or publication.</p>${candidate.results.map(r => `<article><h2>${esc(r.id)}</h2><p>${esc(r.evidence.provenance || '')}</p><div class="pair"><section><h3>Baseline</h3>${post(previous.get(r.id))}</section><section><h3>Candidate</h3>${post(r)}</section></div><details><summary>Frozen evidence and publication history</summary><pre>${esc(JSON.stringify({ evidence: r.evidence, history: r.historyBefore }, null, 2))}</pre></details></article>`).join('')}</main></html>`;
}
