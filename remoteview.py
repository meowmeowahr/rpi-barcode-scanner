import socket
from typing import Callable

from loguru import logger
from PIL import Image

from vnc.vncserver import VNCClientThread, VNCConfig


def vnc_server_thread(
    vnc_config: VNCConfig,
    vnc_bind: str,
    vnc_port: int,
    source: Callable[[], Image.Image],
):
    sockServer = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sockServer.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sockServer.bind((vnc_bind, vnc_port))

    logger.debug("VNC server started")
    while True:
        sockServer.listen(4)
        (conn, (ip, port)) = sockServer.accept()

        newthread = VNCClientThread(
            sock=conn,
            image_source=source,
            ip=ip,
            port=port,
            vnc_config=vnc_config,
        )
        newthread.daemon = True
        newthread.start()
