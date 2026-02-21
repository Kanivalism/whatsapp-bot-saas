from typing import Any, Text, Dict, List
from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet
import requests
import os
import json
from datetime import datetime

# Configuración
ERPNEXT_URL = os.getenv("ERPNEXT_URL")
ERPNEXT_API_KEY = os.getenv("ERPNEXT_API_KEY")
ERPNEXT_API_SECRET = os.getenv("ERPNEXT_API_SECRET")

class ActionConsultarProductos(Action):
    def name(self) -> Text:
        return "action_consultar_productos"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        try:
            # Headers para ERPNext API
            headers = {
                "Authorization": f"token {ERPNEXT_API_KEY}:{ERPNEXT_API_SECRET}",
                "Content-Type": "application/json"
            }
            
            # Consultar productos
            response = requests.get(
                f"{ERPNEXT_URL}/api/resource/Item",
                headers=headers,
                params={
                    "fields": '["item_code","item_name","standard_rate","stock_qty"]',
                    "limit_page_length": 10
                },
                timeout=10
            )
            
            if response.status_code == 200:
                items = response.json().get("data", [])
                
                if items:
                    mensaje = "📦 *Productos disponibles:*\n\n"
                    for item in items[:10]:
                        mensaje += f"• *{item['item_name']}*\n"
                        mensaje += f"  💰 ${item.get('standard_rate', 0):.2f}\n"
                        if item.get('stock_qty'):
                            mensaje += f"  📊 Stock: {item['stock_qty']} unidades\n"
                        mensaje += "\n"
                    
                    mensaje += "¿Qué te gustaría ordenar?"
                    dispatcher.utter_message(text=mensaje)
                else:
                    dispatcher.utter_message(text="No tenemos productos registrados aún.")
            else:
                dispatcher.utter_message(text="Lo siento, no pude consultar los productos en este momento.")
        
        except Exception as e:
            print(f"Error: {e}")
            dispatcher.utter_message(text="Hubo un error al consultar productos. Por favor intenta más tarde.")
        
        return []

class ActionConsultarPrecio(Action):
    def name(self) -> Text:
        return "action_consultar_precio"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        producto = tracker.get_slot("producto")
        
        if not producto:
            dispatcher.utter_message(text="¿De qué producto quieres saber el precio?")
            return []
        
        try:
            headers = {
                "Authorization": f"token {ERPNEXT_API_KEY}:{ERPNEXT_API_SECRET}"
            }
            
            response = requests.get(
                f"{ERPNEXT_URL}/api/resource/Item",
                headers=headers,
                params={
                    "filters": json.dumps([["item_name", "like", f"%{producto}%"]]),
                    "fields": '["item_name","standard_rate","stock_qty"]',
                    "limit_page_length": 5
                },
                timeout=10
            )
            
            if response.status_code == 200:
                items = response.json().get("data", [])
                
                if items:
                    mensaje = f"💰 *Precios de '{producto}':*\n\n"
                    for item in items:
                        mensaje += f"• {item['item_name']}: ${item.get('standard_rate', 0):.2f}\n"
                    
                    dispatcher.utter_message(text=mensaje)
                else:
                    dispatcher.utter_message(text=f"No encontré '{producto}' en nuestro catálogo.")
            else:
                dispatcher.utter_message(text="No pude consultar el precio en este momento.")
        
        except Exception as e:
            print(f"Error: {e}")
            dispatcher.utter_message(text="Hubo un error. Por favor intenta nuevamente.")
        
        return []

class ActionAgregarAlCarrito(Action):
    def name(self) -> Text:
        return "action_agregar_al_carrito"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        producto = tracker.get_slot("producto")
        cantidad = int(tracker.get_slot("cantidad") or 1)
        carrito = tracker.get_slot("carrito") or []
        
        if not producto:
            dispatcher.utter_message(text="¿Qué producto quieres agregar?")
            return []
        
        # Agregar al carrito
        carrito.append({
            "producto": producto,
            "cantidad": cantidad,
            "timestamp": datetime.now().isoformat()
        })
        
        mensaje = f"✅ Agregado al carrito:\n"
        mensaje += f"• {cantidad}x {producto}\n\n"
        mensaje += "¿Deseas agregar algo más o confirmar tu pedido?"
        
        dispatcher.utter_message(text=mensaje)
        
        return [SlotSet("carrito", carrito)]

class ActionCalcularTotal(Action):
    def name(self) -> Text:
        return "action_calcular_total"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        carrito = tracker.get_slot("carrito") or []
        
        if not carrito:
            dispatcher.utter_message(text="Tu carrito está vacío.")
            return []
        
        try:
            headers = {
                "Authorization": f"token {ERPNEXT_API_KEY}:{ERPNEXT_API_SECRET}"
            }
            
            total = 0
            mensaje = "🛒 *Resumen de tu pedido:*\n\n"
            
            for item in carrito:
                # Buscar precio del producto
                response = requests.get(
                    f"{ERPNEXT_URL}/api/resource/Item",
                    headers=headers,
                    params={
                        "filters": json.dumps([["item_name", "like", f"%{item['producto']}%"]]),
                        "fields": '["item_name","standard_rate"]',
                        "limit_page_length": 1
                    },
                    timeout=10
                )
                
                if response.status_code == 200:
                    items = response.json().get("data", [])
                    if items:
                        precio = float(items[0].get("standard_rate", 0))
                        subtotal = precio * item['cantidad']
                        total += subtotal
                        
                        mensaje += f"• {item['cantidad']}x {items[0]['item_name']}\n"
                        mensaje += f"  ${precio:.2f} c/u = ${subtotal:.2f}\n\n"
            
            mensaje += "━━━━━━━━━━━━━━━\n"
            mensaje += f"💵 *TOTAL: ${total:.2f}*\n\n"
            mensaje += "Para confirmar tu pedido, proporciona tu dirección de entrega."
            
            dispatcher.utter_message(text=mensaje)
            
            return [SlotSet("total", total)]
        
        except Exception as e:
            print(f"Error: {e}")
            dispatcher.utter_message(text="Hubo un error al calcular el total.")
            return []

class ActionConfirmarPedido(Action):
    def name(self) -> Text:
        return "action_confirmar_pedido"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        carrito = tracker.get_slot("carrito")
        total = tracker.get_slot("total")
        direccion = tracker.get_slot("direccion_entrega")
        
        if not all([carrito, direccion]):
            dispatcher.utter_message(text="Necesito más información para confirmar tu pedido.")
            return []
        
        mensaje = f"✅ *¡Pedido confirmado!*\n\n"
        mensaje += f"📍 Dirección: {direccion}\n"
        mensaje += f"💵 Total: ${total:.2f}\n\n"
        mensaje += f"Tu pedido será enviado pronto. ¡Gracias por tu compra!"
        
        dispatcher.utter_message(text=mensaje)
        
        # Limpiar carrito
        return [
            SlotSet("carrito", None),
            SlotSet("total", None),
            SlotSet("direccion_entrega", None)
        ]

class ActionImprimirPedido(Action):
    def name(self) -> Text:
        return "action_imprimir_pedido"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        # Esta acción se implementará después cuando configuremos impresoras
        print("Impresión de pedido pendiente de configuración")
        return []

class ActionAgendarCita(Action):
    def name(self) -> Text:
        return "action_agendar_cita"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        # Esta acción se implementará después para clínicas
        dispatcher.utter_message(text="La funcionalidad de agendar citas estará disponible pronto.")
        return []

class ActionActualizarInventario(Action):
    def name(self) -> Text:
        return "action_actualizar_inventario"

    def run(self, dispatcher: CollectingDispatcher,
            tracker: Tracker,
            domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        
        # Esta acción se implementará después para plan VIP2
        print("Actualización de inventario pendiente")
        return []
```

---

## ⏸️ **PAUSA 6**

**Confirma que creaste estos archivos en la carpeta `rasa-actions/`:**
- ✅ `Dockerfile`
- ✅ `requirements.txt`
- ✅ `actions/__init__.py` (vacío)
- ✅ `actions/actions.py`

**Tu carpeta `rasa-actions/` ahora debería tener:**
```
rasa-actions/
├── Dockerfile
├── requirements.txt
└── actions/
    ├── __init__.py
    └── actions.py
