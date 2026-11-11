"""
Live Integration Test with local Ollama service.
Can be executed whenever Ollama is running with an installed model.
Run: python -m tests.test_live_ollama
"""

import sys
from services.ollama_client import OllamaClient
from models.schemas import OpportunityResponse

def run_live_test():
    print("=" * 60)
    print("PRODUCT SYNTHESIS STUDIO — LIVE OLLAMA INTEGRATION TEST")
    print("=" * 60)

    client = OllamaClient()
    connected, msg = client.check_connection()
    print(f"1. Service Check: {'ONLINE' if connected else 'OFFLINE'} ({msg})")

    if not connected:
        print("\n[!] Ollama is not running on localhost:11434.")
        print("    To run this test with live inference, start Ollama:")
        print("    ollama serve")
        return

    models = client.list_models()
    print(f"2. Installed Models Detected: {models}")
    if not models:
        print("\n[!] No models detected in Ollama. Pull recommended model:")
        print("    ollama pull qwen3.5:9b")
        return

    test_model = models[0]
    print(f"3. Running inference probe against: {test_model}...")

    prompt = "Synthesize 3 customer pain points for small coffee shop owners."
    ok, resp = client.generate(prompt=prompt, model=test_model, temperature=0.7)
    if ok:
        print(f"✓ Probe generation successful ({len(resp.split())} words generated).")
        print("\nSample Output Snippet:\n", resp[:300], "...\n")
    else:
        print(f"✗ Probe generation failed: {resp}")

    print("=" * 60)
    print("LIVE INTEGRATION TEST COMPLETE")
    print("=" * 60)

if __name__ == "__main__":
    run_live_test()

