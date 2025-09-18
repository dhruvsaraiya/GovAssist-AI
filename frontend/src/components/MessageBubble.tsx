import React, { useState } from 'react';
import { View, Text, StyleSheet, Image, TouchableOpacity } from 'react-native';
import { dbg } from '../services/debug';
import { Audio } from 'expo-av';
import { ChatMessage } from '../types/chat';
import * as Clipboard from 'expo-clipboard';
import { Ionicons } from '@expo/vector-icons';
import Toast from 'react-native-toast-message';

interface Props { message: ChatMessage }

export const MessageBubble: React.FC<Props> = ({ message }) => {
  const isUser = message.role === 'user';
  const [sound, setSound] = useState<Audio.Sound | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);

  const handleCopy = async () => {
    try {
      const textToCopy = message.content || '';
      if (textToCopy.trim()) {
        await Clipboard.setStringAsync(textToCopy);
        Toast.show({
          type: 'success',
          text1: 'Copied!',
          text2: 'Message copied to clipboard',
          visibilityTime: 2000,
        });
      }
    } catch (error) {
      Toast.show({
        type: 'error',
        text1: 'Error',
        text2: 'Failed to copy message',
        visibilityTime: 3000,
      });
    }
  };

  const playAudio = async () => {
    if (!message.mediaUri && !message.audioData) return;
    
    try {
      if (sound) {
        await sound.unloadAsync();
        setSound(null);
      }

      let audioSource;
      
      // Prefer base64 audio data if available
      if (message.audioData) {
        // Create data URI from base64
        const dataUri = `data:audio/wav;base64,${message.audioData}`;
        audioSource = { uri: dataUri };
        console.log('[MessageBubble] Playing base64 audio, size:', message.audioData.length);
      } else if (message.mediaUri) {
        // Fallback to URL-based audio
        let audioUri = message.mediaUri;
        if (audioUri.startsWith('/static/')) {
          // Import config to get backend URL
          const { BACKEND_URL } = require('../config');
          audioUri = `${BACKEND_URL}${audioUri}`;
        }
        audioSource = { uri: audioUri };
        console.log('[MessageBubble] Playing URL audio:', audioUri);
      } else {
        return;
      }

      const { sound: newSound } = await Audio.Sound.createAsync(
        audioSource,
        { shouldPlay: true }
      );
      
      setSound(newSound);
      setIsPlaying(true);

      newSound.setOnPlaybackStatusUpdate((status) => {
        if (status.isLoaded && status.didJustFinish) {
          setIsPlaying(false);
        }
      });
    } catch (error) {
      console.error('[MessageBubble] Audio playback error:', error);
      Toast.show({
        type: 'error',
        text1: 'Error',
        text2: `Failed to play audio: ${error}`,
        visibilityTime: 3000,
      });
    }
  };

  const stopAudio = async () => {
    if (sound) {
      await sound.stopAsync();
      setIsPlaying(false);
    }
  };

  React.useEffect(() => {
    return sound
      ? () => {
          sound.unloadAsync();
        }
      : undefined;
  }, [sound]);

  return (
    <View style={[styles.container, isUser ? styles.userAlign : styles.assistantAlign]}>
      <View style={styles.messageRow}>
        <View style={[styles.bubble, isUser ? styles.userBubble : styles.assistantBubble]}>        
          {message.type === 'image' && message.mediaUri && /^\w+:/.test(message.mediaUri) ? (
            <Image source={{ uri: message.mediaUri }} style={styles.image} />
          ) : (message.type === 'image' && message.mediaUri ? (dbg('image', 'skipping invalid uri', message.mediaUri), null) : null)}
          {message.type === 'audio' && (
            <View style={styles.audioContainer}>
              <TouchableOpacity 
                style={styles.audioButton} 
                onPress={isPlaying ? stopAudio : playAudio}
              >
                <Ionicons 
                  name={isPlaying ? "pause" : "play"} 
                  size={20} 
                  color={isUser ? "#ffffff" : "#374151"} 
                />
              </TouchableOpacity>
              <Text style={[styles.audioText, isUser ? styles.userText : styles.assistantText]}>
                {isPlaying ? "Playing..." : "Audio message"}
              </Text>
            </View>
          )}
          {message.content?.length > 0 && (
            <Text style={[styles.text, isUser ? styles.userText : styles.assistantText]}>{message.content}</Text>
          )}
        </View>
        {message.content?.trim() && (
          <TouchableOpacity style={styles.copyButton} onPress={handleCopy}>
            <Ionicons 
              name="copy-outline" 
              size={16} 
              color="#6b7280" 
            />
          </TouchableOpacity>
        )}
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { marginVertical: 4, paddingHorizontal: 8 },
  userAlign: { alignItems: 'flex-end' },
  assistantAlign: { alignItems: 'flex-start' },
  messageRow: { 
    flexDirection: 'row', 
    alignItems: 'flex-end', 
    maxWidth: '85%' 
  },
  bubble: { 
    flex: 1,
    maxWidth: '100%', 
    borderRadius: 16, 
    padding: 10 
  },
  userBubble: { backgroundColor: '#2563eb' },
  assistantBubble: { backgroundColor: '#e5e7eb' },
  text: { color: '#111827' },
  userText: { color: '#ffffff' },
  assistantText: { color: '#111827' },
  audioPlaceholder: { fontStyle: 'italic', color: '#374151' },
  audioContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingVertical: 4,
  },
  audioButton: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: 'rgba(0,0,0,0.1)',
    justifyContent: 'center',
    alignItems: 'center',
    marginRight: 8,
  },
  audioText: {
    fontSize: 14,
    fontStyle: 'italic',
  },
  image: { width: 180, height: 120, borderRadius: 12, marginBottom: 6, backgroundColor: '#ddd' },
  copyButton: {
    marginLeft: 8,
    padding: 6,
    backgroundColor: '#f3f4f6',
    borderRadius: 12,
    justifyContent: 'center',
    alignItems: 'center',
    minHeight: 28,
    minWidth: 28,
  }
});
