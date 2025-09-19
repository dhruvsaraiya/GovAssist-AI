import React from 'react';
import { TouchableOpacity, Alert } from 'react-native';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { Ionicons } from '@expo/vector-icons';
import Toast from 'react-native-toast-message';
import { ChatScreen } from './src/screens/ChatScreen';
import { WebFormScreen } from './src/screens/WebFormScreen';
import { RootStackParamList } from './src/navigation/types';
import { restartAllSessions } from './src/services/api';

const Stack = createNativeStackNavigator<RootStackParamList>();

export default function App() {
  return (
    <NavigationContainer>
      <Stack.Navigator>
        <Stack.Screen 
          name="Chat" 
          component={ChatScreen} 
          options={({ navigation }) => ({ 
            title: 'FormAssist AI',
            headerRight: () => (
              <TouchableOpacity
                onPress={() => {
                  Alert.alert(
                    'Restart Session',
                    'This will clear the current chat and form session. Are you sure?',
                    [
                      { text: 'Cancel', style: 'cancel' },
                      { 
                        text: 'Restart', 
                        style: 'destructive',
                        onPress: async () => {
                          try {
                            // Clear backend form sessions
                            const result = await restartAllSessions();
                            if (result.success) {
                              console.log('Backend sessions cleared');
                            } else {
                              console.warn('Backend restart failed:', result.error);
                            }
                          } catch (e) {
                            console.warn('Restart API error:', e);
                          }
                          
                          // Reset the navigation stack to refresh the ChatScreen
                          navigation.reset({
                            index: 0,
                            routes: [{ name: 'Chat' }],
                          });
                        }
                      }
                    ]
                  );
                }}
                style={{ 
                  padding: 8,
                  marginRight: 8,
                }}
                accessibilityLabel="Restart session"
              >
                <Ionicons name="refresh-outline" size={24} color="#007AFF" />
              </TouchableOpacity>
            )
          })} 
        />
        <Stack.Screen name="WebForm" component={WebFormScreen} options={{ title: 'Form Preview' }} />
      </Stack.Navigator>
      <Toast />
    </NavigationContainer>
  );
}

