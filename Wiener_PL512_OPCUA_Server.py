from opcua import Server
from opcua import ua
import os
import logging
import threading
import xml.etree.ElementTree as ET
from HMP4040_driver import HMP4040Driver
import time
import random
import signal
import copy
import multiprocessing

logging.getLogger('opcua.server.address_space').setLevel(logging.ERROR)
logging.getLogger('opcua.common.xmlimporter').setLevel(logging.ERROR)
logging.getLogger('opcua.server.uaprocessor').setLevel(logging.ERROR)


class DatapointValue:
    def __init__(self, value, write_only=False, read_only=False):
        self.value = value
        self.write_only = write_only
        self.read_only = read_only

# ──────────────────────────────────────────────────────────────────────────────
#  Worker process – owns a single HMP4040 device
# ──────────────────────────────────────────────────────────────────────────────

def ps_worker_process(ps_name, device_address, command_queue, status_queue, stop_flag):
    """
    Runs in its own OS process.
    - Creates the HMP4040 driver.
    - Polls the device every POLL_INTERVAL seconds and puts a status dict on
      status_queue so the main process can update OPC UA nodes.
    - Executes commands received from the main process via command_queue.
    """
    # Ignore SIGINT in worker processes – shutdown is coordinated by the main
    # process via stop_flag.  Without this, Ctrl+C broadcasts SIGINT to every
    # process in the group and the VISA select() call raises KeyboardInterrupt
    # inside the worker before portTermination() can run.
    signal.signal(signal.SIGINT, signal.SIG_IGN)

    try:
        ps = HMP4040Driver(ps_name, device_address)
    except Exception as e:
        print(f"[{ps_name}] Failed to connect to device: {e}")
        return

    POLL_INTERVAL = 0.5  # seconds

    def dispatch(cmd):
        t   = cmd.get("type")
        ch  = cmd.get("channel")
        val = cmd.get("value")
        changed = False
        try:
            if t == "setOutput":
                ps.setOutput(ch, bool(val))
                changed = True
            elif t == "enablePower":
                ps.enablePower(bool(val))
                if not bool(val):
                    for c in ps.CHANNELS:
                        ps.setOutput(c, False)
                changed = True
            elif t == "setVoltage":
                ps.setVoltage(ch, float(val))
                changed = True
            elif t == "setCurrent":
                ps.setCurrent(ch, float(val))
                changed = True
            elif t == "setOVPLimit":
                ps.setOVPLimit(ch, float(val))
                changed = True
            elif t == "setOVP_Clear":
                ps.setOVP_Clear(ch)
                changed = True
            elif t == "setVoltageRiseRate":
                ps.get_params[f"Ch{ch}"]["ramp_up_rate_V_S"] = float(val)
                changed = True
            elif t == "setVoltageFallRate":
                ps.get_params[f"Ch{ch}"]["ramp_down_rate_V_S"] = float(val)
                changed = True
            elif t == "setCurrentRiseRate":
                ps.get_params[f"Ch{ch}"]["ramp_up_rate_A_S"] = float(val)
                changed = True
            elif t == "setCurrentFallRate":
                ps.get_params[f"Ch{ch}"]["ramp_down_rate_A_S"] = float(val)
                changed = True
        except Exception as e:
            print(f"[{ps_name}] Command error ({t}): {e}")
            return

        # For every writable datapoint command: set first, then read back status.
        if changed:
            try:
                ps.get_all_channels_params()
                status_queue.put({
                    "ps_name": ps_name,
                    "params": copy.deepcopy(ps.get_params),
                })
            except Exception as e:
                print(f"[{ps_name}] Post-command readback error ({t}): {e}")

    # First readback right after connection so OPC UA datapoints are initialized
    # with real values from the power supply.
    try:
        ps.get_all_channels_params()
        status_queue.put({
            "ps_name": ps_name,
            "params": copy.deepcopy(ps.get_params),
        })
    except Exception as e:
        print(f"[{ps_name}] Initial readback error: {e}")

    next_poll = time.monotonic() + POLL_INTERVAL

    while not stop_flag.is_set():
        # Drain command queue before each poll
        while True:
            try:
                cmd = command_queue.get_nowait()
                dispatch(cmd)
            except Exception:
                break  # queue empty

        now = time.monotonic()
        if now >= next_poll:
            try:
                ps.get_all_channels_params()
                status_queue.put({
                    "ps_name": ps_name,
                    "params":  copy.deepcopy(ps.get_params),
                })
            except Exception as e:
                print(f"[{ps_name}] Poll error: {e}")
            next_poll = time.monotonic() + POLL_INTERVAL

        time.sleep(0.01)  # yield CPU between drain/poll cycles

    # Close VISA connection without touching channel outputs
    try:
        ps._HMP4040Driver__remoteMode(False)  # return to local control
    except Exception:
        pass
    try:
        ps.hmp4040.close()
    except Exception as e:
        print(f"[{ps_name}] Termination error: {e}")
    print(f"[{ps_name}] Worker stopped.")


# ──────────────────────────────────────────────────────────────────────────────
#  OPC UA server – runs in the main process
# ──────────────────────────────────────────────────────────────────────────────

class OPCUA_HMP4040():
    def __init__(self, ipAddress, serverName, ps_configs, datapoint_config=None):
        """
        ps_configs : list of (ps_name, device_address) tuples.
                     HMP4040 objects are created inside worker processes.
        """
        self.ps_configs = ps_configs          # [(name, address), ...]
        self.CHANNELS = range(1, 5)
        self.datapoint_config = datapoint_config or os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            'datapoints.xml'
        )

        self.start_time = time.time()

        # Graceful shutdown state
        self.stop_event = threading.Event()
        self._shutdown_lock = threading.Lock()
        self._shutdown_complete = False
        self._internal_writes = {}
        self._internal_writes_lock = threading.Lock()

        # ── IPC structures (created before the server so SubHandler can reference them) ──
        # command_queues: ps_name → Queue  (main → worker, one per PS)
        # status_queue:   single Queue     (all workers → main)
        self.command_queues = {
            ps_name: multiprocessing.Queue()
            for ps_name, _ in ps_configs
        }
        self.status_queue  = multiprocessing.Queue()
        self._stop_flags   = {
            ps_name: multiprocessing.Event()
            for ps_name, _ in ps_configs
        }
        self._workers = []   # list of (ps_name, Process)

        # ── OPC UA server setup ────────────────────────────────────────────────
        self.server = Server()
        self.server.set_endpoint(ipAddress)
        self.server.set_server_name(serverName)
        self.server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        self.server.set_security_IDs(["Anonymous"])

        uri = "http://" + ipAddress.split('//')[1]
        self.idx = self.server.register_namespace(uri)

        self.__createDPs_structure()

        print("Starting OPC UA Server...")
        self.server.start()

        # Container for subscriptions so they are not garbage-collected
        self._subscriptions = []
        self._subscription_handlers = []

        # Start per-PS worker processes and initialize OPC UA nodes from the
        # first device readback before enabling writable subscriptions.
        self.__start_workers()
        self.__initialize_from_workers(timeout=20)

        # Subscribe to writable datapoints only after initialization to avoid
        # startup writes from XML default values.
        self.__createDPs_EventList()

        # Thread: reads status messages from workers → updates OPC UA nodes
        self._status_thread = threading.Thread(
            target=self.__status_updater,
            name="status-updater",
            daemon=True,
        )
        self._status_thread.start()

    # ── XML datapoint helpers ──────────────────────────────────────────────────

    def __determine_access_flags(self, element_tag):
        if element_tag == 'WriteOnlyProperty':
            return True, False
        elif element_tag == 'ReadOnlyProperty':
            return False, True
        return False, False

    def __parse_variable_value(self, element, write_only, read_only):
        value_type = element.attrib.get('type', 'string').strip().lower()
        raw_value = element.attrib.get('value', '')

        if value_type == 'bool':
            parsed_value = raw_value.strip().lower() in ('1', 'true', 'yes', 'on')
        elif value_type == 'int':
            parsed_value = int(raw_value)
        elif value_type == 'float':
            parsed_value = float(raw_value)
        else:
            parsed_value = raw_value

        return DatapointValue(parsed_value, write_only, read_only)

    def __parse_datapoint_elements(self, parent_element):
        data = {}

        for element in parent_element:
            name = element.attrib.get('name')
            if not name:
                raise ValueError('Each datapoint element must define a name attribute')

            if element.tag == 'object':
                # Recursively parse nested objects
                data[name] = self.__parse_datapoint_elements(element)
            elif element.tag in ('WriteOnlyProperty', 'ReadOnlyProperty', 'ReadWriteProperty'):
                # Parse property element
                write_only, read_only = self.__determine_access_flags(element.tag)
                data[name] = self.__parse_variable_value(element, write_only, read_only)
            else:
                raise ValueError("Unsupported datapoint tag '{}'".format(element.tag))

        return data

    def __load_datapoint_structure(self):
        tree = ET.parse(self.datapoint_config)
        root = tree.getroot()

        if root.tag != 'datapoints':
            raise ValueError("Root XML tag must be 'datapoints'")

        return self.__parse_datapoint_elements(root)

    def __apply_access_properties(self, node, write_only=False, read_only=False):
        if write_only and read_only:
            raise ValueError('Node cannot be configured as both write-only and read-only')

        if read_only:
            access_mask = ua.AccessLevel.CurrentRead
        elif write_only:
            access_mask = ua.AccessLevel.CurrentWrite
        else:
            access_mask = ua.AccessLevel.CurrentRead | ua.AccessLevel.CurrentWrite

        access_mask_value = int(access_mask)

        node.set_attribute(
            ua.AttributeIds.AccessLevel,
            ua.DataValue(ua.Variant(access_mask_value, ua.VariantType.Byte))
        )
        node.set_attribute(
            ua.AttributeIds.UserAccessLevel,
            ua.DataValue(ua.Variant(access_mask_value, ua.VariantType.Byte))
        )

        if not read_only:
            node.set_writable()

    def __get_operating_time(self):
        """Calculate operating time as long integer in day+time form, e.g. 53654010."""
        total_seconds = int(time.time() - self.start_time)
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)
        return int("{}{}{:02d}{:02d}0".format(days, hours, minutes, seconds))

    def __create_opcua_variables(self, parent_node, data_dict, namespace_idx, node_id_prefix):
        for key, value in data_dict.items():
            child_node_id = "{}.{}".format(node_id_prefix, key)
            if isinstance(value, dict):
                new_node = parent_node.add_object(
                    ua.NodeId(child_node_id, namespace_idx, ua.NodeIdType.String),
                    ua.QualifiedName(key, namespace_idx)
                )
                self.__create_opcua_variables(new_node, value, namespace_idx, child_node_id)
            else:
                var_node = parent_node.add_variable(
                    ua.NodeId(child_node_id, namespace_idx, ua.NodeIdType.String),
                    ua.QualifiedName(key, namespace_idx),
                    value.value
                )
                self.__apply_access_properties(
                    var_node,
                    write_only=value.write_only,
                    read_only=value.read_only
                )

    # ── OPC UA node tree construction ──────────────────────────────────────────

    def __createDPs_structure(self):
        data = self.__load_datapoint_structure()
        for ps_name, _ in self.ps_configs:
            root_node_id = ua.NodeId(ps_name, self.idx, ua.NodeIdType.String)
            root_node = self.server.nodes.objects.add_object(
                root_node_id,
                ua.QualifiedName(ps_name, self.idx)
            )
            self.__create_opcua_variables(root_node, data, self.idx, ps_name)

    def __collect_writable_datapoints(self, data_dict, path_prefix=""):
        result = []
        for key, value in data_dict.items():
            current_path = "{}.{}".format(path_prefix, key) if path_prefix else key
            if isinstance(value, dict):
                result.extend(self.__collect_writable_datapoints(value, current_path))
            elif isinstance(value, DatapointValue) and not value.read_only:
                result.append((current_path, value))
        return result

    def __createDPs_EventList(self):
        objects = self.server.get_objects_node()
        data = self.__load_datapoint_structure()
        writable_datapoints = self.__collect_writable_datapoints(data)

        for ps_name, _ in self.ps_configs:
            handler = SubHandler(
                ps_name,
                self.command_queues,
                self._internal_writes,
                self._internal_writes_lock,
            )
            sub = self.server.create_subscription(100, handler)
            self._subscription_handlers.append(handler)
            self._subscriptions.append(sub)

            subscribed_count = 0
            for datapoint_path, _ in writable_datapoints:
                try:
                    path_parts = datapoint_path.split('.')
                    node_path = ["2:{}".format(ps_name)] + ["2:{}".format(p) for p in path_parts]
                    node = objects.get_child(node_path)
                    sub.subscribe_data_change(node)
                    subscribed_count += 1
                except Exception as e:
                    print("Warning: Could not subscribe to {}: {}".format(datapoint_path, e))

            print("DataChange subscriptions registered for '{}': {}/{} datapoints".format(
                ps_name, subscribed_count, len(writable_datapoints)))

    # ── Worker process management ──────────────────────────────────────────────

    def __start_workers(self):
        for ps_name, device_address in self.ps_configs:
            p = multiprocessing.Process(
                target=ps_worker_process,
                args=(
                    ps_name,
                    device_address,
                    self.command_queues[ps_name],
                    self.status_queue,
                    self._stop_flags[ps_name],
                ),
                name="worker-{}".format(ps_name),
                daemon=True,
            )
            p.start()
            self._workers.append((ps_name, p))
            print("Started worker process for '{}' (pid={})".format(ps_name, p.pid))

    def __values_equal(self, expected, actual):
        if isinstance(expected, float) and isinstance(actual, float):
            return abs(expected - actual) < 1e-6
        return expected == actual

    def __set_node_value(self, node, value):
        node_id = node.nodeid.to_string()
        with self._internal_writes_lock:
            self._internal_writes[node_id] = (value, time.monotonic())
        node.set_value(value)

    def __apply_status_to_nodes(self, objects, ps_name, params):
        for ch in self.CHANNELS:
            p = params.get("Ch{}".format(ch), {})
            ch_idx = ch - 1
            base = ["2:{}".format(ps_name), "2:Output", "2:Channel", "2:{}".format(ch_idx)]

            def ch_node(*path_parts):
                return objects.get_child(base + ["2:{}".format(x) for x in path_parts])

            self.__set_node_value(ch_node("Voltage"), p.get("get_voltage", 0.0))
            self.__set_node_value(ch_node("Current"), p.get("get_current", 0.0))
            self.__set_node_value(ch_node("OnOff"), p.get("get_output_status", False))
            self.__set_node_value(ch_node("VoltageRiseRate"), p.get("ramp_up_rate_V_S", 10.0))
            self.__set_node_value(ch_node("VoltageFallRate"), p.get("ramp_down_rate_V_S", 10.0))
            self.__set_node_value(ch_node("CurrentRiseRate"), p.get("ramp_up_rate_A_S", 10.0))
            self.__set_node_value(ch_node("CurrentFallRate"), p.get("ramp_down_rate_A_S", 10.0))
            ch_node("Name").set_value("U{}".format(ch_idx))
            ch_node("MeasurementCurrent").set_value(p.get("mess_current", 0.0))
            ch_node("MeasurementSenseVoltage").set_value(p.get("mess_voltage", 0.0))
            ch_node("MeasurementTerminalVoltage").set_value(p.get("mess_voltage", 0.0))
            ch_node("MeasurementTemperature").set_value(random.uniform(20, 25))
            ch_node("Status", "RampUp").set_value(p.get("voltage_ramping_up", False))
            ch_node("Status", "RampDown").set_value(p.get("voltage_ramping_down", False))
            self.__set_node_value(ch_node("Status", "On"), p.get("get_output_status", False))
            ch_node("Status", "CurrentIncreasing").set_value(p.get("current_ramping_up", False))
            ch_node("Status", "CurrentDecreasing").set_value(p.get("current_ramping_down", False))
            self.__set_node_value(ch_node("SupervisionMaxTerminalVoltage"), p.get("get_max_voltage", 0.0))
            self.__set_node_value(ch_node("SupervisionMaxSenseVoltage"), p.get("get_max_voltage", 0.0))
            self.__set_node_value(ch_node("SupervisionMinSenseVoltage"), p.get("get_min_voltage", 0.0))
            self.__set_node_value(ch_node("SupervisionMaxCurrent"), p.get("get_max_current", 0.0))
            ch_node("SupervisionBehavior", "Inhibit").set_value(p.get("get_voltage_prot_trip", False))

        global_on = params.get("Ch1", {}).get("get_global_output_status", False)
        objects.get_child(["2:{}".format(ps_name), "2:System", "2:Status", "2:On"]).set_value(global_on)

        operating_time = self.__get_operating_time()
        objects.get_child(["2:{}".format(ps_name), "2:PowerSupply", "2:OperatingTime"]).set_value(operating_time)
        objects.get_child(["2:{}".format(ps_name), "2:System", "2:UpTime"]).set_value(operating_time)
        #objects.get_child(["2:{}".format(ps_name), "2:FanTray", "2:FanSpeed"]).set_value(random.uniform(3000, 3200))

    def __initialize_from_workers(self, timeout=20):
        objects = self.server.get_objects_node()
        pending = {ps_name for ps_name, _ in self.ps_configs}
        deadline = time.monotonic() + timeout

        while pending and time.monotonic() < deadline:
            try:
                msg = self.status_queue.get(timeout=0.5)
            except Exception:
                continue

            ps_name = msg.get("ps_name")
            params = msg.get("params")
            if ps_name not in pending or params is None:
                continue

            try:
                self.__apply_status_to_nodes(objects, ps_name, params)
                pending.remove(ps_name)
                print("Initialized '{}' from first PSU readback".format(ps_name))
            except Exception as e:
                print("Initialization error for '{}': {}".format(ps_name, e))

        if pending:
            print("Warning: timed out waiting initial readback for {}".format(list(pending)))

    # ── Status updater thread (main process) ──────────────────────────────────

    def __status_updater(self):
        """
        Reads status dicts published by worker processes and updates OPC UA nodes.
        Runs as a daemon thread in the main process.
        """
        objects = self.server.get_objects_node()

        while not self.stop_event.is_set():
            try:
                msg = self.status_queue.get(timeout=0.2)
            except Exception:
                continue

            ps_name = msg["ps_name"]
            params  = msg["params"]

            try:
                self.__apply_status_to_nodes(objects, ps_name, params)

            except Exception as e:
                print("[status_updater] Error updating OPC UA for '{}': {}".format(ps_name, e))

    # ── Shutdown ───────────────────────────────────────────────────────────────

    def serverTermination(self):
        with self._shutdown_lock:
            if self._shutdown_complete:
                return
            self._shutdown_complete = True

        self.stop_event.set()

        # Signal all worker processes to stop
        for ps_name, flag in self._stop_flags.items():
            flag.set()

        # Wait for worker processes to exit cleanly
        for ps_name, p in self._workers:
            p.join(timeout=5)
            if p.is_alive():
                print("[{}] Worker did not stop in time, terminating.".format(ps_name))
                p.terminate()
        self._workers.clear()

        for sub in self._subscriptions:
            try:
                sub.delete()
            except Exception:
                pass
        self._subscriptions.clear()
        self._subscription_handlers.clear()

        try:
            self.server.stop()
        except Exception as e:
            print("Error stopping OPC UA Server: {}".format(e))


# ──────────────────────────────────────────────────────────────────────────────
#  OPC UA subscription handler (main process)
#  Receives data-change events and forwards them as commands to worker processes
#  via command_queues.  No direct device access happens here.
# ──────────────────────────────────────────────────────────────────────────────

class SubHandler:
    def __init__(self, ps_name, command_queues, internal_writes, internal_writes_lock):
        self.ps_name = ps_name
        self.command_queues = command_queues
        self.internal_writes = internal_writes
        self.internal_writes_lock = internal_writes_lock
        self._primed_nodes = set()

    def _send(self, cmd):
        q = self.command_queues.get(self.ps_name)
        if q is not None:
            q.put(cmd)

    def get_full_node_path(self, node):
        path = []
        while node:
            path.insert(0, node.get_browse_name().Name)
            try:
                node = node.get_parent()
            except Exception:
                break
        return "/".join(path)

    def _is_internal_update(self, node, val):
        node_id = node.nodeid.to_string()
        now = time.monotonic()
        with self.internal_writes_lock:
            entry = self.internal_writes.get(node_id)
            if entry is None:
                return False

            expected, ts = entry
            if now - ts > 2.0:
                self.internal_writes.pop(node_id, None)
                return False

            if isinstance(expected, float) and isinstance(val, float):
                equal = abs(expected - val) < 1e-6
            else:
                equal = expected == val

            if equal:
                self.internal_writes.pop(node_id, None)
                return True

            return False

    def datachange_notification(self, node, val, data):
        try:
            node_id = node.nodeid.to_string()
            if node_id not in self._primed_nodes:
                # First notification after subscribe is treated as initial state,
                # not as a client command.
                self._primed_nodes.add(node_id)
                return

            if self._is_internal_update(node, val):
                return

            full_path = self.get_full_node_path(node)
            print("DataChange: " + full_path)
            parts = full_path.split('/')

            if len(parts) >= 2 and parts[-2] == 'System' and parts[-1] == 'OnOff':
                # Reflect into OPC UA Status/On immediately (local, no device call)
                try:
                    node.get_parent().get_child(["2:Status", "2:On"]).set_value(bool(val))
                except Exception:
                    pass
                self._send({"type": "enablePower", "value": bool(val)})
                return

            if len(parts) >= 4 and parts[-4:] == ['Output', 'Group', 'ALL', 'OnOff']:
                self._send({"type": "enablePower", "value": bool(val)})
                return

            if 'Channel' not in parts:
                return

            channel_idx = parts.index('Channel') + 1
            if channel_idx >= len(parts):
                return

            try:
                selected_channel = int(parts[channel_idx]) + 1
            except ValueError:
                return

            selected_path = parts[channel_idx + 1:]
            if not selected_path:
                return

            try:
                if selected_path == ["OnOff"]:
                    self._send({"type": "setOutput", "channel": selected_channel, "value": bool(val)})

                elif selected_path == ["Voltage"]:
                    self._send({"type": "setVoltage", "channel": selected_channel, "value": float(val)})

                elif selected_path == ["Current"]:
                    self._send({"type": "setCurrent", "channel": selected_channel, "value": float(val)})

                elif selected_path == ["VoltageRiseRate"]:
                    self._send({"type": "setVoltageRiseRate", "channel": selected_channel, "value": float(val)})

                elif selected_path == ["VoltageFallRate"]:
                    self._send({"type": "setVoltageFallRate", "channel": selected_channel, "value": float(val)})

                elif selected_path == ["CurrentRiseRate"]:
                    self._send({"type": "setCurrentRiseRate", "channel": selected_channel, "value": float(val)})

                elif selected_path == ["CurrentFallRate"]:
                    self._send({"type": "setCurrentFallRate", "channel": selected_channel, "value": float(val)})

                elif selected_path in (["SupervisionMaxTerminalVoltage"],
                                       ["SupervisionMaxSenseVoltage"],
                                       ["SupervisionBehavior", "MaxSenseVoltage"],
                                       ["SupervisionBehavior", "MaxTerminalVoltage"]):
                    self._send({"type": "setOVPLimit", "channel": selected_channel, "value": float(val)})

                elif selected_path in (["SupervisionMaxCurrent"],
                                       ["SupervisionBehavior", "MaxCurrent"]):
                    self._send({"type": "setCurrent", "channel": selected_channel, "value": float(val)})

                elif selected_path == ["ClearEvents"]:
                    if bool(val):
                        self._send({"type": "setOVP_Clear", "channel": selected_channel})

            except Exception as e:
                print("Error dispatching command {}: {}".format(selected_path, e))

        except Exception as e:
            print("Error in datachange_notification: {}".format(e))


# ──────────────────────────────────────────────────────────────────────────────
#  Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Use 'spawn' so worker processes start with a clean Python interpreter
    # (avoids inheriting the OPC UA server socket and VISA state from the parent)
    multiprocessing.set_start_method('spawn')

    ## USB Device
    # ps_devices = {"ps1": "ASRL/dev/ttyACM0::INSTR",
    #               "ps2": "ASRL/dev/ttyUSB0::INSTR"}

    ## LAN Device
    ps_devices = {"LV_PSU2": "TCPIP::192.168.2.60::5025::SOCKET",
		          #"LV_PSU1": "TCPIP::192.168.2.30::5025::SOCKET",
		          #"LV_PSU0": "TCPIP::192.168.2.20::5025::SOCKET"
          }

    # ps_configs: list of (ps_name, device_address) tuples
    ps_configs = list(ps_devices.items())

    ipAddress        = "opc.tcp://localhost:4848/WIENER-PL512/server/"
    serverName       = "WIENER-PL512 OPC UA Server"
    datapoint_config = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'WIENER-PL512_datapoints.xml')

    hmp4040_opcua = OPCUA_HMP4040(ipAddress, serverName, ps_configs, datapoint_config)

    shutdown_requested = threading.Event()

    def _handle_shutdown_signal(signum, frame):
        print("\nShutdown signal received ({}).".format(signum))
        shutdown_requested.set()

    signal.signal(signal.SIGINT,  _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)

    try:
        while not shutdown_requested.is_set():
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown_requested.set()
    finally:
        print("")
        hmp4040_opcua.serverTermination()
        print("Stopped.")
