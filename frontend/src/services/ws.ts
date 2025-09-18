import { BACKEND_HOST, BACKEND_PORT } from '../config';
import { ChatMessage } from '../types/chat';

export type WSState = 'connecting' | 'open' | 'closed';

export interface AssistantDeltaEvent { type: 'assistant_delta'; delta: string }
export interface AssistantAudioDeltaEvent { type: 'assistant_audio_delta'; delta: string }
export interface AssistantMessageEvent { type: 'assistant_message'; message: ChatMessageLike }
export interface AudioMessageEvent { type: 'audio_message'; message: ChatMessageLike }
export interface AssistantMessageEvent { type: 'assistant_message'; message: ChatMessageLike }
export interface AudioMessageEvent { type: 'audio_message'; message: ChatMessageLike }
export interface FormOpenEvent { type: 'form_open'; url: string }
export interface FormFieldUpdateEvent { 
  type: 'form_field_update'; 
  field_update: { field_id: string; value: any };
  form_progress: { current_index: number; total_fields: number; percentage: number; is_complete: boolean }
}
export interface FormFieldFocusEvent { 
  type: 'form_field_focus'; 
  field_focus: { field_id: string };
  form_progress: { current_index: number; total_fields: number; percentage: number; is_complete: boolean }
}
export interface FormCompletedEvent { type: 'form_completed'; form_data: Record<string, any> }
export interface FormFieldErrorEvent { type: 'form_field_error'; error: string; field?: any }
export interface AckEvent { type: 'ack'; message_id: string }
export interface ErrorEvent { type: 'error'; error: string }
export interface PongEvent { type: 'pong' }
export type IncomingEvent = AssistantDeltaEvent | AssistantAudioDeltaEvent | AssistantMessageEvent | AudioMessageEvent | FormOpenEvent | FormFieldUpdateEvent | FormFieldFocusEvent | FormCompletedEvent | FormFieldErrorEvent | AckEvent | ErrorEvent | PongEvent;

export interface ChatMessageLike {
  id?: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  type?: 'text' | 'audio';
  form_url?: string | null;
  media_uri?: string;
  audio_data?: string;  // base64 audio data
}

export interface WSListeners {
  onState?: (s: WSState) => void;
  onDelta?: (delta: string) => void;
  onAudioDelta?: (delta: string) => void;
  onAssistantMessage?: (msg: ChatMessageLike) => void;
  onAudioMessage?: (msg: ChatMessageLike) => void;
  onFormOpen?: (url: string) => void;
  onFormFieldUpdate?: (fieldId: string, value: any, progress: any) => void;
  onFormFieldFocus?: (fieldId: string, progress: any) => void;
  onFormCompleted?: (formData: Record<string, any>) => void;
  onFormFieldError?: (error: string, field?: any) => void;
  onError?: (err: string) => void;
  debug?: boolean;
}

const RETRY_BASE = 800; // ms

export class ChatWebSocket {
  private ws: WebSocket | null = null;
  private listeners: WSListeners;
  private retry = 0;
  private url: string;
  private heartbeat?: any;

  constructor(listeners: WSListeners) {
    this.listeners = listeners;
    const proto = 'ws';
    this.url = `${proto}://${BACKEND_HOST}:${BACKEND_PORT}/api/chat/ws`;
    this.connect();
  }

  private log(...args: any[]) { if (this.listeners.debug) console.log('[ws]', ...args); }

  private setState(s: WSState) { this.listeners.onState?.(s); }

  private scheduleReconnect() {
    this.cleanup();
    const delay = Math.min(8000, RETRY_BASE * Math.pow(1.7, this.retry++));
    this.log('reconnect in', delay);
    setTimeout(() => this.connect(), delay);
  }

  private connect() {
    if (this.ws) return;
    try {
      this.setState('connecting');
      this.ws = new WebSocket(this.url);
      this.ws.onopen = () => {
        this.retry = 0;
        this.setState('open');
        this.log('open');
        this.startHeartbeat();
      };
      this.ws.onclose = () => {
        this.setState('closed');
        this.log('closed');
        this.scheduleReconnect();
      };
      this.ws.onerror = (e) => {
        this.log('error', e);
      };
      this.ws.onmessage = (ev) => this.handleMessage(ev.data);
    } catch (e: any) {
      this.listeners.onError?.(String(e?.message || e));
      this.scheduleReconnect();
    }
  }

  private startHeartbeat() {
    if (this.heartbeat) clearInterval(this.heartbeat);
    this.heartbeat = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 15000);
  }

  private handleMessage(raw: string) {
    let evt: IncomingEvent | any;
    try { evt = JSON.parse(raw); } catch { return; }
    switch (evt.type) {
      case 'assistant_delta':
        this.listeners.onDelta?.(evt.delta || '');
        break;
      case 'assistant_audio_delta':
        this.listeners.onAudioDelta?.(evt.delta || '');
        break;
      case 'assistant_message':
        this.listeners.onAssistantMessage?.(evt.message);
        // Check if the message contains form information
        if (evt.form && evt.form.url) {
          const formUrl = `http://${BACKEND_HOST}:${BACKEND_PORT}${evt.form.url}`;
          this.listeners.onFormOpen?.(formUrl);
        }
        break;
      case 'audio_message':
        this.listeners.onAudioMessage?.(evt.message);
        break;
      case 'form_open':
        // Ensure form URLs use HTTP protocol
        const formUrl = evt.url.replace(/^ws:\/\//, 'http://');
        this.listeners.onFormOpen?.(formUrl);
        break;
      case 'form_field_update':
        this.listeners.onFormFieldUpdate?.(
          evt.field_update.field_id, 
          evt.field_update.value, 
          evt.form_progress
        );
        break;
      case 'form_field_focus':
        this.listeners.onFormFieldFocus?.(
          evt.field_focus.field_id, 
          evt.form_progress
        );
        break;
      case 'form_completed':
        this.listeners.onFormCompleted?.(evt.form_data);
        break;
      case 'form_field_error':
        this.listeners.onFormFieldError?.(evt.error, evt.field);
        break;
      case 'error':
        this.listeners.onError?.(evt.error);
        break;
      default:
        break;
    }
  }

  sendUserMessage(content: string) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.listeners.onError?.('socket_not_open');
      return;
    }
    this.ws.send(JSON.stringify({ type: 'user_message', content }));
  }

  async sendAudioMessage(audioUri: string) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.listeners.onError?.('socket_not_open');
      return;
    }

    try {
      // Convert blob URI to base64 data
      const response = await fetch(audioUri);
      const blob = await response.blob();
      const arrayBuffer = await blob.arrayBuffer();
      const base64 = btoa(String.fromCharCode(...new Uint8Array(arrayBuffer)));
      
      this.ws.send(JSON.stringify({ 
        type: 'user_audio_message', 
        audio_data: base64
      }));
    } catch (error) {
      this.listeners.onError?.('Failed to process audio: ' + String(error));
    }
  }

  cleanup() {
    if (this.heartbeat) clearInterval(this.heartbeat);
    if (this.ws) {
      try { this.ws.close(); } catch {}
      this.ws = null;
    }
  }
}

export function createChatWebSocket(listeners: WSListeners) {
  return new ChatWebSocket(listeners);
}
