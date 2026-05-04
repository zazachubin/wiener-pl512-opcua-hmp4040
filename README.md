# HMP4040 OPC UA Server — Fake Wiener PL512 Emulator

> **This package implements a fake [Wiener PL512](https://www.wiener-d.com/) LV power supply OPC UA server
> backed by a [Rohde & Schwarz HMP4040](https://www.rohde-schwarz.com/product/hmp4040) LV power supply.**

It presents the standard Wiener PL512 OPC UA address space and datapoint layout to clients, while all actual
hardware communication is performed through the HMP4040 SCPI driver over VISA.  
This allows existing Wiener PL512 OPC UA clients to control the HMP4040 **without any client-side modifications**.

---

## Table of Contents

- [Repository Layout](#repository-layout)
- [Requirements](#requirements)
- [Connecting to the Device](#connecting-to-the-device)
- [OPC UA Address Space](#opc-ua-address-space)
- [HMP4040 Driver API](#hmp4040-driver-api)
  - [Constructor](#constructor)
  - [Voltage](#voltage)
  - [Current](#current)
  - [Output Control](#output-control)
  - [Over-Voltage Protection (OVP)](#over-voltage-protection-ovp)
  - [Fuse](#fuse)
  - [Diagnostics / Misc](#diagnostics--misc)
- [OPCUA\_HMP4040 Server API](#opcua_hmp4040-server-api)
- [Running the Server](#running-the-server)
- [Architecture Notes](#architecture-notes)
- [Security](#security)

---

## Repository Layout

| File | Description |
|------|-------------|
| `HMP4040_driver.py` | Low-level VISA/SCPI driver for the HMP4040 |
| `Wiener_PL512_OPCUA_Server.py` | OPC UA server wrapping the driver |
| `WIENER-PL512_datapoints.xml` | OPC UA address-space definition (node layout) |
| `requirements.txt` | Python dependencies |
| `README.md` | This file |

---

## Requirements

Python **3.8+** is required. Install dependencies with pip:

```bash
pip install -r requirements.txt
```

> `pyvisa-py` is a pure-Python VISA back-end — no NI-VISA installation is needed.  
> If you already have NI-VISA or R&S VISA installed, pyvisa will use it automatically.

---

## Connecting to the Device

The HMP4040 can be reached over **LAN** (TCP socket, port 5025) or **USB** (USBTMC).

| Interface | VISA resource string |
|-----------|----------------------|
| LAN | `TCPIP0::<ip_address>::5025::SOCKET` |
| USB | `USB0::0x0AAD::0x0117::<serial>::INSTR` |

To discover available devices at startup, pass the optional flags to the constructor
(see [Constructor](#constructor) below).

---

## OPC UA Address Space

The node hierarchy mirrors the Wiener PL512 layout so that existing PL512 OPC UA clients
work without modification. The full node definition is loaded from `WIENER-PL512_datapoints.xml`
at server startup.

```
<ServerRoot>
└── <ps_name>                     (e.g. "PS1")
    ├── FanTray
    │   └── FanSpeed              float  read-write
    ├── Output
    │   └── Channel
    │       └── 0 … 3             (HMP4040 channels 1–4, zero-indexed)
    │           ├── Voltage                       float  read-write
    │           ├── Current                       float  read-write
    │           ├── OnOff                         bool   read-write
    │           ├── VoltageRiseRate               float  read-write  (V/s)
    │           ├── VoltageFallRate               float  read-write  (V/s)
    │           ├── CurrentRiseRate               float  read-write  (A/s)
    │           ├── CurrentFallRate               float  read-write  (A/s)
    │           ├── MeasurementCurrent            float  read-only
    │           ├── MeasurementSenseVoltage       float  read-only
    │           ├── MeasurementTerminalVoltage    float  read-only
    │           ├── SupervisionMaxTerminalVoltage float  read-write
    │           ├── SupervisionMaxSenseVoltage    float  read-write
    │           ├── SupervisionMinSenseVoltage    float  read-write
    │           ├── SupervisionMaxCurrent         float  read-write
    │           └── Status
    │               ├── On        bool  read-only
    │               ├── RampUp    bool  read-only
    │               └── RampDown  bool  read-only
    ├── System
    │   └── Status
    │       └── On                bool  mirrors global output enable
    └── PowerSupply
        └── OperatingTime         float  seconds since server start
```

---

## HMP4040 Driver API

### Constructor

```python
HMP4040Driver(ps_name, deviceAddress,
              find_lan_devices_flag=False,
              find_usb_devices_flag=False,
              reset_device_flag=False)
```

| Parameter | Description |
|-----------|-------------|
| `ps_name` | Logical name used in log messages |
| `deviceAddress` | VISA resource string |
| `find_lan_devices_flag` | Scan `192.168.2.0/24` and print found devices |
| `find_usb_devices_flag` | Print all USB VISA resources |
| `reset_device_flag` | Send `*RST` before use |

---

### Voltage

| Method | Description |
|--------|-------------|
| `setVoltage(ch, V)` | Set target voltage |
| `getVoltage(ch)` | Read set voltage |
| `measVoltage(ch)` | Measure actual output voltage |
| `getVoltageMax(ch)` / `getVoltageMin(ch)` | Hardware voltage limits |
| `stepVoltage(ch, step_v)` | Configure voltage step size |
| `step_up_voltage(ch)` / `step_down_voltage(ch)` | Single voltage step |
| `ramp_up_voltage(ch, start, stop, rate_V_s)` | Controlled voltage ramp-up |
| `ramp_down_voltage(ch, start, stop, rate_V_s)` | Controlled voltage ramp-down |

---

### Current

| Method | Description |
|--------|-------------|
| `setCurrent(ch, A)` | Set current limit |
| `getCurrent(ch)` | Read set current |
| `measCurrent(ch)` | Measure actual output current |
| `getCurrentMax(ch)` / `getCurrentMin(ch)` | Hardware current limits |
| `stepCurrent(ch, step_i)` | Configure current step size |
| `step_up_current(ch)` / `step_down_current(ch)` | Single current step |
| `ramp_up_current(ch, start, stop, rate_A_s)` | Controlled current ramp-up |
| `ramp_down_current(ch, start, stop, rate_A_s)` | Controlled current ramp-down |

---

### Output Control

| Method | Description |
|--------|-------------|
| `setOutput(ch, bool)` | Enable/disable individual channel output |
| `getOutput(ch)` | Read channel output state |
| `enablePower(bool)` | Toggle global power output (master switch) |
| `getEnablePower()` | Read global power output state |

---

### Over-Voltage Protection (OVP)

| Method | Description |
|--------|-------------|
| `setOVPLimit(ch, V)` | Set OVP trip voltage |
| `getOVPLimit(ch)` | Read OVP trip voltage |
| `getOVP_MAX_Limit(ch)` | Maximum configurable OVP limit |
| `getOVP_MIN_Limit(ch)` | Minimum configurable OVP limit |
| `setOVP_Clear(ch)` | Clear a tripped OVP condition |
| `getOVP_Trip(ch)` | Check whether OVP has tripped |
| `setOVP_Mode(ch, mode)` | Set OVP mode (`'MEAS'` or `'PROT'`) |
| `resetOVPstate(ch)` | Alias for `setOVP_Clear` |

---

### Fuse

| Method | Description |
|--------|-------------|
| `setFuseState(ch, bool)` | Enable/disable electronic fuse |
| `getFuseState(ch)` | Read fuse enable state |
| `getFuseTripped(ch)` | Check whether fuse has tripped |
| `setFuseDelay(ch, ms)` | Set fuse delay in milliseconds |
| `getFuseDelay(ch)` | Read fuse delay |
| `getFuseDelayMax(ch)` | Maximum configurable fuse delay |
| `getFuseDelayMin(ch)` | Minimum configurable fuse delay |
| `setFuseLink(ch, linkCh)` | Link fuse of `ch` to `linkCh` |
| `getFuseLink(ch, linkCh)` | Read fuse link state |
| `setFuseUnLink(ch, linkCh)` | Remove fuse link |

---

### Diagnostics / Misc

| Method | Description |
|--------|-------------|
| `get_all_channels_params()` | Refresh internal state dict from all 4 channels |
| `get_error()` | Read the SCPI error queue |
| `portTermination()` | Safe shutdown: disable outputs, return to local |

---

## OPCUA_HMP4040 Server API

```python
OPCUA_HMP4040(ipAddress, serverName, ps_configs, datapoint_config=None)
```

| Parameter | Description |
|-----------|-------------|
| `ipAddress` | OPC UA endpoint, e.g. `"opc.tcp://0.0.0.0:4840/"` |
| `serverName` | Human-readable server name |
| `ps_configs` | List of `(ps_name, device_address)` tuples |
| `datapoint_config` | Path to `WIENER-PL512_datapoints.xml` (defaults to the bundled file) |

```python
server.serverTermination()   # Graceful shutdown
```

---

## Running the Server

1. **Edit the device map** at the bottom of `Wiener_PL512_OPCUA_Server.py` to match your setup:

```python
ps_devices = {
    "LV_PSU2": "TCPIP::192.168.2.60::5025::SOCKET",   # LAN
    # "LV_PSU1": "ASRL/dev/ttyACM0::INSTR",            # USB
}
```

2. **Start the server:**

```bash
python Wiener_PL512_OPCUA_Server.py
```

The server will:
- Connect to each power supply in a dedicated subprocess
- Read back real device values to initialise all OPC UA nodes
- Begin serving at `opc.tcp://localhost:4848/WIENER-PL512/server/`

3. **Stop the server** with `Ctrl+C` or `SIGTERM`. All outputs are left in their current state; the device returns to local control.

---

## Architecture Notes

- **One OS process per power supply** (via `multiprocessing`). VISA calls are strictly
  isolated to the worker process that owns the device.
- The **main (OPC UA) process never calls VISA directly**; it communicates with workers
  via `multiprocessing.Queue`.
- Worker processes **ignore SIGINT** so that Ctrl+C in the terminal reaches only the main
  process, which then coordinates a clean shutdown.
- **Internal OPC UA writes** (from device readbacks) are suppressed in the subscription
  handler to avoid feedback loops.
- The server **initializes all OPC UA nodes from a real device readback** before accepting
  client write subscriptions.

---

## Security

> **Note:** The OPC UA server runs with `NoSecurity` policy and Anonymous authentication.  
> Restrict network access (firewall, VLAN) so that only trusted clients can reach the endpoint.
