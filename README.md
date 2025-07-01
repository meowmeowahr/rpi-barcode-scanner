# rpi-barcode-scanner

## Installation

* Install System Packages

    ```console
    sudo apt install python3-picamera2 python3-venv python3-pip fonts-dejavu git
    ```

* Clone Repo

    ```console
    git clone https://github.com/meowmeowahr/rpi-barcode-scanner && cd rpi-barcode-scanner
    ```

* Create Environment

    ```console
    python3 -m venv .venv
    source ./.venv/bin/activate
    pip install uv
    uv sync
    ```

* Link pykms and libcamera

    ```console
    ln -s /usr/lib/python3/dist-packages/pykms ./.venv/lib/python3.11/site-packages
    ln -s /usr/lib/python3/dist-packages/libcamera ./.venv/lib/python3.11/site-packages
    ```