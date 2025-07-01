# coding=utf-8
# pyvncs
# Copyright (C) 2017-2018 Matias Fernandez, Copyright (C) 2025 Kevin Ahr
#
# This program is free software: you can redistribute it and/or modify it under
# the terms of the GNU Lesser General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option) any
# later version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

import struct
from time import sleep
from typing import Callable
from PIL import Image, ImageChops

import socket
import time
from loguru import logger

from vnc.util.rfb_bitmap import RfbBitmap

# encodings support
import vnc.util.encodings as encs
from vnc.util.encodings.common import ENCODINGS

# auth support
from vnc.util.auth.vnc_auth import VNCAuth


class VNCServer:
    class RFB_SECTYPES:
        vncauth = 2  # plain VNC auth

    encoding_object = None

    def __init__(
        self,
        socket,
        image_source: Callable[[], Image],
        password=None,
        auth_type=2,
        pem_file="",
        vnc_config=None,
    ):
        self.RFB_VERSION = "003.008"
        self.initmsg = "RFB %s\n" % self.RFB_VERSION
        self.socket = socket
        self.framebuffer = None
        self.password = password
        self.auth_type = auth_type
        self.pem_file = pem_file
        self.vnc_config = vnc_config
        self.image_source = image_source
        logger.debug("Configured auth type:", self.auth_type)

    def __del__(self):
        logger.debug("VncServer died")

    def send_message(self, message):
        message = bytes(message, "iso8859-1")
        buff = struct.pack("I%ds" % (len(message),), len(message), message)
        self.socket.send(buff)

    def get_buffer(self, timeout):
        self.socket.settimeout(timeout)
        try:
            data = self.socket.recv(1024)
        except socket.timeout:
            data = None
            logger.debug("getbuff() timeout")
        return data

    def init(self):
        sock = self.socket
        sock.send(self.initmsg.encode())
        data = self.get_buffer(30)
        logger.debug("init received: '%s'" % data)
        server_version = float(self.RFB_VERSION)
        if not data:
            return False

        try:
            client_version = float(data[4:11])
        except ValueError:
            logger.debug("Error parsing client version")
            return False
        logger.debug("client, server:", client_version, server_version)

        # Determine auth type
        if not self.password:
            self.auth_type = 1  # None
            sectypes = [1]
        else:
            self.auth_type = 2  # VNCAuth
            sectypes = [2]

        sendbuff = struct.pack("B", len(sectypes))
        sendbuff += struct.pack("%sB" % len(sectypes), *sectypes)
        sock.send(sendbuff)

        data = self.get_buffer(30)
        sectype = struct.unpack("B", data)[0]

        if sectype not in sectypes:
            logger.debug("Incompatible security type: %s" % data)
            sock.send(struct.pack("B", 1))
            self.send_message("Incompatible security type")
            sock.close()
            return False

        if sectype == 1:  # No auth
            logger.debug("Client selected no authentication (None)")
            sock.send(struct.pack("!I", 0))
        elif sectype == self.RFB_SECTYPES.vncauth:
            auth = VNCAuth()
            auth.getbuff = self.get_buffer
            if not auth.auth(sock, self.password):
                msg = "Auth failed."
                sendbuff = struct.pack("I", len(msg))
                sendbuff += msg.encode()
                sock.send(sendbuff)
                sock.close()
                return False
        else:
            logger.debug("Unsupported auth type")
            sock.close()
            return False

        data = self.get_buffer(30)
        logger.debug("Clientinit (shared flag)", repr(data))
        self.server_init()
        return True

    def server_init(self):
        sock = self.socket
        screen = self.image_source()
        size = screen.size
        del screen

        width = size[0]
        self.width = width
        height = size[1]
        self.height = height
        bpp = 32
        depth = 24
        self.depth = depth
        self.bpp = bpp
        bigendian = 0
        self.truecolor = 1
        red_maximum = 255
        green_maximum = 255
        blue_maximum = 255
        red_shift = 16
        green_shift = 8
        blue_shift = 0
        self.rfb_bitmap = RfbBitmap()

        sendbuff = struct.pack("!HH", width, height)
        sendbuff += struct.pack("!BBBB", bpp, depth, bigendian, self.truecolor)
        sendbuff += struct.pack(
            "!HHHBBB",
            red_maximum,
            green_maximum,
            blue_maximum,
            red_shift,
            green_shift,
            blue_shift,
        )
        sendbuff += struct.pack("!xxx")

        desktop_name = self.vnc_config.win_title
        desktop_name_len = len(desktop_name)
        sendbuff += struct.pack("!I", desktop_name_len)
        sendbuff += desktop_name.encode()

        sock.send(sendbuff)

    def handle_client(self):
        self.socket.settimeout(None)
        last_rfbu = time.time()
        self.primaryOrder = "bgr"
        self.encoding = ENCODINGS.raw
        self.encoding_object = encs.common.encodings[self.encoding]()
        sock = self.socket

        while True:
            try:
                data = sock.recv(1)
            except (socket.timeout, socket.error):
                continue
            except Exception as e:
                logger.debug("exception '%s'" % e)
                sock.close()
                break

            if not data:
                sock.close()
                break

            if data[0] == 0:  # SetPixelFormat
                data2 = sock.recv(19, socket.MSG_WAITALL)
                logger.debug("Client Message Type: Set Pixel Format (0)")
                (
                    self.bpp,
                    self.depth,
                    self.bigendian,
                    self.truecolor,
                    self.red_maximum,
                    self.green_maximum,
                    self.blue_maximum,
                    self.red_shift,
                    self.green_shift,
                    self.blue_shift,
                ) = struct.unpack("!xxxBBBBHHHBBBxxx", data2)

                self.primaryOrder = "rgb" if self.red_shift > self.blue_shift else "bgr"

                self.rfb_bitmap.bpp = self.bpp
                self.rfb_bitmap.depth = self.depth
                self.rfb_bitmap.dither = False
                self.rfb_bitmap.primaryOrder = self.primaryOrder
                self.rfb_bitmap.truecolor = self.truecolor
                self.rfb_bitmap.red_shift = self.red_shift
                self.rfb_bitmap.green_shift = self.green_shift
                self.rfb_bitmap.blue_shift = self.blue_shift
                self.rfb_bitmap.red_maximum = self.red_maximum
                self.rfb_bitmap.green_maximum = self.green_maximum
                self.rfb_bitmap.blue_maximum = self.blue_maximum
                self.rfb_bitmap.bigendian = self.bigendian

                if self.bpp == 8:
                    self.primaryOrder = "bgr"

                logger.debug("Using order:", self.primaryOrder)

                # FIX: Clear framebuffer cache and force full update
                self.framebuffer = None
                self.send_rectangles(sock, 0, 0, self.width, self.height, incremental=0)

                continue
            elif data[0] == 2:  # SetEncoding
                data2 = sock.recv(3)
                logger.debug("Client Message Type: SetEncoding (2)")
                (nencodings,) = struct.unpack("!xH", data2)
                data2 = sock.recv(4 * nencodings, socket.MSG_WAITALL)
                self.client_encodings = struct.unpack("!%si" % nencodings, data2)
                for e in encs.common.encodings_priority:
                    if e in self.client_encodings:
                        if self.encoding != e:
                            self.encoding = e
                            self.encoding_object = encs.common.encodings[
                                self.encoding
                            ]()
                        break
                continue
            elif data[0] == 3:  # FBUpdateRequest
                data2 = sock.recv(9, socket.MSG_WAITALL)
                if not data2:
                    logger.debug("connection closed?")
                    break
                if time.time() - last_rfbu < 0.05:
                    try:
                        sock.sendall(struct.pack("!BxH", 0, 0))
                    except (ConnectionResetError, BrokenPipeError):
                        break
                    continue
                last_rfbu = time.time()
                (incremental, x, y, w, h) = struct.unpack("!BHHHH", data2)
                self.send_rectangles(sock, x, y, w, h, incremental)
                continue
            elif data[0] == 4:  # keyboard event
                sock.recv(7, socket.MSG_WAITALL)
                continue
            elif data[0] == 5:  # PointerEvent
                sock.recv(5, socket.MSG_WAITALL)
                continue
            elif data[0] == 6:  # ClientCutText
                sock.recv(5, socket.MSG_WAITALL)
            else:
                data2 = sock.recv(4096)
                logger.debug(f"Server received data: {repr(data[0]), data + data2}")
                sock.recv(5, socket.MSG_WAITALL)

    def get_rectangle(self, x, y, w, h):
        scr = self.image_source()
        if scr.mode != "RGB":
            img = scr.convert("RGB")
        else:
            img = scr
        crop = img.crop((x, y, x + w, y + h))
        return crop

    def send_rectangles(self, sock, x, y, w, h, incremental=0):
        rectangle = self.get_rectangle(x, y, w, h)
        if not rectangle:
            rectangle = Image.new("RGB", [w, h], (0, 0, 0))

        lastshot = rectangle
        sendbuff = bytearray()
        self.encoding_object.firstUpdateSent = False

        if self.framebuffer is not None and incremental == 1:
            diff = ImageChops.difference(rectangle, self.framebuffer)
            if diff.getbbox() is None:
                rectangles = 0
                sendbuff.extend(struct.pack("!BxH", 0, rectangles))
                sleep(0.05)
                try:
                    sock.sendall(sendbuff)
                except (ConnectionResetError, BrokenPipeError):
                    return False
                return
            else:
                if hasattr(diff, "getbbox"):
                    rectangle = rectangle.crop(diff.getbbox())
                    (x, y, _, _) = diff.getbbox()
                    w = rectangle.width
                    h = rectangle.height

        bitmap = self.rfb_bitmap
        bitmap.bpp = self.bpp
        bitmap.depth = self.depth
        bitmap.dither = False
        bitmap.primaryOrder = self.primaryOrder
        bitmap.truecolor = self.truecolor
        bitmap.red_shift = self.red_shift
        bitmap.green_shift = self.green_shift
        bitmap.blue_shift = self.blue_shift

        image = bitmap.get_bitmap(rectangle)
        sendbuff.extend(self.encoding_object.send_image(x, y, w, h, image))
        self.framebuffer = lastshot
        try:
            sock.sendall(sendbuff)
        except (ConnectionResetError, BrokenPipeError):
            return False
