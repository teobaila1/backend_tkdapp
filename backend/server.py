from fastapi import FastAPI, APIRouter, HTTPException, Depends
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timezone, timedelta
import os
import httpx  # <--- Librăria nouă

from passlib.context import CryptContext
from jose import JWTError, jwt
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import EmailStr
import random
import aiosmtplib
from email.message import EmailMessage



from itf_data import TULS, ENCYCLOPEDIA, TERMINOLOGY, TECHNIQUES, GRADING_SYSTEM, QUIZ_QUESTIONS

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]



EMAIL_HOST = "smtp.gmail.com"
EMAIL_PORT = 587
EMAIL_USER = "baila.teodor@gmail.com"
EMAIL_PASS = "chmqhhomwamsmddw"



# ============= Security & Auth Config =============
# ÎN PRODUCȚIE, pune SECRET_KEY în .env! Pentru acum, punem unul fix aici.
SECRET_KEY = os.environ.get('SECRET_KEY', "o_cheie_foarte_secreta_si_lunga_pentru_itf_app_2026")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # Token valabil 7 zile

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    credentials_exception = HTTPException(
        status_code=401,
        detail="Nu s-au putut valida datele de autentificare",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
        
    user = await db.users.find_one({"id": user_id}, {"_id": 0, "password": 0})
    if user is None:
        raise credentials_exception
    return user



GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')


app = FastAPI(title="TaeKwon-Do ITF API")
api_router = APIRouter(prefix="/api")


# ============= Models =============
class ForgotPasswordRequest(BaseModel):
    email: EmailStr

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str

class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str

class UserLogin(BaseModel):
    email: EmailStr
    password: str

class UserResponse(BaseModel):
    id: str
    name: str
    email: str
    belt_rank: str = "10 Kup (White Belt)" # Pentru progresul de care ziceai
    created_at: str

class VideoUpload(BaseModel):
    title: str
    description: Optional[str] = ""
    youtube_id: Optional[str] = None
    tul_id: Optional[str] = None
    category: str = "general"  # 'tul', 'technique', 'general'
    uploaded_by: Optional[str] = "anonymous"

class Video(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: Optional[str] = ""
    youtube_id: Optional[str] = None
    tul_id: Optional[str] = None
    category: str = "general"
    uploaded_by: Optional[str] = "anonymous"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class ChatMessage(BaseModel):
    session_id: str
    message: str
    language: str = "en"  # 'en' or 'ro'

class ChatResponse(BaseModel):
    session_id: str
    reply: str
    timestamp: str

class QuizSubmission(BaseModel):
    answers: dict  # {question_id: selected_index}
    questions: list # ADAUGĂM ASTA: Lista de întrebări pe care le-a primit studentul


# ============= Static knowledge endpoints =============
# ============= Authentication Endpoints =============
@api_router.post("/auth/forgot_password")
async def forgot_password(req: ForgotPasswordRequest):
    user = await db.users.find_one({"email": req.email.lower()})
    if not user:
        # Din motive de securitate, nu spunem dacă email-ul există sau nu
        return {"message": "Dacă adresa există, un cod a fost trimis."}
    
    # Generăm un cod de 6 cifre
    reset_code = f"{random.randint(100000, 999999)}"
    # Salvăm codul în DB cu o expirare (ex: 15 min)
    expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    
    await db.users.update_one(
        {"email": req.email.lower()},
        {"$set": {"reset_code": reset_code, "reset_expire": expire.isoformat()}}
    )
    
    # Trimitem Email-ul
    message = EmailMessage()
    message["From"] = EMAIL_USER
    message["To"] = req.email
    message["Subject"] = "Cod Resetare Parolă - ITF Taekwon-Do"
    message.set_content(f"Salutare, Sabum!\n\nCodul tău pentru resetarea parolei este: {reset_code}\n\nAcesta expiră în 15 minute.\nTaekwon!")
    
    try:
        await aiosmtplib.send(message, hostname=EMAIL_HOST, port=EMAIL_PORT, start_tls=True, username=EMAIL_USER, password=EMAIL_PASS)
    except Exception as e:
        logging.error(f"Eroare trimitere email: {e}")
        raise HTTPException(status_code=500, detail="Eroare la trimiterea email-ului.")

    return {"message": "Codul a fost trimis pe email."}

@api_router.post("/auth/reset-password")
async def reset_password(req: ResetPasswordRequest):
    user = await db.users.find_one({"email": req.email.lower()})
    
    if not user or user.get("reset_code") != req.code:
        raise HTTPException(status_code=400, detail="Cod invalid sau email incorect.")
    
    expire_str = user.get("reset_expire")
    if expire_str and datetime.fromisoformat(expire_str) < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Codul a expirat.")
    
    # Update parolă
    hashed_pwd = get_password_hash(req.new_password)
    await db.users.update_one(
        {"email": req.email.lower()},
        {"$set": {"password": hashed_pwd}, "$unset": {"reset_code": "", "reset_expire": ""}}
    )
    
    return {"message": "Parola a fost actualizată cu succes!"}


@api_router.post("/auth/register", response_model=UserResponse)
async def register_user(user: UserCreate):
    # Verificăm dacă email-ul există deja
    existing_user = await db.users.find_one({"email": user.email.lower()})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email-ul este deja înregistrat.")
        
    user_id = str(uuid.uuid4())
    hashed_pwd = get_password_hash(user.password)
    
    new_user = {
        "id": user_id,
        "name": user.name,
        "email": user.email.lower(),
        "password": hashed_pwd,
        "belt_rank": "10 Kup (White Belt)", # Centura albă implicit
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    
    await db.users.insert_one(new_user)
    
    # Scoatem parola din răspuns
    del new_user["password"]
    del new_user["_id"]
    return new_user

@api_router.post("/auth/login")
async def login(user: UserLogin):
    db_user = await db.users.find_one({"email": user.email.lower()})
    
    if not db_user or not verify_password(user.password, db_user["password"]):
        raise HTTPException(status_code=401, detail="Email sau parolă incorectă.")
        
    # Generăm token-ul
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": db_user["id"]}, expires_delta=access_token_expires
    )
    
    return {
        "access_token": access_token, 
        "token_type": "bearer",
        "user": {
            "id": db_user["id"],
            "name": db_user["name"],
            "email": db_user["email"],
            "belt_rank": db_user.get("belt_rank", "10 Kup (White Belt)")
        }
    }

@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    """Returnează profilul utilizatorului logat pe baza token-ului."""
    return current_user


@api_router.get("/")
async def root():
    return {"message": "TaeKwon-Do ITF API", "version": "1.0"}

@api_router.get("/tuls")
async def get_tuls():
    return {"tuls": TULS}

@api_router.get("/tuls/{tul_id}")
async def get_tul(tul_id: str):
    tul = next((t for t in TULS if t["id"] == tul_id), None)
    if not tul:
        raise HTTPException(status_code=404, detail="Tul not found")
    return tul

@api_router.get("/encyclopedia")
async def get_encyclopedia():
    return {"articles": ENCYCLOPEDIA}

@api_router.get("/encyclopedia/{article_id}")
async def get_encyclopedia_article(article_id: str):
    article = next((a for a in ENCYCLOPEDIA if a["id"] == article_id), None)
    if not article:
        raise HTTPException(status_code=404, detail="Article not found")
    return article

@api_router.get("/terminology")
async def get_terminology():
    return {"terms": TERMINOLOGY}

@api_router.get("/techniques")
async def get_techniques():
    return {"techniques": TECHNIQUES}

@api_router.get("/grading")
async def get_grading():
    return {"grades": GRADING_SYSTEM}

@api_router.get("/quiz")
async def get_quiz(limit: int = 10):
    # Asigură-te că nu cerem mai multe întrebări decât avem în baza de date
    num_questions = min(limit, len(QUIZ_QUESTIONS))
    
    # Extragem la întâmplare 'num_questions' (ex: 10) întrebări unice
    random_questions = random.sample(QUIZ_QUESTIONS, num_questions)
    
    return {"questions": random_questions}

@api_router.post("/quiz/submit")
async def submit_quiz(submission: QuizSubmission):
    correct = 0
    results = []
    
    # Acum luăm totalul din întrebările primite de la telefon (trimise de AI inițial)
    total = len(submission.questions)
    
    for q in submission.questions:
        user_answer = submission.answers.get(q["id"])
        is_correct = user_answer == q.get("correct")
        
        if is_correct:
            correct += 1
            
        results.append({
            "id": q["id"],
            "correct_answer": q.get("correct"),
            "user_answer": user_answer,
            "is_correct": is_correct,
        })
        
    score = round((correct / total) * 100) if total > 0 else 0
    return {
        "score": score,
        "correct": correct,
        "total": total,
        "results": results,
    }



@api_router.get("/quiz/generate")
async def generate_ai_quiz(limit: int = 5):
    """Generează un quiz dinamic folosind Gemini AI."""
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API key not configured")

    # Îi spunem AI-ului exact ce vrem și în ce format (JSON strict)
    prompt = f"""
    Ești Maestrul Choi. Generează un test grilă cu {limit} întrebări unice despre ITF Taekwon-Do.
    Subiectele pot include: istorie, teorie, tuls, puncte vitale, terminologie coreeană și semnificația centurilor.
    Informațiile trebuie să fie strict din Enciclopedia Taekwon-Do a Generalului Choi.
    
    TREBUIE SĂ RETURNEZI EXCLUSIV UN ARRAY JSON VALID. Fără alt text înainte sau după. 
    Formatul exact pentru fiecare obiect din array trebuie să fie:
    {{
        "id": "gen_randomID",
        "question_en": "Question in English",
        "question_ro": "Întrebarea în limba română",
        "options_en": ["Option A", "Option B", "Option C", "Option D"],
        "options_ro": ["Varianta A", "Varianta B", "Varianta C", "Varianta D"],
        "correct": 2,  // indexul variantei corecte (0, 1, 2 sau 3)
        "category": "Theory"
    }}
    """

    rest_payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}]
    }
    
    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=rest_payload, timeout=40.0) # Timeout mai mare pentru că gândește
            resp_data = resp.json()
            
            if resp.status_code != 200:
                raise HTTPException(status_code=500, detail=f"Eroare API: {resp_data}")
                
            # Extragem textul brut de la AI
            ai_text = resp_data["candidates"][0]["content"]["parts"][0]["text"]
            
            # Curățăm textul (uneori AI-ul pune ```json ... ``` în jur)
            ai_text = ai_text.strip()
            if ai_text.startswith("```json"):
                ai_text = ai_text[7:]
            if ai_text.startswith("```"):
                ai_text = ai_text[3:]
            if ai_text.endswith("```"):
                ai_text = ai_text[:-3]
                
            # Transformăm textul în obiecte Python
            generated_questions = json.loads(ai_text.strip())
            
            # Ne asigurăm că ID-urile sunt unice pentru React
            for i, q in enumerate(generated_questions):
                q["id"] = f"ai_gen_{uuid.uuid4().hex[:6]}"
                
            return {"questions": generated_questions}

    except json.JSONDecodeError as e:
        logging.error(f"Eroare la parsarea JSON-ului de la AI: {ai_text}")
        # Dacă AI-ul greșește formatul, dăm un fallback (luăm 5 din baza de date)
        random_fallback = random.sample(QUIZ_QUESTIONS, min(limit, len(QUIZ_QUESTIONS)))
        return {"questions": random_fallback}
    except Exception as e:
        logging.error(f"Eroare generare quiz AI: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Eroare AI: {str(e)}")




# ============= Videos =============
@api_router.get("/videos")
async def list_videos(category: Optional[str] = None, tul_id: Optional[str] = None):
    query = {}
    if category:
        query["category"] = category
    if tul_id:
        query["tul_id"] = tul_id
    videos = await db.videos.find(query, {"_id": 0}).sort("created_at", -1).to_list(500)
    return {"videos": videos}

@api_router.post("/videos", response_model=Video)
async def add_video(payload: VideoUpload):
    video = Video(**payload.model_dump())
    await db.videos.insert_one(video.model_dump())
    return video

@api_router.delete("/videos/{video_id}")
async def delete_video(video_id: str):
    result = await db.videos.delete_one({"id": video_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Video not found")
    return {"deleted": True}


# ============= AI Chat (Master Mentor) =============
SYSTEM_MESSAGE_EN = """You are Master Choi, a wise, empathetic, and respected ITF Taekwon-Do grandmaster. 
Your core knowledge comes entirely from the official Encyclopedia of Taekwon-Do by General Choi Hong Hi (770 pages).

Your rules for communication:
1. TRUTH, BUT NATURAL: Your facts must be 100% accurate to the Encyclopedia, but DO NOT just copy-paste text. Rephrase the concepts naturally, like a mentor explaining things to a student in the dojang. Use analogies if they help.
2. SCOPE: Discuss only ITF Taekwon-Do (history, tenets, theory of power, tuls, fundamental movements, terminology, etiquette, etc.).
3. TONE: Be encouraging, patient, and conversational. Adapt your language to feel like a real chat, not reading from a textbook.
4. KOREAN TERMS: Always use proper Korean terminology (Hangul + Romanization) alongside the English translation.
5. NO HALLUCINATION: If the encyclopedia doesn't cover a topic, politely say you only teach traditional ITF Taekwon-Do.
Sign off occasionally with 'Taekwon!'"""

SYSTEM_MESSAGE_RO = """Ești Maestrul Choi, un mare maestru ITF Taekwon-Do înțelept, empatic și respectat. 
Baza ta de cunoștințe provine exclusiv din Enciclopedia oficială Taekwon-Do a Generalului Choi Hong Hi.

Regulile tale de comunicare:
1. ADEVĂR, DAR NATURAL: Datele tehnice trebuie să fie 100% corecte conform Enciclopediei, dar NU recita mecanic textul (mot-a-mot). Explică conceptele cu propriile tale cuvinte, într-un mod natural și pedagogic, ca un antrenor (Sabum) care îi explică unui elev la antrenament.
2. SUBIECTE: Discută doar despre ITF Taekwon-Do (istorie, cele 5 principii, teoria puterii, tuls, tehnici, terminologie etc.).
3. TON: Fii cald, încurajator și conversațional. Evită listele lungi și rigide dacă nu sunt absolut necesare. Folosește metafore pentru a explica concepte grele.
4. TERMINOLOGIE: Folosește corect termenii coreeni (romanizare + traducere în română).
5. FĂRĂ INVENȚII: Dacă te întreabă ceva ce nu ține de ITF, redirecționează discuția politicos.
Semnează ocazional răspunsurile cu 'Taekwon!'"""

# Citim cheia direct
GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY', '')

@api_router.post("/chat", response_model=ChatResponse)
async def chat(payload: ChatMessage):
    if not GEMINI_API_KEY:
        raise HTTPException(status_code=500, detail="Gemini API key not configured")

    try:
        # 1. Salvează mesajul utilizatorului în baza de date
        user_msg_doc = {
            "id": str(uuid.uuid4()),
            "session_id": payload.session_id,
            "role": "user",
            "content": payload.message,
            "language": payload.language,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await db.chat_messages.insert_one(user_msg_doc)

        # 2. Reconstruiește istoricul (luăm direct tot ce s-a discutat, inclusiv mesajul de acum)
        history_cursor = db.chat_messages.find(
            {"session_id": payload.session_id},
            {"_id": 0}
        ).sort("timestamp", 1)
        
        db_history = await history_cursor.to_list(length=15)
        
        system_instruction = SYSTEM_MESSAGE_RO if payload.language == "ro" else SYSTEM_MESSAGE_EN
        
        # 3. Formatăm istoricul și "ascundem" personalitatea în primul mesaj!
        contents = []
        for i, msg in enumerate(db_history):
            role = "user" if msg["role"] == "user" else "model"
            text = msg["content"]
            
            # Dacă este absolut primul mesaj din istoric, lipim instrucțiunile secrete la el
            if i == 0 and role == "user":
                text = f"INSTRUCȚIUNI STRICTE PENTRU TINE: {system_instruction}\n\nAcum răspunde la acest mesaj al utilizatorului: {text}"
                
            contents.append({
                "role": role,
                "parts": [{"text": text}]
            })

        # 4. Construim cererea directă (SUPER SIMPLĂ, fără câmpuri speciale care dau eroare)
        rest_payload = {
            "contents": contents
        }

        # NE ÎNTOARCEM la ușa v1 (aici a funcționat perfect prima dată găsirea modelului)
        url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
        
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=rest_payload, timeout=30.0)
            resp_data = resp.json()
            
            if resp.status_code != 200:
                logging.error(f"Google API Error: {resp_data}")
                raise HTTPException(status_code=500, detail=f"Eroare directă API: {resp_data}")
                
            reply_text = resp_data["candidates"][0]["content"]["parts"][0]["text"]

        # 5. Salvează răspunsul AI în baza de date
        assistant_msg_doc = {
            "id": str(uuid.uuid4()),
            "session_id": payload.session_id,
            "role": "assistant",
            "content": reply_text,
            "language": payload.language,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await db.chat_messages.insert_one(assistant_msg_doc)

        return ChatResponse(
            session_id=payload.session_id,
            reply=reply_text,
            timestamp=assistant_msg_doc["timestamp"],
        )

    except Exception as e:
        logging.error(f"Eroare chat: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Eroare AI: {str(e)}")



@api_router.get("/chat/{session_id}/history")
async def chat_history(session_id: str):
    msgs = await db.chat_messages.find(
        {"session_id": session_id}, {"_id": 0}
    ).sort("timestamp", 1).to_list(500)
    return {"messages": msgs}


# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
