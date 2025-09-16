import { BACKEND_HOST, BACKEND_PORT } from '../config';
import { ChatMessage } from '../types/chat';

export type WSState = 'connecting' | 'open' | 'closed';

export interface AssistantDeltaEvent { type: 'assistant_delta'; delta: string }
export interface AssistantMessageEvent { type: 'assistant_message'; message: ChatMessageLike }
export interface FormOpenEvent { type: 'form_open'; url: string }
export interface AckEvent { type: 'ack'; message_id: string }
export interface ErrorEvent { type: 'error'; error: string }
export interface PongEvent { type: 'pong' }
export type IncomingEvent = AssistantDeltaEvent | AssistantMessageEvent | FormOpenEvent | AckEvent | ErrorEvent | PongEvent;

export interface ChatMessageLike {
  id?: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  type?: 'text';
  form_url?: string | null;
}

export interface WSListeners {
  onState?: (s: WSState) => void;
  onDelta?: (delta: string) => void;
  onAssistantMessage?: (msg: ChatMessageLike) => void;
  onFormOpen?: (url: string) => void;
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
      case 'assistant_message':
        this.listeners.onAssistantMessage?.(evt.message);
        break;
      case 'form_open':
        // Ensure form URLs use HTTP protocol
        const formUrl = evt.url.replace(/^ws:\/\//, 'http://');
        this.listeners.onFormOpen?.(formUrl);
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
