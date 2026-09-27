"""
Delta DVP14SS2 PLC — Modbus ASCII Communication Module
RS485 | 9600 baud | 7 data bits | Even parity | 1 stop bit (7E1) | ASCII framing
"""

import threading
import time
import logging
from typing import Optional, Dict, Any

from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)

try:
    from pymodbus.client import ModbusSerialClient
    PYMODBUS_OK = True
except ImportError:
    PYMODBUS_OK = False
    ModbusSerialClient = None

# ── Modbus address constants ───────────────────────────────────────────────────
# Coils (M relay base: M0 = 0x0800 = 2048)
COIL_LABEL_ENABLE  = 2048 + 2   # M2
COIL_COUNTER_RESET = 2048 + 3   # M3
COIL_ALARM_SIGNAL  = 2048 + 8   # M8
COIL_M10           = 2048 + 10  # M10 — carton limit warning 1
COIL_CONVEYOR_STOP = 2048 + 11  # M11
COIL_M12           = 2048 + 12  # M12 — carton limit warning 2
COIL_M15           = 2048 + 15  # M15 — batch activation signal
COIL_M20           = 2048 + 20  # M20 — mismatch signal (sent after next frame)
COIL_M21           = 2048 + 21  # M21 — match signal (valid image & correct decision)
COIL_M43           = 2048 + 43  # M43 — Air Pressure Low warning

# Registers (D register base: D0 = 0x1000 = 4096)
REG_SPEED           = 4096 + 416  # D416 = 4512
REG_CARTON_COUNT    = 4096 + 412  # D412 = 4508 — number of cartons
REG_DELAY_TIME      = 4096 + 422  # D422 = 4518
REG_WRONG_COUNT     = 4096 + 420  # D420 = 4516
REG_GOOD_COUNT      = 4096 + 422  # D422 = 4518
REG_TOTAL_COUNT     = 4096 + 423  # D423 = 4519
REG_REJECT_ON_DELAY = 4096 + 425  # D425 = 4521 — Reject on delay

# C236 current value — 32-bit counter, C200 group base = 3784
# C236 offset = (236 - 200) * 2 = 72  →  3784 + 72 = 3856
REG_COUNTER_LO = 3856   # low  word
REG_COUNTER_HI = 3857   # high word

# Batch coil read: M2 (2050) → M43 (2091) = 42 bits
_COIL_START = COIL_LABEL_ENABLE
_COIL_COUNT = COIL_M43 - COIL_LABEL_ENABLE + 1  # 42

# Batch register read: D412 (4508) → D425 (4521) = 14 words
_REG_START = REG_CARTON_COUNT
_REG_COUNT = REG_REJECT_ON_DELAY - REG_CARTON_COUNT + 1  # 14


class PlcCommunicator(QObject):
    """
    Thread-safe Delta DVP Modbus RTU (ASCII) communicator.
    Polls PLC in a background daemon thread and delivers
    results to Qt main thread via signals.
    """

    connection_changed = Signal(bool, str)   # (is_connected, message)
    values_updated     = Signal(dict)         # process data dict
    error_occurred     = Signal(str)          # error string
    carton_warning     = Signal(int)          # D412 value when M10 or M12 fires
    plc_alarm_warning  = Signal(str)          # rising edge on M10 or M12
    plc_signal_sent    = Signal()             # emitted on every write (for LED blink)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._client: Optional[Any] = None
        self._connected = False
        self._poll_thread: Optional[threading.Thread] = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        
        from core.config import settings
        self.slave_id = settings.PLC_SLAVE_ID
        
        # Track previous states for rising-edge warning triggers
        self._prev_m10 = False
        self._prev_m12 = False
        self._prev_m43 = False

    def _get_modbus_kwargs(self) -> dict:
        """
        Dynamically determine the correct parameter name for Modbus client unit/slave ID.
        Different versions of pymodbus use 'unit', 'slave', or 'device_id'.
        """
        if not self._client:
            return {}
        try:
            import inspect
            sig = inspect.signature(self._client.read_coils)
            if 'device_id' in sig.parameters:
                return {'device_id': self.slave_id}
            elif 'slave' in sig.parameters:
                return {'slave': self.slave_id}
            elif 'unit' in sig.parameters:
                return {'unit': self.slave_id}
        except Exception:
            pass
        return {'slave': self.slave_id}

    # ── Connection ────────────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect_plc(self, port: str, baudrate: int = 9600) -> bool:
        if not PYMODBUS_OK:
            self.error_occurred.emit(
                "pymodbus not installed.\nRun:  pip install pymodbus pyserial"
            )
            return False
        if self._connected:
            self.disconnect_plc()

        try:
            self._client = ModbusSerialClient(
                port=port,
                baudrate=baudrate,
                bytesize=7,
                parity='E',
                stopbits=1,
                timeout=1,
                framer="ascii"
            )
            ok = self._client.connect()
        except Exception as exc:
            self.error_occurred.emit(f"Cannot open {port}: {exc}")
            return False

        if not ok:
            self.error_occurred.emit(f"Port {port} could not be opened.")
            return False

        self._connected = True
        self.connection_changed.emit(True, f"Connected → {port} [9600-7-E-1 ASCII]")
        self._start_poll()
        return True

    def disconnect_plc(self):
        self._stop_evt.set()
        if self._poll_thread and self._poll_thread.is_alive():
            self._poll_thread.join(timeout=3)
        self._stop_evt.clear()
        if self._client:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None
        self._connected = False
        self.connection_changed.emit(False, "Disconnected")

    # ── Write helpers ─────────────────────────────────────────────────────────

    def write_coil(self, address: int, value: bool):
        """FC05 — write single coil."""
        if not self._connected or not self._client:
            return
        with self._lock:
            try:
                # pymodbus 3+ uses slave= or device_id=
                self._client.write_coil(address, value, **self._get_modbus_kwargs())
                self.plc_signal_sent.emit()
            except Exception as e:
                logger.error(f"Write coil error: {e}")

    def pulse_coil(self, address: int, ms: int = 300):
        """Write True then False after `ms` ms — momentary push-button."""
        def _run():
            self.write_coil(address, True)
            time.sleep(ms / 1000.0)
            self.write_coil(address, False)
        threading.Thread(target=_run, daemon=True).start()

    def write_register(self, address: int, value: int):
        """FC06 — write single 16-bit register."""
        if not self._connected or not self._client:
            return
        value = max(0, min(65535, int(value)))
        with self._lock:
            try:
                self._client.write_register(address, value, **self._get_modbus_kwargs())
                self.plc_signal_sent.emit()
            except Exception as e:
                logger.error(f"Write register error: {e}")

    def write_carton_count(self, count: int):
        """Write the number-of-cartons value to D412 (4508)."""
        self.write_register(REG_CARTON_COUNT, count)

    def write_label_count(self, count: int):
        """Alias for write_carton_count."""
        self.write_carton_count(count)

    def write_reject_on_delay(self, value: int):
        """Write Reject On Delay to D425 (4521)."""
        self.write_register(REG_REJECT_ON_DELAY, value)

    def signal_mismatch(self):
        """Pulse M20 (2068) to signal a mismatch to the PLC."""
        self.pulse_coil(COIL_M20, ms=300)

    def signal_match(self):
        """Pulse M21 (2069) to signal a match (valid image & correct decision) to the PLC."""
        self.pulse_coil(COIL_M21, ms=300)

    def activate_m15(self):
        """Pulse coil M15 (2063) to notify PLC of batch activation."""
        self.pulse_coil(COIL_M15, ms=300)

    def reset_counters(self):
        """Pulse M3 (2051) to reset counters on the PLC."""
        self.pulse_coil(COIL_COUNTER_RESET, ms=300)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _start_poll(self):
        self._stop_evt.clear()
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="plc_poll"
        )
        self._poll_thread.start()

    def _poll_loop(self):
        while not self._stop_evt.is_set():
            data = self._read_all()
            if data is not None:
                self.values_updated.emit(data)
            else:
                # We don't immediately disconnect on one error, but maybe after retries
                pass
            self._stop_evt.wait(0.5)

    def _read_all(self) -> Optional[Dict]:
        if not self._client:
            return None
            
        try:
            # 1. Coils M2–M20 (21 bits)
            with self._lock:
                cr = self._client.read_coils(
                    address=_COIL_START, count=_COIL_COUNT, **self._get_modbus_kwargs())
            if cr.isError():
                return None
            bits = cr.bits

            def bit(addr: int) -> bool:
                return bool(bits[addr - _COIL_START])

            # Give waiting write threads a chance to acquire lock
            time.sleep(0.01)

            # 2. Registers D412–D423 (12 words)
            with self._lock:
                rr = self._client.read_holding_registers(
                    address=_REG_START, count=_REG_COUNT, **self._get_modbus_kwargs())
            if rr.isError():
                return None

            # Give waiting write threads a chance to acquire lock
            time.sleep(0.01)

            # 3. C236 — 32-bit counter (2 registers)
            with self._lock:
                cr2 = self._client.read_holding_registers(
                    address=REG_COUNTER_LO, count=2, **self._get_modbus_kwargs())
            counter = 0
            if not cr2.isError():
                lo = cr2.registers[0]
                hi = cr2.registers[1]
                counter = (hi << 16) | lo

            # Give waiting write threads a chance to acquire lock
            time.sleep(0.01)

            # 4. C0 (Count IN) and C2 (Count Out) at address 3584, count 3
            with self._lock:
                cr_c = self._client.read_holding_registers(
                    address=3584, count=3, **self._get_modbus_kwargs())
            c0_val = 0
            c2_val = 0
            if not cr_c.isError():
                c0_val = cr_c.registers[0]
                c2_val = cr_c.registers[2]

            carton_count = rr.registers[REG_CARTON_COUNT - _REG_START]
            wrong_count = rr.registers[REG_WRONG_COUNT - _REG_START]
            good_count = rr.registers[REG_GOOD_COUNT - _REG_START]
            total_count = rr.registers[REG_TOTAL_COUNT - _REG_START]
            reject_on_delay = rr.registers[REG_REJECT_ON_DELAY - _REG_START]
            
            m10_high = bit(COIL_M10)
            m12_high = bit(COIL_M12)
            m43_high = bit(COIL_M43)

            # Detect rising edge (transition from 0 to 1) for M10, M12, and M43
            m10_rose = m10_high and not self._prev_m10
            m12_rose = m12_high and not self._prev_m12
            m43_rose = m43_high and not self._prev_m43

            self._prev_m10 = m10_high
            self._prev_m12 = m12_high
            self._prev_m43 = m43_high

            # Emit carton warning only on rising edges
            if m10_rose:
                self.carton_warning.emit(carton_count)
                self.plc_alarm_warning.emit("Consecutive Error")
            if m12_rose:
                self.carton_warning.emit(carton_count)
                self.plc_alarm_warning.emit("Reject Not Exist")
            if m43_rose:
                self.plc_alarm_warning.emit("Air Pressure Low")

            return {
                "label_enable":    bit(COIL_LABEL_ENABLE),
                "counter_reset":   bit(COIL_COUNTER_RESET),
                "alarm_signal":    bit(COIL_ALARM_SIGNAL),
                "m10":             m10_high,
                "conveyor_stop":   bit(COIL_CONVEYOR_STOP),
                "m12":             m12_high,
                "m20":             bit(COIL_M20),
                "m21":             bit(COIL_M21),
                "m43":             m43_high,
                "air_pressure_low": m43_high,
                "carton_count":    carton_count,
                "speed":           rr.registers[REG_SPEED - _REG_START],
                "delay_time":      rr.registers[REG_DELAY_TIME - _REG_START],
                "counter":         counter,
                "wrong_count":     wrong_count,
                "good_count":      good_count,
                "total_count":     total_count,
                "reject_on_delay": reject_on_delay,
                "count_in":        c0_val,
                "count_out":       c2_val,
            }
        except Exception as exc:
            logger.error("Poll error: %s", exc)
            return None

    def auto_detect_port(self, baudrate: int = 9600) -> Optional[str]:
        return self.auto_detect_and_connect(baudrate)

    def auto_detect_and_connect(self, baudrate: int = 9600) -> Optional[str]:
        """
        Scan available serial ports, connect to each and test if it is the PLC.
        If the PLC is found, keep the connection open and return the port.
        If not, disconnect and try the next port.
        """
        if not PYMODBUS_OK:
            logger.error("pymodbus not installed. Auto detection bypassed.")
            return None

        import serial.tools.list_ports
        ports = [p.device for p in serial.tools.list_ports.comports()]
        logger.info(f"Available ports for PLC detection: {ports}")
        print(f"[PLC Auto-detect] Found ports: {ports}")
        
        for port in ports:
            logger.info(f"Testing port {port}...")
            print(f"[PLC Auto-detect] Testing port {port}...")
            try:
                # Try to connect normally using connect_plc
                if self.connect_plc(port, baudrate):
                    # We do a direct test read using the client to verify
                    with self._lock:
                        if self._client:
                            import inspect
                            sig = inspect.signature(self._client.read_holding_registers)
                            kwargs = {}
                            if 'device_id' in sig.parameters:
                                kwargs = {'device_id': self.slave_id}
                            elif 'slave' in sig.parameters:
                                kwargs = {'slave': self.slave_id}
                            elif 'unit' in sig.parameters:
                                kwargs = {'unit': self.slave_id}
                            else:
                                kwargs = {'slave': self.slave_id}
                            
                            # Read holding registers D412 (4508)
                            result = self._client.read_holding_registers(address=4508, count=1, **kwargs)
                            if result and not result.isError():
                                logger.info(f"PLC successfully detected and connected on port {port}!")
                                print(f"[PLC Auto-detect] PLC successfully detected and connected on port {port}!")
                                return port
                    
                    # If not successful, disconnect
                    logger.info(f"Failed to read from PLC on port {port}. Disconnecting...")
                    print(f"[PLC Auto-detect] Failed to read from PLC on port {port}. Disconnecting...")
                    self.disconnect_plc()
            except Exception as e:
                logger.error(f"Error testing port {port}: {e}")
                self.disconnect_plc()
                
        logger.info("PLC not found on any available serial port.")
        print("[PLC Auto-detect] PLC not found on any available serial port.")
        return None

