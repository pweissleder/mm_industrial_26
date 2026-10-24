"""Read-only access to the official kit, pinned to kit v1.1.0 (starter-kit commit KIT_COMMIT).

Buildings, images and the manifest come from the supplied v1.0.0 ZIP: v1.1.0 did not change them, and
every building.json read is checked against the git blob id recorded in the v1.1.0 commit. The
reference modules (starter_agent/building_map.py, benchmark/score.py) and the Train/Dev questions with
their v1.1.0 answers come from a git checkout of the starter kit, each file verified against the same
commit (git blob id, CRLF-normalised). Nothing here writes to the kit or changes an expected answer."""
from __future__ import annotations
import copy
from functools import lru_cache
import os
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile
from errors import ModelError

sys.dont_write_bytecode = True
# Official kit ZIP (v1.0.0; buildings unchanged in v1.1.0) and a git checkout of the starter kit (read only).
ARCHIVE = Path(os.environ.get('SYNERA_KIT_ZIP', Path.home() / 'Downloads/industrial-agents-hackathon-2026-main.zip'))
REFERENCE = Path(os.environ.get('SYNERA_REFERENCE_DIR', Path.home() / 'Documents/HackathonTransfer/SyneraStarterReference'))
KIT_VERSION = '1.1.0'
KIT_COMMIT = 'dc03c447dbe5b2e5f82c7db0d72249931817d86d'
REFERENCE_COMMIT = KIT_COMMIT  # kept name: the commit every reference file is verified against
# Git blob ids at KIT_COMMIT; checked again against `git ls-tree` so a wrong pin cannot pass silently.
PINNED_MODULE_BLOBS = {'starter_agent/building_map.py': '8029290de2daaef82b161fdaa701a8f4679f06a4',
                       'benchmark/score.py': '11723a362747e93419b4f29631f2369385329094'}
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

def git_blob_id(content, normalise_crlf=True):
    """`git hash-object` of content; CRLF is normalised so a Windows autocrlf checkout still matches."""
    if normalise_crlf: content = content.replace(b'\r\n', b'\n')
    return hashlib.sha1(b'blob %d\0' % len(content) + content).hexdigest()

@lru_cache(maxsize=1)
def pinned_tree():
    """{path: git blob id} of KIT_COMMIT, read from the reference checkout's object store (no network)."""
    env = {**os.environ, 'GIT_NO_LAZY_FETCH': '1', 'GIT_TERMINAL_PROMPT': '0'}
    try:
        out = subprocess.run(['git', '-C', str(REFERENCE), 'ls-tree', '-r', '--full-tree', KIT_COMMIT],
                             capture_output=True, check=True, env=env, timeout=60).stdout.decode('utf-8')
    except (OSError, subprocess.SubprocessError) as error:
        raise ModelError('source_unavailable', f'Kit {KIT_VERSION} commit {KIT_COMMIT[:7]} not readable in the reference checkout',
                         {'reference_dir': str(REFERENCE)}) from error
    tree = {}
    for line in out.splitlines():
        meta, path = line.split('\t', 1)
        tree[path] = meta.split()[2]
    for path, blob in PINNED_MODULE_BLOBS.items():
        if tree.get(path) != blob: raise ModelError('source_drift', 'Pinned module blob does not match the kit commit', {'path': path})
    return tree

def kit_file(relative):
    """Bytes of `relative` exactly as in KIT_COMMIT: the checkout's file, or (if not checked out) the
    identical ZIP member. Anything that does not hash to the commit's blob id is refused."""
    if not isinstance(relative, str) or '..' in relative.split('/'):
        raise ModelError('invalid_input', 'Invalid kit path')
    expected = pinned_tree().get(relative)
    if expected is None:
        raise ModelError('invalid_input', 'File is not part of the pinned kit commit', {'path': relative})
    path = REFERENCE / relative
    if path.is_file():
        content = path.read_bytes()
        if git_blob_id(content) != expected:
            raise ModelError('source_drift', 'Reference file differs from the pinned kit commit', {'path': relative})
        return content
    try:
        content = read_member(relative)
    except ModelError:
        content = None
    if content is not None and git_blob_id(content, normalise_crlf=False) == expected:
        return content
    raise ModelError('source_unavailable', f'{relative} of kit {KIT_VERSION} is neither checked out nor identical in the ZIP',
                     {'path': relative})

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
    relative=building_index()[building_id];raw=read_member(relative)
    # One check per loaded building: the ZIP's building.json must be the v1.1.0 blob.
    if git_blob_id(raw, normalise_crlf=False)!=pinned_tree().get(relative):
        raise ModelError('source_drift','building.json in the ZIP differs from the pinned kit commit',{'building_id':building_id})
    return json.loads(raw)

def load_building(building_id='proc-factory-dev-003'):
    return copy.deepcopy(_building(building_id))

def verify_buildings(building_ids=None):
    """Compare ZIP building.json files with the KIT_COMMIT tree (all Train/Dev by default)."""
    tree=pinned_tree();index=building_index();ids=sorted(index) if building_ids is None else list(building_ids)
    differing=[bid for bid in ids if git_blob_id(read_member(index[bid]),normalise_crlf=False)!=tree.get(index[bid])]
    return {'checked':len(ids),'identical':len(ids)-len(differing),'differing':differing,'commit':KIT_COMMIT}

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
    if not path.is_file() or git_blob_id(path.read_bytes())!=pinned_tree()[relative]:
        raise ModelError('source_drift','Official reference module differs from the pinned kit commit',{'path':relative})
    spec=importlib.util.spec_from_file_location('synera_pinned_'+name,path)
    module=importlib.util.module_from_spec(spec)
    sys.modules[spec.name]=module
    spec.loader.exec_module(module)
    return module

def reference_map(building):
    return reference_module('building_map').BuildingMap(copy.deepcopy(building))

def _questions(split, filename):
    if split not in {'train','dev'}: raise ModelError('invalid_input','Train/Dev only')
    for bid,path in building_index().items():
        if path.split('/')[1]!=split: continue
        rel=path.rsplit('/',1)[0]+'/'+filename
        for line in kit_file(rel).decode('utf-8').splitlines():
            if line.strip(): yield json.loads(line)

def examples(split='dev'):
    """Test harness only: kit v1.1.0 benchmark questions (questions.jsonl). No Test answers or production lookups."""
    yield from _questions(split, 'questions.jsonl')

def basic_examples(split='dev'):
    """Test harness only: kit v1.1.0 warm-up questions (questions_basic.jsonl)."""
    yield from _questions(split, 'questions_basic.jsonl')
