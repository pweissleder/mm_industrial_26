"""ElevenLabs STT/TTS, reused unchanged from SyneraVoicePOC/synera_voice/voice_eleven.py (official REST endpoints)."""
import os
import re
import logging
import httpx2


class VoiceServiceError(RuntimeError):
    def __init__(self, stage, status=None):
        self.stage, self.http_status = stage, status
        super().__init__(f'ElevenLabs {stage} failed' + (f' (HTTP {status}).' if status else ' (connection, timeout, or invalid response).'))


class ElevenREST:
    def __init__(self, voice_id, client=None):
        if not re.fullmatch(r'[A-Za-z0-9]{10,64}', voice_id):
            raise ValueError('Invalid ElevenLabs voice ID.')
        self.voice_id = voice_id
        if client is None:
            key = os.environ.get('ELEVENLABS_API_KEY', '')
            if not key:
                raise ValueError('The ElevenLabs server-process key is missing. Use the DPAPI launcher.')
            for name in ('httpx2', 'httpcore2'):
                logger = logging.getLogger(name)
                logger.handlers.clear(); logger.addHandler(logging.NullHandler()); logger.propagate = False
            self.client = httpx2.Client(base_url='https://api.elevenlabs.io',
                                       headers={'xi-api-key': key}, timeout=30.0, follow_redirects=False)
            key = None
        else:
            self.client = client

    def transcribe(self, wav, *, filename='speech.wav', mime='audio/wav'):
        try:
            response = self.client.post('/v1/speech-to-text',
                data={'model_id':'scribe_v2', 'language_code':'en', 'tag_audio_events':'false'},
                files={'file':(filename, wav, mime)})
            if response.status_code != 200:
                raise VoiceServiceError('STT', response.status_code)
            text = response.json().get('text')
            if not isinstance(text, str) or not text.strip() or len(text) > 2000:
                raise VoiceServiceError('STT')
            return text.strip()
        except VoiceServiceError:
            raise
        except Exception:
            raise VoiceServiceError('STT') from None

    def synthesize(self, text):
        if not text or len(text) > 600:
            raise VoiceServiceError('TTS text limit')
        try:
            response = self.client.post('/v1/text-to-speech/' + self.voice_id,
                params={'output_format':'pcm_16000'},
                json={'text':text, 'model_id':'eleven_flash_v2_5', 'language_code':'en'})
            if response.status_code != 200:
                raise VoiceServiceError('TTS', response.status_code)
            pcm = response.content
            if 'json' in response.headers.get('content-type', '') or not pcm or len(pcm) > 4000000 or len(pcm) % 2:
                raise VoiceServiceError('TTS audio format')
            return pcm
        except VoiceServiceError:
            raise
        except Exception:
            raise VoiceServiceError('TTS') from None

    def close(self):
        self.client.close()
