// Use Expo's metro config for proper asset & plugin resolution
const { getDefaultConfig } = require('expo/metro-config');
const config = getDefaultConfig(__dirname);

module.exports = config;
