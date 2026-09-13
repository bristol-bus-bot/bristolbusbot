// Offline preparation/validation only. This tool has no social-media credentials
// or publishing client. A caller may execute the explicit request plan separately.
// node scripts/audition.mjs cases.json output.json [writer-responses.json] [verifier-responses.json]
import fs from 'node:fs';
import { DateTime } from 'luxon';
import { AICommentary, buildGeminiStructuredGenerationConfig } from '../dist/services/ai-commentary.js';
import { buildStoryPrompt, observationIssue, factualStoryIssues } from '../dist/services/story-brief.js';
import { WRITER_RESPONSE_SCHEMA, VERIFIER_RESPONSE_SCHEMA, parseEditorialWriterOutput,
  parseEditorialVerifierOutput, cleanEditorialPost, validateCommentaryCandidate } from '../dist/services/editorial-commentary-policy.js';

const [inputPath, outputPath, writerPath, verifierPath] = process.argv.slice(2);
if (!inputPath || !outputPath) throw new Error('Provide input and output JSON paths');
const input = JSON.parse(fs.readFileSync(inputPath, 'utf8'));
if (!Array.isArray(input.cases) || input.cases.length > 20) throw new Error('Audition accepts at most 20 cases');
const writers = writerPath ? JSON.parse(fs.readFileSync(writerPath, 'utf8')) : [];
const verifiers = verifierPath ? JSON.parse(fs.readFileSync(verifierPath, 'utf8')) : [];
const ai = Object.create(AICommentary.prototype);
const results = [];
const requests = [];
for (const item of input.cases) {
  const now = DateTime.fromISO(input.now);
  const hook = item.hook || null;
  const result = { ...item, status: 'skipped', reason: observationIssue(item.event, now.toMillis()) };
  if (!result.reason) {
    const prompt = buildStoryPrompt(item.event, input.now, hook, input.recentPosts || []);
    if (!writerPath) {
      result.status = 'awaiting_writer';
      requests.push({ id: item.id, prompt,
        generationConfig: buildGeminiStructuredGenerationConfig(WRITER_RESPONSE_SCHEMA, 1, hook ? 'MEDIUM' : 'LOW') });
    } else {
      try {
        const response = writers.find(r => r.id === item.id);
        if (response?.error) throw new Error(response.error);
        const writer = parseEditorialWriterOutput(response?.text || '');
        result.post = cleanEditorialPost(writer.post);
        result.issues = result.post ? [...validateCommentaryCandidate(result.post, item.event, hook, writer.hookUsed),
          ...factualStoryIssues(result.post)] : ['empty or invalid prose'];
        result.writerUsage = response.usage;
        if (result.issues.length) result.reason = result.issues.join('; ');
        else if (!verifierPath) {
          result.status = 'awaiting_verifier';
          requests.push({ id: item.id, prompt: ai.buildVerifierPrompt({ event: item.event },
            writer.hookUsed ? hook : null, result.post, now),
          generationConfig: buildGeminiStructuredGenerationConfig(VERIFIER_RESPONSE_SCHEMA, 0, 'LOW') });
        } else {
          const response = verifiers.find(r => r.id === item.id);
          if (response?.error) throw new Error(response.error);
          const verdict = parseEditorialVerifierOutput(response?.text || '');
          result.verifierUsage = response.usage;
          result.status = verdict.verdict === 'PASS' ? 'passed_checks' : 'skipped';
          result.reason = verdict.reasons.join('; ');
        }
      } catch (error) { result.reason = error.message; }
    }
  }
  results.push(result);
}
fs.writeFileSync(outputPath, JSON.stringify({ provenance: input.provenance, now: input.now, results, requests }, null, 2));
console.log(JSON.stringify({ cases: results.length, requests: requests.length,
  statuses: results.reduce((r, x) => ({ ...r, [x.status]: (r[x.status] || 0) + 1 }), {}) }));
