"""Loopback-only prototype: deterministic Service plus optional Claude/ElevenLabs assistant on the same state."""
from __future__ import annotations
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import base64
import json
import os
import re
import secrets
from pathlib import Path
import sys
import traceback
from urllib.parse import parse_qs,urlparse
from data import ARCHIVE,REFERENCE,image
from errors import ModelError
from service import Service

sys.dont_write_bytecode=True
HERE=Path(__file__).resolve().parent

def handler_for(service,assistant=None,voice=None):
    class Handler(BaseHTTPRequestHandler):
        def local(self):
            return self.headers.get('Host') in {f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        def send(self,value,status=200,mime='application/json; charset=utf-8'):
            body=value if isinstance(value,bytes) else json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Permissions-Policy','microphone=(self)')
            for cookie in getattr(self,'cookies_out',[]):self.send_header('Set-Cookie',cookie)
            self.cookies_out=[]
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers();self.wfile.write(body)
        def failure(self,error):
            if isinstance(error,ModelError):return self.send({'error':error.as_dict()},422)
            if isinstance(error,(ValueError,KeyError,TypeError,UnicodeDecodeError)):
                return self.send({'error':{'code':'invalid_input','message':str(error),'details':{}}},400)
            traceback.print_exc()
            return self.send({'error':{'code':'internal_error','message':'Local calculation failed; see server log','details':{}}},500)
        def do_GET(self):
            if not self.local():return self.send({'error':{'code':'local_only','message':'Loopback Host required'}},403)
            path=urlparse(self.path);args=parse_qs(path.query);value=lambda key,default='':args.get(key,[default])[0]
            try:
                if path.path in {'/','/app.js','/style.css','/assistant.js','/recorder.js'}:
                    name='index.html' if path.path=='/' else path.path[1:]
                    mime='text/html' if name=='index.html' else 'text/css' if name=='style.css' else 'text/javascript'
                    return self.send((HERE/name).read_bytes(),mime=mime+'; charset=utf-8')
                if path.path=='/health':return self.send({'status':'ok','model_calls':bool(assistant and assistant.transport),'bind':'127.0.0.1'})
                if path.path=='/api/assistant/session':
                    self.session_token()
                    return self.send({'live':bool(assistant and assistant.transport),'voice':voice is not None,
                                      'model':'claude-sonnet-5-5','effort':'medium'})
                if path.path=='/catalog':return self.send(service.catalog())
                if path.path=='/building':
                    with service.lock:return self.send(service.building(value('id')))
                if path.path=='/specs':
                    from operations import operation_specs
                    return self.send(operation_specs())
                if path.path=='/examples':return self.send(service.examples(value('split','dev')))
                if path.path=='/map':
                    body,mime=image(value('id'),int(value('level')),value('variant','png'))
                    return self.send(body,mime=mime)
                return self.send({'error':{'code':'not_found','message':'Unknown endpoint'}},404)
            except Exception as error:return self.failure(error)
        def do_POST(self):
            if not self.local() or self.headers.get('Origin')!=f"http://{self.headers.get('Host')}" or self.headers.get('X-Prototype-Action')!='local':
                return self.send({'error':{'code':'same_origin_required','message':'Same-origin local action required'}},403)
            if self.headers.get('Content-Type')!='application/json':
                return self.send({'error':{'code':'json_required','message':'JSON required'}},415)
            if self.path.startswith('/api/assistant/'):return self.assistant_post()
            actions={'/api/navigation':service.navigate,'/api/analyze':service.analyze,'/api/operation':service.operation,'/api/scenario':service.change,'/api/example':service.example}
            if self.path not in actions:return self.send({'error':{'code':'not_found','message':'Unknown action'}},404)
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=65536:raise ModelError('invalid_input','Request too large or empty')
                self.connection.settimeout(10)
                def constant(value):raise ValueError('Non-finite JSON number: '+value)
                body=json.loads(self.rfile.read(length),parse_constant=constant)
                if not isinstance(body,dict) or not isinstance(body.get('building_id'),str) or not isinstance(body.get('arguments'),dict):
                    raise ModelError('invalid_input','building_id and structured arguments required')
                with service.lock:return self.send(actions[self.path](body['building_id'],body['arguments']))
            except Exception as error:return self.failure(error)
        def session_token(self):
            match=re.search(r'(?:^|;\s*)synera_assistant=([a-f0-9]{32})',self.headers.get('Cookie',''))
            if match:return match.group(1)
            token=secrets.token_hex(16)
            self.cookies_out=[f'synera_assistant={token}; Path=/; HttpOnly; SameSite=Strict']
            return token
        def assistant_post(self):
            from assistant import AssistantError
            from voice import VoiceServiceError
            from time import perf_counter
            limit=700000 if self.path=='/api/assistant/transcribe' else 65536
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=limit:raise AssistantError('Request too large or empty.')
                self.connection.settimeout(15)
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict):raise AssistantError('JSON object required.')
                if assistant is None:raise AssistantError('Assistant not available in this server.')
                token=self.session_token()
                if self.path=='/api/assistant/turn':
                    value=assistant.turn(token,body.get('text'),body.get('ui'),body.get('voice') is True)
                    value['audio_pcm16']=None;value['sample_rate']=16000
                    if body.get('speak') and voice is not None:
                        began=perf_counter()
                        try:value['audio_pcm16']=base64.b64encode(voice.synthesize(spoken(value['reply']))).decode('ascii')
                        except VoiceServiceError as error:value['voice_error']=str(error)
                        value['timing_ms']['tts_ms']=round((perf_counter()-began)*1000,1)
                    return self.send(value)
                if self.path=='/api/assistant/transcribe':
                    if voice is None:raise AssistantError('Voice is off: no ElevenLabs key in the server process.')
                    audio=body.get('audio')
                    if not isinstance(audio,str):raise AssistantError('Audio missing.')
                    wav=base64.b64decode(audio,validate=True)
                    if len(wav)<44 or wav[:4]!=b'RIFF' or wav[8:12]!=b'WAVE' or not 3200<=len(wav)-44<=320000:
                        raise AssistantError('Recording must be a 0.1 to 10 second WAV.')
                    began=perf_counter();text=voice.transcribe(wav)
                    return self.send({'text':text,'stt_ms':round((perf_counter()-began)*1000,1)})
                if self.path=='/api/assistant/confirm':
                    return self.send(assistant.confirm(token,body.get('proposal_id'),body.get('approved') is True))
                if self.path=='/api/assistant/reset':
                    assistant.reset(token);return self.send({'status':'reset'})
                return self.send({'error':{'code':'not_found','message':'Unknown action'}},404)
            except AssistantError as error:return self.send({'error':{'code':'assistant','message':str(error),'details':{}}},503)
            except VoiceServiceError as error:return self.send({'error':{'code':'voice','message':str(error),'details':{}}},502)
            except Exception as error:return self.failure(error)
        def log_message(self,*_args):pass
    return Handler

def spoken(text,limit=600):
    """Shorten the reply for TTS at a sentence boundary; the full text stays on screen."""
    text=re.sub(r'[*_`#]+','',text).strip()
    if len(text)<=limit:return text
    cut=max(text.rfind('. ',0,limit-1),text.rfind('? ',0,limit-1))
    return text[:cut+1] if cut>100 else text[:limit-1]+'...'

def build_assistant(service,offline=False):
    from assistant import Assistant,LiveTransport
    transport=None
    if not offline and os.environ.get('ANTHROPIC_API_KEY','').strip():transport=LiveTransport()
    voice=None
    if not offline and os.environ.get('ELEVENLABS_API_KEY','').strip():
        from voice import ElevenREST
        voice=ElevenREST(os.environ.get('SYNERA_VOICE_ID','JBFqnCBsd6RMkjVDRZzb'))
    for name in ('ANTHROPIC_API_KEY','ELEVENLABS_API_KEY'):os.environ.pop(name,None)
    return Assistant(service,transport),voice

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--port',type=int,default=8766)
    parser.add_argument('--offline',action='store_true',help='never call Claude or ElevenLabs')
    args=parser.parse_args();service=Service();assistant,voice=build_assistant(service,args.offline)
    server=ThreadingHTTPServer(('127.0.0.1',args.port),handler_for(service,assistant,voice))
    metadata={'url':f'http://127.0.0.1:{server.server_port}','pid':os.getpid(),
              'assistant_live':assistant.transport is not None,'voice':voice is not None,
              'source_zip_sha256':hashlib.sha256(ARCHIVE.read_bytes()).hexdigest(),
              'reference_module_sha256':hashlib.sha256((REFERENCE/'starter_agent/building_map.py').read_bytes()).hexdigest()}
    (HERE/'session.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8');print(metadata['url'],flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
