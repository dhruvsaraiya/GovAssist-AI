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
    // Guard against undefined or null messages array
    if (!resp || !resp.messages || !Array.isArray(resp.messages)) {
      console.warn('[chat] Invalid response format:', resp);
      return;
    }
    
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
            type: 'text', 
            formUrl: m.form_url || undefined 
          };
          
          // Handle form activation from websocket message
          if (m.form_url) {
            console.log('[chat] activating form url from websocket', m.form_url);
            setActiveFormUrl(m.form_url);
            try {
              const fname = String(m.form_url).split('/').pop() || '';
              if (fname.includes('formAadhaar')) setActiveFormMapping(aadhaarMapping as any);
              else if (fname.includes('formIncome')) setActiveFormMapping(incomeMapping as any);
              else setActiveFormMapping(null);
            } catch (e) { setActiveFormMapping(null); }
          }
          
          return [...prev, msg];
        });
      },
      onTranscript: (transcript) => {
        // Handle voice message transcript from websocket
        console.log('[ws] transcript received:', transcript);
        // Replace the "Processing..." message with the actual transcript
        setMessages(prev => {
          const filtered = prev.filter(m => !m.id.startsWith('user-audio-processing-'));
          return [...filtered, { 
            id: 'user-transcript-' + Date.now(), 
            role: 'user', 
            content: `🎤 "${transcript}"`, 
            createdAt: Date.now(), 
            type: 'text' 
          }];
        });
      },
      onSpeechStarted: () => {
        console.log('[ws] speech started');
        setMessages(prev => {
          const filtered = prev.filter(m => !m.id.startsWith('user-audio-processing-'));
          return [...filtered, { 
            id: 'user-audio-processing-' + Date.now(), 
            role: 'user', 
            content: '🎤 Listening...', 
            createdAt: Date.now(), 
            type: 'text' 
          }];
        });
      },
      onSpeechStopped: () => {
        console.log('[ws] speech stopped');
        setMessages(prev => {
          const filtered = prev.filter(m => !m.id.startsWith('user-audio-processing-'));
          return [...filtered, { 
            id: 'user-audio-processing-' + Date.now(), 
            role: 'user', 
            content: '🎤 Processing speech...', 
            createdAt: Date.now(), 
            type: 'text' 
          }];
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
        console.warn('[ws] error', err);
        // Clear any processing messages on error
        setMessages(prev => {
          const filtered = prev.filter(m => 
            !m.id.startsWith('user-audio-processing-') && 
            !m.id.startsWith('recording-tip-')
          );
          return [...filtered, {
            id: 'error-' + Date.now(),
            role: 'assistant',
            content: `Audio processing error: ${err}. Please try speaking again or type your message.`,
            createdAt: Date.now(),
            type: 'text'
          }];
        });
      },
      debug: true  // Enable debug logging
    });
    wsRef.current = ws;
    return () => { ws.cleanup(); };
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

  const startRecording = useCallback(async () => {
    try {
      const perm = await Audio.requestPermissionsAsync();
      if (!perm.granted) { Alert.alert('Permission required', 'Microphone permission is needed'); return; }
      await Audio.setAudioModeAsync({ allowsRecordingIOS: true, playsInSilentModeIOS: true });

      // Custom PCM16 mono 16k options (aiming for easier realtime ingestion)
      const recordingOptions: Audio.RecordingOptions = {
        android: {
          extension: '.wav',
          outputFormat: Audio.AndroidOutputFormat.DEFAULT, // container may still be WAV/PCM
          audioEncoder: Audio.AndroidAudioEncoder.DEFAULT,
          sampleRate: 16000,
          numberOfChannels: 1,
          bitRate: 256000
        },
        ios: {
          extension: '.wav',
          audioQuality: Audio.IOSAudioQuality.HIGH,
          sampleRate: 16000,
          numberOfChannels: 1,
          bitRate: 256000,
          linearPCMBitDepth: 16,
          linearPCMIsBigEndian: false,
          linearPCMIsFloat: false
        },
        web: {}
      };

      const { recording } = await Audio.Recording.createAsync(recordingOptions);
      setRecording(recording);
      
      // Show helpful message about recording
      setMessages(prev => [...prev, {
        id: 'recording-tip-' + Date.now(),
        role: 'assistant',
        content: '🎤 Recording... Please speak clearly for at least 2-3 seconds for best results.',
        createdAt: Date.now(),
        type: 'text'
      }]);
      
    } catch (e) {
      Alert.alert('Error', 'Could not start recording');
    }
  }, []);

  const stopRecording = useCallback(async () => {
    if (!recording) return;
    
    // Remove recording tip message
    setMessages(prev => prev.filter(m => !m.id.startsWith('recording-tip-')));
    
    try {
      await recording.stopAndUnloadAsync();
      const uri = recording.getURI();
      if (!uri) return;
      
      // Check recording duration
      const status = await recording.getStatusAsync();
      if (status.canRecord === false && status.durationMillis && status.durationMillis < 2000) {
        setMessages(prev => [...prev, {
          id: 'audio-too-short-' + Date.now(),
          role: 'assistant',
          content: '⚠️ Recording too short. Please record for at least 3-4 seconds for better voice recognition.',
          createdAt: Date.now(),
          type: 'text'
        }]);
        return;
      }
      
      // Send audio via websocket for AI interaction
      if (wsRef.current && wsState === 'open') {
        try {
          // Read audio file as binary data using fetch
          const response = await fetch(uri);
          const arrayBuffer = await response.arrayBuffer();
          const binaryData = new Uint8Array(arrayBuffer);
          
          console.log('[audio] Sending audio data, size:', binaryData.length, 'bytes');
          
          // Send binary audio data to websocket
          const rawWs = wsRef.current.websocket;
          if (rawWs && rawWs.readyState === WebSocket.OPEN) {
            rawWs.send(binaryData);
            
            // Show user message indicating audio was sent
            setMessages(prev => [...prev, { 
              id: 'user-audio-processing-' + Date.now(), 
              role: 'user', 
              content: '🎤 Processing voice message... (Please speak for at least 2-3 seconds)', 
              createdAt: Date.now(), 
              type: 'text' 
            }]);
            
            console.log('[audio] Audio sent via websocket successfully');
          } else {
            throw new Error('WebSocket not open');
          }
        } catch (e) {
          console.error('Failed to send audio via websocket:', e);
          
          // Remove processing message and show error
          setMessages(prev => {
            const filtered = prev.filter(m => !m.id.startsWith('user-audio-processing-'));
            return [...filtered, {
              id: 'audio-error-' + Date.now(),
              role: 'assistant',
              content: 'Sorry, there was an issue processing your voice message. Please try typing your message instead.',
              createdAt: Date.now(),
              type: 'text'
            }];
          });
        }
      } else {
        // Fallback to HTTP POST for transcription only (not AI interaction)
        setLoading(true);
        try {
          const resp = await sendChatMessage({ type: 'audio', mediaUri: uri });
          const { transcript, success, error } = resp as BackendChatResponse & { transcript?: string; success?: boolean; error?: string };
          
          // Check if response is successful and has transcript
          if (success && transcript) {
            // Send transcript as text message via websocket for AI interaction
            if (wsRef.current && wsState === 'open') {
              wsRef.current.sendUserMessage(transcript);
            } else {
              // Double fallback - use HTTP for both transcription and AI
              const chatResp = await sendChatMessage({ content: transcript, type: 'text' });
              if (chatResp && chatResp.messages && Array.isArray(chatResp.messages)) {
                appendBackendMessages(chatResp);
              }
            }
            
            // Show user message with transcript
            setMessages(prev => [...prev, { 
              id: 'user-transcript-' + Date.now(), 
              role: 'user', 
              content: `🎤 "${transcript}"`, 
              createdAt: Date.now(), 
              type: 'text' 
            }]);
          } else if (error) {
            Alert.alert('Audio Error', error);
          } else {
            Alert.alert('Error', 'Could not process audio');
          }
        } catch (e: any) {
          Alert.alert('Error', e.message || 'Could not process audio');
        } finally {
          setLoading(false);
        }
      }
    } catch (e) {
      console.error('Error in stopRecording:', e);
      Alert.alert('Error', 'Could not stop recording');
    } finally {
      setRecording(null);
    }
  }, [recording, wsState, appendBackendMessages]);

  const toggleRecording = useCallback(() => {
    if (recording) {
      stopRecording();
    } else {
      startRecording();
    }
  }, [recording, startRecording, stopRecording]);

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
          <Ionicons name={recording ? 'stop-circle-outline' : 'mic-outline'} size={22} color={recording ? '#dc2626' : '#374151'} />
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
