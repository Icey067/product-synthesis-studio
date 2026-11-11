import pytest
from unittest.mock import patch, MagicMock
from services.ollama_client import OllamaClient
from services.generation_service import GenerationService
from services.project_service import ProjectService
from models.schemas import OpportunityResponse, ProjectCreate
import requests

def test_offline_ollama_graceful_handling():
    # Probe nonexistent port
    bad_client = OllamaClient(base_url="http://localhost:59999")
    ok, msg = bad_client.check_connection()
    assert ok is False
    assert "Cannot connect to Ollama" in msg or "Connection error" in msg

    # Calling generate on offline client should return False without raising an unhandled exception
    gen_ok, gen_err = bad_client.generate("Test prompt")
    assert gen_ok is False
    assert "Lost connection" in gen_err or "failed" in gen_err

def test_malformed_json_recovery_and_retry():
    client = OllamaClient()

    # Simulate model returning malformed JSON initially, then valid JSON on retry
    with patch.object(client, "generate") as mock_gen:
        # First call: malformed JSON text
        # Second call: valid JSON matching OpportunityResponse
        mock_gen.side_effect = [
            (True, "Here is the result: { 'opportunities': [ broken json..."),
            (True, '{"opportunities": [{"name": "Retried Opp", "exact_customer": "Retailers", "customer_problem": "Problem", "problem_urgency": "High", "desired_outcome": "Outcome", "potential_solution": "Solution"}]}')
        ]

        ok, validated, err = client.generate_structured(
            prompt="Generate opportunities",
            schema=OpportunityResponse,
            max_retries=1
        )
        assert ok is True
        assert validated is not None
        assert len(validated.opportunities) == 1
        assert validated.opportunities[0].name == "Retried Opp"

def test_interrupted_generation_preserves_existing_chapter_data():
    p_id = ProjectService.create_project(ProjectCreate(name="Interruption Protection Test"))
    
    from database.db import transaction
    with transaction() as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO chapters (project_id, chapter_number, title, purpose, content, word_count, is_locked)
            VALUES (?, 1, 'Protected Chapter', 'Purpose', 'ORIGINAL VALUABLE CHAPTER CONTENT', 4, 1)
            """,
            (p_id,)
        )
        ch_id = cur.lastrowid

    # Simulate interrupted generation (client timeout or exception)
    mock_client = MagicMock(spec=OllamaClient)
    mock_client.generate.return_value = (False, "Generation timed out after 180 seconds.")

    gen_service = GenerationService(mock_client)
    chapter_dict = {"id": ch_id, "chapter_number": 1, "title": "Protected Chapter", "is_locked": 1}
    charter_dict = {"product_title": "Title", "core_promise": "Promise"}

    # Attempt to regenerate locked chapter
    ok, content, msg = gen_service.generate_chapter_content(chapter_dict, charter_dict, [])
    assert ok is False
    assert "locked" in msg.lower()

    # Verify original content in database was NOT destroyed
    from services.outline_service import OutlineService
    chaps = OutlineService.get_chapters(p_id)
    assert chaps[0]["content"] == "ORIGINAL VALUABLE CHAPTER CONTENT"

    ProjectService.delete_project(p_id)

