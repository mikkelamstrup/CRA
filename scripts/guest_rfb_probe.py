"""Synthetic acceptance helper: bounded localhost-only raw RFB screenshot in own guest."""
import base64
import socket
import struct
import zlib


def capture():
    s=socket.create_connection(('127.0.0.1',5900),timeout=15)
    def read(n):
        if not 0<=n<=1280*800*4:raise ValueError('Probe limit')
        result=bytearray()
        while len(result)<n:
            part=s.recv(n-len(result))
            if not part:raise EOFError()
            result.extend(part)
        return bytes(result)
    assert read(12).startswith(b'RFB 003.');s.sendall(b'RFB 003.008\n')
    choices=read(read(1)[0]);assert 1 in choices;s.sendall(b'\x01');assert read(4)==b'\0'*4
    s.sendall(b'\x01');header=read(24);w,h=struct.unpack('!HH',header[:4]);assert w==1280 and h==800
    read(struct.unpack('!I',header[20:])[0])
    s.sendall(b'\0\0\0\0'+struct.pack('!BBBBHHHBBBxxx',32,24,0,1,255,255,255,16,8,0))
    s.sendall(struct.pack('!BBHi',2,0,1,0));s.sendall(struct.pack('!BBHHHH',3,0,0,0,w,h))
    image=bytearray(w*h*3)
    assert read(1)==b'\0';read(1);rectangles=struct.unpack('!H',read(2))[0];assert rectangles<=1024
    for _ in range(rectangles):
        x,y,rw,rh,encoding=struct.unpack('!HHHHi',read(12));assert encoding==0 and x+rw<=w and y+rh<=h
        data=read(rw*rh*4)
        for row in range(rh):
            pixels=data[row*rw*4:(row+1)*rw*4];rgb=bytearray(rw*3)
            rgb[0::3]=pixels[2::4];rgb[1::3]=pixels[1::4];rgb[2::3]=pixels[0::4]
            start=((y+row)*w+x)*3;image[start:start+len(rgb)]=rgb
    s.close()
    def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data))
    pixels=b''.join(b'\0'+image[i*w*3:(i+1)*w*3] for i in range(h))
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!IIBBBBB',w,h,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(pixels))+chunk(b'IEND',b'')
    print(base64.b64encode(png).decode())

if __name__=='__main__':capture()
