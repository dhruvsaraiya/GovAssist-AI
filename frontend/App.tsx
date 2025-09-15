import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { ChatScreen } from './src/screens/ChatScreen';
import { WebFormScreen } from './src/screens/WebFormScreen';
import { RootStackParamList } from './src/navigation/types';

const Stack = createNativeStackNavigator<RootStackParamList>();

export default function App() {
  return (
    <NavigationContainer>
      <Stack.Navigator>
        <Stack.Screen name="Chat" component={ChatScreen} options={{ title: 'GovAssist AI' }} />
        <Stack.Screen name="WebForm" component={WebFormScreen} options={{ title: 'Form Preview' }} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}

