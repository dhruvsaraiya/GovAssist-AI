// Entry point required because package.json specifies "main": "index.js"
// This registers the root App component with Expo.
import { registerRootComponent } from 'expo';
import App from './App';

registerRootComponent(App);
