import type { BusEvent } from '../types/bus-types.js';
import type {
    EditorialRequirement,
    EditorialSelection,
} from './editorial-context.js';

export interface EditorialWriterOutput {
    post: string;
    hookUsed: boolean;
}

export interface EditorialVerifierOutput {
    verdict: 'PASS' | 'FAIL';
    reasons: string[];
}

export const WRITER_RESPONSE_SCHEMA = {
    type: 'object',
    additionalProperties: false,
    properties: {
        post: {
            type: 'string',
            description: 'The finished Bluesky post, at most 300 characters.',
        },
        hook_used: {
            type: 'boolean',
            description: 'Whether the supplied editorial hook is actually present in the post.',
        },
    },
    required: ['post', 'hook_used'],
} as const;

export const VERIFIER_RESPONSE_SCHEMA = {
    type: 'object',
    additionalProperties: false,
    properties: {
        verdict: {
            type: 'string',
            enum: ['PASS', 'FAIL'],
        },
        reasons: {
            type: 'array',
            items: { type: 'string' },
            maxItems: 6,
        },
    },
    required: ['verdict', 'reasons'],
} as const;

const NUMBER_WORDS = new Map<number, string>([
    [0, 'zero'],
    [1, 'one'],
    [2, 'two'],
    [3, 'three'],
    [4, 'four'],
    [5, 'five'],
    [6, 'six'],
    [7, 'seven'],
    [8, 'eight'],
    [9, 'nine'],
    [10, 'ten'],
    [11, 'eleven'],
    [12, 'twelve'],
    [13, 'thirteen'],
    [14, 'fourteen'],
    [15, 'fifteen'],
    [16, 'sixteen'],
    [17, 'seventeen'],
    [18, 'eighteen'],
    [19, 'nineteen'],
    [20, 'twenty'],
    [21, 'twenty-one'],
    [22, 'twenty-two'],
    [23, 'twenty-three'],
    [24, 'twenty-four'],
    [25, 'twenty-five'],
    [26, 'twenty-six'],
    [27, 'twenty-seven'],
    [28, 'twenty-eight'],
    [29, 'twenty-nine'],
    [30, 'thirty'],
]);

const GENERIC_LOCATION_WORDS = new Set([
    'avenue',
    'bus',
    'centre',
    'center',
    'close',
    'drive',
    'lane',
    'park',
    'parade',
    'place',
    'ride',
    'road',
    'station',
    'stop',
    'street',
]);

function requireObject(value: unknown, name: string): Record<string, unknown> {
    if (!value || typeof value !== 'object' || Array.isArray(value)) {
        throw new Error(`${name} must be an object`);
    }
    return value as Record<string, unknown>;
}

function parseJsonObject(value: string, name: string): Record<string, unknown> {
    let cleaned = value.trim();
    if (cleaned.startsWith('```')) {
        cleaned = cleaned
            .replace(/^```(?:json)?\s*/i, '')
            .replace(/\s*```$/, '');
    }
    return requireObject(JSON.parse(cleaned), name);
}

export function parseEditorialWriterOutput(value: string): EditorialWriterOutput {
    const parsed = parseJsonObject(value, 'writer response');
    if (typeof parsed.post !== 'string' || !parsed.post.trim()) {
        throw new Error('writer response.post must be a non-empty string');
    }
    if (typeof parsed.hook_used !== 'boolean') {
        throw new Error('writer response.hook_used must be boolean');
    }
    return {
        post: parsed.post,
        hookUsed: parsed.hook_used,
    };
}

export function parseEditorialVerifierOutput(value: string): EditorialVerifierOutput {
    const parsed = parseJsonObject(value, 'verifier response');
    if (parsed.verdict !== 'PASS' && parsed.verdict !== 'FAIL') {
        throw new Error('verifier response.verdict must be PASS or FAIL');
    }
    if (!Array.isArray(parsed.reasons)
        || parsed.reasons.some(reason => typeof reason !== 'string')) {
        throw new Error('verifier response.reasons must be an array of strings');
    }
    return {
        verdict: parsed.verdict,
        reasons: parsed.reasons.slice(0, 6),
    };
}

export function containsWebLink(value: string): boolean {
    return /(?:https?:\/\/|www\.)\S+/i.test(value);
}

export function containsSourceReference(value: string): boolean {
    return containsWebLink(value) || /(?:^|\s)Source\s*:/i.test(value);
}

export function cleanEditorialPost(value: string): string | null {
    let result = value
        .trim()
        .replace(/^```(?:text)?\s*/i, '')
        .replace(/\s*```$/, '')
        .replace(/^(?:Here's|Here is|Post:|Draft:)\s*/i, '')
        .replace(/\s+/g, ' ')
        .trim();
    if (!result || containsSourceReference(result)) return null;

    result = result
        .replace(/#[\p{L}\p{N}_]+/gu, '')
        .replace(/\p{Emoji_Presentation}|\p{Extended_Pictographic}/gu, '')
        .replace(
            /\b(usb|port|socket|kernel|stack|modem|ram|cpu|gpu|cache|firmware|io|ethernet|wi[- ]?fi|bluetooth)\b/gi,
            '',
        )
        .replace(/\s{2,}/g, ' ')
        .trim();
    return result || null;
}

function normalise(value: string): string {
    return value
        .normalize('NFKC')
        .toLocaleLowerCase('en-GB')
        .replace(/[’‘]/g, "'")
        .replace(/[–—]/g, '-')
        .replace(/\s+/g, ' ')
        .trim();
}

export function missingEditorialRequirements(
    post: string,
    requirements: EditorialRequirement[],
): string[] {
    const normalisedPost = normalise(post);
    return requirements
        .filter(requirement => !requirement.alternatives.some(
            alternative => normalisedPost.includes(normalise(alternative)),
        ))
        .map(requirement => requirement.label);
}

function escapeRegExp(value: string): string {
    return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function hasRoute(post: string, route: string): boolean {
    const pattern = new RegExp(
        `(?:^|[^\\p{L}\\p{N}])(?:route\\s+|the\\s+)?${escapeRegExp(route)}(?:$|[^\\p{L}\\p{N}])`,
        'iu',
    );
    return pattern.test(post);
}

function hasLocation(post: string, stopName: string): boolean {
    const postWords = new Set(
        normalise(post).match(/[\p{L}\p{N}]+/gu) || [],
    );
    const allStopWords = normalise(stopName).match(/[\p{L}\p{N}]+/gu) || [];
    const distinctive = allStopWords.filter(
        word => word.length >= 4 && !GENERIC_LOCATION_WORDS.has(word),
    );
    const required = distinctive.length > 0
        ? distinctive
        : allStopWords.filter(word => word.length >= 3);
    return required.length > 0 && required.some(word => postWords.has(word));
}

function hasObservedStatus(post: string, event: BusEvent): boolean {
    const normalisedPost = normalise(post);
    if (event.eventType === 'punctual') {
        return /\b(on time|on schedule|spot on|precisely on time|right on time|keeps? (?:to )?(?:its |the )?timing|exactly when (?:it|the timetable) said)\b/.test(
            normalisedPost,
        );
    }

    const minutes = Math.abs(event.delayMinutes);
    const word = NUMBER_WORDS.get(minutes);
    const numberPattern = word
        ? `(?:${minutes}|${escapeRegExp(word)})`
        : `${minutes}`;
    if (!new RegExp(`\\b${numberPattern}\\b`, 'i').test(normalisedPost)) {
        return false;
    }
    if (event.eventType === 'early') {
        return /\b(early|ahead)\b/.test(normalisedPost);
    }
    return /\b(late|behind|delayed|adrift|off the pace)\b/.test(normalisedPost);
}

function sentenceCount(post: string): number {
    return post.split(/(?<=[.!?])\s+/).filter(Boolean).length;
}

export function validateCommentaryCandidate(
    post: string,
    event: BusEvent,
    hook: EditorialSelection | null,
    hookUsed: boolean,
): string[] {
    const issues: string[] = [];
    if (post.length > 300) issues.push(`post is ${post.length} characters; maximum is 300`);
    if (!/[.!?]["']?$/.test(post)) issues.push('post must end with sentence punctuation');
    const sentences = sentenceCount(post);
    if (sentences < 1 || sentences > 2) issues.push('post must contain one or two sentences');
    if (containsSourceReference(post)) issues.push('post contains a source reference or web link');
    if (/#[\p{L}\p{N}_]+/u.test(post)) issues.push('post contains a hashtag');
    if (/\p{Emoji_Presentation}|\p{Extended_Pictographic}/u.test(post)) {
        issues.push('post contains an emoji');
    }
    if (!hasRoute(post, event.line)) issues.push(`post is missing route ${event.line}`);
    if (!new RegExp(`\\b${escapeRegExp(event.direction)}\\b`, 'i').test(post)) {
        issues.push(`post is missing ${event.direction} direction`);
    }
    if (event.lastStopName && !hasLocation(post, event.lastStopName)) {
        issues.push(`post is missing location ${event.lastStopName}`);
    }
    if (!hasObservedStatus(post, event)) {
        const status = event.eventType === 'punctual'
            ? 'on time'
            : `${Math.abs(event.delayMinutes)} minutes ${event.eventType === 'early' ? 'early' : 'late'}`;
        issues.push(`post is missing the exact observed status: ${status}`);
    }
    if (hookUsed && !hook) issues.push('hook_used is true but no hook was supplied');
    if (hookUsed && hook) {
        issues.push(...missingEditorialRequirements(post, hook.requirements).map(
            label => `editorial requirement is missing: ${label}`,
        ));
    }
    return issues;
}
