HARDWARE_CONFIGURATION = {
    "i2c": {
        "1": { "sda": 21, "scl": 22, "freq": 400000 },
    },
    "uart": {
        "1": { "tx": 17, "rx": 16, "baudrate": 9600 },
    },
    "devices": {
        "rtc": { "driver": "DS3231", "bus_type": "i2c", "bus_id": "1", "address": 0x68 },
        "display": { "driver": "LCD_I2C", "bus_type": "i2c", "bus_id": "1", "address": 0x27, "rows": 2, "cols": 16 },
        "wake_up_button": { "driver": "IRQ_Pin", "pin": 32, "mode": "IN", "pull": "PULL_UP"},
        "primary_adc":  { "driver": "ADC_Pin", "pin": 34, "attenuation": "ATTN_11DB"},
        #"lora_m0":      { "driver": "GPIO_Pin", "pin": 19, "mode": "OUT", "initial_value": 0 },
        "lora_module":  { "driver": "LoRa_E220", "model": "900T30D", "bus_type": "uart", "bus_id": "1", 
                          "m0_pin": 19, "m1_pin": 18, "aux_pin":5}
    },
}

MODULE_CONFIGURATION = {
    "clock":            { "device_key": "rtc", "drift_check_interval_s": 60, "max_drift_s": 10 },
    "display":          { "device_key": "display", "refresh_interval_s": 0.1, "boot_duration_s": 5, "backlight_timeout_s": 60, "rows": 2, "cols": 16, "subs": "wake_up_button"},
    "temperature":      { "device_key": "rtc", "read_interval_s": 5 },
    "analog_adc_1":     { "device_key": "primary_adc", "read_interval_s": 0.05, "median_filter_size": 11, "adc_max_value": 4095.0},
    "pressure_1":       { "V_TO_MPA_SLOPE": 12.5, "V_TO_MPA_INTERCEPT": -1.25, "PSI_PER_MPA": 145.038, "subs":"analog_adc_1"},
    #"routing":          { "hello_interval_s": 30, "route_update_interval_s": 600, "bus_type": "uart", "bus_id": "1"},
    #"message":          { "read_interval_s": 0.1 , "bus_type": "uart", "bus_id": "1"},
    "data_reporter":    { "report_interval_s": 30 , "bus_type": "uart", "bus_id": "1"},
    "lora_tx":          { "device_key": "lora_module", "check_interval_s": 0.1, "bus_type": "uart", "bus_id": "1"},
}

MODULE_REGISTRY = {
    "clock":            { "class": "Clock",         "order": 10, "autostart": True, "critical": True  },
    "display":          { "class": "Display",       "order": 15, "autostart": True, "critical": False },
    "temperature":      { "class": "Temperature",   "order": 20, "autostart": True, "critical": False },
    "analog_adc_1":     { "class": "AnalogInput",   "order": 25, "autostart": True, "critical": False },
    "pressure_1":       { "class": "Pressure",      "order": 30, "autostart": True, "critical": False },
    #"routing":          { "class": "Routing",       "order": 35, "autostart": True, "critical": True  },
    #"message":          { "class": "MessageLora",   "order": 45, "autostart": True, "critical": True  },
    "data_reporter":    { "class": "DataReporter",  "order": 50, "autostart": True, "critical": False },
    "lora_tx":          { "class": "LoraTX",        "order": 40, "autostart": True, "critical": False },
}

STORAGE_PATH = 'storage.json'
DEFAULT_LOG_LEVEL = 'INFO'
SYSTEM_NAME = 'Nodo01'
SYSTEM_ID = 1
BASE_STATION_ID = 0

print("[env.py] Project configuration loaded.")