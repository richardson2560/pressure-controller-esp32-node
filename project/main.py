import time, gc, json, sys
import hardware, board, modules
from config import config_manager
from pubsub import event_manager

def handle_config_change(key, value):
    """
    Callback que se ejecuta cuando ConfigManager publica un cambio.
    Decide qué sistema necesita ser reinicializado.
    """
    print(f"\n[Main] Se detectó un cambio de configuración en '{key}'.")
    
    if key.startswith('HARDWARE_CONFIGURATION'):
        hardware.reinit()
        # Dado que el hardware cambió, los módulos que dependen de él también deben reiniciarse.
        modules.reinit()
        
    elif key.startswith('MODULE_CONFIGURATION') or key.startswith('MODULE_REGISTRY'):
        modules.reinit()

# --- setup ---
config_manager.load()
event_manager.subscribe('config:updated', handle_config_change)

gc.enable()
hardware.init()
modules.init()
print("SYSTEM_ID:",config_manager.get("SYSTEM_ID"))
print("SYSTEM_NAME:",config_manager.get("SYSTEM_NAME"))
#print(modules._modules)

# --- loop ---
try:
    while True:
        hardware.update()
        hardware.process_irq_events()
        modules.update()
        time.sleep_ms(10)
        #print(board.messages)
        if time.ticks_ms() % 60000 < 10:
            gc.collect()

except KeyboardInterrupt:
    print("\n[main.py] Execution interrupted forcefully.")
except Exception as e:
    print("\n[main.py] FATAL UNHANDLED EXCEPTION")
    sys.print_exception(e)
finally:
    gc.collect()