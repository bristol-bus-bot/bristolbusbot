// Where Gemini requests go. "gemini" is the Gemini Developer API (AI Studio
// billing, now prepaid credit); "agent-platform" is Gemini on Agent Platform, billed to the
// Google Cloud account like any other Cloud service. Same models and request
// body, except Agent Platform takes responseFormat as a list.

export type GeminiProvider = 'gemini' | 'agent-platform';

export interface GeminiEndpointConfig {
    apiKey: string;
    provider?: GeminiProvider;
    agentPlatformProject?: string;
    agentPlatformLocation?: string;
}

const SAFE_NAME = /^[A-Za-z0-9._-]{1,100}$/;

export function geminiEndpoint(config: GeminiEndpointConfig, model: string): { url: string; headers: Record<string, string> } {
    if (!SAFE_NAME.test(model)) throw new Error('Gemini model name is unsafe');
    const headers = { 'Content-Type': 'application/json', 'x-goog-api-key': config.apiKey };
    if (config.provider !== 'agent-platform') {
        return { url: `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, headers };
    }
    const project = config.agentPlatformProject || '';
    const location = config.agentPlatformLocation || 'global';
    if (!SAFE_NAME.test(project) || !SAFE_NAME.test(location)) throw new Error('Agent Platform project or location is unsafe');
    const host = location === 'global' ? 'aiplatform.googleapis.com' : `${location}-aiplatform.googleapis.com`;
    return {
        url: `https://${host}/v1/projects/${project}/locations/${location}/publishers/google/models/${model}:generateContent`,
        headers,
    };
}
