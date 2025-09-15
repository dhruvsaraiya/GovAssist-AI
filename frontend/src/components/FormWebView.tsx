import React, { useCallback, useRef, useState, useEffect } from 'react';
import { Animated, PanResponder, StyleSheet, View, Text, TouchableOpacity, Dimensions, Easing } from 'react-native';
import { WebView } from 'react-native-webview';
import { Ionicons } from '@expo/vector-icons';

export interface FormWebViewProps {
  url: string;
  onClose: () => void;
  initialSnap?: number;      // starting index in snapPoints
  snapPoints?: number[];     // height fractions (0-1) from screen height
  title?: string;
}

/*
  FormWebView: single canonical component for rendering a resizable inline WebView form panel.
  - Non-overlay: pushes chat below
  - Adjustable via snap points and drag gestures
  - Designed to replace previous variants (FormSheet, TopFormShutter, SplitFormPanel)
*/
export const FormWebView: React.FC<FormWebViewProps> = ({
  url,
  onClose,
  initialSnap = 1,
  snapPoints = [0.25, 0.4, 0.55],
  title = 'Form'
}) => {
  const screenH = Dimensions.get('window').height;
  const [currentSnap, setCurrentSnap] = useState(initialSnap);
  const fracAnim = useRef(new Animated.Value(snapPoints[initialSnap] || 0)).current;

  const animateToSnap = useCallback((index: number) => {
    const clamped = Math.max(0, Math.min(index, snapPoints.length - 1));
    setCurrentSnap(clamped);
    Animated.timing(fracAnim, {
      toValue: snapPoints[clamped],
      duration: 220,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: false
    }).start();
  }, [fracAnim, snapPoints]);

  useEffect(() => { animateToSnap(currentSnap); /* eslint-disable-line react-hooks/exhaustive-deps */ }, []);

  const responder = useRef(PanResponder.create({
    onStartShouldSetPanResponder: () => true,
    onPanResponderMove: () => {},
    onPanResponderRelease: (_, gesture) => {
      const threshold = 40;
      if (gesture.dy > threshold && currentSnap < snapPoints.length - 1) {
        animateToSnap(currentSnap + 1);
      } else if (gesture.dy < -threshold && currentSnap > 0) {
        animateToSnap(currentSnap - 1);
      }
    }
  })).current;

  const heightStyle = {
    height: fracAnim.interpolate({ inputRange: [0,1], outputRange: [0, screenH] })
  };

  // Determine effective URL (embed PDF if necessary)
  const isPdf = /\.pdf($|\?)/i.test(url.split('#')[0]);
  // Basic strategy: use Google Docs viewer for PDFs to avoid download prompts in RN WebView.
  // This is a temporary solution; for production consider a native PDF renderer (e.g. react-native-pdf).
  const effectiveUrl = isPdf ? `https://docs.google.com/gview?embedded=1&url=${encodeURIComponent(url)}` : url;

  return (
    <Animated.View style={[styles.container, heightStyle]} accessibilityLabel="Form panel" testID="form-webview">
      <View style={styles.header} {...responder.panHandlers}>
        <View style={styles.dragHandle} />
        <Text style={styles.title} numberOfLines={1}>{title}</Text>
        <View style={{ flexDirection:'row', alignItems:'center' }}>
          <TouchableOpacity accessibilityLabel="Increase size" onPress={() => animateToSnap(Math.min(currentSnap + 1, snapPoints.length - 1))} style={styles.iconBtn}>
            <Ionicons name="chevron-down" size={18} color="#334155" />
          </TouchableOpacity>
          <TouchableOpacity accessibilityLabel="Decrease size" onPress={() => animateToSnap(Math.max(currentSnap - 1, 0))} style={styles.iconBtn}>
            <Ionicons name="chevron-up" size={18} color="#334155" />
          </TouchableOpacity>
          <TouchableOpacity accessibilityLabel="Close form" onPress={onClose} style={styles.iconBtn}>
            <Ionicons name="close" size={18} color="#334155" />
          </TouchableOpacity>
        </View>
      </View>
      <View style={styles.webContainer}>
        <WebView
          source={{ uri: effectiveUrl }}
          style={{ flex:1 }}
          startInLoadingState
          originWhitelist={["*"]}
          allowsFullscreenVideo
          onError={(e) => {
            // eslint-disable-next-line no-console
            console.warn('[FormWebView] load error', e.nativeEvent);
          }}
          onHttpError={(e) => {
            // eslint-disable-next-line no-console
            console.warn('[FormWebView] HTTP error', e.nativeEvent.statusCode);
          }}
        />
      </View>
    </Animated.View>
  );
};

const styles = StyleSheet.create({
  container: { width:'100%', backgroundColor:'#f1f5f9', borderBottomWidth:1, borderColor:'#e2e8f0' },
  header: { flexDirection:'row', alignItems:'center', paddingHorizontal:8, paddingVertical:8, borderBottomWidth:1, borderColor:'#e2e8f0' },
  dragHandle: { width:40, height:4, borderRadius:2, backgroundColor:'#cbd5e1', position:'absolute', top:4, left:'50%', marginLeft:-20 },
  title: { fontSize:14, fontWeight:'600', color:'#334155', flex:1, marginLeft:8 },
  iconBtn: { padding:6 },
  webContainer: { flex:1 }
});

export default FormWebView;