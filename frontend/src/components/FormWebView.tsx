import React, { useCallback, useRef, useState, useEffect } from 'react';
import { Animated, PanResponder, StyleSheet, View, Text, TouchableOpacity, Dimensions, Easing, Platform } from 'react-native';
import { Ionicons } from '@expo/vector-icons';

export interface FormWebViewProps {
  url: string;
  onClose: () => void;
  initialSnap?: number;      // starting index in snapPoints
  snapPoints?: number[];     // height fractions (0-1) from screen height
  title?: string;
  autoFillData?: Record<string, any>;
  autoFillOnLoad?: boolean;
  onFieldUpdate?: (fieldId: string, value: any) => void;
}

/*
  FormWebView: single canonical component for rendering a resizable inline WebView form panel.
  - Non-overlay: pushes chat below
  - Adjustable via snap points and drag gestures
  - Designed to replace previous variants (FormSheet, TopFormShutter, SplitFormPanel)
*/
export const FormWebView = React.forwardRef<any, FormWebViewProps>(({
  url,
  onClose,
  initialSnap = 1,
  snapPoints = [0.25, 0.4, 0.55],
  title = 'Form',
  ...props
}, ref) => {
  const screenH = Dimensions.get('window').height;
  const [currentSnap, setCurrentSnap] = useState(initialSnap);
  const fracAnim = useRef(new Animated.Value(snapPoints[initialSnap] || 0)).current;
  const webviewRef = useRef<any>(null);
  // Load WebView dynamically only on native platforms to avoid web import errors
  let WebViewComponent: any = null;
  if (Platform.OS !== 'web') {
    // eslint-disable-next-line @typescript-eslint/no-var-requires, global-require
    WebViewComponent = require('react-native-webview').WebView;
  }

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
  const [webSrcDoc, setWebSrcDoc] = useState<string | null>(null);

  const injectMapping = useCallback((mapping?: Record<string, any>) => {
    if (!mapping) return;
    try {
      const script = `(function(){
        function fill(m){ for(const k in m){ const el = document.getElementById(k) || document.querySelector('[name="'+k+'"]'); if(el){ el.value = m[k]; el.dispatchEvent(new Event('input',{bubbles:true})); } } }
        if(typeof window.fillForm !== 'function'){ window.fillForm = fill; }
        window.fillForm(${JSON.stringify(mapping)});
      })(); true;`;
      webviewRef.current?.injectJavaScript(script);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('[FormWebView] injectMapping failed', e);
    }
  }, []);

  const updateSingleField = useCallback((fieldId: string, value: any) => {
    console.log('[FormWebView] updateSingleField called:', fieldId, value);
    try {
      // More robust script that tries multiple selection methods and handles different input types
      const script = `(function(){
        console.log('Trying to update field:', '${fieldId}', 'with value:', ${JSON.stringify(value)});
        
        // Try multiple ways to find the element
        let el = document.getElementById('${fieldId}') || 
                 document.querySelector('[name="${fieldId}"]') ||
                 document.querySelector('input[id*="${fieldId}"]') ||
                 document.querySelector('input[name*="${fieldId}"]') ||
                 document.querySelector('select[id*="${fieldId}"]') ||
                 document.querySelector('select[name*="${fieldId}"]') ||
                 document.querySelector('textarea[id*="${fieldId}"]') ||
                 document.querySelector('textarea[name*="${fieldId}"]');
        
        console.log('Found element:', el, 'tagName:', el ? el.tagName : 'null');
        
        if(el){ 
          // Clear any existing value first
          el.value = '';
          
          // Set the new value
          el.value = ${JSON.stringify(value)};
          
          // Focus the element to ensure it's active
          el.focus();
          
          // Trigger multiple events to ensure compatibility
          el.dispatchEvent(new Event('focus', {bubbles: true}));
          el.dispatchEvent(new Event('input', {bubbles: true}));
          el.dispatchEvent(new Event('change', {bubbles: true}));
          el.dispatchEvent(new Event('blur', {bubbles: true}));
          
          // For React forms, also trigger React's synthetic events
          if (el._valueTracker) {
            el._valueTracker.setValue('');
          }
          
          // Highlight the field to show it was updated
          const originalBg = el.style.backgroundColor;
          el.style.backgroundColor = '#90EE90';
          el.style.transition = 'background-color 0.3s';
          
          setTimeout(function() {
            el.style.backgroundColor = originalBg;
          }, 2000);
          
          console.log('Field updated successfully:', '${fieldId}', 'final value:', el.value);
          return true;
        } else {
          console.warn('Element not found for field:', '${fieldId}');
          // Log all form elements for debugging
          const allInputs = document.querySelectorAll('input, select, textarea');
          console.log('Available form elements:', Array.from(allInputs).map(el => ({
            id: el.id,
            name: el.name,
            type: el.type || el.tagName
          })));
          return false;
        }
      })(); true;`;
      
      if (Platform.OS === 'web') {
        // For web, try to access iframe content directly
        const iframe = document.querySelector('iframe') as HTMLIFrameElement;
        if (iframe && iframe.contentDocument) {
          console.log('[FormWebView] Executing script in iframe content');
          const scriptEl = iframe.contentDocument.createElement('script');
          scriptEl.textContent = script;
          iframe.contentDocument.head.appendChild(scriptEl);
          iframe.contentDocument.head.removeChild(scriptEl);
        } else if (iframe && iframe.contentWindow) {
          console.log('[FormWebView] Posting message to iframe');
          iframe.contentWindow.postMessage({
            type: 'UPDATE_FIELD',
            fieldId: fieldId,
            value: value
          }, '*');
        } else {
          console.warn('[FormWebView] Iframe not accessible');
        }
      } else {
        // For mobile WebView - this should work properly
        console.log('[FormWebView] Injecting JavaScript into WebView');
        webviewRef.current?.injectJavaScript(script);
      }
      
      props.onFieldUpdate?.(fieldId, value);
    } catch (e) {
      // eslint-disable-next-line no-console
      console.warn('[FormWebView] updateSingleField failed', e);
    }
  }, [props]);

  // Expose methods via ref
  React.useImperativeHandle(ref, () => ({
    updateField: updateSingleField,
    fillForm: injectMapping
  }), [updateSingleField, injectMapping]);

  const onWebviewLoad = useCallback(() => {
    if (props.autoFillOnLoad && props.autoFillData) {
      injectMapping(props.autoFillData);
    }
  }, [injectMapping, props.autoFillData, props.autoFillOnLoad]);

  // Web: fetch HTML and inject mapping into srcDoc so iframe runs fillForm on load
  useEffect(() => {
    if (Platform.OS !== 'web') return;
    let cancelled = false;
    async function fetchAndPrepare() {
      try {
        // eslint-disable-next-line no-console
        console.log('[FormWebView] fetching for srcDoc', effectiveUrl);
        const res = await fetch(effectiveUrl, { credentials: 'omit' });
        // Debugging: log response headers for CORS troubleshooting
        // eslint-disable-next-line no-console
        try { const headerArr: any[] = []; (res.headers as any).forEach((v: string, k: string) => headerArr.push([k,v])); console.log('[FormWebView] fetch response headers:', headerArr); } catch (e) { console.log('[FormWebView] could not enumerate headers'); }
        if (!res.ok) {
          const text = await res.text().catch(() => '<no body>');
          throw new Error(`HTTP ${res.status}: ${text}`);
        }
        let html = await res.text();
        if (props.autoFillOnLoad && props.autoFillData) {
          const injector = `\n<script>document.addEventListener('DOMContentLoaded', function(){ try{ if(window.fillForm) window.fillForm(${JSON.stringify(props.autoFillData)}); }catch(e){ console.error('fillForm failed', e); } });</script>`;
          if (html.includes('</body>')) html = html.replace('</body>', injector + '</body>'); else html += injector;
        }
        if (!cancelled) setWebSrcDoc(html);
      } catch (e) {
        // eslint-disable-next-line no-console
        console.warn('[FormWebView] failed to fetch/prepare srcDoc for web', e);
        if (!cancelled) setWebSrcDoc(null);
      }
    }
    fetchAndPrepare();
    return () => { cancelled = true; };
  }, [effectiveUrl, props.autoFillOnLoad, JSON.stringify(props.autoFillData)]);

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
        {Platform.OS === 'web' ? (
          <iframe
            title={title}
            srcDoc={webSrcDoc ?? '<p>Loading form...</p>'}
            style={{ flex:1, width: '100%', height: '100%', border: 0 }}
          />
        ) : (
          WebViewComponent ? (
            <WebViewComponent
              ref={webviewRef}
              source={{ uri: effectiveUrl }}
              style={{ flex:1 }}
              startInLoadingState
              originWhitelist={["*"]}
              allowsFullscreenVideo
              onLoadEnd={() => onWebviewLoad()}
              onError={(e: any) => {
                // eslint-disable-next-line no-console
                console.warn('[FormWebView] load error', e.nativeEvent);
              }}
              onHttpError={(e: any) => {
                // eslint-disable-next-line no-console
                console.warn('[FormWebView] HTTP error', e.nativeEvent.statusCode);
              }}
            />
          ) : (
            <View style={{ flex:1, justifyContent:'center', alignItems:'center' }}><Text>WebView not available</Text></View>
          )
        )}
      </View>
    </Animated.View>
  );
});

const styles = StyleSheet.create({
  container: { width:'100%', backgroundColor:'#f1f5f9', borderBottomWidth:1, borderColor:'#e2e8f0' },
  header: { flexDirection:'row', alignItems:'center', paddingHorizontal:8, paddingVertical:8, borderBottomWidth:1, borderColor:'#e2e8f0' },
  dragHandle: { width:40, height:4, borderRadius:2, backgroundColor:'#cbd5e1', position:'absolute', top:4, left:'50%', marginLeft:-20 },
  title: { fontSize:14, fontWeight:'600', color:'#334155', flex:1, marginLeft:8 },
  iconBtn: { padding:6 },
  webContainer: { flex:1 }
});

export default FormWebView;