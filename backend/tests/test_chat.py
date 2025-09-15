from fastapi.testclient import TestClient
from app.main import app
import io

client = TestClient(app)

def test_chat_text():
    resp = client.post('/api/chat', data={'text': 'Hello', 'media_type': 'text'})
    assert resp.status_code == 200
    data = resp.json()
    assert 'messages' in data
    assert len(data['messages']) == 2
    user, assistant = data['messages']
    assert user['content'] == 'Hello'
    assert user['type'] == 'text'
    assert assistant['content'].startswith('Echo:')

def test_chat_image_upload():
    file_content = b'fake image bytes'
    resp = client.post('/api/chat', data={'media_type': 'image'}, files={'file': ('test.png', io.BytesIO(file_content), 'image/png')})
    assert resp.status_code == 200
    data = resp.json()
    user, assistant = data['messages']
    assert user['type'] == 'image'
    assert user['content'] == 'test.png'
    assert assistant['content'].startswith('Echo:')

def test_chat_audio_upload():
    file_content = b'fake audio bytes'
    resp = client.post('/api/chat', data={'media_type': 'audio'}, files={'file': ('sample.wav', io.BytesIO(file_content), 'audio/wav')})
    assert resp.status_code == 200
    data = resp.json()
    user, assistant = data['messages']
    assert user['type'] == 'audio'
    assert user['content'] == 'sample.wav'
    assert assistant['content'].startswith('Echo:')

def test_chat_form_url_trigger():
    resp = client.post('/api/chat', data={'text': 'Please open the tax form for me', 'media_type': 'text'})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data['messages']) == 2
    user, assistant = data['messages']
    assert user['content'].startswith('Please open')
    assert assistant.get('form_url') is not None
    assert 'Opening form:' in assistant['content']
