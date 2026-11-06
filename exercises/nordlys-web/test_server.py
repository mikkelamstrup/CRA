import http.client
from html.parser import HTMLParser
from pathlib import Path
import threading
import unittest
from server import make_server

class Visible(HTMLParser):
    def __init__(self):
        super().__init__();self.hidden=0;self.text=[];self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('style','script'):self.hidden+=1
        if tag=='a':self.links.append(dict(attrs).get('href',''))
    def handle_endtag(self,tag):
        if tag in ('style','script'):self.hidden-=1
    def handle_data(self,text):
        if not self.hidden:self.text.append(text)

class OriginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=make_server(Path(__file__).parent/'public',0)
        cls.thread=threading.Thread(target=cls.server.serve_forever);cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
    def request(self,path,method='GET'):
        conn=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=3)
        try:
            conn.request(method,path);r=conn.getresponse();return r.status,dict(r.getheaders()),r.read().decode()
        finally:conn.close()
    def test_public_front_does_not_reveal_trail_in_visible_text(self):
        status,_,html=self.request('/');self.assertEqual(status,200)
        doc=Visible();doc.feed(html);text=' '.join(doc.text)
        for secret in ('robots.txt','NL-207','/drift','rapport-207','CRA{'):
            self.assertNotIn(secret,text)
        self.assertTrue(all(h=='/' or h.startswith('#') for h in doc.links))
        self.assertIn('/robots.txt',html)
    def test_actual_http_trail_and_demo_has_no_cra_flag(self):
        self.assertIn('Disallow: /drift/',self.request('/robots.txt')[2])
        self.assertIn('/drift/journal.txt',self.request('/drift/')[2])
        self.assertIn('/rapporter/rapport-207.txt',self.request('/drift/journal.txt')[2])
        status,_,text=self.request('/rapporter/rapport-207.txt')
        self.assertEqual(status,200);self.assertIn('NL-207',text);self.assertIn('Demonstrationskopi',text);self.assertNotIn('CRA{',text)
    def test_exact_allowlist_denies_file_traversal_listing_and_source(self):
        for path in ('/../server.py','/%2e%2e/server.py','/server.py','/.env','/etc/passwd','/rapporter/','/drift/../../server.py','/api/v1/auth/me','//etc/passwd','/%252e%252e/server.py'):
            self.assertEqual(self.request(path)[0],404,path)
    def test_read_only_http_and_headers(self):
        for method in ('POST','PUT','DELETE','PATCH'):
            status,h,_=self.request('/',method);self.assertEqual(status,405);self.assertEqual(h['Allow'],'GET, HEAD')
        status,h,body=self.request('/','HEAD');self.assertEqual(status,200);self.assertEqual(body,'');self.assertGreater(int(h['Content-Length']),0)
        self.assertEqual(h['X-Robots-Tag'],'noindex, nofollow');self.assertIn("default-src 'none'",h['Content-Security-Policy'])
        self.assertEqual(self.request('/missing')[0],404)
        self.assertEqual(self.request('/'+'a'*2050)[0],414)

if __name__=='__main__':unittest.main()
