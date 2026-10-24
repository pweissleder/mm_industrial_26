"""LIVE voice check (paid): WAV → ElevenLabs STT → Claude → real tool → map data → ElevenLabs TTS.

Uses exactly the browser endpoints (/api/assistant/transcribe, /api/assistant/turn with speak=true).
Usage: python -B voice_check.py --port 8770 --wav live/voice-question.wav --out live/voice-run.json
"""
import argparse
import base64
import http.cookiejar
import json
import time
import urllib.request
import wave
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, default=8770)
parser.add_argument('--wav', required=True)
parser.add_argument('--out', required=True)
parser.add_argument('--building', default='proc-factory-dev-003')
args = parser.parse_args()
base = f'http://127.0.0.1:{args.port}'
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
opener.open(base + '/api/assistant/session').read()


def post(path, body):
    request = urllib.request.Request(base + path, data=json.dumps(body).encode(), method='POST', headers={
        'Content-Type': 'application/json', 'X-Prototype-Action': 'local', 'Origin': base})
    with opener.open(request, timeout=300) as response:
        return json.loads(response.read())


began = time.perf_counter()
heard = post('/api/assistant/transcribe', {'audio': base64.b64encode(Path(args.wav).read_bytes()).decode()})
turn = post('/api/assistant/turn', {'text': heard['text'], 'speak': True, 'voice': True, 'ui': {'building_id': args.building}})
total = (time.perf_counter() - began) * 1000
pcm = base64.b64decode(turn['audio_pcm16'] or '')
reply_wav = Path(args.out).with_suffix('.reply.wav')
with wave.open(str(reply_wav), 'wb') as out:
    out.setnchannels(1); out.setsampwidth(2); out.setframerate(turn['sample_rate']); out.writeframes(pcm)
route = (turn['ui'].get('route') or {}).get('result') or {}
record = {'transcript': heard['text'], 'stt_ms': heard['stt_ms'], 'tools': [(t['tool'], t['status']) for t in turn['tools']],
          'route_status': route.get('status'), 'route_length_m': route.get('length_m'), 'route_segments': len(route.get('segments') or []),
          'reply': turn['reply'], 'tts_audio_seconds': round(len(pcm) / 2 / turn['sample_rate'], 2),
          'voice_error': turn.get('voice_error'), 'timing_ms': turn['timing_ms'], 'client_total_ms': round(total),
          'usage': turn['usage']}
Path(args.out).write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps(record, indent=2, ensure_ascii=False))
