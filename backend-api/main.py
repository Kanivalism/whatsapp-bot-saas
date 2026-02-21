from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import PlainTextResponse, JSONResponse
from sqlalchemy import create_engine, Column, String, Integer, JSON, DateTime, Float, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime
from typing import Optional
import os
import requests
import redis
import json
import logging

# Configuración de logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Variables de entorno
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")
RASA_URL = os.getenv("RASA_URL", "http://rasa:5005")
WHATSAPP_API_TOKEN = os.getenv("WHATSAPP_API_TOKEN")
WHATSAPP_PHONE_ID = os.getenv("WHATSAPP_PHONE_ID")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN")

# Database
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Redis
redis_client = redis.from_url(REDIS_URL, decode_responses=True)

# FastAPI
app = FastAPI(
    title="WhatsApp SaaS Bot API",
    version="1.0.0",
    description="Backend API para bot de WhatsApp multi-tenant"
)

# ============================================
# MODELOS DE BASE DE DATOS
# ============================================

class Client(Base):
    __tablename__ = "clients"
    
    id = Column(String, primary_key=True)
    nombre_negocio = Column(String)
    tipo_negocio = Column(String)
    plan = Column(String)
    whatsapp_phone = Column(String, unique=True)
    erpnext_site = Column(String, nullable=True)
    printer_ip = Column(String, nullable=True)
    printer_port = Column(Integer, nullable=True)
    config = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)
    active = Column(Integer, default=1)

class Conversation(Base):
    __tablename__ = "conversations"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(String)
    phone_number = Column(String)
    message = Column(Text)
    response = Column(Text)
    intent = Column(String, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

# Crear tablas
Base.metadata.create_all(bind=engine)

# ============================================
# DEPENDENCY
# ============================================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ============================================
# ENDPOINTS
# ============================================

@app.get("/")
async def root():
    return {
        "message": "WhatsApp SaaS Bot API",
        "status": "running",
        "version": "1.0.0"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    try:
        # Check database
        db = SessionLocal()
        db.execute("SELECT 1")
        db.close()
        
        # Check Redis
        redis_client.ping()
        
        return {
            "status": "healthy",
            "database": "ok",
            "redis": "ok",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "error": str(e)}
        )

@app.get("/webhook")
async def verify_webhook(request: Request):
    """Verificación de webhook de WhatsApp"""
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    
    logger.info(f"Webhook verification: mode={mode}, token={token}")
    
    if mode == "subscribe" and token == WHATSAPP_VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return PlainTextResponse(challenge)
    
    logger.warning("Webhook verification failed")
    raise HTTPException(status_code=403, detail="Verification failed")

@app.post("/webhook")
async def receive_message(request: Request, db: Session = Depends(get_db)):
    """Recibir mensajes de WhatsApp"""
    try:
        body = await request.json()
        logger.info(f"Received webhook: {json.dumps(body, indent=2)}")
        
        # Extraer datos del mensaje
        entry = body.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])
        
        if not messages:
            return {"status": "ok", "message": "No messages"}
        
        message = messages[0]
        phone_number = message.get("from")
        message_text = message.get("text", {}).get("body", "")
        message_type = message.get("type")
        
        logger.info(f"Message from {phone_number}: {message_text}")
        
        # Identificar cliente (multi-tenant)
        phone_id = value.get("metadata", {}).get("phone_number_id")
        client = db.query(Client).filter(Client.whatsapp_phone == phone_id).first()
        
        if not client:
            logger.warning(f"Client not found for phone_id: {phone_id}")
            return {"status": "client_not_found"}
        
        logger.info(f"Client: {client.nombre_negocio} ({client.id})")
        
        # Guardar conversación
        conv = Conversation(
            client_id=client.id,
            phone_number=phone_number,
            message=message_text
        )
        db.add(conv)
        db.commit()
        
        # Enviar a Rasa con contexto del cliente
        try:
            rasa_response = requests.post(
                f"{RASA_URL}/webhooks/rest/webhook",
                json={
                    "sender": phone_number,
                    "message": message_text,
                    "metadata": {
                        "cliente_id": client.id,
                        "tipo_negocio": client.tipo_negocio,
                        "plan_subscripcion": client.plan,
                        "nombre_negocio": client.nombre_negocio
                    }
                },
                timeout=30
            )
            
            logger.info(f"Rasa response status: {rasa_response.status_code}")
            
            if rasa_response.status_code == 200:
                rasa_data = rasa_response.json()
                logger.info(f"Rasa data: {rasa_data}")
                
                for resp in rasa_data:
                    response_text = resp.get("text", "")
                    
                    if response_text:
                        # Enviar respuesta por WhatsApp
                        send_result = send_whatsapp_message(phone_number, response_text, client)
                        logger.info(f"WhatsApp send result: {send_result}")
                        
                        # Actualizar conversación
                        conv.response = response_text
                        conv.intent = resp.get("intent", "")
                        db.commit()
            else:
                logger.error(f"Rasa error: {rasa_response.text}")
        
        except Exception as e:
            logger.error(f"Error communicating with Rasa: {e}")
        
        return {"status": "ok"}
    
    except Exception as e:
        logger.error(f"Error in webhook: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

def send_whatsapp_message(to: str, message: str, client: Client):
    """Enviar mensaje por WhatsApp Cloud API"""
    if not WHATSAPP_API_TOKEN or not client.whatsapp_phone:
        logger.warning("WhatsApp credentials not configured")
        return {"error": "WhatsApp not configured"}
    
    url = f"https://graph.facebook.com/v18.0/{client.whatsapp_phone}/messages"
    
    headers = {
        "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json"
    }
    
    data = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": message}
    }
    
    try:
        response = requests.post(url, headers=headers, json=data, timeout=10)
        return response.json()
    except Exception as e:
        logger.error(f"Error sending WhatsApp message: {e}")
        return {"error": str(e)}

# ============================================
# API para gestión de clientes
# ============================================

@app.post("/api/clients")
async def create_client(client_data: dict, db: Session = Depends(get_db)):
    """Crear nuevo cliente (negocio)"""
    try:
        client = Client(**client_data)
        db.add(client)
        db.commit()
        db.refresh(client)
        return client
    except Exception as e:
        logger.error(f"Error creating client: {e}")
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/clients/{client_id}")
async def get_client(client_id: str, db: Session = Depends(get_db)):
    """Obtener cliente"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    return client

@app.get("/api/clients")
async def list_clients(db: Session = Depends(get_db)):
    """Listar todos los clientes"""
    clients = db.query(Client).filter(Client.active == 1).all()
    return clients

@app.put("/api/clients/{client_id}")
async def update_client(client_id: str, client_data: dict, db: Session = Depends(get_db)):
    """Actualizar cliente"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    
    for key, value in client_data.items():
        setattr(client, key, value)
    
    db.commit()
    db.refresh(client)
    return client

@app.delete("/api/clients/{client_id}")
async def delete_client(client_id: str, db: Session = Depends(get_db)):
    """Eliminar cliente (soft delete)"""
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    
    client.active = 0
    db.commit()
    return {"message": "Cliente eliminado"}

@app.get("/api/conversations/{client_id}")
async def get_conversations(
    client_id: str,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Obtener conversaciones de un cliente"""
    conversations = db.query(Conversation).filter(
        Conversation.client_id == client_id
    ).order_by(Conversation.timestamp.desc()).limit(limit).all()
    
    return conversations

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

---

## ⏸️ **PAUSA 7**

**Confirma que creaste estos archivos en la carpeta `backend-api/`:**
- ✅ `Dockerfile`
- ✅ `requirements.txt`
- ✅ `main.py`

**Tu carpeta `backend-api/` ahora debería tener:**
```
backend-api/
├── Dockerfile
├── requirements.txt
└── main.py
