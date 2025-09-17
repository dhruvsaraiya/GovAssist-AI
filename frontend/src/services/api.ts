import { BACKEND_URL } from '../config';
import * as FileSystem from 'expo-file-system';

export interface SendMessagePayload {
  content?: string;
  type: 'text' | 'image' | 'audio';
  mediaUri?: string; // local file URI for media
}

export interface BackendMessage {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  type: 'text' | 'image' | 'audio' | 'file';
  media_uri?: string | null;
  form_url?: string | null;
}

export interface BackendChatResponse { messages: BackendMessage[] }

async function fileInfoToBlob(uri: string): Promise<any> {
  // Expo fetch FormData can accept { uri, name, type }
  const name = uri.split('/').pop() || 'upload';
  let type = 'application/octet-stream';
  if (name.match(/\.(png|jpg|jpeg|gif|webp)$/i)) type = 'image/jpeg';
  if (name.match(/\.(wav)$/i)) type = 'audio/wav';
  if (name.match(/\.(mp3)$/i)) type = 'audio/mpeg';
  return { uri, name, type };
}

export async function checkBackend(timeoutMs = 4000): Promise<boolean> {
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${BACKEND_URL}/health`, { signal: controller.signal });
    clearTimeout(t);
    return res.ok;
  } catch (e) {
    clearTimeout(t);
    // eslint-disable-next-line no-console
    console.warn('[api] Health check failed', e);
    return false;
  }
}

export async function sendChatMessage(payload: SendMessagePayload): Promise<BackendChatResponse> {
  const form = new FormData();
  if (payload.type === 'text') {
    form.append('text', payload.content || '');
    form.append('media_type', 'text');
  } else if (payload.mediaUri) {
    const fileObj = await fileInfoToBlob(payload.mediaUri);
    form.append('media_type', payload.type);
    // @ts-ignore - React Native FormData file structure
    form.append('file', fileObj);
  }

  const url = `${BACKEND_URL}/api/chat`;
  let res: Response;
  try {
    // eslint-disable-next-line no-console
    console.log('[api] POST', url, 'payloadType=', payload.type);
    res = await fetch(url, { method: 'POST', body: form });
  } catch (e: any) {
    throw new Error(`Network request failed: ${e?.message || e}`);
  }
  if (!res.ok) {
    let text: string;
    try { text = await res.text(); } catch { text = '<no body>'; }
    throw new Error(`Backend error ${res.status}: ${text}`);
  }
  try {
    const data = await res.json() as BackendChatResponse;
    return data;
  } catch (e) {
    throw new Error('Invalid JSON in response');
  }
}

export async function restartAllSessions(): Promise<{ success: boolean; message?: string; error?: string }> {
  try {
    const res = await fetch(`${BACKEND_URL}/api/chat/restart`, { 
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      }
    });
    
    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }
    
    const data = await res.json();
    return data;
  } catch (e: any) {
    return {
      success: false,
      error: e?.message || 'Failed to restart sessions'
    };
  }
}
