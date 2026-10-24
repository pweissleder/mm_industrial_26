"""Read-only access to the supplied official ZIP and pinned reference modules."""
from __future__ import annotations
import copy
from functools import lru_cache
import os
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from zipfile import ZipFile
from errors import ModelError

sys.dont_write_bytecode = True
# Official kit v1.0.0 ZIP and a checkout of its starter_agent/benchmark at REFERENCE_COMMIT (read only).
ARCHIVE = Path(os.environ.get('SYNERA_KIT_ZIP', Path.home() / 'Downloads/industrial-agents-hackathon-2026-main.zip'))
REFERENCE = Path(os.environ.get('SYNERA_REFERENCE_DIR', Path.home() / 'Documents/HackathonTransfer/SyneraStarterReference'))
REFERENCE_COMMIT = 'cb51a6f2e357db743b0c8b2b377fe902ee3ced13'
MAX_MEMBER = 20_000_000

@lru_cache(maxsize=1)
def members():
    with ZipFile(ARCHIVE) as source:
        prefix = source.namelist()[0].split('/')[0]+'/'
        return prefix, frozenset(source.namelist())

def read_member(relative):
    prefix, names = members()
    if '..' in relative.split('/') or relative.startswith('/') or prefix+relative not in names:
        raise ModelError('invalid_input', 'Unknown official ZIP member')
    with ZipFile(ARCHIVE) as source:
        info = source.getinfo(prefix+relative)
        if info.file_size > MAX_MEMBER:
            raise ModelError('computation_limit', 'ZIP member too large')
        return source.read(info)

@lru_cache(maxsize=1)
def building_index():
    prefix, names=members()
    result={}
    for name in sorted(names):
        rel=name.removeprefix(prefix).split('/')
        if len(rel)==4 and rel[0]=='data' and rel[1] in {'train','dev'} and rel[3]=='building.json':
            result[rel[2]]='/'.join(rel)
    return result

@lru_cache(maxsize=16)
def _building(building_id):
    if not isinstance(building_id,str) or building_id not in building_index():
        raise ModelError('invalid_input', 'Select an existing Train/Dev building')
    return json.loads(read_member(building_index()[building_id]))

def load_building(building_id='proc-factory-dev-003'):
    return copy.deepcopy(_building(building_id))

def source_hash(building_id):
    return hashlib.sha256(read_member(building_index()[building_id])).hexdigest()

def image(building_id, level, variant='png'):
    data=_building(building_id)
    if level not in {f['level'] for f in data['floors']} or variant not in {'png','plain','svg'}:
        raise ModelError('invalid_input','Invalid floor/map selection')
    base=building_index()[building_id].rsplit('/',1)[0]
    filename=f'floor_{level}_plain.png' if variant=='plain' else f'floor_{level}.{variant}'
    return read_member(base+'/'+filename), 'image/svg+xml' if variant=='svg' else 'image/png'

@lru_cache(maxsize=4)
def reference_module(name):
    choices={'building_map':'starter_agent/building_map.py','score':'benchmark/score.py'}
    if name not in choices: raise ModelError('invalid_input','Unknown reference module')
    relative=choices[name]
    path=REFERENCE/relative
    original=read_member(relative)
    if path.read_bytes().replace(b'\r\n',b'\n') != original.replace(b'\r\n',b'\n'):
        raise ModelError('source_drift','Official reference module changed')
    spec=importlib.util.spec_from_file_location('synera_pinned_'+name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module

def reference_map(building):
    return reference_module('building_map').BuildingMap(copy.deepcopy(building))

def examples(split='dev'):
    """Test harness only. No Test answers or production lookups."""
    if split not in {'train','dev'}: raise ModelError('invalid_input','Train/Dev only')
    for bid,path in building_index().items():
        if path.split('/')[1]!=split: continue
        rel=path.rsplit('/',1)[0]+'/questions.jsonl'
        for line in read_member(rel).decode('utf-8').splitlines():
            if line.strip(): yield json.loads(line)
