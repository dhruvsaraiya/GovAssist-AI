import React from 'react';
import { View, StyleSheet } from 'react-native';
import WebView from 'react-native-webview';
import { NativeStackScreenProps } from '@react-navigation/native-stack';
import { RootStackParamList } from '../navigation/types';

type Props = NativeStackScreenProps<RootStackParamList, 'WebForm'>;

export const WebFormScreen: React.FC<Props> = ({ route }) => {
  const { url } = route.params;
  return (
    <View style={styles.container}>
      <WebView source={{ uri: url }} />
    </View>
  );
};

const styles = StyleSheet.create({
  container: { flex: 1 }
});
