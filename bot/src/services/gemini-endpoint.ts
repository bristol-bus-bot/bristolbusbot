// Where Gemini requests go. "gemini" is the Gemini Developer API (AI Studio
// billing, now prepaid credit); "vertex" is Gemini on Vertex AI, billed to the
// Google Cloud account like any other Cloud service. Same models and request
// body, except Vertex takes responseFormat as a list.

export type GeminiProvider = 'gemini' | 'vertex';

export interface GeminiEndpointConfig {
    apiKey: string;
    provider?: GeminiProvider;
    vertexProject?: string;
    vertexLocation?: string;
}

const SAFE_NAME = /^[A-Za-z0-9._-]{1,100}$/;

export function geminiEndpoint(config: GeminiEndpointConfig, model: string): { url: string; headers: Record<string, string> } {
    if (!SAFE_NAME.test(model)) throw new Error('Gemini model name is unsafe');
    const headers = { 'Content-Type': 'application/json', 'x-goog-api-key': config.apiKey };
    if (config.provider !== 'vertex') {
        return { url: `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent`, headers };
    }
    const project = config.vertexProject || '';
    const location = config.vertexLocation || 'global';
    if (!SAFE_NAME.test(project) || !SAFE_NAME.test(location)) throw new Error('Vertex project or location is unsafe');
    const host = location === 'global' ? 'aiplatform.googleapis.com' : `${location}-aiplatform.googleapis.com`;
    return {
        url: `https://${host}/v1/projects/${project}/locations/${location}/publishers/google/models/${model}:generateContent`,
        headers,
    };
}
