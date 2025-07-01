#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from dataclasses import dataclass
from PIL import Image
from typing import Callable
from vnc import pyvncs
from threading import Thread
import socket

from loguru import logger


@dataclass
class VNCConfig:
    vnc_password: str
    win_title: str


class VNCClientThread(Thread):
    def __init__(
        self,
        sock: socket.socket,
        image_source: Callable[[], Image],
        ip: str,
        port: int,
        vnc_config: VNCConfig,
    ):
        Thread.__init__(self)
        self.ip = ip
        self.port = port
        self.sock = sock
        self.daemon = True
        self.image_source = image_source
        self.vnc_config = vnc_config

    def __del__(self):
        logger.debug("ClientThread died")

    def run(self):
        logger.debug(f"[+] New server socket thread started for {self.ip}:{self.port}")
        server = pyvncs.server.VNCServer(
            self.sock,
            self.image_source,
            password=self.vnc_config.vnc_password,
            vnc_config=self.vnc_config,
        )
        status = server.init()

        if not status:
            logger.error("Error negotiating client init")
            return False

        server.handle_client()


# def main(argv):
#     vnc_config = VNCConfig(
#         vnc_password=args.vnc_password,
#         win_title=args.win_title,
#     )

#     sockServer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#     sockServer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
#     sockServer.bind((args.listen_addr, args.listen_port))

#     logger.debug(
#         "Multithreaded Python server : Waiting for connections from TCP clients..."
#     )
#     while True:
#         sockServer.listen(4)
#         (conn, (ip, port)) = sockServer.accept()
#         newthread = VNCClientThread(sock=conn, ip=ip, port=port, vnc_config=vnc_config)
#         newthread.daemon = True
#         newthread.start()
