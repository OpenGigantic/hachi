// ============================================================
// Hachi Web App — voice domain adapter
//
// Wires: POST /voice/transcribe, POST /voice/speak,
//        GET /voice/config, PUT /voice/config
//
// All calls go through the standard gateway base + bearer pattern
// (Vite proxy in dev, VITE_HACHI_GATEWAY_URL in prod).
// ============================================================

import { API_BASE, authHeaders, ApiError } from "../client";

// ---- Types ----

export interface VoiceConfig {
  stt_provider: string;
  tts_provider: string;
  tts_voice: string;
  stt_model: string | null;
  tts_model: string | null;
  elevenlabs_voice_id: string | null;
  available: {
    tts_providers: string[];
    voices: string[];
  };
  configured: boolean;
}

export interface VoiceConfigPatch {
  stt_provider?: string;
  tts_provider?: string;
  tts_voice?: string;
  stt_model?: string;
  tts_model?: string;
  stt_base_url?: string;
  tts_base_url?: string;
  stt_api_key_env?: string;
  tts_api_key_env?: string;
  elevenlabs_api_key_env?: string;
  elevenlabs_voice_id?: string;
}

export interface TranscribeResult {
  text: string;
  language?: string;
}

export interface SpeakOptions {
  voice?: string;
  model?: string;
  format?: "mp3" | "opus" | "aac" | "flac" | "wav";
  provider?: string;
}

// ---- Adapters ----

/**
 * Upload a recorded audio Blob and receive the transcript text.
 *
 * Sends a multipart/form-data POST to POST /voice/transcribe.
 * The file field is named "audio"; filename is derived from the MIME type.
 *
 * @param blob - Raw audio Blob from MediaRecorder (webm, wav, etc.)
 * @returns The transcript result including the `text` field.
 * @throws ApiError on gateway/network errors.
 */
export async function transcribeAudio(blob: Blob): Promise<TranscribeResult> {
  const ext = _blobExt(blob.type);
  const form = new FormData();
  form.append("audio", blob, `recording.${ext}`);

  const url = `${API_BASE}/voice/transcribe`;
  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: authHeaders(),
      body: form,
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Network error";
    throw new ApiError(msg, 0, "network_error");
  }

  if (!res.ok) {
    let message = `POST /voice/transcribe → ${res.status}`;
    try {
      const body = await res.json() as Record<string, unknown>;
      if (typeof body.message === "string") message = body.message;
    } catch { /* ignore */ }
    throw new ApiError(message, res.status);
  }

  return res.json() as Promise<TranscribeResult>;
}

/**
 * Synthesize text to speech and return a temporary object URL for playback.
 *
 * Sends a JSON POST to POST /voice/speak and wraps the response bytes
 * in a Blob object URL. The caller is responsible for revoking the URL
 * with `URL.revokeObjectURL(url)` once playback finishes.
 *
 * @param text - The text to synthesize.
 * @param opts - Optional overrides for voice, model, format, and provider.
 * @returns A temporary object URL pointing to the audio blob.
 * @throws ApiError on gateway/network errors.
 */
export async function speakText(text: string, opts: SpeakOptions = {}): Promise<string> {
  const url = `${API_BASE}/voice/speak`;
  const body = {
    text,
    voice: opts.voice,
    model: opts.model,
    format: opts.format ?? "mp3",
    provider: opts.provider,
  };

  let res: Response;
  try {
    res = await fetch(url, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(body),
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Network error";
    throw new ApiError(msg, 0, "network_error");
  }

  if (!res.ok) {
    let message = `POST /voice/speak → ${res.status}`;
    try {
      const errBody = await res.json() as Record<string, unknown>;
      if (typeof errBody.message === "string") message = errBody.message;
    } catch { /* ignore */ }
    throw new ApiError(message, res.status);
  }

  const contentType = res.headers.get("Content-Type") ?? "audio/mpeg";
  const audioBlob = await res.blob();
  const typedBlob = new Blob([audioBlob], { type: contentType });
  return URL.createObjectURL(typedBlob);
}

/**
 * Fetch the current voice configuration from the gateway.
 *
 * @returns The gateway voice config summary.
 * @throws ApiError on network/gateway errors.
 */
export async function getVoiceConfig(): Promise<VoiceConfig> {
  const url = `${API_BASE}/voice/config`;
  let res: Response;
  try {
    res = await fetch(url, {
      method: "GET",
      headers: authHeaders(),
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Network error";
    throw new ApiError(msg, 0, "network_error");
  }
  if (!res.ok) throw new ApiError(`GET /voice/config → ${res.status}`, res.status);
  return res.json() as Promise<VoiceConfig>;
}

/**
 * Persist voice config fields via the gateway and return the updated config.
 *
 * @param patch - Partial voice settings to persist.
 * @returns The updated voice config summary.
 * @throws ApiError on network/gateway errors.
 */
export async function setVoiceConfig(patch: VoiceConfigPatch): Promise<VoiceConfig> {
  const url = `${API_BASE}/voice/config`;
  let res: Response;
  try {
    res = await fetch(url, {
      method: "PUT",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(patch),
    });
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Network error";
    throw new ApiError(msg, 0, "network_error");
  }
  if (!res.ok) throw new ApiError(`PUT /voice/config → ${res.status}`, res.status);
  return res.json() as Promise<VoiceConfig>;
}

// ---- Helpers ----

function _blobExt(mimeType: string): string {
  const map: Record<string, string> = {
    "audio/webm": "webm",
    "audio/webm;codecs=opus": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/flac": "flac",
  };
  const base = mimeType.split(";")[0].trim().toLowerCase();
  return map[base] ?? "webm";
}
