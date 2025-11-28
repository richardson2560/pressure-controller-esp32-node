# --- START OF FILE modules.py ---

import sys, time, struct
from machine import RTC
import board
import hardware
from utils import Timer, RunningMedianFilter, adc_to_voltage, pad_str
from lib.urtc import tuple2seconds, seconds2timetuple
from config import config_manager
from pubsub import event_manager

# --- Importaciones del Protocolo y Constantes ---
from protocol import (
    build_packet, parse_packet, BROADCAST_ID, INITIAL_TTL,
    FRAME_TYPE_CMD, FRAME_TYPE_RESP,
    CMD_HELLO, CMD_ROUTE_AD, CMD_GET_SENSOR_STATUS, CMD_GET_PARAM, 
    CMD_SET_PARAM, DTYPE_BOOL, DTYPE_UINT, DTYPE_SINT, DTYPE_FLOAT,
    CMD_UPDATE_RTC, CMD_MODULE_CTRL
)
from env import BASE_STATION_ID, MODULE_REGISTRY

# --- Diccionario Global de Módulos ---
# Este diccionario se llenará en la función init() y será accesible
# por todas las clases y funciones dentro de este archivo.
_modules = {}

# --- Mapas para el Protocolo (generados dinámicamente) ---
MODULE_ID_MAP = {name: i for i, name in enumerate(MODULE_REGISTRY.keys())}
ID_MODULE_MAP = {i: name for name, i in MODULE_ID_MAP.items()}

PARAMETER_MAP = {
    # Parámetros de Módulos (disparan reinicio de módulos)
    0x01: "MODULE_CONFIGURATION.data_reporter.report_interval_s",
    0x02: "MODULE_CONFIGURATION.display.backlight_timeout_s",
    0x03: "MODULE_CONFIGURATION.pressure_1.V_TO_MPA_SLOPE",
    # Parámetros de Hardware (disparan reinicio de hardware y módulos)
    0x81: "HARDWARE_CONFIGURATION.uart.1.baudrate",
    # Acciones Directas (NO disparan reinicio)
    0xA1: "direct.display.set_backlight",
}
ID_PARAMETER_MAP = {v: k for k, v in PARAMETER_MAP.items()}


# --- Clases Base y de Módulos ---

class _BaseModule:
    # ... (Esta clase no necesita cambios) ...
    def __init__(self):
        self.timer = {"timer0": Timer()}
        self.autostart = True
        self.states = {}
        self.current_state = None
        self.previous_state = None
        self.polling = True
    def update(self):
        if self.current_state in self.states: self.states[self.current_state]()
    def start(self, interval, timer="timer0"):
        self.autostart = True
        self.timer[timer].start(interval)
    def resume(self, timer="timer0"): self.timer[timer].resume()
    def pause(self, timer="timer0"): self.timer[timer].pause()
    def stop(self, timer="timer0"):
        self.autostart = False
        self.timer[timer].pause()
    def reset(self, timer="timer0"): self.timer[timer].reset()
    def set_interval(self, timer="timer0", interval=None):
        if interval: self.timer[timer].set_interval(interval)
    def check(self, timer="timer0"): return self.timer[timer].check()

class Clock(_BaseModule):
    # ... (Esta clase no necesita cambios) ...
    def __init__(self, config, name=None):
        super().__init__()
        self.device_key = config.get("device_key", None)
        self.drift_check_interval_s = config.get("drift_check_interval_s", 60)
        self.max_drift_s = config.get("max_drift_s", 2)
        self.driver = hardware._drivers[self.device_key]
        self.start(self.drift_check_interval_s)
    def update(self):
        if self.check():
            driver_seconds = tuple2seconds(self.driver.datetime())
            rtc_seconds = time.time()
            if abs(driver_seconds - rtc_seconds) > self.max_drift_s:
                RTC().datetime(seconds2timetuple(driver_seconds))

class Temperature(_BaseModule):
    # ... (Esta clase no necesita cambios) ...
    def __init__(self, config, name=None):
        super().__init__()
        self.device_key = config.get("device_key", None)
        self.read_interval_s = config.get("read_interval_s", 5)
        self.driver = hardware._drivers[self.device_key]
        self.start(self.read_interval_s)
    def update(self):
        if self.driver and self.check():
            board.states["temperature"] = self.driver.get_temperature()

class AnalogInput(_BaseModule):
    # ... (Esta clase no necesita cambios) ...
    def __init__(self, config, name=None):
        super().__init__()
        self.name = name
        self.device_key = config.get("device_key", None)
        self.read_interval_s = config.get("read_interval_s", 0.1)
        self.filter_size = config.get("median_filter_size", 10)
        self.adc_max_value = config.get("adc_max_value", 4095.0)
        self.filter = RunningMedianFilter(self.filter_size)
        self.start(self.read_interval_s)
    def update(self):
        if self.check():
            raw_value = board.states.get(self.device_key, 0) / self.adc_max_value
            self.filter.add(raw_value)
            normalized_value = adc_to_voltage(self.filter.get_median())
            event_manager.publish(f'{self.name}:ready', voltage_value=normalized_value)

class Pressure(_BaseModule):
    # ... (Esta clase no necesita cambios) ...
    def __init__(self, config, name=None):
        super().__init__()
        self.V_TO_MPA_SLOPE = config.get("V_TO_MPA_SLOPE", 12.5)
        self.V_TO_MPA_INTERCEPT = config.get("V_TO_MPA_INTERCEPT", -1.25)
        self.PSI_PER_MPA = config.get("PSI_PER_MPA", 145.038)
        self.subs = config.get("subs")
        self.polling = False
        if self.subs: event_manager.subscribe(f'{self.subs}:ready', self.update)
    def update(self, voltage_value: float):
        if voltage_value is None: return
        mpa_pressure = voltage_value * self.V_TO_MPA_SLOPE + self.V_TO_MPA_INTERCEPT
        psi_pressure = round(mpa_pressure * self.PSI_PER_MPA)
        board.states["pressure"] = psi_pressure

def _format_time(fmt_str, time_tuple):
    s = fmt_str
    s = s.replace("%H", "{:02d}".format(time_tuple[3]))
    s = s.replace("%M", "{:02d}".format(time_tuple[4]))
    s = s.replace("%S", "{:02d}".format(time_tuple[5])) 
    s = s.replace("%d", "{:02d}".format(time_tuple[2]))
    s = s.replace("%m", "{:02d}".format(time_tuple[1]))
    s = s.replace("%y", "{:02d}".format(time_tuple[0] % 100))
    s = s.replace("%Y", "{}".format(time_tuple[0]))
    return s

class Display(_BaseModule):
    # --- AÑADIDO EL MÉTODO 'set_backlight' ---
    def __init__(self, config, name=None):
        super().__init__()
        self.timer["timer1"] = Timer(one_shot=True)
        self.timer["timer2"] = Timer(one_shot=True)
        self.device_key = config.get("device_key", None)
        self.refresh_interval_s = config.get("refresh_interval_s", 1)
        self.boot_duration_s = config.get("boot_duration_s", 5)
        self.backlight_timeout_s = config.get("backlight_timeout_s", 30)
        self.rows = config.get("rows", 2)
        self.cols = config.get("cols", 16)
        self.disp_buffer = [pad_str("", self.cols) for _ in range(self.rows)]
        self.prev_disp_buffer = [""] * self.rows
        self.states = {"boot": self.boot, "idle": self.idle, "read": self.read, "idle_1": self.idle_1, "off": self.off}
        self.current_state = "boot"
        self.driver = hardware._drivers.get(self.device_key)
        if not self.driver:
            self.stop()
            return
        self.subs = config.get("subs")
        if self.subs: event_manager.subscribe(f'irq:{self.subs}:triggered', self.off)
        self.start(self.boot_duration_s, timer="timer1")
    def boot(self):
        self.driver.clear()
        self.driver.putstr("Iniciando ...")
        self.current_state = "idle"
    def idle(self):
        if self.check(timer="timer1"):
            self.start(self.refresh_interval_s, timer="timer0")
            self.start(self.backlight_timeout_s, timer="timer2")
            self.driver.clear()
            self.current_state = "read"
    def read(self):
        pressure = board.states.get("pressure", -1)
        now_tuple = time.localtime(time.time()) 
        time_str = _format_time("%d/%m/%y %H:%M", now_tuple)
        pressure_str = "P: {:4d}psi".format(pressure)
        self.disp_buffer[0] = pad_str(time_str, self.cols)
        self.disp_buffer[1] = pad_str(pressure_str, self.cols)
        for row in range(self.rows):
            if self.disp_buffer[row] != self.prev_disp_buffer[row]:
                self.driver.move_to(0,row)
                self.driver.putstr(self.disp_buffer[row])
                self.prev_disp_buffer[row] = self.disp_buffer[row]
        self.current_state = "idle_1"
    def idle_1(self):
        if self.check(timer="timer2"):
            self.pause(timer="timer0")
            self.current_state = "off"
        elif self.check(timer="timer0"):
            self.current_state = "read"
    def off(self, state=None, pin_value=None):
        if state is not None:
            if state == 1 and self.current_state == "off":
                self.driver.backlight_on()
                self.reset(timer="timer2")
                self.resume(timer="timer0")
                self.current_state = "read"
                self.prev_disp_buffer = [""] * self.rows
        elif self.driver.backlight:
            self.driver.clear()
            self.driver.backlight_off()
    def set_backlight(self, state: bool):
        if state:
            self.driver.backlight_on()
            if self.current_state == "off": self.off(state=1)
            else: self.reset(timer="timer2")
        else:
            self.driver.backlight_off()

# --- MÓDULOS DE RED ---

class LoraTX(_BaseModule):
    # --- CORREGIDO el método de envío ---
    def __init__(self, config, name=None):
        super().__init__()
        self.device_key = config.get("device_key")
        self.check_interval_s = config.get("check_interval_s", 0.1)
        self.bus_type = config.get("bus_type")
        self.bus_id = config.get("bus_id")
        self.driver = hardware._drivers.get(self.device_key)
        self.start(self.check_interval_s)
    def update(self):
        if self.check() and board.messages[f"{self.bus_type}_{self.bus_id}"]["out"]:
            message_to_send = board.messages[f"{self.bus_type}_{self.bus_id}"]["out"].pop(0)
            if isinstance(message_to_send, bytes):
                self.driver.send_transparent_message(message_to_send)
                print(f"send message ... {parse_packet(message_to_send)}")

class Routing(_BaseModule):
    # ... (Esta clase no necesita cambios) ...
    def __init__(self, config, name=None):
        super().__init__()
        self.my_id = config_manager.get("SYSTEM_ID")
        self.bus_type = config.get("bus_type")
        self.bus_id = config.get("bus_id")
        self.neighbor_table = {}
        self.routing_table = {self.my_id: {"next_hop": self.my_id, "cost": 0, "last_updated": time.ticks_ms()}}
        self.timer["hello"] = Timer()
        self.timer["route_update"] = Timer()
        self.hello_interval_s = config.get("hello_interval_s", 30)
        self.route_update_interval_s = config.get("route_update_interval_s", 60)
        self.neighbor_timeout_s = self.hello_interval_s * 3.5
        event_manager.subscribe('lora:message:received', self.process_network_packet)
        event_manager.subscribe('route:forward_request', self.forward_packet)
        self.start(self.hello_interval_s, timer="hello")
        self.start(self.route_update_interval_s, timer="route_update")
    def update(self):
        if self.timer["hello"].check(): self._send_hello()
        if self.timer["route_update"].check():
            self._send_route_advertisement()
            self._prune_tables()
    def _send_hello(self):
        packet = build_packet(BROADCAST_ID, self.my_id, FRAME_TYPE_CMD, 1, CMD_HELLO)
        board.messages[f"{self.bus_type}_{self.bus_id}"]["out"].insert(0, packet)
    def _send_route_advertisement(self):
        payload = b''
        for dest, route_info in self.routing_table.items():
            if dest != self.my_id: payload += struct.pack('>BH', dest, int(route_info['cost']))
        if payload:
            packet = build_packet(BROADCAST_ID, self.my_id, FRAME_TYPE_CMD, 1, CMD_ROUTE_AD, payload)
            board.messages[f"{self.bus_type}_{self.bus_id}"]["out"].insert(0, packet)
    def process_network_packet(self, parsed_packet, rssi):
        src_id, command = parsed_packet["src_id"], parsed_packet["command"]
        link_cost = max(1, 255 - rssi)
        self.neighbor_table[src_id] = {"rssi": rssi, "last_seen": time.ticks_ms(), "cost": link_cost}
        if command == CMD_ROUTE_AD:
            payload, cost_to_neighbor = parsed_packet["payload"], link_cost
            for i in range(0, len(payload), 3):
                dest_id, cost_from_neighbor = struct.unpack('>BH', payload[i:i+3])
                if dest_id == self.my_id: continue
                new_total_cost = cost_to_neighbor + cost_from_neighbor
                current_route = self.routing_table.get(dest_id)
                if not current_route or new_total_cost < current_route['cost']:
                    self.routing_table[dest_id] = {"next_hop": src_id, "cost": new_total_cost, "last_updated": time.ticks_ms()}
    def forward_packet(self, packet: bytes):
        parsed = parse_packet(packet)
        if not parsed or parsed["ttl"] <= 1: return
        route_info = self.routing_table.get(parsed["dest_id"])
        if route_info:
            new_packet = build_packet(parsed["dest_id"], parsed["src_id"], parsed["control"], parsed["ttl"] - 1, parsed["command"], parsed["payload"])
            board.messages[f"{self.bus_type}_{self.bus_id}"]["out"].append(new_packet)
    def _prune_tables(self):
        now = time.ticks_ms()
        timeout_ms = self.neighbor_timeout_s * 1000
        expired_neighbors = [nid for nid, data in self.neighbor_table.items() if time.ticks_diff(now, data['last_seen']) > timeout_ms]
        for nid in expired_neighbors: del self.neighbor_table[nid]
        expired_routes = [did for did, route in self.routing_table.items() if route['next_hop'] in expired_neighbors]
        for did in expired_routes: del self.routing_table[did]

class MessageLora(_BaseModule):
    # --- SIN CAMBIOS, ya estaba bien diseñada ---
    def __init__(self, config, name=None):
        super().__init__()
        self.read_interval_s = config.get("read_interval_s", 0.1)
        self.device_id = config_manager.get("SYSTEM_ID")
        self.bus_type = config.get("bus_type")
        self.bus_id = config.get("bus_id")
        self.command_handlers = {
            CMD_GET_SENSOR_STATUS: self._handle_get_status,
            CMD_UPDATE_RTC: self._handle_update_rtc,
            CMD_MODULE_CTRL: self._handle_module_ctrl,
            CMD_GET_PARAM: self._handle_get_param,
            CMD_SET_PARAM: self._handle_set_param,
        }
        self.start(self.read_interval_s)
    def update(self):
        if self.check() and board.messages[f"{self.bus_type}_{self.bus_id}"]["in"]:
            msg_obj = board.messages[f"{self.bus_type}_{self.bus_id}"]["in"].pop(0)
            raw_data, rssi = msg_obj.get('data'), msg_obj.get('rssi', 0)
            parsed = parse_packet(raw_data)
            if not parsed: return
            event_manager.publish('lora:message:received', parsed_packet=parsed, rssi=rssi)
            if parsed["dest_id"] == self.device_id:
                handler = self.command_handlers.get(parsed["command"])
                if handler: handler(parsed["src_id"], parsed["payload"])
            elif parsed["dest_id"] != BROADCAST_ID:
                event_manager.publish('route:forward_request', packet=raw_data)
    def _handle_get_status(self, originator_id: int, payload: bytes):
        pressure = board.states.get("pressure", 0)
        temperature_scaled = int(board.states.get("temperature", 0) * 100)
        response_payload = struct.pack('>hH', temperature_scaled, pressure)
        response_packet = build_packet(originator_id, self.device_id, FRAME_TYPE_RESP, INITIAL_TTL, CMD_GET_SENSOR_STATUS, response_payload)
        board.messages[f"{self.bus_type}_{self.bus_id}"]["out"].append(response_packet)
    def _handle_update_rtc(self, originator_id: int, payload: bytes):
        if len(payload) < 4: return
        seconds_since_epoch, = struct.unpack('>I', payload)
        time_tuple = seconds2timetuple(seconds_since_epoch)
        try:
            RTC().datetime(time_tuple)
            rtc_driver = hardware._drivers.get('rtc')
            if rtc_driver: rtc_driver.datetime(time_tuple)
        except Exception as e: print(f"[Message] Error al actualizar RTC: {e}")
    def _handle_module_ctrl(self, originator_id: int, payload: bytes):
        if len(payload) < 2: return
        module_id, action = struct.unpack('>BB', payload)
        module_name = ID_MODULE_MAP.get(module_id)
        if not module_name: return
        target_module = _modules.get(module_name)
        if not target_module: return
        if action == 0: target_module.stop()
        elif action == 1:
            target_module.resume()
            target_module.autostart = True
    def _handle_get_param(self, originator_id: int, payload: bytes):
        if not payload: return
        param_id = payload[0]
        path = PARAMETER_MAP.get(param_id)
        if not path or path.startswith("direct."): return
        value = config_manager.get(path)
        if value is None: return
        if isinstance(value, bool): dtype, packed_value = DTYPE_BOOL, struct.pack('>B', 1 if value else 0)
        elif isinstance(value, float): dtype, packed_value = DTYPE_FLOAT, struct.pack('>f', value)
        elif isinstance(value, int): dtype, packed_value = DTYPE_UINT, struct.pack('>I', value)
        else: return
        response_payload = bytes([param_id, dtype]) + packed_value
        response_packet = build_packet(originator_id, self.device_id, FRAME_TYPE_RESP, INITIAL_TTL, CMD_GET_PARAM, response_payload)
        board.messages[f"{self.bus_type}_{self.bus_id}"]["out"].append(response_packet)
    def _handle_set_param(self, originator_id: int, payload: bytes):
        if len(payload) < 3: return
        param_id, dtype, value_bytes = payload[0], payload[1], payload[2:]
        path = PARAMETER_MAP.get(param_id)
        if not path: return
        try:
            if dtype == DTYPE_BOOL: value = value_bytes[0] > 0
            elif dtype == DTYPE_UINT: value, = struct.unpack('>I', value_bytes)
            elif dtype == DTYPE_SINT: value, = struct.unpack('>i', value_bytes)
            elif dtype == DTYPE_FLOAT: value, = struct.unpack('>f', value_bytes)
            else: return
        except struct.error: return
        if path.startswith("direct."): self._execute_direct_action(path, value)
        else: config_manager.set(path, value, persistent=True)
    def _execute_direct_action(self, action_path: str, value):
        _, module_name, method_name = action_path.split('.')
        target_module = _modules.get(module_name)
        if target_module and hasattr(target_module, method_name):
            try: getattr(target_module, method_name)(value)
            except Exception as e: print(f"Error en acción directa: {e}")

class DataReporter(_BaseModule):
    # ... (Esta clase no necesita cambios) ...
    def __init__(self, config, name=None):
        super().__init__()
        self.report_interval_s = config.get("report_interval_s", 300)
        self.my_id = config_manager.get("SYSTEM_ID")
        self.bus_type = config.get("bus_type")
        self.bus_id = config.get("bus_id")
        if self.my_id == BASE_STATION_ID: self.stop()
        else: self.start(self.report_interval_s)
    def update(self):
        if self.check(): self._send_status_to_base()
    def _send_status_to_base(self):
        pressure = board.states.get("pressure", 0)
        temperature_scaled = int(board.states.get("temperature", 0) * 100)
        payload = struct.pack('>hh', temperature_scaled, pressure)
        packet = build_packet(BASE_STATION_ID, self.my_id, FRAME_TYPE_CMD, INITIAL_TTL, CMD_GET_SENSOR_STATUS, payload)
        board.messages[f"{self.bus_type}_{self.bus_id}"]["out"].append(packet)

# --- Funciones de Gestión de Módulos ---

def init():
    # --- CORREGIDO para usar config_manager ---
    MODULE_REGISTRY = config_manager.get("MODULE_REGISTRY", {})
    MODULE_CONFIGURATION = config_manager.get("MODULE_CONFIGURATION", {})
    ordered_modules = sorted(MODULE_REGISTRY.items(), key=lambda x: x[1]["order"])
    for name, module_info in ordered_modules:
        try:
            module_class = globals().get(module_info["class"])
            if module_class:
                config = MODULE_CONFIGURATION.get(name, {})
                _modules[name] = module_class(config, name)
                if not module_info["autostart"]: _modules[name].stop()
        except Exception as e:
            #sys.print_exception(e)
            if module_info["critical"]: break

def reinit():
    global _modules
    _modules.clear()
    init()
    print("[Modules] Módulos reinicializados.")

def update():
    for name, module in _modules.items():
        if module.autostart and module.polling:
            module.update()
            
# --- END OF FILE modules.py ---