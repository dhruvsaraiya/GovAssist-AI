// Central place for environment-style configuration.
// Detect Android emulator and map to host machine via 10.0.2.2
import { Platform } from 'react-native';

// Minimal ambient declaration to satisfy TypeScript in React Native environment
// without pulling full @types/node (Expo injects env at build time)
// eslint-disable-next-line @typescript-eslint/no-unused-vars
declare const process: { env: Record<string, string | undefined> };

const LOCAL_HOST = '127.0.0.1';
const ANDROID_EMULATOR_HOST = '10.0.2.2';

// Expo injects EXPO_PUBLIC_* at build/runtime.
const envHost = process.env.EXPO_PUBLIC_BACKEND_HOST;
const envPort = process.env.EXPO_PUBLIC_BACKEND_PORT;

let resolvedHost = envHost || (Platform.OS === 'android' ? ANDROID_EMULATOR_HOST : LOCAL_HOST);
// If user accidentally set localhost/127.0.0.1 while on Android emulator, fix automatically.
if (Platform.OS === 'android' && ['127.0.0.1', 'localhost'].includes(resolvedHost)) {
	resolvedHost = ANDROID_EMULATOR_HOST;
}

export const BACKEND_HOST = resolvedHost;
export const BACKEND_PORT = envPort ? Number(envPort) : 8000;
export const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;

// Development diagnostic log (will be stripped in production builds)
// eslint-disable-next-line no-console
console.log('[config] BACKEND_RESOLUTION', { envHost, platform: Platform.OS, BACKEND_URL });

// Optional: override via global (for quick manual testing in DevTools)
// @ts-ignore
if (global.__BACKEND_URL_OVERRIDE__) {
	// @ts-ignore
	// eslint-disable-next-line no-console
	console.log('[config] Using BACKEND_URL override:', global.__BACKEND_URL_OVERRIDE__);
	// @ts-ignore
	// eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
	(global as any).BACKEND_URL = global.__BACKEND_URL_OVERRIDE__;
}
