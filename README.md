# Raspberry Pi Zero Smart Barcode Scanner

## Requirements

* Raspberry Pi Zero 2W
* SD Card with Raspberry Pi OS Bookworm (with Legacy Camera System Disabled)
* Pi Camera v1/v2 and Cable
* Rotary Encoder with Button
* Push button for scan trigger
* ST7789 TFT LCD (Other SPI LCDs may work, but haven't been tested)
* (Optional) 30mm Fan - only required for overclocking
* USB Cable (Micro-B to USB-A or directly solder USB-A cable to pads)

## 3D Printed handheld Scanner

The 3D Printed Scanner is a modular and compact handheld scanner housing.

### Required Parts

* [Adafruit 1.54in ST7789 Display](https://www.adafruit.com/product/3787)

> [!WARNING]
> The 3D models are made for the pre-July 24, 2019 displays.
> Slight modifications are required for the newer models.

    <img src="https://cdn-shop.adafruit.com/970x728/3787-13.jpg" alt="Product photo" width=120></img>

* [Raspberry Pi Zero 2W](https://www.adafruit.com/product/5291)

    <img src="https://cdn-shop.adafruit.com/970x728/5291-05.jpg" alt="Product photo" width=120></img>

* [16x Neopixel Ring](https://www.adafruit.com/product/1463)

    <img src="https://cdn-shop.adafruit.com/970x728/1463-04.jpg" alt="Product photo" width=120></img>


* [KY-040 Rotary Encoder PCB](https://www.amazon.com/Taiss-KY-040-Encoder-15×16-5-Arduino/dp/B07F26CT6B)
    
    <img src="https://m.media-amazon.com/images/I/61CczUpzXkL._SL1500_.jpg" alt="Product photo" width=120></img>

* 6x6x9 Tactile Push Button
* 5mm LED (Optional for Power Indication)
* PS1240P02BT (or similar) 3V 12.2mmx6.5mm Piezo Buzzer
* 5V 2A Power Supply (hard-wired into scanner, or modify the base for a connector of your choice)
* USB-A Cable with end cut off (for hard-wiring into scanner)

## Hardware Setup

* Follow the Adafruit LCD wiring guide

    The default configuration uses the wiring available [here](https://learn.adafruit.com/adafruit-1-3-and-1-54-240-x-240-wide-angle-tft-lcd-displays/python-wiring-and-setup)

* Encoder Wiring

| GPIO Pin      | Encoder       |
| ------------- | ------------- |
| GPIO17        | Encoder A/CLK |
| GPIO18        | Encoder B/DAT |
| GPIO27        | Encoder Btn   |
| GND           | GND           |
| 3.3V          | +             |

* Trigger Wiring

| GPIO Pin      | Button Pin    |
| ------------- | ------------- |
| GND           | Pin 1         |
| GPIO20        | Pin 2         |

* LED Wiring

| GPIO Pin      | LED Pin       |
| ------------- | ------------- |
| GND           | GND           |
| 5V            | +             |
| GPIO21        | DAT_IN        |

* Buzzer Wiring

| GPIO Pin      | Buzzer Pin    |
| ------------- | ------------- |
| GND           | 1             |
| GPIO19        | 2             |

* Connect the Pi Camera

## Installation

* Install System Packages

    ```console
    sudo apt install python3-picamera2 python3-venv python3-pip libzbar-dev fonts-dejavu git supervisor
    ```

* Install USB Gadget Driver

> [!IMPORTANT]
> A reboot is required after this step

    ```console
    git clone https://github.com/thewh1teagle/zero-hid
    cd zero-hid/usb_gadget
    sudo ./installer
    sudo reboot
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

* (Optional) Test Camera

    ```console
    libcamera-hello
    ```

    You should see output containing something like the following

    ```log
    [1:10:17.433392327] [2017]  INFO Camera camera_manager.cpp:326 libcamera v0.5.0+59-d83ff0a4
    [1:10:17.500249834] [2020]  WARN RPiSdn sdn.cpp:40 Using legacy SDN tuning - please consider moving SDN inside rpi.denoise
    [1:10:17.506046852] [2020]  INFO RPI vc4.cpp:447 Registered camera /base/soc/i2c0mux/i2c@1/ov5647@36 to Unicam device /dev/media3 and ISP device /dev/media0
    ```

* Create the configuration

    We will copy the example configuration file to the home directory, where it will be detected by the autostart script.

    ```console
    cp config.yml ~/config.yml
    ```

    Get the UDC device address

    ```console
    ls /sys/class/udc
    ```

    You should get something like the following

    ```
    3f980000.usb
    ```

    Note down this value, and set it in the configuration section, `hid/udc`

> [!NOTE]
> If there are multiple device addresses, you will need to trial-and-error them.
> Try each, and restart the scanner.
> The connection status in the top left should reflect if the USB-HID interface is connected.
>
> You can restart the scanner process with `sudo supervisorctl restart scanner`

* Configure Autostart

    ```console
    sudo systemctl enable supervisor
    sudo systemctl start supervisors
    ```

    Edit the `/etc/supervisor/conf.d/barcode-scanner.conf` file to contain the following:

> [!NOTE]
> Replace /home/scanner with your user path

    ```conf
    [program:scanner]
    user=root
    directory=/home/scanner
    command=/home/scanner/rpi-barcode-scanner/.venv/bin/python /home/scanner/rpi-barcode-scanner/main.py

    autostart=true
    autorestart=true
    stdout_logfile=/var/log/barcode-scanner/stdout.log
    stderr_logfile=/var/log/barcode-scanner/stderr.log
    ```

    Start the scanner

    ```console
    sudo mkdir /var/log/barcode-scanner
    sudo supervisorctl start scanner
    ```

## Configuration

tbd

## FAQ

Q: The scanner application fails on auto-start.

A: 

1) Check log files

    ```console
    cat /var/log/barcode-scanner/stderr.log
    cat /var/log/barcode-scanner/stdout.log
    ```

2) Check service startup

    ```console
    sudo supervisorctl status scanner
    sudo supervisorctl start scanner
    sudo supervisorctl status scanner
    ```

    You should get something like the following:

    ```log
    scanner                          RUNNING   pid 2469, uptime 0:02:43
    ```
