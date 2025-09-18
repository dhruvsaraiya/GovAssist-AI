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

// Helper function to create WAV blob from PCM16 data
const createWavBlob = (pcmData: Uint8Array, sampleRate: number, channels: number, bitsPerSample: number): Blob => {
  const length = pcmData.length;
  const buffer = new ArrayBuffer(44 + length);
  const view = new DataView(buffer);
  
  // WAV header
  const writeString = (offset: number, string: string) => {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  };
  
  // RIFF chunk descriptor
  writeString(0, 'RIFF');
  view.setUint32(4, 36 + length, true);
  writeString(8, 'WAVE');
  
  // fmt sub-chunk
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, channels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * channels * bitsPerSample / 8, true);
  view.setUint16(32, channels * bitsPerSample / 8, true);
  view.setUint16(34, bitsPerSample, true);
  
  // data sub-chunk
  writeString(36, 'data');
  view.setUint32(40, length, true);
  
  // PCM data
  const dataView = new Uint8Array(buffer, 44);
  dataView.set(pcmData);
  
  return new Blob([buffer], { type: 'audio/wav' });
};

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
            // Convert base64 PCM16 audio to blob URL for playback
            try {
              // Decode base64 audio data (PCM16 format)
              const binaryString = atob(m.audio_data);
              const bytes = new Uint8Array(binaryString.length);
              for (let i = 0; i < binaryString.length; i++) {
                bytes[i] = binaryString.charCodeAt(i);
              }
              
              // Create WAV file blob from PCM16 data
              const wavBlob = createWavBlob(bytes, 16000, 1, 16);
              msg.mediaUri = URL.createObjectURL(wavBlob);
            } catch (e) {
              console.warn('Failed to create audio blob:', e);
            }
          }
          
          return [...prev, msg];
        });
      },
      onAssistantAudio: (audioData) => {
        // Handle separate audio events if needed
        console.log('[audio] Received assistant audio data');
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
  const recordingStartTime = useRef<number | null>(null);

  // Audio streaming functionality
  const startAudioStreaming = useCallback(async () => {
    console.log('[audio] Starting audio recording...');
    try {
      const perm = await Audio.requestPermissionsAsync();
      if (!perm.granted) { 
        Alert.alert('Permission required', 'Microphone permission is needed'); 
        return; 
      }
      console.log('[audio] Permissions granted');
      
      await Audio.setAudioModeAsync({ 
        allowsRecordingIOS: true, 
        playsInSilentModeIOS: true,
        shouldDuckAndroid: true,
        playThroughEarpieceAndroid: false,
      });
      console.log('[audio] Audio mode set');

      // Detect supported MIME type for web platform
      let webMimeType = 'audio/webm;codecs=opus';
      if (Platform.OS === 'web' && typeof MediaRecorder !== 'undefined') {
        const supportedTypes = [
          'audio/webm;codecs=opus',
          'audio/webm',
          'audio/ogg;codecs=opus',
          'audio/mp4'
        ];
        
        for (const type of supportedTypes) {
          if (MediaRecorder.isTypeSupported(type)) {
            webMimeType = type;
            console.log('[audio] Using supported web format:', type);
            break;
          }
        }
      }

      // Create recording options with all required platforms
      const recordingOptions = {
        android: {
          extension: '.wav',
          outputFormat: Audio.AndroidOutputFormat.DEFAULT,
          audioEncoder: Audio.AndroidAudioEncoder.DEFAULT,
          sampleRate: 16000,
          numberOfChannels: 1,
          bitRate: 256000,
        },
        ios: {
          extension: '.wav',
          outputFormat: Audio.IOSOutputFormat.LINEARPCM,
          audioQuality: Audio.IOSAudioQuality.HIGH,
          sampleRate: 16000,
          numberOfChannels: 1,
          bitRate: 256000,
          linearPCMBitDepth: 16,
          linearPCMIsBigEndian: false,
          linearPCMIsFloat: false,
        },
        web: {
          mimeType: webMimeType,
          bitsPerSecond: 256000,
        },
      };

      console.log('[audio] Creating recording with options:', recordingOptions);
      let recording;
      try {
        const result = await Audio.Recording.createAsync(recordingOptions);
        recording = result.recording;
        console.log('[audio] Recording created successfully with custom options');
      } catch (customError) {
        console.warn('[audio] Custom recording options failed, trying preset:', customError);
        // Fallback to high quality preset if custom options fail
        const result = await Audio.Recording.createAsync(Audio.RecordingOptionsPresets.HIGH_QUALITY);
        recording = result.recording;
        console.log('[audio] Recording created successfully with preset');
      }
      
      setRecording(recording);
      setIsStreamingAudio(true);
      recordingStartTime.current = Date.now();
      console.log('[audio] State updated - recording started');

      // For React Native with Expo, real-time chunk streaming is complex.
      // Instead, we'll use a simpler approach: start recording and commit on stop.
      // This provides good UX while working with the existing Azure Realtime API expectations.

    } catch (e) {
      console.error('[audio] Error starting recording:', e);
      Alert.alert('Error', 'Could not start audio streaming: ' + String(e));
      setIsStreamingAudio(false);
    }
  }, [wsState]);

  const stopAudioStreaming = useCallback(async () => {
    console.log('[audio] Stopping audio recording...');
    if (recordingIntervalRef.current) {
      clearInterval(recordingIntervalRef.current);
      recordingIntervalRef.current = null;
    }

    if (!recording) {
      console.log('[audio] No recording to stop');
      return;
    }
    
    try {
      console.log('[audio] Stopping and unloading recording...');
      await recording.stopAndUnloadAsync();
      const uri = recording.getURI();
      console.log('[audio] Recording stopped, URI:', uri);
      
      // Check minimum recording duration (at least 500ms)
      const recordingDuration = recordingStartTime.current ? Date.now() - recordingStartTime.current : 0;
      console.log('[audio] Recording duration:', recordingDuration, 'ms');
      
      if (recordingDuration < 500) {
        Alert.alert('Recording Too Short', 'Please record for at least half a second');
        return;
      }
      
      if (uri && wsRef.current && wsState === 'open') {
        console.log('[audio] Reading audio file from URI:', uri);
        
        let audioData: string;
        
        if (Platform.OS === 'web') {
          // For web, convert the blob URI to base64
          try {
            const response = await fetch(uri);
            const blob = await response.blob();
            console.log('[audio] Blob size:', blob.size, 'type:', blob.type);
            
            if (blob.size === 0) {
              throw new Error('Audio blob is empty');
            }
            
            // Convert blob to base64
            const reader = new FileReader();
            audioData = await new Promise<string>((resolve, reject) => {
              reader.onload = () => {
                const result = reader.result as string;
                const base64 = result.split(',')[1]; // Remove data:audio/webm;base64, prefix
                if (!base64 || base64.length === 0) {
                  reject(new Error('Failed to convert blob to base64'));
                  return;
                }
                resolve(base64);
              };
              reader.onerror = () => reject(new Error('FileReader error'));
              reader.readAsDataURL(blob);
            });
          } catch (e) {
            console.error('[audio] Failed to read web audio file:', e);
            Alert.alert('Audio Error', 'Failed to process recorded audio: ' + String(e));
            return;
          }
        } else {
          // For mobile platforms, use FileSystem
          try {
            audioData = await FileSystem.readAsStringAsync(uri, {
              encoding: FileSystem.EncodingType.Base64,
            });
          } catch (e) {
            console.error('[audio] Failed to read mobile audio file:', e);
            Alert.alert('Audio Error', 'Failed to read recorded audio file');
            return;
          }
        }
        
        console.log('[audio] Audio data length:', audioData.length);
        console.log('[audio] Platform:', Platform.OS);
        
        if (audioData.length === 0) {
          console.error('[audio] Audio data is empty');
          Alert.alert('Audio Error', 'Recorded audio is empty. Please try recording again.');
          return;
        }
        
        // Note: For web, the audio might be in WebM/Opus format instead of PCM16
        // The backend Azure Realtime API should handle format conversion
        
        // Send the complete audio as a chunk and commit
        console.log('[audio] Sending audio chunk and committing...');
        wsRef.current.sendAudioChunk(audioData);
        wsRef.current.commitAudioInput();
        
        // Add a user message indicating audio was sent (only after successful processing)
        setMessages(prev => [...prev, { 
          id: 'user-audio-' + Date.now(), 
          role: 'user', 
          content: '[Audio message]', 
          createdAt: Date.now(), 
          type: 'audio',
          mediaUri: Platform.OS === 'web' ? uri : undefined // Keep the blob URL for playback on web
        }]);
        console.log('[audio] Audio message added to chat');
      } else if (!uri) {
        console.error('[audio] No URI received from recording');
        Alert.alert('Audio Error', 'Failed to get recording URI');
      }
      
    } catch (e) {
      console.error('[audio] Error stopping recording:', e);
      Alert.alert('Error', 'Could not stop audio streaming: ' + String(e));
    } finally {
      setRecording(null);
      setIsStreamingAudio(false);
      recordingStartTime.current = null;
      console.log('[audio] Recording state cleared');
    }
  }, [recording, wsState]);

  const toggleRecording = useCallback(() => {
    console.log('[audio] Toggle recording - current state:', isStreamingAudio);
    console.log('[audio] WebSocket state:', wsState);
    
    if (wsState !== 'open') {
      Alert.alert('Connection Error', 'WebSocket is not connected. Please wait for connection.');
      return;
    }
    
    if (isStreamingAudio) {
      console.log('[audio] Stopping recording...');
      stopAudioStreaming();
    } else {
      console.log('[audio] Starting recording...');
      startAudioStreaming();
    }
  }, [isStreamingAudio, startAudioStreaming, stopAudioStreaming, wsState]);

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
        <TouchableOpacity 
          style={[
            styles.iconBtn, 
            isStreamingAudio && styles.recordingBtn,
            wsState !== 'open' && styles.disabledBtn
          ]} 
          onPress={toggleRecording}
          disabled={wsState !== 'open'}
        >
          <Ionicons 
            name={isStreamingAudio ? 'stop-circle' : 'mic-outline'} 
            size={24} 
            color={isStreamingAudio ? '#ffffff' : '#374151'} 
          />
          {isStreamingAudio && (
            <View style={styles.recordingIndicator}>
              <Text style={styles.recordingText}>REC</Text>
            </View>
          )}
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
  iconBtn: { padding: 6 },
  recordingBtn: { 
    backgroundColor: '#dc2626', 
    borderRadius: 20, 
    padding: 6,
    shadowColor: '#dc2626',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.3,
    shadowRadius: 4,
    elevation: 4,
    position: 'relative'
  },
  recordingIndicator: {
    position: 'absolute',
    top: -5,
    right: -5,
    backgroundColor: '#ef4444',
    borderRadius: 8,
    paddingHorizontal: 4,
    paddingVertical: 1
  },
  recordingText: {
    color: '#ffffff',
    fontSize: 8,
    fontWeight: 'bold'
  },
  disabledBtn: {
    opacity: 0.5
  }
});
