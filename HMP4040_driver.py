import pyvisa
import time
import datetime

class HMP4040Driver:
    def __init__(self,  ps_name,
                        deviceAddress,
                        find_lan_devices_flag = False,
                        find_usb_devices_flag = False,
                        reset_device_flag = False):
        self.CHANNELS = range(1, 5)
        ## Initialize the HMP4040 name
        self.ps_name = ps_name
        ## Initialize the HMP4040 device address
        self.deviceAddress = deviceAddress
        # Connect to HMP4040 system by LAN or USB
        self.devObj=pyvisa.ResourceManager()

        ## Get available devices through LAN
        if find_lan_devices_flag:
            self.__find_lan_devices_list("192.168.2", range_start=0, range_end=255, step=10)

        ## Get available devices through USB
        if find_usb_devices_flag:
            self.__get_usb_devices_list()

        ## Connect to the device
        self.hmp4040=self.devObj.open_resource(self.deviceAddress)
        ## Set read termination to newline character 
        self.hmp4040.read_termination = '\n'
        ## Set write termination to newline character
        self.hmp4040.write_termination = '\n'
        ## Set timeout to 10 seconds and query delay to 0 for faster communication
        self.hmp4040.timeout = 10000
        ## Set query delay to 0 for faster communication
        self.hmp4040.query_delay = 0

        ## Get Device Model
        self.device_model = self.__deviceModel()
        ## Get SCPI version
        self.get_iscp_version = self.__get_iscp_version()
        self.scpi_version = self.get_iscp_version

        ### RESET DEVICE
        if reset_device_flag:
            self.__reset()

        self.__clear_status()

        ## Activate Remote control
        self.__remoteMode(True)

        self.get_params = {
            f"Ch{channel}": self._default_channel_params().copy()
            for channel in self.CHANNELS
        }

    @staticmethod
    def _default_channel_params():
        return {
            "ramp_up_rate_V_S": 10.0,
            "ramp_down_rate_V_S": 10.0,
            "ramp_up_rate_A_S": 10.0,
            "ramp_down_rate_A_S": 10.0,
            "voltage_ramping_up": False,
            "voltage_ramping_down": False,
            "voltage_stable": False,
            "current_ramping_up": False,
            "current_ramping_down": False,
            "current_stable": False,
        }

    def __str__(self):
        return "HMP4040 connection to: {} ".format(self.deviceAddress)

    def __repr__(self):
        return str(self)

    def __validate_channel(self, iChannel):
        if iChannel not in self.CHANNELS:
            raise ValueError(f"Invalid channel {iChannel}. Supported channels are 1-4.")

    def __select_channel(self, iChannel):
        self.__validate_channel(iChannel)
        self.hmp4040.write('INST OUT{}'.format(iChannel))

    def __query_bool(self, command):
        return self.hmp4040.query(command).strip() == '1'

    ############### GENERAL FUNCTIONS ################
    ## Try to connect to the device with the given address and return the IDN if successful
    def __try_addr(self, addr):
        try:
            inst = self.devObj.open_resource(addr)
            inst.timeout = 1000
            inst.read_termination = "\n"
            inst.write_termination = "\n"
            idn = inst.query("*IDN?").strip()
            inst.close()
            return idn
        except Exception:
            return None

    ## Find devices in the LAN by scanning IP addresses and trying to connect to them
    def __find_lan_devices_list(self, ipv4, range_start=1, range_end=255, step=1):
        for i in range(range_start, range_end, step):
            ip = f"{ipv4}.{i}"
            print(f"Trying {ip}...")

            for addr in (
                f"TCPIP0::{ip}::5025::SOCKET",
            ):
                idn = self.__try_addr(addr)
                if idn:
                    print(ip, addr, idn)

    ## Return Device model
    def __deviceModel(self):
        return self.hmp4040.query('*IDN?')

    ## Retun available devices through USB
    def __get_usb_devices_list(self):
        print("Available USB Devices: {}".format(self.devObj.list_resources()))

    ## Set Remote MODE status
    def __remoteMode(self, status=False):
        if status:
            ## Set Remote MODE
            self.hmp4040.write('SYSTEM:REMOTE')
        else:
            ## Set Local MODE
            self.hmp4040.write('SYST:LOC')

    ## Reset Device
    def __reset(self):
        ## Reset the device to its default settings
        self.hmp4040.write("*RST")

    ## Clear the status registers
    def __clear_status(self):
        self.hmp4040.write("*CLS")
    
    ##  Queries the SCPI version
    def __get_iscp_version(self):
        return self.hmp4040.query('SYST:VERS?')

    ## Sound (beep) to indicate an event
    def __check_sound(self):
        self.hmp4040.write('SYST:BEEP')

    ## Get the error code from the device's error queue
    def get_error(self):
        err_code = self.hmp4040.query('SYST:ERR?')
        return err_code

    ################ VOLTAGE FUNCTIONS ###############
    ## Set voltage
    def setVoltage(self, iChannel, fValue):
        self.__select_channel(iChannel)
        self.hmp4040.write('SOUR:VOLT {}'.format(fValue))
        self.__check_sound()

    ## Get setting voltage
    def getVoltage(self, iChannel):
        self.__select_channel(iChannel)
        return float(self.hmp4040.query('VOLT?'))

    ## Get Max voltage
    def getVoltageMax(self, iChannel):
        self.__select_channel(iChannel)
        return float(self.hmp4040.query('VOLT? MAX'))

    ## Get Min voltage
    def getVoltageMin(self, iChannel):
        self.__select_channel(iChannel)
        return float(self.hmp4040.query('VOLT? MIN'))

    ## Measure voltage
    def measVoltage(self, iChannel):
        self.__select_channel(iChannel)
        return float(self.hmp4040.query('MEAS:VOLT?'))

    def stepVoltage(self, iChannel, step_v):
        self.__select_channel(iChannel)
        self.hmp4040.write(f"VOLT:STEP {step_v}")

    def step_up_voltage(self, iChannel):
        self.__select_channel(iChannel)
        self.hmp4040.write(f"VOLT UP")
    
    def step_down_voltage(self, iChannel):
        self.__select_channel(iChannel)
        self.hmp4040.write(f"VOLT DOWN")

    ## Print voltage flags for a channel
    def print_voltage_flags(self, channel):
        flags = self.get_params[f"Ch{channel}"]
        print(
            "Flags -> "
            f"ramp_up={flags['voltage_ramping_up']}, "
            f"ramp_down={flags['voltage_ramping_down']}, "
            f"stable={flags['voltage_stable']}"
        )
    ## Set voltage flags and print them
    def set_voltage_flags(self, channel, ramping_up, ramping_down, stable):
        self.get_params[f"Ch{channel}"].update({
            "voltage_ramping_up": ramping_up,
            "voltage_ramping_down": ramping_down,
            "voltage_stable": stable
        })
        self.print_voltage_flags(channel)
    ## RAMP VOLTAGE FUNCTIONS
    def __run_ramp_voltage(self, channel, start_voltage, stop_voltage, rate_v_s):
        channel_key = f"Ch{channel}"
        if channel_key not in self.get_params:
            raise ValueError(f"Unsupported channel: {channel}")
        if rate_v_s <= 0:
            raise ValueError("rate_v_s must be > 0")
        if stop_voltage == start_voltage:
            raise ValueError("stop_voltage must be different from start_voltage")

        delta_v = abs(stop_voltage - start_voltage)
        internal_step_v = min(0.5, delta_v)
        num_steps = max(1, int(delta_v / internal_step_v))
        step_v = delta_v / num_steps
        total_duration_s = delta_v / rate_v_s
        step_interval_s = total_duration_s / num_steps
        is_ramp_up = stop_voltage > start_voltage
        ramp_label = "RAMP UP" if is_ramp_up else "RAMP DOWN"
        step_command = self.step_up_voltage if is_ramp_up else self.step_down_voltage

        print(f"\n[INFO] Start {ramp_label.lower()} on channel {channel} at {rate_v_s} V/s")
        self.get_params[channel_key].update({f"ramp_{'up' if is_ramp_up else 'down'}_rate_V_S": rate_v_s})
        self.set_voltage_flags(channel, ramping_up=is_ramp_up, ramping_down=not is_ramp_up, stable=False)

        self.setVoltage(channel, start_voltage)
        self.stepVoltage(channel, step_v)

        ramp_start_t = time.monotonic()
        log_every = max(1, num_steps // 10)

        try:
            for step_index in range(1, num_steps + 1):
                step_command(channel)
                if step_index == 1 or step_index == num_steps or step_index % log_every == 0:
                    expected_voltage = start_voltage + ((step_index * step_v) if is_ramp_up else -(step_index * step_v))
                    expected_voltage = min(max(expected_voltage, min(start_voltage, stop_voltage)), max(start_voltage, stop_voltage))
                    print(f"[{ramp_label}] step {step_index}/{num_steps}, expected_voltage={expected_voltage:.3f} V")

                target_elapsed_s = step_index * step_interval_s
                elapsed_s = time.monotonic() - ramp_start_t
                remaining_s = target_elapsed_s - elapsed_s
                if remaining_s > 0:
                    time.sleep(remaining_s)

            self.setVoltage(channel, stop_voltage)
            actual_duration_s = time.monotonic() - ramp_start_t
            measured_final_voltage = self.measVoltage(channel)
            self.get_params[channel_key].update({
                "get_voltage": stop_voltage,
                "mess_voltage": measured_final_voltage,
            })

            print(f"[{ramp_label}] final voltage={measured_final_voltage} V")
            print(
                f"[{ramp_label}] target_time={total_duration_s:.3f}s, "
                f"actual_time={actual_duration_s:.3f}s"
            )
            self.set_voltage_flags(channel, ramping_up=False, ramping_down=False, stable=True)
            print(f"[INFO] {ramp_label.title()} complete\n")
        except Exception:
            self.set_voltage_flags(channel, ramping_up=False, ramping_down=False, stable=False)
            raise
        except KeyboardInterrupt:
            self.portTermination()
            print("STOP!")

    def ramp_up_voltage(self, channel, start_voltage, stop_voltage, rate_v_s):
        if stop_voltage <= start_voltage:
            raise ValueError("For ramp_up_voltage, stop_voltage must be > start_voltage")
        self.get_params[f"Ch{channel}"].update({"ramp_up_rate_V_S": rate_v_s})
        self.__run_ramp_voltage(channel, start_voltage, stop_voltage, rate_v_s)

    # Backwards-compatible alias.
    def rump_up_voltage(self, channel, start_voltage, stop_voltage, rate_v_s):
        self.ramp_up_voltage(channel, start_voltage, stop_voltage, rate_v_s)

    def ramp_down_voltage(self, channel, start_voltage, stop_voltage, rate_v_s):
        if stop_voltage >= start_voltage:
            raise ValueError("For ramp_down_voltage, stop_voltage must be < start_voltage")
        self.get_params[f"Ch{channel}"].update({"ramp_down_rate_V_S": rate_v_s})
        self.__run_ramp_voltage(channel, start_voltage, stop_voltage, rate_v_s)

    # Backwards-compatible alias.
    def rump_down_voltage(self, channel, start_voltage, stop_voltage, rate_v_s):
        self.ramp_down_voltage(channel, start_voltage, stop_voltage, rate_v_s)

    ################ CURRENT FUNCTIONS ###############
    ## Set current
    def setCurrent(self, iChannel, fValue):
        self.__select_channel(iChannel)
        self.hmp4040.write('SOUR:CURR {}'.format(fValue))
        self.__check_sound()

    ## Get setting current
    def getCurrent(self, iChannel):
        self.__select_channel(iChannel)
        return float(self.hmp4040.query('CURR?'))

    ## Get Max current
    def getCurrentMax(self, iChannel):
        self.__select_channel(iChannel)
        return float(self.hmp4040.query('CURR? MAX'))

    ## Get Min current
    def getCurrentMin(self, iChannel):
        self.__select_channel(iChannel)
        return float(self.hmp4040.query('CURR? MIN'))

    ## Measure Current
    def measCurrent(self, iChannel):
        self.__select_channel(iChannel)
        return float(self.hmp4040.query('MEAS:CURR?'))

    def stepCurrent(self, iChannel, step_i):
        self.__select_channel(iChannel)
        self.hmp4040.write(f"CURR:STEP {step_i}")

    def step_up_current(self, iChannel):
        self.__select_channel(iChannel)
        self.hmp4040.write("CURR UP")

    def step_down_current(self, iChannel):
        self.__select_channel(iChannel)
        self.hmp4040.write("CURR DOWN")

    ##### RumpUp and RumpDown current functions ######
    ## Get event status
    def print_current_flags(self, channel):
        flags = self.get_params[f"Ch{channel}"]
        print(
                "Flags -> "
                f"ramp_up={flags['current_ramping_up']}, "
                f"ramp_down={flags['current_ramping_down']}, "
                f"stable={flags['current_stable']}"
        )
    ## Set current flags and print them
    def set_current_flags(self, channel, ramping_up, ramping_down, stable):
        self.get_params[f"Ch{channel}"].update({
            "current_ramping_up": ramping_up,
            "current_ramping_down": ramping_down,
            "current_stable": stable
        })
        self.print_current_flags(channel)
    ## RAMP CURRENT FUNCTIONS
    def __run_ramp_current(self, channel, start_current, stop_current, rate_A_s):
        channel_key = f"Ch{channel}"
        if channel_key not in self.get_params:
            raise ValueError(f"Unsupported channel: {channel}")
        if rate_A_s <= 0:
            raise ValueError("rate_A_s must be > 0")
        if stop_current == start_current:
            raise ValueError("stop_current must be different from start_current")

        delta_i = abs(stop_current - start_current)
        internal_step_i = min(0.5, delta_i)
        num_steps = max(1, int(delta_i / internal_step_i))
        step_i = delta_i / num_steps
        total_duration_s = delta_i / rate_A_s
        step_interval_s = total_duration_s / num_steps
        is_ramp_up = stop_current > start_current
        ramp_label = "RAMP UP" if is_ramp_up else "RAMP DOWN"
        step_command = self.step_up_current if is_ramp_up else self.step_down_current

        print(f"\n[INFO] Start {ramp_label.lower()} on channel {channel} at {rate_A_s} A/s")
        self.get_params[channel_key].update({f"ramp_{'up' if is_ramp_up else 'down'}_rate_A_S": rate_A_s})
        self.set_current_flags(channel, ramping_up=is_ramp_up, ramping_down=not is_ramp_up, stable=False)

        self.setCurrent(channel, start_current)
        self.stepCurrent(channel, step_i)

        ramp_start_t = time.monotonic()
        log_every = max(1, num_steps // 10)
        try:
            for step_index in range(1, num_steps + 1):
                step_command(channel)
                if step_index == 1 or step_index == num_steps or step_index % log_every == 0:
                    expected_current = start_current + ((step_index * step_i) if is_ramp_up else -(step_index * step_i))
                    expected_current = min(max(expected_current, min(start_current, stop_current)), max(start_current, stop_current))
                    print(f"[{ramp_label}] step {step_index}/{num_steps}, expected_current={expected_current:.3f} A")

                target_elapsed_s = step_index * step_interval_s
                elapsed_s = time.monotonic() - ramp_start_t
                remaining_s = target_elapsed_s - elapsed_s
                if remaining_s > 0:
                    time.sleep(remaining_s)

            self.setCurrent(channel, stop_current)
            actual_duration_s = time.monotonic() - ramp_start_t
            measured_final_current = self.measCurrent(channel)
            self.get_params[channel_key].update({
                "get_current": stop_current,
                "mess_current": measured_final_current,
            })

            print(f"[{ramp_label}] final current={measured_final_current} A")
            print(
                f"[{ramp_label}] target_time={total_duration_s:.3f}s, "
                f"actual_time={actual_duration_s:.3f}s"
            )
            self.set_current_flags(channel, ramping_up=False, ramping_down=False, stable=True)
            print(f"[INFO] {ramp_label.title()} complete\n")
        except Exception:
            self.set_current_flags(channel, ramping_up=False, ramping_down=False, stable=False)
            raise

        except KeyboardInterrupt:
            self.portTermination()
            print("STOP!")

    def ramp_up_current(self, channel, start_current, stop_current, rate_A_s):
        if stop_current <= start_current:
            raise ValueError("For ramp_up_current, stop_current must be > start_current")
        self.get_params[f"Ch{channel}"].update({"ramp_up_rate_A_S": rate_A_s})
        self.__run_ramp_current(channel, start_current, stop_current, rate_A_s)

    # Backwards-compatible alias.
    def rump_up_current(self, channel, start_current, stop_current, rate_A_s):
        self.ramp_up_current(channel, start_current, stop_current, rate_A_s)

    def ramp_down_current(self, channel, start_current, stop_current, rate_A_s):
        if stop_current >= start_current:
            raise ValueError("For ramp_down_current, stop_current must be < start_current")
        self.get_params[f"Ch{channel}"].update({"ramp_down_rate_A_S": rate_A_s})
        self.__run_ramp_current(channel, start_current, stop_current, rate_A_s)

    # Backwards-compatible alias.
    def rump_down_current(self, channel, start_current, stop_current, rate_A_s):
        self.ramp_down_current(channel, start_current, stop_current, rate_A_s)

    ################ OUTPUT FUNCTIONS ################
    ## Set individual channels output status
    def setOutput(self, iChannel, bEnable):
        self.__select_channel(iChannel)
        self.hmp4040.write('OUTP {}'.format(int(bEnable)))
        self.__check_sound()

    ## Get individual channels output status
    def getOutput(self, iChannel):
        self.__select_channel(iChannel)
        return self.__query_bool('OUTP?')

    ## Set Global power OUTPUT Button status
    def enablePower(self, bValue):
        self.hmp4040.write('OUTP:GEN {0:d}'.format(int(bValue)))
        self.__check_sound()

    ## Get Global power OUTPUT Button status
    def getEnablePower(self):
        return self.__query_bool('OUTP:GEN?')

    ################## OVP FUNCTIONS #################
    ## Set Over voltage protection Limit
    def setOVPLimit(self, iChannel, fValue):
        self.__select_channel(iChannel)
        self.hmp4040.write('VOLT:PROT {}'.format(fValue))
        self.__check_sound()
    
    ## Get Over voltage protection Limit
    def getOVPLimit(self, iChannel):
        self.__select_channel(iChannel)
        return float(self.hmp4040.query('VOLT:PROT?'))

    ## Get Over voltage protection the upper Limit
    def getOVP_MAX_Limit(self, iChannel):
        self.__select_channel(iChannel)
        return float(self.hmp4040.query('VOLT:PROT? MAX'))

    ## Get Over voltage protection the lower Limit
    def getOVP_MIN_Limit(self, iChannel):
        self.__select_channel(iChannel)
        return float(self.hmp4040.query('VOLT:PROT? MIN'))

    ## Resets a tripped OVP in the selected channel
    def setOVP_Clear(self, iChannel):
        self.__select_channel(iChannel)
        self.hmp4040.write('VOLT:PROT:CLE')
        self.__check_sound()

    ## Get the OVP channels output trip status
    def getOVP_Trip(self, iChannel):
        self.__select_channel(iChannel)
        return self.__query_bool('VOLT:PROT:TRIP?')

    ## Set over VOLTAGE Protection MODE (mode='MEAS' or mode='PROT')
    def setOVP_Mode(self, iChannel, mode='MEAS'):
        self.__select_channel(iChannel)
        self.hmp4040.write('SOUR:VOLT:PROT:MODE {}'.format(mode))
        self.__check_sound()

    ## Get over VOLTAGE Protection MODE
    def getOVP_Mode(self, iChannel):
        self.__select_channel(iChannel)
        return self.hmp4040.query('VOLT:PROT:MODE PROT?')

    ## Resets the OVP state of the selected channel
    def resetOVPstate(self, iChannel):
        self.__select_channel(iChannel)
        self.hmp4040.write('VOLT:PROT:CLE')
        self.__check_sound()

    ################# FUSE FUNCTIONS #################
    ## Set Fuse Delay in min
    def setFuseDelay(self, iChannel, delayms):
        self.__select_channel(iChannel)
        self.hmp4040.write('FUSE:DEL {}'.format(delayms))
        self.__check_sound()

    ## Get Fuse Delay
    def getFuseDelay(self, iChannel):
        self.__select_channel(iChannel)
        return int(self.hmp4040.query('FUSE:DEL?'))
    
    ## Get Fuse Delay Max
    def getFuseDelayMax(self, iChannel):
        self.__select_channel(iChannel)
        return int(self.hmp4040.query('FUSE:DEL? MAX'))
    
    ## Get Fuse Delay Min
    def getFuseDelayMin(self, iChannel):
        self.__select_channel(iChannel)
        return int(self.hmp4040.query('FUSE:DEL? MIN'))

    ## Set Fuse Link
    def setFuseLink(self, iChannel, linkChannel):
        self.__select_channel(iChannel)
        self.hmp4040.write('FUSE:LINK {}'.format(linkChannel))
        self.__check_sound()

    ## Get Fuse Link
    def getFuseLink(self, iChannel, linkChannel):
        self.__select_channel(iChannel)
        return int (self.hmp4040.query('FUSE:LINK? {}'.format(linkChannel)))

    ## Dissolve the link of the electronic fuses
    def setFuseUnLink(self, iChannel, linkChannel):
        self.__select_channel(iChannel)
        self.hmp4040.write('FUSE:UNL {}'.format(linkChannel))
        self.__check_sound()

    ## Set Fuse State
    def setFuseState(self, iChannel, onOff=False):
        self.__select_channel(iChannel)
        if onOff:
            self.hmp4040.write('FUSE:STATE ON')
            self.__check_sound()
        else:
            self.hmp4040.write('FUSE:STATE OFF')
            self.__check_sound()

    ## Get Fuse State
    def getFuseState(self, iChannel):
        self.__select_channel(iChannel)
        return self.__query_bool('FUSE?')

    ## Get Fuse Tripped Status
    def getFuseTripped(self, iChannel):
        self.__select_channel(iChannel)
        return self.__query_bool('FUSE:TRIPPED?')

    ## Terminate the Device
    def portTermination(self):
        try:
            for ch in self.CHANNELS:
                try:
                    self.setOutput(ch, False)
                except Exception:
                    pass

            try:
                self.enablePower(False)
            except Exception:
                pass

            try:
                self.__remoteMode(False)
            except Exception:
                pass
        finally:
            try:
                self.hmp4040.close()
            except Exception:
                pass
    
    ##################################################
    ########## Get All Channels parameters ###########
    def get_all_channels_params(self):
        for iChannel in self.CHANNELS:
            self.__select_channel(iChannel)

            get_voltage = float(self.hmp4040.query('VOLT?'))
            get_current = float(self.hmp4040.query('CURR?'))
            mess_voltage = float(self.hmp4040.query('MEAS:VOLT?'))
            mess_current = float(self.hmp4040.query('MEAS:CURR?'))
            get_max_current = float(self.hmp4040.query('CURR? MAX'))
            get_min_current = float(self.hmp4040.query('CURR? MIN'))
            get_max_voltage = float(self.hmp4040.query('VOLT? MAX'))
            get_min_voltage = float(self.hmp4040.query('VOLT? MIN'))
            get_output_status = self.__query_bool('OUTP?')
            get_global_output_status = self.__query_bool('OUTP:GEN?')
            get_voltage_prot = float(self.hmp4040.query('VOLT:PROT?'))
            get_voltage_prot_max = float(self.hmp4040.query('VOLT:PROT? MAX'))
            get_voltage_prot_min = float(self.hmp4040.query('VOLT:PROT? MIN'))
            get_voltage_prot_trip = self.__query_bool('VOLT:PROT:TRIP?')
            get_ovp_mode_prot = str(self.hmp4040.query('VOLT:PROT:MODE PROT?'))
            get_ovp_mode = str(self.hmp4040.query('VOLT:PROT:MODE?'))
            get_fuse_delay = int(self.hmp4040.query('FUSE:DEL?'))
            get_fuse_delay_max = int(self.hmp4040.query('FUSE:DEL? MAX'))
            get_fuse_delay_min = int(self.hmp4040.query('FUSE:DEL? MIN'))

            if iChannel == 1:
                get_fuse_link_ch1_to_ch2 = self.__query_bool('FUSE:LINK? 2')
                get_fuse_link_ch1_to_ch3 = self.__query_bool('FUSE:LINK? 3')
                get_fuse_link_ch1_to_ch4 = self.__query_bool('FUSE:LINK? 4')
            elif iChannel == 2:
                get_fuse_link_ch2_to_ch1 = self.__query_bool('FUSE:LINK? 1')
                get_fuse_link_ch2_to_ch3 = self.__query_bool('FUSE:LINK? 3')
                get_fuse_link_ch2_to_ch4 = self.__query_bool('FUSE:LINK? 4')
            elif iChannel == 3:
                get_fuse_link_ch3_to_ch1 = self.__query_bool('FUSE:LINK? 1')
                get_fuse_link_ch3_to_ch2 = self.__query_bool('FUSE:LINK? 2')
                get_fuse_link_ch3_to_ch4 = self.__query_bool('FUSE:LINK? 4')
            elif iChannel == 4:
                get_fuse_link_ch4_to_ch1 = self.__query_bool('FUSE:LINK? 1')
                get_fuse_link_ch4_to_ch2 = self.__query_bool('FUSE:LINK? 2')
                get_fuse_link_ch4_to_ch3 = self.__query_bool('FUSE:LINK? 3')

            get_fuse_status = self.__query_bool('FUSE?')
            get_fuse_tripped = self.__query_bool('FUSE:TRIPPED?')

            self.get_params[f"Ch{iChannel}"].update({"get_voltage": get_voltage})
            self.get_params[f"Ch{iChannel}"].update({"get_current": get_current})
            self.get_params[f"Ch{iChannel}"].update({"mess_voltage": mess_voltage})
            self.get_params[f"Ch{iChannel}"].update({"mess_current": mess_current})
            self.get_params[f"Ch{iChannel}"].update({"get_max_current": get_max_current})
            self.get_params[f"Ch{iChannel}"].update({"get_min_current": get_min_current})
            self.get_params[f"Ch{iChannel}"].update({"get_max_voltage": get_max_voltage})
            self.get_params[f"Ch{iChannel}"].update({"get_min_voltage": get_min_voltage})
            self.get_params[f"Ch{iChannel}"].update({"get_output_status": get_output_status})
            self.get_params[f"Ch{iChannel}"].update({"get_global_output_status": get_global_output_status})
            self.get_params[f"Ch{iChannel}"].update({"get_voltage_prot": get_voltage_prot})
            self.get_params[f"Ch{iChannel}"].update({"get_voltage_prot_max": get_voltage_prot_max})
            self.get_params[f"Ch{iChannel}"].update({"get_voltage_prot_min": get_voltage_prot_min})
            self.get_params[f"Ch{iChannel}"].update({"get_voltage_prot_trip": get_voltage_prot_trip})
            self.get_params[f"Ch{iChannel}"].update({"get_ovp_mode_prot": get_ovp_mode_prot})
            self.get_params[f"Ch{iChannel}"].update({"get_ovp_mode": get_ovp_mode})
            self.get_params[f"Ch{iChannel}"].update({"get_fuse_delay": get_fuse_delay})
            self.get_params[f"Ch{iChannel}"].update({"get_fuse_delay_max": get_fuse_delay_max})
            self.get_params[f"Ch{iChannel}"].update({"get_fuse_delay_min": get_fuse_delay_min})

            if iChannel == 1:
                self.get_params[f"Ch{iChannel}"].update({"get_fuse_link_ch1_to_ch2": get_fuse_link_ch1_to_ch2})
                self.get_params[f"Ch{iChannel}"].update({"get_fuse_link_ch1_to_ch3": get_fuse_link_ch1_to_ch3})
                self.get_params[f"Ch{iChannel}"].update({"get_fuse_link_ch1_to_ch4": get_fuse_link_ch1_to_ch4})
            elif iChannel == 2:
                self.get_params[f"Ch{iChannel}"].update({"get_fuse_link_ch2_to_ch1": get_fuse_link_ch2_to_ch1})
                self.get_params[f"Ch{iChannel}"].update({"get_fuse_link_ch2_to_ch3": get_fuse_link_ch2_to_ch3})
                self.get_params[f"Ch{iChannel}"].update({"get_fuse_link_ch2_to_ch4": get_fuse_link_ch2_to_ch4})
            elif iChannel == 3:
                self.get_params[f"Ch{iChannel}"].update({"get_fuse_link_ch3_to_ch1": get_fuse_link_ch3_to_ch1})
                self.get_params[f"Ch{iChannel}"].update({"get_fuse_link_ch3_to_ch2": get_fuse_link_ch3_to_ch2})
                self.get_params[f"Ch{iChannel}"].update({"get_fuse_link_ch3_to_ch4": get_fuse_link_ch3_to_ch4})
            elif iChannel == 4:
                self.get_params[f"Ch{iChannel}"].update({"get_fuse_link_ch4_to_ch1": get_fuse_link_ch4_to_ch1})
                self.get_params[f"Ch{iChannel}"].update({"get_fuse_link_ch4_to_ch2": get_fuse_link_ch4_to_ch2})
                self.get_params[f"Ch{iChannel}"].update({"get_fuse_link_ch4_to_ch3": get_fuse_link_ch4_to_ch3})

            self.get_params[f"Ch{iChannel}"].update({"get_fuse_status": get_fuse_status})
            self.get_params[f"Ch{iChannel}"].update({"get_fuse_tripped": get_fuse_tripped})

if __name__ == "__main__":
    ###############################################################################
    ################################### HMP4040 ###################################
    ###############################################################################
    ## LAN Device
    deviceAddress = "TCPIP::192.168.2.60::5025::SOCKET"

    ## USB Device
    #deviceAddress = 'ASRL/dev/ttyACM0::INSTR'

    ## Get the list of available devices and device model
    hmp4040 = HMP4040Driver('ps3', deviceAddress)
    print('Device Model:', hmp4040.device_model)
    print('Queries the SCPI version:', hmp4040.get_iscp_version)
    print('Get Error:', hmp4040.get_error())

    ## Enable Output on channel 1 example
    hmp4040.setOutput(1, True)

    ## Disable Output on channel 1 example
    #hmp4040.setOutput(1, False)

    ## Set OVP limit to 32.5V on channel 1 example
    hmp4040.setOVPLimit(1, 32.5)
    hmp4040.setOVPLimit(2, 32.5)
    hmp4040.setOVPLimit(3, 32.5)
    hmp4040.setOVPLimit(4, 32.5)

    ## Set voltage to 1V on channel 1 example
    hmp4040.setVoltage(1, 1.0)
    hmp4040.setCurrent(1, 0.5)
    
    '''
    ######### Ramp up/down current example #########
    hmp4040.setVoltage(1, 1.0)

    CHANNEL = 1
    START_CURRENT = 0.0
    STOP_CURRENT = 2.0
    RATE_A_S = 0.1
    
    hmp4040.ramp_up_current(CHANNEL,
                            START_CURRENT,
                            STOP_CURRENT,
                            RATE_A_S)
    
    hmp4040.ramp_down_current(CHANNEL,
                            STOP_CURRENT,
                            START_CURRENT,
                            RATE_A_S)
    
    ######### Ramp up/down voltage example #########
    hmp4040.setCurrent(1, 0.5)
    CHANNEL = 1
    START_VOLTAGE = 0.0
    STOP_VOLTAGE = 30.0
    RATE_V_S = 10.0
    
    hmp4040.ramp_up_voltage(CHANNEL,
                            START_VOLTAGE,
                            STOP_VOLTAGE,
                            RATE_V_S)

    
    hmp4040.ramp_down_voltage(CHANNEL,
                            STOP_VOLTAGE,
                            START_VOLTAGE,
                            RATE_V_S)
    
    '''
    while True:
        try:
            t0 = time.time()
            #################### Current UTC time ##########################
            CurrentTime = datetime.datetime.now(datetime.timezone.utc)

            # Print all Channels data
            print('#################################################')
            print("Time: {}".format(CurrentTime))

            ## Read all channels parameters
            hmp4040.get_all_channels_params()

            ## Print the parameters for all channels
            print(hmp4040.get_params)

            print("Readout Duration [s] {}".format(time.time() - t0))
            #time.sleep(1)

        except pyvisa.errors.VisaIOError:
            print("Connect USB!")
        except ValueError:
            pass
        except KeyboardInterrupt:
            hmp4040.portTermination()
            print("STOP!")
            break