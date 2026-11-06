"""Bounded mTLS SSH streams. No client-selected destination or exposed guest port."""
import asyncio
import json
import socket
import threading

from cra.node_contracts import TerminalAllocation


class TerminalStreamServer:
    def __init__(self, address, context, allocate, *, limit=64):
        self.context=context
        self.allocate=allocate
        self.limit=limit
        self.socket=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        self.socket.bind(address)
        self.socket.listen(64)
        self.socket.setblocking(False)
        self.started=threading.Event()
        self.thread=threading.Thread(target=self.run,daemon=True,name='cra-terminal-stream')
        self.loop=None
        self.main_task=None

    def start(self):
        self.thread.start()
        if not self.started.wait(5) or not self.thread.is_alive():
            raise RuntimeError('Terminal stream unavailable')

    def close(self):
        if self.loop and self.main_task:
            self.loop.call_soon_threadsafe(self.main_task.cancel)
        self.thread.join(timeout=15)
        self.socket.close()

    def run(self):
        try:asyncio.run(self.serve())
        except asyncio.CancelledError:pass
        finally:self.socket.close()

    async def serve(self):
        self.loop=asyncio.get_running_loop()
        self.main_task=asyncio.current_task()
        tasks=set()
        self.started.set()
        try:
            while True:
                client,_=await self.loop.sock_accept(self.socket)
                # Count raw sockets before TLS, preventing unbounded stalled handshakes.
                if len(tasks)>=self.limit:
                    client.close();continue
                task=asyncio.create_task(self.handle(client))
                tasks.add(task);task.add_done_callback(tasks.discard)
        finally:
            for task in list(tasks):task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)

    async def handle(self,client):
        writers=[];pumps=[]
        try:
            loop=asyncio.get_running_loop()
            reader=asyncio.StreamReader(limit=8192)
            protocol=asyncio.StreamReaderProtocol(reader)
            transport,_=await loop.connect_accepted_socket(lambda:protocol,client,ssl=self.context,ssl_handshake_timeout=5,ssl_shutdown_timeout=3)
            writer=asyncio.StreamWriter(transport,protocol,reader,loop);writers.append(writer)
            subject=dict(pair for group in writer.get_extra_info('peercert',{}).get('subject',()) for pair in group)
            if subject.get('commonName')!='cra-terminal':raise ValueError('Role denied')
            line=await asyncio.wait_for(reader.readline(),5)
            if not line.endswith(b'\n') or len(line)>8192:raise ValueError('Invalid stream header')
            allocation=await asyncio.to_thread(self.allocate,TerminalAllocation.model_validate_json(line))
            # allocate has checked the exact journal-derived port. No destination from wire.
            guest_reader,guest_writer=await asyncio.wait_for(asyncio.open_connection('127.0.0.1',allocation['port'],limit=65536),5)
            writers.append(guest_writer)
            header=json.dumps(allocation,separators=(',',':')).encode()+b'\n'
            if len(header)>16384:raise ValueError('Invalid allocation size')
            writer.write(header);await asyncio.wait_for(writer.drain(),5)

            async def pump(source,destination):
                while data:=await source.read(16384):
                    destination.write(data)
                    await asyncio.wait_for(destination.drain(),15)
            pumps=[asyncio.create_task(pump(reader,guest_writer)),asyncio.create_task(pump(guest_reader,writer))]
            done,_=await asyncio.wait(pumps,return_when=asyncio.FIRST_COMPLETED)
            for task in done:task.result()
        except (Exception,asyncio.CancelledError):
            # Never log protocol bodies, credentials, guest bytes or exception payloads.
            pass
        finally:
            for task in pumps:task.cancel()
            await asyncio.gather(*pumps,return_exceptions=True)
            for writer in writers:writer.close()
            for writer in writers:
                try:await asyncio.wait_for(writer.wait_closed(),3)
                except (Exception,asyncio.CancelledError):pass
            client.close()
