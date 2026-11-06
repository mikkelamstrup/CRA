"""Real HTTP negative controls for the deliberately vulnerable isolated exercise."""
import http.cookiejar
import importlib.util
from pathlib import Path
import threading
import urllib.error
import urllib.request
import json

import pytest

spec=importlib.util.spec_from_file_location('course_training_app',Path(__file__).resolve().parents[1]/'content/labs/course/training_app.py')
app=importlib.util.module_from_spec(spec);spec.loader.exec_module(app)


def test_real_login_and_intended_idor_only():
    flag='SYNTHETIC_APP_FLAG'
    server=app.TrainingServer(('127.0.0.1',0),flag)
    thread=threading.Thread(target=server.serve_forever);thread.start()
    url='http://127.0.0.1:'+str(server.server_port)
    client=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def get(path,data=None):
        with client.open(url+path,data=data,timeout=3) as response:return json.load(response)
    try:
        with pytest.raises(urllib.error.HTTPError) as denied:get('/api/reports?id=702')
        assert denied.value.code==401
        with pytest.raises(urllib.error.HTTPError) as denied:get('/login',b'username=analyst&password=wrong')
        assert denied.value.code==403
        get('/login',b'username=analyst&password=nordlys-training-only')
        assert get('/api/me')['allowed_reports']==[701]
        assert flag not in str(get('/api/reports?id=701'))
        assert flag in get('/api/reports?id=702')['body']
        for path,status in [('/api/reports?id=999',404),('/api/reports?id=701&id=702',400),('/etc/passwd',404),('/api/reports?id=../../etc/passwd',404)]:
            with pytest.raises(urllib.error.HTTPError) as denied:get(path)
            assert denied.value.code==status
        assert len(server.sessions)==1
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
