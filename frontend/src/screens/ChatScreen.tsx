import React, { useCallback, useEffect, useRef, useState } from 'react';
import { FlatList, KeyboardAvoidingView, Platform, StyleSheet, TextInput, TouchableOpacity, View, Text, Alert } from 'react-native';
import { ChatMessage } from '../types/chat';
import { MessageBubble } from '../components/MessageBubble';
import { Ionicons } from '@expo/vector-icons';
import * as ImagePicker from 'expo-image-picker';
import * as FileSystem from 'expo-file-system';
import { Audio } from 'expo-av';
import { sendChatMessage, BackendChatResponse } from '../services/api';
import { BACKEND_URL } from '../config';
// WebView fallback not used with top shutter; retained if future inline rendering is reintroduced
// import { WebView } from 'react-native-webview';
import FormWebView from '../components/FormWebView';
import aadhaarMapping from '../forms/mappings/formAadhaar.json';
import incomeMapping from '../forms/mappings/formIncome.json';
import { createChatWebSocket, ChatWebSocket } from '../services/ws';

export const ChatScreen: React.FC = () => {
  const [messages, setMessages] = useState<ChatMessage[]>([{
    id: 'welcome', role: 'assistant', content: 'Hi! How can I assist you with government forms today?', createdAt: Date.now(), type: 'text'
  }]);
  const [input, setInput] = useState('');
  const [loading, setLoading] = useState(false);
  const [wsState, setWsState] = useState<'connecting' | 'open' | 'closed'>('connecting');
  const wsRef = useRef<ChatWebSocket | null>(null);
  const streamingMsgRef = useRef<ChatMessage | null>(null);
  const [activeFormUrl, setActiveFormUrl] = useState<string | null>(null);
  const [activeFormMapping, setActiveFormMapping] = useState<Record<string, any> | null>(null);
  const [formProgress, setFormProgress] = useState<any>(null);
  const listRef = useRef<FlatList<ChatMessage>>(null);
  const formWebViewRef = useRef<any>(null);

  const appendBackendMessages = (resp: BackendChatResponse) => {
    let newFormUrl: string | null = null;
    setMessages(prev => {
      const existingIds = new Set(prev.map(p => p.id));
      const newMsgs: ChatMessage[] = [];
      resp.messages.forEach((m, idx) => {
        let baseId = m.id || `${Date.now()}-${idx}`;
        // If duplicate, append role + idx + random suffix
        if (existingIds.has(baseId)) {
          baseId = `${baseId}-${m.role}-${idx}-${Math.random().toString(36).slice(2,6)}`;
        }
        existingIds.add(baseId);
        newMsgs.push({
          id: baseId,
          role: m.role,
          content: m.content,
          createdAt: Date.now(),
          type: (m.type === 'file' ? 'text' : m.type) as ChatMessage['type'],
          mediaUri: m.media_uri || undefined,
          formUrl: m.form_url || undefined
        });
        console.log("Received response ", m);
        if (m.role === 'assistant' && m.form_url) {
          newFormUrl = m.form_url;
        }
      });
      return [...prev, ...newMsgs];
    });
    if (newFormUrl) {
      // eslint-disable-next-line no-console
      console.log('[chat] activating form url', newFormUrl);
      setActiveFormUrl(newFormUrl);
      // pick a demo mapping based on filename
      try {
        const fname = String(newFormUrl).split('/').pop() || '';
        if (fname.includes('formAadhaar')) setActiveFormMapping(aadhaarMapping as any);
        else if (fname.includes('formIncome')) setActiveFormMapping(incomeMapping as any);
        else setActiveFormMapping(null);
      } catch (e) { setActiveFormMapping(null); }
    }
    requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));
  };

  // Audio streaming removed - now handled as complete audio messages in MessageBubble

  // Initialize websocket once
  useEffect(() => {
    const ws = createChatWebSocket({
      onState: (s) => setWsState(s),
      onDelta: (delta) => {
        setMessages(prev => {
          if (!streamingMsgRef.current) {
            const msg: ChatMessage = { id: 'streaming-' + Date.now(), role: 'assistant', content: delta, createdAt: Date.now(), type: 'text' };
            streamingMsgRef.current = msg;
            return [...prev, msg];
          } else {
            streamingMsgRef.current.content += delta;
            return [...prev];
          }
        });
      },
      // Remove audio delta streaming - we now handle complete audio messages
      onUserAudioTranscript: (transcript) => {
        // Update the user's audio message with the transcript for display
        setMessages(prev => {
          const updated = [...prev];
          const lastUserMsg = updated.findLast(m => m.role === 'user' && m.content === '[Audio message]');
          if (lastUserMsg) {
            lastUserMsg.content = transcript;
          }
          return updated;
        });
      },
      onAssistantMessage: (m) => {
        setMessages(prev => {
          // finalize streaming message if exists
          if (streamingMsgRef.current) {
            streamingMsgRef.current = null;
          }
          const msg: ChatMessage = { 
            id: m.id || 'assistant-' + Date.now(), 
            role: 'assistant', 
            content: m.content, 
            createdAt: Date.now(), 
            type: m.type || 'text',
            formUrl: m.form_url || undefined 
          };
          
          // Store audio data if this is an audio message
          if (m.type === 'audio' && m.audio_data) {
            // Convert base64 audio to blob URL for playback
            try {
              const binaryString = atob(m.audio_data);
              const bytes = new Uint8Array(binaryString.length);
              for (let i = 0; i < binaryString.length; i++) {
                bytes[i] = binaryString.charCodeAt(i);
              }
              const blob = new Blob([bytes], { type: 'audio/wav' });
              msg.mediaUri = URL.createObjectURL(blob);
            } catch (e) {
              console.warn('Failed to create audio blob:', e);
            }
          }
          
          return [...prev, msg];
        });
      },
      onFormOpen: (url) => {
        setActiveFormUrl(url);
        // No longer auto-fill on form open - wait for field-by-field updates
        setActiveFormMapping(null);
      },
      onFormFieldUpdate: (fieldId, value, progress) => {
        // eslint-disable-next-line no-console
        console.log('[form] field update:', fieldId, value, progress);
        setFormProgress(progress);
        
        // Update the specific field in the form
        if (formWebViewRef.current) {
          formWebViewRef.current.updateField(fieldId, value);
        }
      },
      onFormFieldFocus: (fieldId, progress) => {
        // eslint-disable-next-line no-console
        console.log('[form] field focus:', fieldId, progress);
        setFormProgress(progress);
        
        // Focus on the specific field in the form (highlight it)
        if (formWebViewRef.current) {
          formWebViewRef.current.focusField(fieldId);
        }
      },
      onFormCompleted: (formData) => {
        // eslint-disable-next-line no-console
        console.log('[form] completed:', formData);
        
        // Optionally show completion message or close form
        setMessages(prev => [...prev, {
          id: 'form-complete-' + Date.now(),
          role: 'assistant',
          content: 'Form completed successfully! All your information has been saved.',
          createdAt: Date.now(),
          type: 'text'
        }]);
      },
      onFormFieldError: (error, field) => {
        // eslint-disable-next-line no-console
        console.warn('[form] field error:', error, field);
        
        // Show error message in chat
        setMessages(prev => [...prev, {
          id: 'form-error-' + Date.now(),
          role: 'assistant',
          content: `Error: ${error}. Please try again.`,
          createdAt: Date.now(),
          type: 'text'
        }]);
      },
      onError: (err) => {
        // eslint-disable-next-line no-console
        console.warn('[ws] error', err);
      },
      debug: false
    });
    wsRef.current = ws;
    return () => { 
      ws.cleanup(); 
      // Cleanup audio resources
      if (recordingIntervalRef.current) {
        clearInterval(recordingIntervalRef.current);
      }
    };
  }, []);

  const sendMessage = useCallback(async () => {
    if (!input.trim() || loading) return;
    const text = input.trim();
    setMessages(prev => [...prev, { id: 'user-' + Date.now(), role: 'user', content: text, createdAt: Date.now(), type: 'text' }]);
    setInput('');
    if (wsRef.current && wsState === 'open') {
      wsRef.current.sendUserMessage(text);
    } else {
      // fallback to HTTP if websocket not ready
      setLoading(true);
      try {
        const resp = await sendChatMessage({ content: text, type: 'text' });
        appendBackendMessages(resp);
      } catch (e: any) {
        Alert.alert('Error', e.message || 'Failed to send message');
      } finally { setLoading(false); }
    }
  }, [input, loading, wsState]);

  const pickImage = useCallback(async () => {
    if (loading) return;
    const perm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!perm.granted) { Alert.alert('Permission required', 'Media library permission is needed'); return; }
    const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ImagePicker.MediaTypeOptions.Images, quality: 0.7 });
    if (result.canceled) return;
    const asset = result.assets?.[0];
    if (!asset || !asset.uri) { Alert.alert('Error', 'No image URI returned'); return; }
    try {
      const info = await FileSystem.getInfoAsync(asset.uri);
      if (!info.exists || (info.size != null && info.size === 0)) {
        // eslint-disable-next-line no-console
        console.warn('[image] invalid file', asset.uri, info);
        Alert.alert('Error', 'Selected image file is not accessible or empty');
        return;
      }
    } catch (err) {
      Alert.alert('Error', 'Could not verify image file');
      return;
    }
    setLoading(true);
    try {
      const resp = await sendChatMessage({ type: 'image', mediaUri: asset.uri });
      appendBackendMessages(resp);
    } catch (e: any) {
      Alert.alert('Error', e.message || 'Failed to send image');
    } finally {
      setLoading(false);
    }
  }, [loading, appendBackendMessages]);

  const [recording, setRecording] = useState<Audio.Recording | null>(null);
  const [isStreamingAudio, setIsStreamingAudio] = useState(false);
  const recordingIntervalRef = useRef<any>(null);

  // Audio streaming functionality
  const startAudioStreaming = useCallback(async () => {
    try {
      const perm = await Audio.requestPermissionsAsync();
      if (!perm.granted) { Alert.alert('Permission required', 'Microphone permission is needed'); return; }
      
      await Audio.setAudioModeAsync({ 
        allowsRecordingIOS: true, 
        playsInSilentModeIOS: true,
        shouldDuckAndroid: true,
        playThroughEarpieceAndroid: false,
      });

      // Create recording with high quality settings
      const { recording } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY
      );

      setRecording(recording);
      setIsStreamingAudio(true);

      // Note: For production audio streaming, you'd typically use a more sophisticated
      // approach to capture audio chunks in real-time rather than periodically
      // reading the entire recording file. This is a simplified implementation.

    } catch (e) {
      Alert.alert('Error', 'Could not start audio streaming');
      setIsStreamingAudio(false);
    }
  }, [wsState]);

  const stopAudioStreaming = useCallback(async () => {
    if (recordingIntervalRef.current) {
      clearInterval(recordingIntervalRef.current);
      recordingIntervalRef.current = null;
    }

    if (!recording) return;
    
    try {
      await recording.stopAndUnloadAsync();
      
      // Commit the final audio input
      if (wsRef.current && wsState === 'open') {
        wsRef.current.commitAudioInput();
      }
      
      // Add a user message indicating audio was sent
      setMessages(prev => [...prev, { 
        id: 'user-audio-' + Date.now(), 
        role: 'user', 
        content: '[Audio message]', 
        createdAt: Date.now(), 
        type: 'text' 
      }]);
      
    } catch (e) {
      Alert.alert('Error', 'Could not stop audio streaming');
    } finally {
      setRecording(null);
      setIsStreamingAudio(false);
    }
  }, [recording, wsState]);

  const toggleRecording = useCallback(() => {
    if (isStreamingAudio) {
      stopAudioStreaming();
    } else {
      startAudioStreaming();
    }
  }, [isStreamingAudio, startAudioStreaming, stopAudioStreaming]);

  // (Deprecated) formHeight logic removed – top shutter overlay handles sizing.

  return (
    <KeyboardAvoidingView style={styles.container} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      {activeFormUrl && (
        <FormWebView 
          ref={formWebViewRef}
          url={activeFormUrl} 
          autoFillData={activeFormMapping || undefined} 
          autoFillOnLoad={false}
          title={formProgress ? `Form Progress: ${Math.round(formProgress.percentage)}%` : 'Form'}
          onClose={() => { 
            setActiveFormUrl(null); 
            setActiveFormMapping(null); 
            setFormProgress(null);
          }} 
          onFieldUpdate={(fieldId, value) => {
            // eslint-disable-next-line no-console
            console.log('[chat] field updated:', fieldId, value);
          }}
        />
      )}
      <View style={styles.chatArea}>
        <FlatList
          ref={listRef}
          data={messages}
          keyExtractor={m => m.id}
          renderItem={({ item }) => <MessageBubble message={item} />}
          contentContainerStyle={styles.listContent}
          onContentSizeChange={() => listRef.current?.scrollToEnd({ animated: true })}
        />
      </View>
      <View style={styles.inputRow}>
        <TouchableOpacity style={styles.iconBtn} onPress={pickImage}>
          <Ionicons name="image-outline" size={22} color="#374151" />
        </TouchableOpacity>
        <TouchableOpacity style={styles.iconBtn} onPress={toggleRecording}>
          <Ionicons name={isStreamingAudio ? 'stop-circle-outline' : 'mic-outline'} size={22} color={isStreamingAudio ? '#dc2626' : '#374151'} />
        </TouchableOpacity>

        <TextInput
          style={styles.textInput}
            placeholder={wsState === 'open' ? 'Type a message' : `Connecting... (${wsState})`}
            value={input}
            onChangeText={setInput}
            onSubmitEditing={sendMessage}
            returnKeyType="send"
            editable={wsState !== 'connecting'}
        />
        <TouchableOpacity style={[styles.sendBtn, loading && { opacity: 0.5 }]} onPress={sendMessage} disabled={loading}>
          <Ionicons name={loading ? 'hourglass-outline' : 'send'} size={18} color="#fff" />
        </TouchableOpacity>
      </View>
    </KeyboardAvoidingView>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: '#ffffff' },
  chatArea: { flex: 1 },
  listContent: { paddingVertical: 12 },
  inputRow: { flexDirection: 'row', padding: 8, alignItems: 'center', borderTopWidth: 1, borderColor: '#e5e7eb', backgroundColor: '#f9fafb' },
  textInput: { flex: 1, backgroundColor: '#fff', borderWidth: 1, borderColor: '#d1d5db', borderRadius: 20, paddingHorizontal: 14, paddingVertical: 8, marginHorizontal: 6 },
  sendBtn: { backgroundColor: '#2563eb', borderRadius: 20, padding: 10 },
  iconBtn: { padding: 6 }
});
