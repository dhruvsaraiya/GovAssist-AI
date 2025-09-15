// Central place for environment-style configuration.
// Detect Android emulator and map to host machine via 10.0.2.2
import { Platform } from 'react-native';

const LOCAL_HOST = '127.0.0.1';
const ANDROID_EMULATOR_HOST = '10.0.2.2';

export const BACKEND_HOST = Platform.OS === 'android' ? ANDROID_EMULATOR_HOST : LOCAL_HOST;
export const BACKEND_PORT = 8000;
export const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;

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
