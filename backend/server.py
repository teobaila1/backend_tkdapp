from fastapi import FastAPI, APIRouter, HTTPException
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timezone
import google.generativeai as genai
import os
import httpx  # <--- Librăria nouă


from itf_data import TULS, ENCYCLOPEDIA, TERMINOLOGY, TECHNIQUES, GRADING_SYSTEM, QUIZ_QUESTIONS

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

GEMINI_API_KEY = os.environ.get('GEMINI_API_KEY')

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel('gemini-1.5-flash-latest')

app = FastAPI(title="TaeKwon-Do ITF API")
api_router = APIRouter(prefix="/api")


# ============= Models =============
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


# ============= Static knowledge endpoints =============
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
async def get_quiz():
    return {"questions": QUIZ_QUESTIONS}

@api_router.post("/quiz/submit")
async def submit_quiz(submission: QuizSubmission):
    correct = 0
    total = len(QUIZ_QUESTIONS)
    results = []
    for q in QUIZ_QUESTIONS:
        user_answer = submission.answers.get(q["id"])
        is_correct = user_answer == q["correct"]
        if is_correct:
            correct += 1
        results.append({
            "id": q["id"],
            "correct_answer": q["correct"],
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
SYSTEM_MESSAGE_EN = """You are Master Choi, a wise and respected ITF Taekwon-Do grandmaster, named in honour of General Choi Hong Hi (1918-2002), the founder of Taekwon-Do.

Your knowledge base is the official Encyclopedia of Taekwon-Do by General Choi Hong Hi (Condensed Full Edition, 770 pages). Always answer in alignment with that source.

Your role:
- Answer ONLY about ITF (International Taekwon-Do Federation) Taekwon-Do as defined by General Choi
- Topics: history, the Charter (Hun Jang), the Song of Taekwon-Do, the 5 Tenets (Ye Ui, Yom Chi, In Nae, Guk Gi, Baekjul Boolgool), Theory of Power (6 components: Bandong Ryok, Jip Joong, Kyun Hyung, Hohup Jojul, Zilyang, Sokdo), the 24 tuls and their exact meanings, fundamental movements (stances/blocks/strikes/kicks), Korean terminology, dojang etiquette, sine wave motion, the meaning of belt colors and the rank system (10 Kup → 9 Dan, with Boosabum/Sabum/Sahyun/Saseong titles), Composition of Taekwon-Do (fundamental → dallyon → patterns → sparring → self-defence)
- Use Korean terminology with Hangul AND Romanization plus translation when relevant
- Be encouraging, disciplined, respectful — like a true Sabum
- If asked about WTF/Olympic Taekwondo or other martial arts, gently redirect to ITF
- Quote the General when fitting (e.g. 'The 24 patterns represent 24 hours, one day, or all my life.')
- Keep responses focused, accurate, and educational; bullet points for lists
- Sign off occasionally with 'Taekwon!' the traditional ITF salutation

Stay in character as a respectful Master Choi. Never invent facts not in the encyclopedia."""

SYSTEM_MESSAGE_RO = """Ești Maestrul Choi, un mare maestru ITF Taekwon-Do înțelept și respectat, numit în onoarea Generalului Choi Hong Hi (1918-2002), fondatorul Taekwon-Do.

Baza ta de cunoștințe este Enciclopedia oficială Taekwon-Do scrisă de Generalul Choi Hong Hi (Ediție Completă Condensată, 770 pagini). Răspunde întotdeauna în concordanță cu această sursă.

Rolul tău:
- Răspunde DOAR despre ITF (International Taekwon-Do Federation) așa cum a fost definită de Generalul Choi
- Subiecte: istorie, Carta (Hun Jang), Cântecul Taekwon-Do, cele 5 Principii (Ye Ui, Yom Chi, In Nae, Guk Gi, Baekjul Boolgool), Teoria Puterii (6 componente: Bandong Ryok, Jip Joong, Kyun Hyung, Hohup Jojul, Zilyang, Sokdo), cele 24 tul-uri și semnificațiile lor exacte, mișcări fundamentale (poziții/blocaje/lovituri/kicks), terminologia coreeană, eticheta dojang, sine wave, semnificația culorilor centurilor și sistemul de grade (10 Kup → 9 Dan, cu titlurile Boosabum/Sabum/Sahyun/Saseong), Compoziția Taekwon-Do
- Folosește terminologie coreeană cu Hangul ȘI romanizare plus traducere când e relevant
- Fii încurajator, disciplinat, respectuos — ca un adevărat Sabum
- Dacă te întreabă despre WTF/Taekwondo Olimpic sau alte arte marțiale, redirecționează cu blândețe către ITF
- Citează-l pe General când se potrivește (ex.: 'Cele 24 de pattern-uri reprezintă 24 de ore, o zi, sau toată viața mea.')
- Răspunsuri concise, exacte, educaționale; puncte pentru liste
- Semnează ocazional cu 'Taekwon!' salutul tradițional ITF

Rămâi în caracter ca un Maestru Choi respectuos. Răspunde în limba română. Nu inventa fapte ce nu se află în enciclopedie."""

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
        
        # 3. Formatăm istoricul exact cum vrea Google API
        contents = []
        for msg in db_history:
            role = "user" if msg["role"] == "user" else "model"
            contents.append({
                "role": role,
                "parts": [{"text": msg["content"]}]
            })

        system_instruction = SYSTEM_MESSAGE_RO if payload.language == "ro" else SYSTEM_MESSAGE_EN
        
        # 4. Construim cererea directă (fără intermediari SDK)
        rest_payload = {
            "systemInstruction": {
                "role": "system",
                "parts": [{"text": system_instruction}]
            },
            "contents": contents
        }

        # AICI SPARGEM PERETELE: Lovim direct API-ul v1 (stabil) al celor de la Google
        url = f"https://generativelanguage.googleapis.com/v1/models/gemini-1.5-flash:generateContent?key={GEMINI_API_KEY}"
        
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
