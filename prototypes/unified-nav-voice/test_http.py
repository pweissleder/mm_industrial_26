import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from server import handler_for
from service import Service

class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service=Service();cls.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(cls.service))
        cls.base='http://127.0.0.1:'+str(cls.server.server_port)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()
    @classmethod
    def tearDownClass(cls):cls.server.shutdown();cls.server.server_close();cls.thread.join(timeout=2)

    def call(self,path,body=None,headers=None,raw=None):
        if body is not None:raw=json.dumps(body).encode()
        head={'Origin':self.base,'Content-Type':'application/json','X-Prototype-Action':'local'}
        if headers:head.update(headers)
        req=Request(self.base+path,data=raw,headers=head)
        try:
            with urlopen(req,timeout=30) as r:return r.status,r.read(),dict(r.headers)
        except HTTPError as e:return e.code,e.read(),dict(e.headers)

    def test_get_and_security_headers(self):
        code,body,headers=self.call('/health');self.assertEqual(code,200)
        self.assertFalse(json.loads(body)['model_calls']);self.assertIn("default-src 'self'",headers['Content-Security-Policy'])
        self.assertEqual(self.call('/catalog')[0],200);self.assertEqual(self.call('/specs')[0],200)
        self.assertEqual(self.call('/../data.py')[0],404)
        self.assertEqual(self.call('/health',headers={'Host':'evil.test'})[0],403)

    def test_origin_and_body_validation(self):
        body={'building_id':'proc-factory-dev-003','arguments':{'action':'reset'}}
        self.assertEqual(self.call('/api/scenario',body,{'Origin':'https://evil.test'})[0],403)
        self.assertEqual(self.call('/api/scenario',body,{'X-Prototype-Action':''})[0],403)
        self.assertEqual(self.call('/api/scenario',body,{'Content-Type':'text/plain'})[0],415)
        self.assertEqual(self.call('/api/scenario',raw=b'[]')[0],422)
        self.assertEqual(self.call('/api/scenario',raw=b'{')[0],400)
        self.assertEqual(self.call('/api/scenario',raw=b'{"number":NaN}')[0],400)
        self.assertEqual(self.call('/api/scenario?action=reset')[0],404)

    def test_operation_and_error_path(self):
        request={'building_id':'proc-factory-dev-003','arguments':{'operation':'largest_room','arguments':{'floor_id':'proc-factory-dev-003-F0','room_type':'office'}}}
        code,body,_=self.call('/api/operation',request);self.assertEqual(code,200)
        self.assertEqual(json.loads(body)['model'],'benchmark')
        request['arguments']['operation']='not-real'
        self.assertEqual(self.call('/api/operation',request)[0],422)

    def test_scenario_only_in_ram(self):
        before=self.service.building('proc-factory-dev-003')
        body={'building_id':'proc-factory-dev-003','arguments':{'action':'close','element':'proc-factory-dev-003-F0-S001'}}
        code,result,_=self.call('/api/scenario',body);self.assertEqual(code,200)
        self.assertTrue(json.loads(result)['closed'])
        body['arguments']={'action':'reset'};self.assertEqual(self.call('/api/scenario',body)[0],200)
        self.assertEqual(self.service.building('proc-factory-dev-003')['data'],before['data'])

    def test_train_dev_examples_and_live_comparison(self):
        code,body,_=self.call('/examples?split=dev');self.assertEqual(code,200)
        example=next(q for q in json.loads(body) if q['kind']=='largest_room')
        request={'building_id':example['building_id'],'arguments':{'split':'dev','id':example['id']}}
        code,body,_=self.call('/api/example',request);self.assertEqual(code,200)
        value=json.loads(body);self.assertTrue(value['comparison']['matches_reference'])
        self.assertEqual(value['example']['expected'],example['answer'])
        self.assertEqual(self.call('/examples?split=test')[0],422)
        request['arguments']['split']='test'
        self.assertEqual(self.call('/api/example',request)[0],422)

    def test_navigation_diagnostic_survives_http(self):
        request={'building_id':'proc-factory-dev-003','arguments':{
            'model':'precise','start':{'kind':'space','id':'proc-factory-dev-003-F0-S019'},
            'target':{'kind':'space','id':'proc-factory-dev-003-F0-S001'},
            'closed':['proc-factory-dev-003-F0-S019']}}
        code,body,_=self.call('/api/navigation',request);self.assertEqual(code,422)
        value=json.loads(body)['error'];self.assertEqual(value['code'],'blocked_by_closure')
        self.assertEqual(value['details']['diagnosis']['category'],'closed')

if __name__=='__main__':unittest.main(verbosity=2)
