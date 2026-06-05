"""
🖼️ Local Vision API Wrapper for FloodClaims Pro Photo-to-Claim
==============================================================

This module provides a drop-in replacement for the OpenRouter vision API
that runs against a LOCAL Ollama vision model on Mingo's machine.

WHY: $0 cost, privacy (photos never leave the machine), works offline.

PREREQUISITE: Mingo must install Ollama + Qwen2-VL first:
    curl -fsSL https://ollama.com/install.sh | sh
    ollama pull qwen2-vl:2b

USAGE in services/ai.py:
    from services.vision_local import local_vision_analyze
    
    result, error = local_vision_analyze(
        image_paths=["/uploads/photo1.jpg", "/uploads/photo2.jpg"],
        room_name="Kitchen"
    )

Author: Django (OWL) — 2026-06-05
"""

import base64
import json
import logging
import os
import requests

logger = logging.getLogger(__name__)

# Ollama runs on localhost:11434 by default
OLLAMA_BASE_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "qwen2-vl:2b")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", 120))

# Analysis prompt — same as the OpenRouter version for consistency
BATCH_ANALYSIS_PROMPT = """You are an expert flood damage insurance adjuster. You will receive multiple photos of {room_name} in a flood-damaged property. Analyze ALL photos together and return ONLY valid JSON (no markdown, no code fences):

{{
  "room": "<room name>",
  "items": [
    {{
      "description": "<specific damaged item>",
      "quantity": <number>,
      "unit": "<sq_ft|each|linear_ft|walls>",
      "estimated_area": "<size if applicable>",
      "condition": "<damage description>",
      "category": "<flooring|walls|ceiling|electrical|plumbing|furniture|personal_property|structural|insulation|other>",
      "priority": "<standard|high_value>"
    }}
  ],
  "summary": "<2-3 sentence overall damage assessment>",
  "water_category": <1|2|3>,
  "water_class": <1|2|3|4>,
  "confidence": "<low|medium|high>",
  "needs_closeup": ["<items needing close-up photos>"]
}}

RULES: Be specific. Flag high-value items (jewelry, designer bags, art, antiques, electronics). If unsure, use "unknown". Return ONLY the JSON, nothing else."""


def check_ollama_available():
    """
    Check if Ollama is running and the vision model is available.
    Returns (available: bool, error_message: str or None)
    """
    try:
        # Check if Ollama is running
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        if resp.status_code != 200:
            return False, f"Ollama returned status {resp.status_code}"
        
        # Check if vision model is loaded
        models = resp.json().get("models", [])
        model_names = [m["name"] for m in models]
        
        if OLLAMA_MODEL not in model_names:
            available = ", ".join(model_names[:5]) if model_names else "none"
            return False, f"Vision model '{OLLAMA_MODEL}' not found. Available: {available}"
        
        return True, None
    except requests.ConnectionError:
        return False, "Ollama is not running on localhost:11434"
    except Exception as e:
        return False, f"Ollama check failed: {str(e)}"


def local_vision_analyze(image_paths, room_name="Unknown Room"):
    """
    Analyze multiple photos using local Ollama vision model.
    
    Args:
        image_paths: List of file paths to images
        room_name: Name of the room being analyzed
    
    Returns:
        (data_dict, error_string) — one will be None
    
    Example:
        data, error = local_vision_analyze(
            ["/uploads/photo1.jpg", "/uploads/photo2.jpg"],
            room_name="Kitchen"
        )
        if error:
            print(f"Error: {error}")
        else:
            for item in data["items"]:
                print(f"  - {item['description']}: {item['quantity']} {item['unit']}")
    """
    # Validate inputs
    if not image_paths:
        return None, "No image paths provided"
    
    # Limit to 20 images per batch (Ollama context window)
    if len(image_paths) > 20:
        logger.warning(f"Truncating {len(image_paths)} images to 20 for Ollama batch")
        image_paths = image_paths[:20]
    
    # Check Ollama availability first
    available, error = check_ollama_available()
    if not available:
        return None, error
    
    # Encode images as base64
    content = []
    for path in image_paths:
        if not os.path.exists(path):
            logger.warning(f"Image not found: {path}, skipping")
            continue
        try:
            with open(path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            # Detect MIME type from extension
            ext = os.path.splitext(path)[1].lower()
            mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
            mime = mime_map.get(ext, "image/jpeg")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{b64}"}
            })
        except Exception as e:
            logger.error(f"Failed to encode {path}: {e}")
    
    if not content:
        return None, "No valid images could be encoded"
    
    # Add text prompt
    prompt = BATCH_ANALYSIS_PROMPT.format(room_name=room_name)
    content.append({"type": "text", "text": prompt})
    
    # Call Ollama
    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        "options": {
            "temperature": 0.1,  # Low temp for consistent JSON output
            "num_predict": 4096
        }
    }
    
    try:
        logger.info(f"Sending {len(content)-1} images to Ollama ({OLLAMA_MODEL}) for room: {room_name}")
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/chat",
            json=payload,
            timeout=OLLAMA_TIMEOUT
        )
        resp.raise_for_status()
        result = resp.json()
        
        # Extract text from Ollama response
        text = result.get("message", {}).get("content", "")
        if not text:
            return None, "Ollama returned empty response"
        
        # Parse JSON from response (handle markdown code fences if present)
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        
        data = json.loads(text)
        logger.info(f"Ollama analysis complete: {len(data.get('items', []))} items detected")
        return data, None
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Ollama JSON response: {e}")
        return None, f"Invalid JSON from Ollama: {str(e)}"
    except requests.Timeout:
        return None, f"Ollama timed out after {OLLAMA_TIMEOUT}s (try fewer images or smaller model)"
    except Exception as e:
        logger.error(f"Ollama request failed: {e}")
        return None, f"Ollama error: {str(e)}"


def get_vision_status():
    """
    Get status of the local vision system.
    Returns a dict with status info for health checks.
    """
    available, error = check_ollama_available()
    return {
        "available": available,
        "model": OLLAMA_MODEL,
        "ollama_url": OLLAMA_BASE_URL,
        "error": error
    }


# ─── Standalone test ───────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    
    print("🔍 FloodClaims Pro — Local Vision API Test")
    print("=" * 50)
    
    # Check status
    status = get_vision_status()
    print(f"\nOllama URL: {status['ollama_url']}")
    print(f"Model: {status['model']}")
    print(f"Available: {'✅ Yes' if status['available'] else '❌ No'}")
    if status['error']:
        print(f"Error: {status['error']}")
        print("\nTo fix:")
        print("  1. Install Ollama: curl -fsSL https://ollama.com/install.sh | sh")
        print("  2. Pull model:     ollama pull qwen2-vl:2b")
        print("  3. Start Ollama:   ollama serve")
        sys.exit(1)
    
    # Test with sample images if provided
    if len(sys.argv) > 1:
        paths = sys.argv[1:]
        room = os.path.basename(os.path.dirname(paths[0])) or "Test Room"
        print(f"\nAnalyzing {len(paths)} photos for room: {room}")
        data, error = local_vision_analyze(paths, room_name=room)
        if error:
            print(f"❌ Error: {error}")
            sys.exit(1)
        print(f"\n✅ Analysis complete!")
        print(f"Room: {data.get('room', 'N/A')}")
        print(f"Items found: {len(data.get('items', []))}")
        print(f"Water category: {data.get('water_category', 'N/A')}")
        print(f"Confidence: {data.get('confidence', 'N/A')}")
        print(f"\nItems:")
        for item in data.get("items", []):
            print(f"  • {item['description']} — {item.get('quantity', 1)} {item.get('unit', 'each')} [{item.get('category', 'unknown')}]")
        if data.get("needs_closeup"):
            print(f"\n⚠️ Needs close-up: {', '.join(data['needs_closeup'])}")
    else:
        print("\n✅ Ollama + vision model is ready!")
        print("Usage: python services/vision_local.py /path/to/photo1.jpg /path/to/photo2.jpg")
