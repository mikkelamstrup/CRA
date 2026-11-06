"""Small, read-only exercise origin: only six preloaded public documents exist."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
from urllib.parse import unquote, urlsplit

FILES = {
    '/': ('index.html', 'text/html; charset=utf-8'),
    '/robots.txt': ('robots.txt', 'text/plain; charset=utf-8'),
    '/drift/': ('drift/index.html', 'text/html; charset=utf-8'),
    '/drift/journal.txt': ('drift/journal.txt', 'text/plain; charset=utf-8'),
    '/rapporter/rapport-206.txt': ('rapporter/rapport-206.txt', 'text/plain; charset=utf-8'),
    '/rapporter/rapport-207.txt': ('rapporter/rapport-207.txt', 'text/plain; charset=utf-8'),
}


def make_server(root, port):
    root = Path(root).resolve()
    pages = {}
    for route, (relative, mime) in FILES.items():
        path = (root / relative).resolve()
        path.relative_to(root)
        content = path.read_bytes()
        if len(content) > 262144:
            raise ValueError('Exercise document too large')
        pages[route] = (content, mime)

    class Handler(BaseHTTPRequestHandler):
        server_version = 'Nordlys'
        sys_version = ''

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *args):
            pass  # No visitor addresses, query strings or student content in logs.

        def reply(self, status, content, mime='text/plain; charset=utf-8'):
            self.close_connection = True
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Connection', 'close')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Robots-Tag', 'noindex, nofollow')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            if status == 405:
                self.send_header('Allow', 'GET, HEAD')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(content)

        def do_GET(self):
            if len(self.path) > 2048:
                return self.reply(414, b'Address too long\n')
            try:
                route = unquote(urlsplit(self.path).path, errors='strict')
            except (ValueError, UnicodeError):
                return self.reply(400, b'Invalid address\n')
            if route == '/index.html':
                route = '/'
            if route == '/drift':
                route = '/drift/'
            if route in pages:
                content, mime = pages[route]
                return self.reply(200, content, mime)
            self.reply(404, b'404 - Ressourcen blev ikke fundet.\n')

        do_HEAD = do_GET

        def do_POST(self):
            self.reply(405, b'Only GET and HEAD are supported.\n')

        do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_POST

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        request_queue_size = 32

        def __init__(self, *args):
            self.slots = threading.BoundedSemaphore(16)
            super().__init__(*args)

        def process_request(self, request, address):
            if not self.slots.acquire(blocking=False):
                request.close()
                return
            try:
                super().process_request(request, address)
            except BaseException:
                self.slots.release()
                raise

        def process_request_thread(self, request, address):
            try:
                super().process_request_thread(request, address)
            finally:
                self.slots.release()

    return Server(('127.0.0.1', port), Handler)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).parent / 'public')
    parser.add_argument('--port', type=int, default=28110)
    args = parser.parse_args()
    with make_server(args.root, args.port) as server:
        print('Nordlys exercise origin ready on loopback port', server.server_port, flush=True)
        server.serve_forever()
