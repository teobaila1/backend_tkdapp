"""Backend tests for TaeKwon-Do ITF API."""
import uuid
import time
import pytest


# ---------- Static knowledge endpoints ----------
class TestStaticContent:
    def test_root(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/")
        assert r.status_code == 200
        assert "TaeKwon-Do" in r.json().get("message", "")

    def test_tuls_list_24(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/tuls")
        assert r.status_code == 200
        data = r.json()
        assert "tuls" in data
        assert len(data["tuls"]) == 24
        # Validate structure of first tul
        t0 = data["tuls"][0]
        for k in ("id", "name"):
            assert k in t0

    def test_tuls_chon_ji(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/tuls/chon-ji")
        assert r.status_code == 200
        d = r.json()
        assert d["id"] == "chon-ji"

    def test_tul_not_found(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/tuls/does-not-exist")
        assert r.status_code == 404

    def test_encyclopedia_list_8(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/encyclopedia")
        assert r.status_code == 200
        data = r.json()
        assert "articles" in data
        assert len(data["articles"]) == 8

    def test_encyclopedia_general_choi(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/encyclopedia/general-choi-hong-hi")
        assert r.status_code == 200
        d = r.json()
        assert d["id"] == "general-choi-hong-hi"

    def test_terminology(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/terminology")
        assert r.status_code == 200
        d = r.json()
        assert "terms" in d
        assert len(d["terms"]) > 0

    def test_techniques(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/techniques")
        assert r.status_code == 200
        d = r.json()
        assert "techniques" in d
        assert len(d["techniques"]) > 0

    def test_grading_19(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/grading")
        assert r.status_code == 200
        d = r.json()
        assert "grades" in d
        assert len(d["grades"]) == 19


# ---------- Quiz ----------
class TestQuiz:
    def test_quiz_15(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/quiz")
        assert r.status_code == 200
        d = r.json()
        assert "questions" in d
        assert len(d["questions"]) == 15
        for q in d["questions"]:
            assert "id" in q and "correct" in q
            # bilingual options
            assert "options_en" in q and "options_ro" in q
            assert len(q["options_en"]) >= 2

    def test_quiz_submit_all_correct(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/quiz")
        questions = r.json()["questions"]
        answers = {q["id"]: q["correct"] for q in questions}
        r2 = api_client.post(f"{base_url}/api/quiz/submit", json={"answers": answers})
        assert r2.status_code == 200
        d = r2.json()
        assert d["score"] == 100
        assert d["correct"] == 15
        assert d["total"] == 15

    def test_quiz_submit_partial(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/quiz")
        questions = r.json()["questions"]
        # Wrong answers for all
        answers = {q["id"]: (q["correct"] + 1) % len(q["options_en"]) for q in questions}
        r2 = api_client.post(f"{base_url}/api/quiz/submit", json={"answers": answers})
        assert r2.status_code == 200
        assert r2.json()["correct"] == 0


# ---------- Videos CRUD ----------
class TestVideos:
    def test_video_lifecycle(self, api_client, base_url):
        payload = {
            "title": "TEST_Chon-Ji Tutorial",
            "description": "Test video",
            "youtube_id": "dQw4w9WgXcQ",
            "tul_id": "chon-ji",
            "category": "tul",
            "uploaded_by": "tester",
        }
        r = api_client.post(f"{base_url}/api/videos", json=payload)
        assert r.status_code == 200, r.text
        v = r.json()
        assert v["title"] == payload["title"]
        assert v["youtube_id"] == payload["youtube_id"]
        vid_id = v["id"]
        assert vid_id

        # GET list and verify presence
        r2 = api_client.get(f"{base_url}/api/videos")
        assert r2.status_code == 200
        assert any(x["id"] == vid_id for x in r2.json()["videos"])

        # Filter by category
        r3 = api_client.get(f"{base_url}/api/videos", params={"category": "tul"})
        assert r3.status_code == 200
        assert any(x["id"] == vid_id for x in r3.json()["videos"])

        # Delete
        r4 = api_client.delete(f"{base_url}/api/videos/{vid_id}")
        assert r4.status_code == 200
        assert r4.json().get("deleted") is True

        # Verify deleted
        r5 = api_client.get(f"{base_url}/api/videos")
        assert not any(x["id"] == vid_id for x in r5.json()["videos"])

        # Delete again -> 404
        r6 = api_client.delete(f"{base_url}/api/videos/{vid_id}")
        assert r6.status_code == 404


# ---------- AI Chat ----------
class TestChat:
    def test_chat_english(self, api_client, base_url):
        session_id = f"TEST_session_{uuid.uuid4()}"
        payload = {
            "session_id": session_id,
            "message": "Who is the founder of Taekwon-Do?",
            "language": "en",
        }
        r = api_client.post(f"{base_url}/api/chat", json=payload, timeout=90)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["session_id"] == session_id
        assert isinstance(d["reply"], str) and len(d["reply"]) > 10
        # Reply should mention Choi
        assert "Choi" in d["reply"] or "choi" in d["reply"].lower()

        # History persisted
        time.sleep(0.5)
        r2 = api_client.get(f"{base_url}/api/chat/{session_id}/history")
        assert r2.status_code == 200
        msgs = r2.json()["messages"]
        roles = [m["role"] for m in msgs]
        assert "user" in roles and "assistant" in roles
        assert len(msgs) >= 2

    def test_chat_romanian(self, api_client, base_url):
        session_id = f"TEST_session_ro_{uuid.uuid4()}"
        payload = {
            "session_id": session_id,
            "message": "Cine este fondatorul Taekwon-Do?",
            "language": "ro",
        }
        r = api_client.post(f"{base_url}/api/chat", json=payload, timeout=90)
        assert r.status_code == 200, r.text
        d = r.json()
        assert isinstance(d["reply"], str) and len(d["reply"]) > 10
