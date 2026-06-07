"""
Enhanced AI Photo Analysis — Water Category & Class Detection for Flood Claims.

Extends the base AI service with flood-specific analysis:
  1. Water Category determination (1=clean, 2=gray, 3=black/flood)
  2. Water Class determination (1-4 based on evaporation/saturation)
  3. Damage severity scoring
  4. Room-type identification from photos
  5. Line-item suggestion from photo analysis
"""
import os
import base64
import json as _json
from services.ai import call_openrouter, _get_setting


def analyze_flood_photo(image_path, claim_context=None):
    """
    Comprehensive flood damage photo analysis.

    Args:
        image_path: Path to the image file
        claim_context: Optional dict with claim info (flood_source, water_category, etc.)

    Returns:
        Dict with:
          - description: Human-readable damage description
          - water_category: 1, 2, or 3
          - water_class: 1, 2, 3, or 4
          - damage_severity: 'minor', 'moderate', 'severe', 'destroyed'
          - room_type: Detected room type (kitchen, bedroom, etc.)
          - suggested_items: List of suggested line items
          - water_evidence: Dict with water_line_height, standing_water, etc.
          - mold_detected: bool
          - structural_damage: bool
          - confidence: float 0-1
    """
    key = _get_setting('openrouter_api_key') or _get_setting('ai_api_key', '')
    if not key:
        return _empty_analysis('No API key configured')

    model = _get_setting('ai_vision_model') or _get_setting('ai_model', 'openrouter/auto')
    text_only_models = {'openrouter/owl-alpha', 'openrouter/owl', 'openai/o3-mini', 'deepseek/deepseek-r1'}
    if model in text_only_models:
        model = 'openrouter/auto'

    # Build context-aware prompt
    context_str = ''
    if claim_context:
        context_str = f"""
Claim context:
- Flood source: {claim_context.get('flood_source', 'Unknown')}
- Current water category: {claim_context.get('water_category', 'Not yet determined')}
- Current water class: {claim_context.get('water_class', 'Not yet determined')}
- Property type: {claim_context.get('property_type', 'Residential')}
"""

    prompt = f"""You are a certified flood damage inspector with 20+ years of experience.
Analyze this flood damage photo and return a structured JSON response.

{context_str}

WATER CATEGORIES (IICRC S500):
- Category 1 (Clean): Water from sanitary source, no significant health risk
- Category 2 (Gray): Contains significant contamination, potential health risk
- Category 3 (Black): Grossly contaminated, includes flood water, sewage, river water

WATER CLASSES (evaporation/saturation level):
- Class 1: Least affected, <5% of room area wet, low porosity materials
- Class 2: Large area affected, 5-40% wet, carpet and cushion wet
- Class 3: Greatest saturation, >40% wet or water from ceiling/walls
- Class 4: Specialty drying, deep saturation in low-porosity materials (hardwood, concrete, plaster)

Return ONLY valid JSON (no markdown, no code fences):
{{
  "description": "2-3 sentence professional damage description",
  "water_category": 1|2|3,
  "water_class": 1|2|3|4,
  "damage_severity": "minor"|"moderate"|"severe"|"destroyed",
  "room_type": "kitchen"|"bathroom"|"bedroom"|"living_room"|"basement"|"garage"|"utility"|"other",
  "suggested_items": [
    {{"description": "Drywall replacement - 4ft water line", "quantity": 120, "unit": "sf", "unit_cost": 4.50}},
    {{"description": "Antimicrobial treatment", "quantity": 120, "unit": "sf", "unit_cost": 0.50}}
  ],
  "water_evidence": {{
    "water_line_height_inches": 24,
    "standing_water": true|false,
    "water_color": "clear"|"gray"|"brown"|"black",
    "odor_detected": true|false
  }},
  "mold_detected": true|false,
  "mold_location": "string or null",
  "structural_damage": true|false,
  "structural_notes": "string or null",
  "flooring_type": "hardwood"|"tile"|"carpet"|"lvp"|"laminate"|"concrete"|"other",
  "flooring_damage": "none"|"stained"|"warped"|"buckled"|"destroyed",
  "wall_damage": "none"|"water_stain"|"peeling_paint"|"soft_drywall"|"hole"|"missing",
  "ceiling_damage": "none"|"staining"|"sagging"|"collapse",
  "personal_property_damage": ["list of damaged items visible"],
  "confidence": 0.0 to 1.0
}}"""

    try:
        result = ''
        with open(image_path, 'rb') as f:
            img_b64 = base64.b64encode(f.read()).decode()
        ext = image_path.rsplit('.', 1)[-1].lower()
        mime = f'image/{ext}' if ext != 'jpg' else 'image/jpeg'

        result = call_openrouter(
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': prompt},
                    {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}}
                ]
            }],
            model=model,
            key=key,
            max_tokens=1500
        )

        if result.startswith('Error:') or result.startswith('[Used fallback:'):
            if result.startswith('[Used fallback:'):
                result = result.split(']\n\n', 1)[-1] if ']\n\n' in result else result
            else:
                return _empty_analysis(result)

        # Parse JSON from response
        # Handle potential markdown code fences
        clean = result.strip()
        if clean.startswith('```'):
            clean = clean.split('\n', 1)[1] if '\n' in clean else clean
            clean = clean.rsplit('```', 1)[0] if '```' in clean else clean
        clean = clean.strip()

        data = _json.loads(clean)

        # Validate and set defaults
        return {
            'description': data.get('description', ''),
            'water_category': _validate_category(data.get('water_category')),
            'water_class': _validate_class(data.get('water_class')),
            'damage_severity': data.get('damage_severity', 'moderate'),
            'room_type': data.get('room_type', 'other'),
            'suggested_items': data.get('suggested_items', []),
            'water_evidence': data.get('water_evidence', {}),
            'mold_detected': data.get('mold_detected', False),
            'mold_location': data.get('mold_location', None),
            'structural_damage': data.get('structural_damage', False),
            'structural_notes': data.get('structural_notes', None),
            'flooring_type': data.get('flooring_type', 'other'),
            'flooring_damage': data.get('flooring_damage', 'none'),
            'wall_damage': data.get('wall_damage', 'none'),
            'ceiling_damage': data.get('ceiling_damage', 'none'),
            'personal_property_damage': data.get('personal_property_damage', []),
            'confidence': min(1.0, max(0.0, float(data.get('confidence', 0.5)))),
            'raw_response': result[:500],
        }

    except _json.JSONDecodeError as e:
        # If JSON parsing fails, return the raw text as description
        raw_text: str = ''
        try:
            raw_text = result[:500]  # type: ignore[assignment]
        except Exception:
            raw_text = ''
        return _empty_analysis(f'JSON parse error: {e}', raw=raw_text)
    except FileNotFoundError:
        return _empty_analysis('Image file not found')
    except Exception as e:
        return _empty_analysis(f'Analysis error: {str(e)}')


def batch_analyze_photos(photo_paths, claim_context=None):
    """
    Analyze multiple photos and aggregate results.

    Returns:
        Dict with:
          - individual_results: List of per-photo analysis results
          - aggregate: Aggregated water category/class/severity
          - all_suggested_items: Combined suggested line items
          - mold_photos: List of photo paths where mold was detected
          - highest_water_line: Maximum water line height across all photos
    """
    individual = []
    all_items = []
    mold_photos = []
    water_lines = []
    categories = []
    classes = []

    for path in photo_paths:
        result = analyze_flood_photo(path, claim_context)
        result['photo_path'] = path
        result['photo_filename'] = os.path.basename(path)
        individual.append(result)

        if result.get('mold_detected'):
            mold_photos.append(path)

        we = result.get('water_evidence', {})
        if we.get('water_line_height_inches'):
            water_lines.append(we['water_line_height_inches'])

        if result.get('water_category'):
            categories.append(result['water_category'])
        if result.get('water_class'):
            classes.append(result['water_class'])

        all_items.extend(result.get('suggested_items', []))

    # Aggregate: use worst-case values
    agg_category = max(categories) if categories else 3  # Default to 3 (flood)
    agg_class = max(classes) if classes else 2
    max_water_line = max(water_lines) if water_lines else 0

    # Determine overall severity
    severities = [r.get('damage_severity', 'moderate') for r in individual]
    severity_order = {'minor': 0, 'moderate': 1, 'severe': 2, 'destroyed': 3}
    agg_severity = max(severities, key=lambda s: severity_order.get(s, 1))

    # Deduplicate suggested items by description
    seen = set()
    unique_items = []
    for item in all_items:
        desc = item.get('description', '').lower().strip()
        if desc and desc not in seen:
            seen.add(desc)
            unique_items.append(item)

    return {
        'individual_results': individual,
        'aggregate': {
            'water_category': agg_category,
            'water_class': agg_class,
            'damage_severity': agg_severity,
            'highest_water_line_inches': max_water_line,
            'mold_detected_any': len(mold_photos) > 0,
            'total_photos_analyzed': len(photo_paths),
            'photos_with_mold': len(mold_photos),
        },
        'all_suggested_items': unique_items,
        'mold_photos': mold_photos,
    }


def _validate_category(val):
    """Validate water category is 1, 2, or 3."""
    try:
        v = int(val)
        if v in (1, 2, 3):
            return v
    except (TypeError, ValueError):
        pass
    return 3  # Default to Category 3 (flood water)


def _validate_class(val):
    """Validate water class is 1, 2, 3, or 4."""
    try:
        v = int(val)
        if v in (1, 2, 3, 4):
            return v
    except (TypeError, ValueError):
        pass
    return 2  # Default to Class 2


def _empty_analysis(error_msg, raw=None):
    """Return empty analysis result with error info."""
    return {
        'description': f'Analysis unavailable: {error_msg}',
        'water_category': 3,
        'water_class': 2,
        'damage_severity': 'moderate',
        'room_type': 'other',
        'suggested_items': [],
        'water_evidence': {},
        'mold_detected': False,
        'mold_location': None,
        'structural_damage': False,
        'structural_notes': None,
        'flooring_type': 'other',
        'flooring_damage': 'none',
        'wall_damage': 'none',
        'ceiling_damage': 'none',
        'personal_property_damage': [],
        'confidence': 0.0,
        'error': error_msg,
        'raw_response': raw or '',
    }
