import React from 'react';
import { View, Text, StyleSheet, Image } from 'react-native';
import { Audio } from 'expo-av';
import { ChatMessage } from '../types/chat';

interface Props { message: ChatMessage }

export const MessageBubble: React.FC<Props> = ({ message }) => {
  const isUser = message.role === 'user';
  return (
    <View style={[styles.container, isUser ? styles.userAlign : styles.assistantAlign]}>
      <View style={[styles.bubble, isUser ? styles.userBubble : styles.assistantBubble]}>        
        {message.type === 'image' && message.mediaUri && (
          <Image source={{ uri: message.mediaUri }} style={styles.image} />
        )}
        {message.type === 'audio' && (
          <Text style={styles.audioPlaceholder}>[Audio message]</Text>
        )}
        {message.content?.length > 0 && (
          <Text style={styles.text}>{message.content}</Text>
        )}
      </View>
    </View>
  );
};

const styles = StyleSheet.create({
  container: { marginVertical: 4, paddingHorizontal: 8 },
  userAlign: { alignItems: 'flex-end' },
  assistantAlign: { alignItems: 'flex-start' },
  bubble: { maxWidth: '80%', borderRadius: 16, padding: 10 },
  userBubble: { backgroundColor: '#2563eb' },
  assistantBubble: { backgroundColor: '#e5e7eb' },
  text: { color: '#111827' },
  audioPlaceholder: { fontStyle: 'italic', color: '#374151' },
  image: { width: 180, height: 120, borderRadius: 12, marginBottom: 6, backgroundColor: '#ddd' }
});
