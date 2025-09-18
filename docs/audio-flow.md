lets make this system work for audio input and output
- we use gpt-realtime model that supports direct audio input and output, we do not need stt or tts.
- we already have websocket, lets use that for audio streaming 
- lets make the basic workflow work first for audio frontend -> backend -> model -> backend -> frontend
- existing workflow should not break 
- form markers should work.
- clean code, no tests needed, no documentation needed, do not run servers I have them running already
- tell model to return audio and form markers separately
- form markers should always be text, it might not need conversational style just instructions to backend.
- tell model to answer in audio when input is audio and in text when input is text.
