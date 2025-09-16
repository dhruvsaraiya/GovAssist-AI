// Simple debug flag + logger to centralize verbose logging control
export const DEBUG_FLAGS = {
  image: true,
  ws: false,
};

export function dbg(scope: keyof typeof DEBUG_FLAGS, ...args: any[]) {
  if (DEBUG_FLAGS[scope]) {
    // eslint-disable-next-line no-console
    console.log(`[dbg:${scope}]`, ...args);
  }
}
