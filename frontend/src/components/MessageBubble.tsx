import React from 'react';
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
            <Text style={styles.audioPlaceholder}>[Audio message]</Text>
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
