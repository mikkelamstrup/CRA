"""Rendered mission served by real stdlib HTTP; synthetic flag material only."""
import functools
import http.server
import threading
import urllib.error
import urllib.request
from uuid import uuid4

import pytest
from test_cleanup import agent, resources


def test_web_trail_real_http_and_scoped_flags(resources, tmp_path):
    _, command, _, _, _ = resources
    (agent.runtime.SECRETS/'flags.key').write_bytes(b'synthetic-test-key'.ljust(32,b'0'))
    command.mission_id='web-trail';command.mission_version=1
    pages=agent.target_pages(command);flag=agent.flag_value(command)
    assert agent.objective_id(command)=='find-report'
    assert [p for p,text in pages.items() if flag in text]==['rapporter/rapport-207.txt']
    site=tmp_path/'site';site.mkdir()
    for path,text in pages.items():
        target=site/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self,*args):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=str(site)))
    thread=threading.Thread(target=server.serve_forever);thread.start()
    base='http://127.0.0.1:'+str(server.server_port)
    def get(path):
        with urllib.request.urlopen(base+path,timeout=3) as response:
            assert response.status==200
            return response.read().decode()
    try:
        assert '/robots.txt' in get('/')
        assert 'Disallow: /drift/' in get('/robots.txt')
        assert '/drift/journal.txt' in get('/drift/')
        assert 'NL-207' in get('/drift/journal.txt')
        assert '/rapporter/rapport-207.txt' in get('/drift/journal.txt')
        assert flag not in get('/rapporter/rapport-206.txt')
        assert flag in get('/rapporter/rapport-207.txt')
        with urllib.request.urlopen(urllib.request.Request(base+'/robots.txt',method='HEAD')) as response:
            assert response.status==200 and response.read()==b''
        with pytest.raises(urllib.error.HTTPError) as error:get('/does-not-exist')
        assert error.value.code==404
    finally:
        server.shutdown();server.server_close();thread.join(timeout=3)
    command.generation+=1
    assert agent.flag_value(command)!=flag
    assert flag not in ''.join(agent.target_pages(command).values())
    command.generation-=1;command.lab_id=uuid4()
    assert agent.flag_value(command)!=flag
    command.mission_id='first-contact'
    assert agent.objective_id(command)=='find-service'
    assert agent.flag_value(command) in agent.target_pages(command)['index.html']
    command.mission_id='first-files'
    assert agent.objective_id(command)=='find-evidence'
    assert 'CRA{' not in agent.target_pages(command)['index.html']
    command.mission_id='unapproved'
    with pytest.raises(ValueError):agent.target_pages(command)
