import React, { useState, useCallback } from 'react';
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
  const [isPlaying, setIsPlaying] = useState(false);
  const [sound, setSound] = useState<Audio.Sound | null>(null);

  const handlePlayAudio = useCallback(async () => {
    console.log('[MessageBubble] Play audio - type:', message.type, 'mediaUri:', message.mediaUri);
    
    if (!message.mediaUri || message.type !== 'audio') {
      console.warn('[MessageBubble] No media URI or not audio type');
      return;
    }

    try {
      if (isPlaying && sound) {
        // Stop current playback
        console.log('[MessageBubble] Stopping current playback');
        await sound.stopAsync();
        setIsPlaying(false);
        return;
      }

      console.log('[MessageBubble] Creating audio sound from URI:', message.mediaUri);
      // Create and play audio
      const { sound: audioSound } = await Audio.Sound.createAsync(
        { uri: message.mediaUri },
        { shouldPlay: true }
      );
      
      setSound(audioSound);
      setIsPlaying(true);
      console.log('[MessageBubble] Audio playing started');

      // Set up completion handler
      audioSound.setOnPlaybackStatusUpdate((status) => {
        if (status.isLoaded && status.didJustFinish) {
          console.log('[MessageBubble] Audio playback finished');
          setIsPlaying(false);
          audioSound.unloadAsync();
          setSound(null);
        }
      });

    } catch (error) {
      console.warn('[MessageBubble] Error playing audio:', error);
      Toast.show({
        type: 'error',
        text1: 'Error',
        text2: 'Failed to play audio message: ' + String(error),
        visibilityTime: 3000,
      });
      setIsPlaying(false);
    }
  }, [message.mediaUri, message.type, isPlaying, sound]);

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

  return (
    <View style={[styles.container, isUser ? styles.userAlign : styles.assistantAlign]}>
      <View style={styles.messageRow}>
        <View style={[styles.bubble, isUser ? styles.userBubble : styles.assistantBubble]}>        
          {message.type === 'image' && message.mediaUri && /^\w+:/.test(message.mediaUri) ? (
            <Image source={{ uri: message.mediaUri }} style={styles.image} />
          ) : (message.type === 'image' && message.mediaUri ? (dbg('image', 'skipping invalid uri', message.mediaUri), null) : null)}
          {message.type === 'audio' && (
            <TouchableOpacity style={styles.audioContainer} onPress={handlePlayAudio}>
              <Ionicons 
                name={isPlaying ? 'pause-circle' : 'play-circle'} 
                size={24} 
                color={isUser ? '#ffffff' : '#2563eb'} 
              />
              <Text style={[styles.audioText, isUser ? styles.userText : styles.assistantText]}>
                {isPlaying ? 'Playing...' : `Audio message ${message.mediaUri ? '🎵' : '(no audio)'}`}
              </Text>
            </TouchableOpacity>
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
  audioContainer: { 
    flexDirection: 'row', 
    alignItems: 'center', 
    padding: 8,
    borderRadius: 8,
    backgroundColor: 'rgba(0,0,0,0.05)'
  },
  audioText: { 
    marginLeft: 8, 
    fontSize: 14,
    fontWeight: '500'
  },
  audioPlaceholder: { fontStyle: 'italic', color: '#374151' },
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
