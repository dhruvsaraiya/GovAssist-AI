export type MessageRole = 'user' | 'assistant' | 'system';

export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  createdAt: number;
  type?: 'text' | 'image' | 'audio';
  mediaUri?: string; // local or remote URI for image/audio
  formUrl?: string; // optional URL to render in an embedded WebView
}
